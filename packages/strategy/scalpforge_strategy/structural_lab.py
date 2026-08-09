from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds


@dataclass(frozen=True)
class StructuralLabConfig:
    horizon_seconds: int = 60
    decision_interval_seconds: int = 60
    breakout_window_seconds: int = 300
    maximum_spread_bps: float = 8.0
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(10, 3, 3, 3, 300, 300)

    def __post_init__(self) -> None:
        if self.horizon_seconds <= 0 or self.decision_interval_seconds < self.horizon_seconds:
            raise ValueError("decisions must be positive and cannot overlap outcome horizons")
        if self.maximum_spread_bps <= 0 or self.final_holdout_days <= 0:
            raise ValueError("spread and holdout limits must be positive")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades <= 0:
            raise ValueError("bootstrap configuration is too small")


@dataclass(frozen=True)
class SliceMetrics:
    slice_type: str
    slice_value: str
    event_count: int
    mean_gross_bps: float
    mean_cost_drag_bps: float
    mean_net_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    paired_random_delta_bps: float
    win_rate: float
    continuation_rate: float
    retest_compatible_rate: float
    sweep_failure_rate: float


@dataclass(frozen=True)
class StructuralLabReport:
    report_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    structural_dataset_id: str
    outcome_dataset_id: str
    config: dict[str, object]
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    fold_count: int
    metrics: list[SliceMetrics]
    research_only: bool = True


@dataclass(frozen=True)
class _Event:
    timestamp: datetime
    session: str
    volatility_regime: str
    spread_regime: str
    activity_regime: str
    gross: float
    cost: float
    net: float
    random_net: float
    classification: str


