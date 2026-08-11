from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class SessionWindow:
    name: str
    start_hour_utc: int
    end_hour_utc: int

    def __post_init__(self) -> None:
        if not self.name or not 0 <= self.start_hour_utc < self.end_hour_utc < 24:
            raise ValueError("session window must have a name and increasing UTC hours")


@dataclass(frozen=True)
class SessionRangeConfig:
    schema_revision: int = 1
    windows: tuple[SessionWindow, ...] = (
        SessionWindow("asia", 0, 7),
        SessionWindow("london_open", 7, 8),
        SessionWindow("new_york_open", 12, 13),
    )
    minimum_breakout_bps: float = 0.25

    def __post_init__(self) -> None:
        if self.schema_revision != 1 or self.minimum_breakout_bps < 0:
            raise ValueError("invalid session-range configuration")
        names = [window.name for window in self.windows]
        if not names or len(names) != len(set(names)):
            raise ValueError("session windows must be non-empty and uniquely named")


@dataclass(frozen=True)
class SessionRangeManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_feature_dataset_id: str
    source_feature_manifest: str
    row_count: int
    session_config: dict[str, object]
    session_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    utc_windows_are_fixed_not_dst_adjusted: bool = True
    external_non_executable: bool = True


@dataclass
class _Range:
    high: float | None = None
    low: float | None = None
    observations: int = 0

    def observe(self, mid: float) -> None:
        self.high = mid if self.high is None else max(self.high, mid)
        self.low = mid if self.low is None else min(self.low, mid)
        self.observations += 1


class CausalSessionRangeBuilder:
    def __init__(self, config: SessionRangeConfig | None = None) -> None:
        self.config = config or SessionRangeConfig()
        self._day: date | None = None
        self._ranges: dict[str, _Range] = {}

    def row(self, occurred_at: datetime, mid: float) -> dict[str, object]:
        timestamp = occurred_at.astimezone(UTC)
        if self._day != timestamp.date():
            self._day = timestamp.date()
            self._ranges = {window.name: _Range() for window in self.config.windows}
        result: dict[str, object] = {
            "occurred_at": timestamp,
            "session_day_utc": timestamp.date().isoformat(),
        }
        current_time = timestamp.timetz().replace(tzinfo=None)
        for window in self.config.windows:
            start = time(window.start_hour_utc)
            end = time(window.end_hour_utc)
            state = self._ranges[window.name]
            complete = current_time >= end and state.observations > 0
            high = state.high if complete else None
            low = state.low if complete else None
            result[f"{window.name}_available_at"] = (
                datetime.combine(timestamp.date(), end, UTC) if complete else None
            )
            result[f"{window.name}_high"] = high
            result[f"{window.name}_low"] = low
            result[f"{window.name}_width_bps"] = (
                (high - low) / ((high + low) / 2) * 10_000
                if high is not None and low is not None
                else None
            )
            breakout = 0
            distance = None
            if complete and high is not None and low is not None:
                above = (mid - high) / high * 10_000
                below = (low - mid) / low * 10_000
                breakout = int(above >= self.config.minimum_breakout_bps) - int(
                    below >= self.config.minimum_breakout_bps
                )
                distance = above if breakout > 0 else below if breakout < 0 else 0.0
            result[f"{window.name}_breakout_side"] = breakout
            result[f"{window.name}_breakout_distance_bps"] = distance
            if start <= current_time < end:
                state.observe(mid)
        return result


def write_session_range_dataset(
    feature_manifest: Path,
    output_root: Path,
    config: SessionRangeConfig | None = None,
    *,
    batch_rows: int = 50_000,
) -> SessionRangeManifest:
    cfg = config or SessionRangeConfig()
    if batch_rows <= 0:
        raise ValueError("batch rows must be positive")
    source = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if not source.get("point_in_time") or source.get("labels_included"):
        raise ValueError("session ranges require causal, label-free features")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": source["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-session-ranges-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return SessionRangeManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    partition = staging / "session-ranges.parquet"
    builder = CausalSessionRangeBuilder(cfg)
    writer: pq.ParquetWriter | None = None
    count = 0
    try:
        for source_partition in source["partitions"]:
            parquet = pq.ParquetFile(source_partition)
            for batch in parquet.iter_batches(
                batch_size=batch_rows, columns=["occurred_at", "mid"]
            ):
                table = pa.Table.from_batches([batch])
                timestamps = _utc_timestamps(table["occurred_at"])
                mids = table["mid"].to_pylist()
                rows = [
                    builder.row(timestamp, float(mid))
                    for timestamp, mid in zip(timestamps, mids, strict=True)
                ]
                output = pa.Table.from_pylist(rows, schema=_schema(cfg))
                writer = writer or pq.ParquetWriter(partition, output.schema, compression="zstd")
                writer.write_table(output)
                count += len(rows)
        if writer is None:
            raise ValueError("feature dataset contains no rows")
        columns = writer.schema.names
        writer.close()
        writer = None
        final_partition = root / partition.name
        manifest = SessionRangeManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_feature_dataset_id=str(source["dataset_id"]),
            source_feature_manifest=str(feature_manifest.resolve()),
            row_count=count,
            session_config=serialized,
            session_columns=columns,
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


def _schema(config: SessionRangeConfig) -> pa.Schema:
    fields = [
        pa.field("occurred_at", pa.timestamp("us", tz="UTC")),
        pa.field("session_day_utc", pa.string()),
    ]
    for window in config.windows:
        fields.extend(
            [
                pa.field(f"{window.name}_available_at", pa.timestamp("us", tz="UTC")),
                pa.field(f"{window.name}_high", pa.float64()),
                pa.field(f"{window.name}_low", pa.float64()),
                pa.field(f"{window.name}_width_bps", pa.float64()),
                pa.field(f"{window.name}_breakout_side", pa.int64()),
                pa.field(f"{window.name}_breakout_distance_bps", pa.float64()),
            ]
        )
    return pa.schema(fields)


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
