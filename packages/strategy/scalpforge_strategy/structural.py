from __future__ import annotations

import hashlib
import json
import shutil
from collections import deque
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class StructuralConfig:
    level_windows_seconds: tuple[int, ...] = (60, 300, 900, 3600)
    breakout_window_seconds: int = 300
    minimum_breakout_bps: float = 0.25

    def __post_init__(self) -> None:
        if not self.level_windows_seconds or min(self.level_windows_seconds) <= 1:
            raise ValueError("level windows must exceed one second")
        if self.breakout_window_seconds not in self.level_windows_seconds:
            raise ValueError("breakout window must be one of the level windows")
        if self.minimum_breakout_bps < 0:
            raise ValueError("minimum breakout distance cannot be negative")


@dataclass(frozen=True)
class StructuralManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_feature_dataset_id: str
    source_feature_manifest: str
    row_count: int
    structural_config: dict[str, object]
    structural_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    tick_vwap_is_proxy: bool = True
    external_non_executable: bool = True


class _Window:
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        self.values: deque[tuple[datetime, float]] = deque()
        self.highs: deque[tuple[datetime, float]] = deque()
        self.lows: deque[tuple[datetime, float]] = deque()

    def expire(self, timestamp: datetime) -> None:
        cutoff = timestamp - timedelta(seconds=self.seconds)
        while self.values and self.values[0][0] < cutoff:
            expired = self.values.popleft()
            if self.highs and self.highs[0] == expired:
                self.highs.popleft()
            if self.lows and self.lows[0] == expired:
                self.lows.popleft()

    def levels(self) -> tuple[float | None, float | None]:
        return (
            self.highs[0][1] if self.highs else None,
            self.lows[0][1] if self.lows else None,
        )

    def add(self, timestamp: datetime, value: float) -> None:
        item = (timestamp, value)
        self.values.append(item)
        while self.highs and self.highs[-1][1] <= value:
            self.highs.pop()
        self.highs.append(item)
        while self.lows and self.lows[-1][1] >= value:
            self.lows.pop()
        self.lows.append(item)


def structural_rows(features: pa.Table, config: StructuralConfig) -> list[dict[str, object]]:
    return list(_iter_structural_rows(features, config))


def _iter_structural_rows(
    features: pa.Table, config: StructuralConfig
) -> Iterator[dict[str, object]]:
    timestamps = _utc_timestamps(features["occurred_at"])
    mids = [float(value) for value in features["mid"].to_pylist()]
    ticks = [int(value) for value in features["tick_count"].to_pylist()]
    gaps = [bool(value) for value in features["is_gap_start"].to_pylist()]
    windows = {seconds: _Window(seconds) for seconds in config.level_windows_seconds}
    session_key: tuple[object, ...] | None = None
    weighted_mid = activity = 0.0
    for timestamp, mid, tick_count, gap in zip(timestamps, mids, ticks, gaps, strict=True):
        current_session = (timestamp.date(), _session(timestamp))
        if current_session != session_key or gap:
            session_key = current_session
            weighted_mid = activity = 0.0
        levels: dict[int, tuple[float | None, float | None]] = {}
        for seconds, window in windows.items():
            window.expire(timestamp)
            levels[seconds] = window.levels()
        weight = max(tick_count, 1)
        weighted_mid += mid * weight
        activity += weight
        tick_vwap = weighted_mid / activity
        breakout_high, breakout_low = levels[config.breakout_window_seconds]
        up_distance = (
            (mid / breakout_high - 1) * 10_000 if breakout_high is not None else None
        )
        down_distance = (
            (breakout_low / mid - 1) * 10_000 if breakout_low is not None else None
        )
        side = 0
        distance = 0.0
        if up_distance is not None and up_distance >= config.minimum_breakout_bps:
            side, distance = 1, up_distance
        elif down_distance is not None and down_distance >= config.minimum_breakout_bps:
            side, distance = -1, down_distance
        range_60 = _range_bps(levels.get(60), mid)
        range_300 = _range_bps(levels.get(300), mid)
        row: dict[str, object] = {
            "occurred_at": timestamp,
            "tick_vwap_proxy": tick_vwap,
            "distance_from_tick_vwap_bps": (mid / tick_vwap - 1) * 10_000,
            "compression_60_to_300": (
                range_60 / range_300 if range_60 is not None and range_300 else None
            ),
            f"breakout_side_{config.breakout_window_seconds}s": side,
            f"breakout_distance_bps_{config.breakout_window_seconds}s": distance,
        }
        for seconds, (high, low) in levels.items():
            row[f"prior_high_{seconds}s"] = high
            row[f"prior_low_{seconds}s"] = low
        yield row
        for window in windows.values():
            window.add(timestamp, mid)


def write_structural_dataset(
    feature_manifest: Path,
    output_root: Path,
    config: StructuralConfig | None = None,
) -> StructuralManifest:
    cfg = config or StructuralConfig()
    source = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if source.get("point_in_time") is not True or source.get("labels_included") is not False:
        raise ValueError("source must contain point-in-time features without labels")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": source["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-structure-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return StructuralManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    table = _read_features(feature_manifest, source)
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    writer: pq.ParquetWriter | None = None
    try:
        partition = staging / "structure.parquet"
        buffer: list[dict[str, object]] = []
        row_count = 0
        columns: list[str] = []
        for row in _iter_structural_rows(table, cfg):
            buffer.append(row)
            row_count += 1
            if len(buffer) == 100_000:
                writer, columns = _write_buffer(buffer, partition, writer)
                buffer = []
        if buffer:
            writer, columns = _write_buffer(buffer, partition, writer)
        if writer is None:
            raise ValueError("feature dataset is empty")
        writer.close()
        writer = None
        final_partition = root / partition.name
        manifest = StructuralManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_feature_dataset_id=str(source["dataset_id"]),
            source_feature_manifest=str(feature_manifest.resolve()),
            row_count=row_count,
            structural_config=serialized,
            structural_columns=columns,
            partitions=[str(final_partition)],
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        if writer is not None:
            writer.close()
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_features(manifest: Path, meta: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    partitions = meta.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise ValueError("feature manifest has no partitions")
    tables = []
    for stored in partitions:
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("feature partition escapes manifest directory")
        tables.append(
            pq.read_table(path, columns=["occurred_at", "mid", "tick_count", "is_gap_start"])
        )
    return pa.concat_tables(tables)


def _range_bps(levels: tuple[float | None, float | None] | None, mid: float) -> float | None:
    if levels is None or levels[0] is None or levels[1] is None:
        return None
    return (levels[0] - levels[1]) / mid * 10_000


def _session(timestamp: datetime) -> str:
    if timestamp.hour < 7:
        return "asia"
    if timestamp.hour < 12:
        return "london"
    if timestamp.hour < 16:
        return "overlap"
    if timestamp.hour < 21:
        return "new_york"
    return "late"


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _write_buffer(
    rows: list[dict[str, object]], path: Path, writer: pq.ParquetWriter | None
) -> tuple[pq.ParquetWriter, list[str]]:
    table = pa.Table.from_pylist(rows)
    writer = writer or pq.ParquetWriter(path, table.schema, compression="zstd")
    writer.write_table(table)
    return writer, table.column_names
