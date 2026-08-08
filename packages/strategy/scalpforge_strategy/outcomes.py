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
class OutcomeConfig:
    horizons_seconds: tuple[int, ...] = (5, 15, 30, 60, 300)
    slippage_bps_per_side: float = 0.5
    maximum_endpoint_delay_seconds: int = 2
    maximum_continuity_gap_seconds: int = 5

    def __post_init__(self) -> None:
        if not self.horizons_seconds or min(self.horizons_seconds) <= 0:
            raise ValueError("outcome horizons must be positive")
        if len(set(self.horizons_seconds)) != len(self.horizons_seconds):
            raise ValueError("outcome horizons must be unique")
        if self.slippage_bps_per_side < 0:
            raise ValueError("slippage cannot be negative")
        if self.maximum_endpoint_delay_seconds < 0:
            raise ValueError("endpoint delay cannot be negative")
        if self.maximum_continuity_gap_seconds <= 0:
            raise ValueError("continuity gap must be positive")


@dataclass(frozen=True)
class OutcomeDatasetManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_feature_dataset_id: str
    source_feature_manifest: str
    row_count: int
    valid_counts: dict[str, int]
    outcome_config: dict[str, object]
    outcome_columns: list[str]
    partitions: list[str]
    horizon_partitions: dict[str, str]
    future_information: bool = True
    join_key: str = "occurred_at"
    external_non_executable: bool = True


def write_outcome_dataset(
    feature_manifest: Path,
    output_root: Path,
    config: OutcomeConfig | None = None,
) -> OutcomeDatasetManifest:
    outcome_config = config or OutcomeConfig()
    serialized_config = json.loads(json.dumps(asdict(outcome_config)))
    source = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if source.get("point_in_time") is not True or source.get("labels_included") is not False:
        raise ValueError("source must be a point-in-time feature dataset without labels")
    source_id = str(source["dataset_id"])
    identity = json.dumps(
        {"source_feature_dataset_id": source_id, "config": serialized_config}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-outcomes-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return OutcomeDatasetManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))

    table = _read_feature_table(feature_manifest, source)
    timestamps, bids, asks, segments = _outcome_inputs(table, outcome_config)
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        partitions: list[str] = []
        horizon_partitions: dict[str, str] = {}
        valid_counts: dict[str, int] = {}
        outcome_columns: list[str] = []
        for horizon in outcome_config.horizons_seconds:
            rows, valid = _build_horizon(
                timestamps, bids, asks, segments, horizon, outcome_config
            )
            horizon_dir = staging / f"horizon={horizon}"
            horizon_dir.mkdir()
            partition = horizon_dir / "outcomes.parquet"
            outcome_table = pa.Table.from_pydict(rows)
            pq.write_table(outcome_table, partition, compression="zstd", row_group_size=100_000)
            final_partition = root / f"horizon={horizon}" / partition.name
            partitions.append(str(final_partition))
            horizon_partitions[str(horizon)] = str(final_partition)
            valid_counts[str(horizon)] = valid
            outcome_columns.extend(
                name for name in outcome_table.column_names if name != "occurred_at"
            )
            del rows, outcome_table
        manifest = OutcomeDatasetManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_feature_dataset_id=source_id,
            source_feature_manifest=str(feature_manifest.resolve()),
            row_count=len(timestamps),
            valid_counts=valid_counts,
            outcome_config=serialized_config,
            outcome_columns=["occurred_at", *outcome_columns],
            partitions=partitions,
            horizon_partitions=horizon_partitions,
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def build_outcome_columns(
    features: pa.Table, config: OutcomeConfig
) -> tuple[dict[str, list[object]], dict[str, int]]:
    timestamps, bids, asks, segments = _outcome_inputs(features, config)
    result: dict[str, list[object]] = {"occurred_at": timestamps}
    valid_counts: dict[str, int] = {}
    for horizon in config.horizons_seconds:
        columns, valid = _build_horizon(timestamps, bids, asks, segments, horizon, config)
        columns.pop("occurred_at")
        result.update(columns)
        valid_counts[str(horizon)] = valid
    return result, valid_counts


def _outcome_inputs(
    features: pa.Table, config: OutcomeConfig
) -> tuple[list[datetime], list[float], list[float], list[int]]:
    timestamps = _utc_timestamps(features["occurred_at"])
    bids = [float(value) for value in features["bid"].to_pylist()]
    asks = [float(value) for value in features["ask"].to_pylist()]
    if not timestamps:
        raise ValueError("feature dataset is empty")
    segments = [0] * len(timestamps)
    for index in range(1, len(timestamps)):
        delta = (timestamps[index] - timestamps[index - 1]).total_seconds()
        segments[index] = segments[index - 1] + int(delta > config.maximum_continuity_gap_seconds)
    return timestamps, bids, asks, segments


