from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class MultiHourConfig:
    decision_bar_seconds: int = 300
    return_windows_seconds: tuple[int, ...] = (1800, 7200, 28800, 43200)
    level_windows_seconds: tuple[int, ...] = (14400, 28800, 43200)
    short_volatility_seconds: int = 1800
    long_volatility_seconds: int = 7200
    maximum_open_quote_silence_seconds: int = 60
    schema_revision: int = 4

    def __post_init__(self) -> None:
        if self.decision_bar_seconds <= 0:
            raise ValueError("decision bar must be positive")
        windows = self.return_windows_seconds + self.level_windows_seconds
        if not windows or any(value % self.decision_bar_seconds for value in windows):
            raise ValueError("all windows must be positive multiples of the decision bar")
        if self.short_volatility_seconds >= self.long_volatility_seconds:
            raise ValueError("short volatility window must be shorter than long window")
        if self.maximum_open_quote_silence_seconds <= 0:
            raise ValueError("maximum open quote silence must be positive")


@dataclass(frozen=True)
class MultiHourManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_feature_dataset_id: str
    source_feature_manifest: str
    row_count: int
    first_timestamp: str
    last_timestamp: str
    config: dict[str, object]
    feature_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    evaluation_role: str = "development_only"
    holdout_eligible: bool = False
    external_non_executable: bool = True


def multi_hour_rows(
    features: pa.Table, config: MultiHourConfig | None = None
) -> list[dict[str, object]]:
    return _derive_rows(
        _aggregate_tables([features], config or MultiHourConfig()), config or MultiHourConfig()
    )


def write_multi_hour_dataset(
    feature_manifest: Path, output_root: Path, config: MultiHourConfig | None = None
) -> MultiHourManifest:
    cfg = config or MultiHourConfig()
    source = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if source.get("point_in_time") is not True or source.get("labels_included") is not False:
        raise ValueError("source must be point-in-time and label-free")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": source["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-multi-hour-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return MultiHourManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        bars = _aggregate_tables(_read_tables(feature_manifest, source), cfg)
        rows = _derive_rows(bars, cfg)
        if not rows:
            raise ValueError("source produced no complete decision bars")
        partition = staging / "multi-hour.parquet"
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, partition, compression="zstd", row_group_size=10_000)
        final_partition = root / partition.name
        manifest = MultiHourManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_feature_dataset_id=str(source["dataset_id"]),
            source_feature_manifest=str(feature_manifest.resolve()),
            row_count=len(rows),
            first_timestamp=rows[0]["occurred_at"].isoformat(),  # type: ignore[union-attr]
            last_timestamp=rows[-1]["occurred_at"].isoformat(),  # type: ignore[union-attr]
            config=serialized,
            feature_columns=table.column_names,
            partitions=[str(final_partition)],
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _aggregate_tables(tables: object, cfg: MultiHourConfig) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for table in tables:  # type: ignore[union-attr]
        names = table.column_names
        required = {
            "occurred_at",
            "bid",
            "ask",
            "mid",
            "spread_bps",
            "tick_count",
            "quote_change_count",
            "is_gap_start",
            "seconds_since_previous_active_bar",
        }
        if not required.issubset(names):
            raise ValueError("feature source is missing required quote columns")
        columns = {name: table[name].to_pylist() for name in required if name != "occurred_at"}
        times = _timestamps(table["occurred_at"])
        for index, timestamp in enumerate(times):
            epoch = int(timestamp.timestamp())
            bucket = epoch - epoch % cfg.decision_bar_seconds
            bucket_at = datetime.fromtimestamp(bucket, UTC)
            if current is None or current["bar_open_at"] != bucket_at:
                if current is not None:
                    bars.append(current)
                mid = float(columns["mid"][index])
                current = {
                    "bar_open_at": bucket_at,
                    "occurred_at": timestamp,
                    "feature_available_at": bucket_at + timedelta(seconds=cfg.decision_bar_seconds),
                    "bar_open": mid,
                    "bar_open_bid": float(columns["bid"][index]),
                    "bar_open_ask": float(columns["ask"][index]),
                    "bar_high": mid,
                    "bar_low": mid,
                    "bar_close": mid,
                    "bid": float(columns["bid"][index]),
                    "ask": float(columns["ask"][index]),
                    "spread_bps": float(columns["spread_bps"][index]),
                    "tick_count": 0,
                    "quote_change_count": 0,
                    "underlying_observation_count": 0,
                    "observed_seconds": 0,
                    "maximum_quote_silence_seconds": 0.0,
                    "scheduled_offline_overlap": None,
                    "bar_complete": True,
                    "is_gap_start": False,
                }
            mid = float(columns["mid"][index])
            current["occurred_at"] = timestamp
            current["bar_high"] = max(float(current["bar_high"]), mid)
            current["bar_low"] = min(float(current["bar_low"]), mid)
            current["bar_close"] = mid
            current["bid"] = float(columns["bid"][index])
            current["ask"] = float(columns["ask"][index])
            current["spread_bps"] = float(columns["spread_bps"][index])
            current["tick_count"] = int(current["tick_count"]) + int(columns["tick_count"][index])
            current["quote_change_count"] = int(current["quote_change_count"]) + int(
                columns["quote_change_count"][index]
            )
            current["underlying_observation_count"] = int(
                current["underlying_observation_count"]
            ) + 1
            current["observed_seconds"] = int(current["observed_seconds"]) + 1
            silence = columns["seconds_since_previous_active_bar"][index]
            silence_seconds = float(silence) if silence is not None else 0.0
            current["maximum_quote_silence_seconds"] = max(
                float(current["maximum_quote_silence_seconds"]), silence_seconds
            )
            if silence_seconds > cfg.maximum_open_quote_silence_seconds:
                current["bar_complete"] = False
                current["is_gap_start"] = True
    if current is not None:
        bars.append(current)
    return bars