def run_structural_lab(
    feature_manifest: Path,
    structural_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: StructuralLabConfig | None = None,
) -> StructuralLabReport:
    cfg = config or StructuralLabConfig()
    feature_meta = _meta(feature_manifest)
    structure_meta = _meta(structural_manifest)
    outcome_meta = _meta(outcome_manifest)
    feature_id = str(feature_meta["dataset_id"])
    if structure_meta.get("source_feature_dataset_id") != feature_id:
        raise ValueError("structural dataset does not belong to feature dataset")
    if outcome_meta.get("source_feature_dataset_id") != feature_id:
        raise ValueError("outcome dataset does not belong to feature dataset")
    features = _read_all(feature_manifest, feature_meta)
    structure = _read_all(structural_manifest, structure_meta)
    outcomes = _read_horizon(outcome_manifest, outcome_meta, str(cfg.horizon_seconds))
    if len({features.num_rows, structure.num_rows, outcomes.num_rows}) != 1:
        raise ValueError("research datasets have different row counts")
    timestamps = _utc_timestamps(features["occurred_at"])
    if timestamps != _utc_timestamps(structure["occurred_at"]):
        raise ValueError("structural timestamps do not align")
    if timestamps != _utc_timestamps(outcomes["occurred_at"]):
        raise ValueError("outcome timestamps do not align")
    start = timestamps[0].replace(hour=0, minute=0, second=0, microsecond=0)
    end = timestamps[-1].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    events = _events(features, structure, outcomes, timestamps, folds, cfg)
    metrics = _slice_metrics(events, cfg)
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_id,
            "structure": structure_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "structural-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / report_id
    path = root / "report.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metrics"] = [SliceMetrics(**item) for item in payload["metrics"]]
        return StructuralLabReport(**payload)
    report = StructuralLabReport(
        report_id=report_id,
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        feature_dataset_id=feature_id,
        structural_dataset_id=str(structure_meta["dataset_id"]),
        outcome_dataset_id=str(outcome_meta["dataset_id"]),
        config=serialized,
        final_holdout_start=holdout_start.isoformat(),
        final_holdout_end_exclusive=end.isoformat(),
        holdout_evaluated=False,
        fold_count=len(folds),
        metrics=metrics,
    )
    root.mkdir(parents=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return report


def _events(features, structure, outcomes, timestamps, folds, cfg) -> list[_Event]:
    mids = features["mid"].to_pylist()
    spreads = features["spread_bps"].to_pylist()
    sessions = features["session"].to_pylist()
    volatility = features["realized_volatility_60s"].to_pylist()
    activity = features["tick_intensity_ratio"].to_pylist()
    sides = structure[f"breakout_side_{cfg.breakout_window_seconds}s"].to_pylist()
    valid = outcomes[f"h{cfg.horizon_seconds}_valid"].to_pylist()
    delays = outcomes[f"h{cfg.horizon_seconds}_endpoint_delay_seconds"].to_pylist()
    longs = outcomes[f"h{cfg.horizon_seconds}_long_net_bps"].to_pylist()
    shorts = outcomes[f"h{cfg.horizon_seconds}_short_net_bps"].to_pylist()
    long_mfe = outcomes[f"h{cfg.horizon_seconds}_long_mfe_bps"].to_pylist()
    long_mae = outcomes[f"h{cfg.horizon_seconds}_long_mae_bps"].to_pylist()
    short_mfe = outcomes[f"h{cfg.horizon_seconds}_short_mfe_bps"].to_pylist()
    short_mae = outcomes[f"h{cfg.horizon_seconds}_short_mae_bps"].to_pylist()
    timestamp_index = {value: index for index, value in enumerate(timestamps)}
    selected: list[_Event] = []
    last: datetime | None = None
    for index, timestamp in enumerate(timestamps):
        if not _in_tests(timestamp, folds) or not valid[index] or not sides[index]:
            continue
        if spreads[index] > cfg.maximum_spread_bps:
            continue
        if last and (timestamp - last).total_seconds() < cfg.decision_interval_seconds:
            continue
        endpoint_time = timestamp + timedelta(seconds=cfg.horizon_seconds + delays[index])
        endpoint = timestamp_index.get(endpoint_time)
        if endpoint is None:
            continue
        side = int(sides[index])
        gross = (mids[endpoint] / mids[index] - 1) * 10_000 * side
        net = float(longs[index] if side > 0 else shorts[index])
        random_side = 1 if hashlib.sha256(timestamp.isoformat().encode()).digest()[0] % 2 else -1
        random_net = float(longs[index] if random_side > 0 else shorts[index])
        mfe = float(long_mfe[index] if side > 0 else short_mfe[index])
        mae = float(long_mae[index] if side > 0 else short_mae[index])
        classification = "indeterminate"
        if net > 0 and mfe > abs(mae):
            classification = "continuation"
        if net > 0 and mae < -max(spreads[index], 1.0):
            classification = "retest_compatible_continuation"
        elif net <= 0 and mfe > 0:
            classification = "sweep_failure"
        selected.append(
            _Event(
                timestamp,
                str(sessions[index]),
                _bucket(float(volatility[index]) * 10_000, 0.5, 1.5),
                _bucket(float(spreads[index]), 1.5, 3.0),
                _bucket(float(activity[index]), 0.8, 1.5),
                gross,
                gross - net,
                net,
                random_net,
                classification,
            )
        )
        last = timestamp
    return selected


def _slice_metrics(events: list[_Event], cfg: StructuralLabConfig) -> list[SliceMetrics]:
    groups: list[tuple[str, str, list[_Event]]] = [("all", "all", events)]
    for field in ("session", "volatility_regime", "spread_regime", "activity_regime"):
        values = sorted({str(getattr(event, field)) for event in events})
        groups.extend(
            (field, value, [e for e in events if getattr(e, field) == value])
            for value in values
        )
    return [_metrics(kind, value, group, cfg) for kind, value, group in groups]


def _metrics(kind, value, events, cfg) -> SliceMetrics:
    if not events:
        return SliceMetrics(kind, value, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    nets = [event.net for event in events]
    low, high = stationary_block_interval(
        nets, cfg.bootstrap_samples, cfg.bootstrap_block_trades, seed=20260809
    )
    count = len(events)
    return SliceMetrics(
        kind,
        value,
        count,
        _mean([event.gross for event in events]),
        _mean([event.cost for event in events]),
        _mean(nets),
        low,
        high,
        _mean([event.net - event.random_net for event in events]),
        sum(event.net > 0 for event in events) / count,
        sum(event.classification == "continuation" for event in events) / count,
        sum(event.classification == "retest_compatible_continuation" for event in events) / count,
        sum(event.classification == "sweep_failure" for event in events) / count,
    )


def stationary_block_interval(
    values: list[float], samples: int, block_size: int, seed: int
) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    means: list[float] = []
    count = len(values)
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < count:
            start = rng.randrange(count)
            sample.extend(values[(start + offset) % count] for offset in range(block_size))
        means.append(_mean(sample[:count]))
    means.sort()
    return means[int(samples * 0.025)], means[min(int(samples * 0.975), samples - 1)]


def _bucket(value: float, low: float, high: float) -> str:
    if value < low:
        return "low"
    if value < high:
        return "medium"
    return "high"


def _in_tests(timestamp, folds) -> bool:
    return any(fold.test_start <= timestamp < fold.test_end_exclusive for fold in folds)


def _mean(values: list[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


def _meta(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_all(manifest: Path, meta: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    parts = meta.get("partitions")
    if not isinstance(parts, list) or not parts:
        raise ValueError("manifest has no partitions")
    tables = []
    for stored in parts:
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("partition escapes manifest directory")
        tables.append(pq.read_table(path))
    return pa.concat_tables(tables)


def _read_horizon(manifest, meta, horizon) -> pa.Table:
    mapping = meta.get("horizon_partitions")
    if not isinstance(mapping, dict) or horizon not in mapping:
        raise ValueError("outcome manifest does not contain requested horizon")
    root = manifest.resolve().parent
    path = Path(str(mapping[horizon])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("outcome partition escapes manifest directory")
    return pq.read_table(path)


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