def _build_horizon(
    timestamps: list[datetime],
    bids: list[float],
    asks: list[float],
    segments: list[int],
    horizon: int,
    config: OutcomeConfig,
) -> tuple[dict[str, list[object]], int]:
    count = len(timestamps)
    prefix = f"h{horizon}"
    columns: dict[str, list[object]] = {
            "occurred_at": timestamps,
            f"{prefix}_valid": [False] * count,
            f"{prefix}_endpoint_delay_seconds": [None] * count,
            f"{prefix}_long_net_bps": [None] * count,
            f"{prefix}_short_net_bps": [None] * count,
            f"{prefix}_long_mfe_bps": [None] * count,
            f"{prefix}_long_mae_bps": [None] * count,
            f"{prefix}_short_mfe_bps": [None] * count,
            f"{prefix}_short_mae_bps": [None] * count,
    }
    endpoints = _endpoints(timestamps, segments, horizon, config)
    future_bid_max, future_bid_min = _forward_extrema(bids, endpoints)
    future_ask_max, future_ask_min = _forward_extrema(asks, endpoints)
    slip = config.slippage_bps_per_side / 10_000
    valid = 0
    for index, endpoint in enumerate(endpoints):
        if endpoint is None:
            continue
        delay = timestamps[endpoint] - (timestamps[index] + timedelta(seconds=horizon))
        long_entry = asks[index] * (1 + slip)
        long_exit = bids[endpoint] * (1 - slip)
        short_entry = bids[index] * (1 - slip)
        short_exit = asks[endpoint] * (1 + slip)
        columns[f"{prefix}_valid"][index] = True
        columns[f"{prefix}_endpoint_delay_seconds"][index] = delay.total_seconds()
        columns[f"{prefix}_long_net_bps"][index] = (long_exit / long_entry - 1) * 10_000
        columns[f"{prefix}_short_net_bps"][index] = (short_entry / short_exit - 1) * 10_000
        columns[f"{prefix}_long_mfe_bps"][index] = (
            future_bid_max[index] * (1 - slip) / long_entry - 1
        ) * 10_000
        columns[f"{prefix}_long_mae_bps"][index] = (
            future_bid_min[index] * (1 - slip) / long_entry - 1
        ) * 10_000
        columns[f"{prefix}_short_mfe_bps"][index] = (
            short_entry / (future_ask_min[index] * (1 + slip)) - 1
        ) * 10_000
        columns[f"{prefix}_short_mae_bps"][index] = (
            short_entry / (future_ask_max[index] * (1 + slip)) - 1
        ) * 10_000
        valid += 1
    return columns, valid


def _endpoints(
    timestamps: list[datetime],
    segments: list[int],
    horizon: int,
    config: OutcomeConfig,
) -> list[int | None]:
    endpoints: list[int | None] = [None] * len(timestamps)
    right = 0
    for left, timestamp in enumerate(timestamps):
        right = max(right, left + 1)
        target = timestamp + timedelta(seconds=horizon)
        while right < len(timestamps) and timestamps[right] < target:
            right += 1
        if right >= len(timestamps):
            break
        delay = (timestamps[right] - target).total_seconds()
        if delay <= config.maximum_endpoint_delay_seconds and segments[right] == segments[left]:
            endpoints[left] = right
    return endpoints


def _forward_extrema(
    values: list[float], endpoints: list[int | None]
) -> tuple[list[float], list[float]]:
    maxima = [math.nan] * len(values)
    minima = [math.nan] * len(values)
    maximum_queue: deque[int] = deque()
    minimum_queue: deque[int] = deque()
    added = 0
    for left, endpoint in enumerate(endpoints):
        if endpoint is None:
            maximum_queue.clear()
            minimum_queue.clear()
            added = max(added, left + 1)
            continue
        while added <= endpoint:
            while maximum_queue and values[maximum_queue[-1]] <= values[added]:
                maximum_queue.pop()
            maximum_queue.append(added)
            while minimum_queue and values[minimum_queue[-1]] >= values[added]:
                minimum_queue.pop()
            minimum_queue.append(added)
            added += 1
        while maximum_queue and maximum_queue[0] <= left:
            maximum_queue.popleft()
        while minimum_queue and minimum_queue[0] <= left:
            minimum_queue.popleft()
        maxima[left] = values[maximum_queue[0]]
        minima[left] = values[minimum_queue[0]]
    return maxima, minima


def _read_feature_table(feature_manifest: Path, source: dict[str, object]) -> pa.Table:
    root = feature_manifest.resolve().parent
    tables: list[pa.Table] = []
    for stored in source.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("feature partition escapes manifest directory")
        tables.append(pq.read_table(path, columns=["occurred_at", "bid", "ask"]))
    if not tables:
        raise ValueError("feature manifest has no partitions")
    return pa.concat_tables(tables)


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    unit = column.type.unit
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