def _derive_rows(bars: list[dict[str, object]], cfg: MultiHourConfig) -> list[dict[str, object]]:
    history: deque[dict[str, object]] = deque()
    maximum = max(
        cfg.return_windows_seconds
        + cfg.level_windows_seconds
        + (cfg.short_volatility_seconds, cfg.long_volatility_seconds)
    )
    rows: list[dict[str, object]] = []
    for bar in bars:
        timestamp = bar["bar_open_at"]
        assert isinstance(timestamp, datetime)
        cutoff = timestamp - timedelta(seconds=maximum)
        while history and history[0]["bar_open_at"] < cutoff:  # type: ignore[operator]
            history.popleft()
        close = float(bar["bar_close"])
        row = dict(bar)
        row["bar_return_bps"] = (close / float(bar["bar_open"]) - 1) * 10_000
        for seconds in cfg.return_windows_seconds:
            values = _window(history, timestamp, seconds)
            row[f"return_{seconds}s_bps"] = (
                ((close / float(values[0]["bar_open"]) - 1) * 10_000) if values else None
            )
            row[f"path_efficiency_{seconds}s"] = _efficiency(values + [bar])
        for seconds in cfg.level_windows_seconds:
            values = _window(history, timestamp, seconds)
            high = max((float(item["bar_high"]) for item in values), default=None)
            low = min((float(item["bar_low"]) for item in values), default=None)
            row[f"prior_high_{seconds}s"] = high
            row[f"prior_low_{seconds}s"] = low
            up = (close / high - 1) * 10_000 if high else None
            down = (low / close - 1) * 10_000 if low else None
            row[f"breakout_side_{seconds}s"] = (
                1 if up is not None and up > 0 else (-1 if down is not None and down > 0 else 0)
            )
            row[f"breakout_distance_{seconds}s_bps"] = max(up or 0.0, down or 0.0)
        short_vol = _volatility(_window(history, timestamp, cfg.short_volatility_seconds) + [bar])
        long_vol = _volatility(_window(history, timestamp, cfg.long_volatility_seconds) + [bar])
        row["realized_volatility_short_bps"] = short_vol
        row["realized_volatility_long_bps"] = long_vol
        row["volatility_expansion_ratio"] = (
            short_vol / long_vol if short_vol is not None and long_vol not in (None, 0.0) else None
        )
        row["session"] = _session(timestamp)
        rows.append(row)
        history.append(bar)
    return rows


def _window(
    history: deque[dict[str, object]], timestamp: datetime, seconds: int
) -> list[dict[str, object]]:
    cutoff = timestamp - timedelta(seconds=seconds)
    return [item for item in history if item["bar_open_at"] >= cutoff]


def _efficiency(values: list[dict[str, object]]) -> float | None:
    closes = [float(item["bar_close"]) for item in values]
    if len(closes) < 2:
        return None
    distance = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    return abs(closes[-1] - closes[0]) / distance if distance else 0.0


def _volatility(values: list[dict[str, object]]) -> float | None:
    closes = [float(item["bar_close"]) for item in values]
    if len(closes) < 2:
        return None
    returns = [(closes[index] / closes[index - 1] - 1) * 10_000 for index in range(1, len(closes))]
    # Normalize by observation count so windows of different lengths are
    # comparable.  The previous root-sum-of-squares definition made the long
    # window structurally dominate its nested short window, preventing a true
    # volatility-expansion ratio from exceeding one in ordinary data.
    return math.sqrt(sum(value * value for value in returns) / len(returns))


def _read_tables(manifest: Path, meta: dict[str, object]):
    root = manifest.resolve().parent
    for stored in meta.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("feature partition escapes manifest directory")
        for batch in pq.ParquetFile(path).iter_batches(batch_size=100_000):
            yield pa.Table.from_batches([batch])


def _timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[column.type.unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _session(timestamp: datetime) -> str:
    return (
        "asia"
        if timestamp.hour < 7
        else "london"
        if timestamp.hour < 12
        else "overlap"
        if timestamp.hour < 16
        else "new_york"
        if timestamp.hour < 21
        else "late"
    )
