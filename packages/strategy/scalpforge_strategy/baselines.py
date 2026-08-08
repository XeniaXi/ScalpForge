from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.research_dataset import (
    WalkForwardConfig,
    WalkForwardFold,
    anchored_walk_forward_folds,
)


@dataclass(frozen=True)
class BaselineConfig:
    horizon_seconds: int = 60
    decision_interval_seconds: int = 60
    maximum_spread_bps: float = 8.0
    final_holdout_days: int = 4
    walk_forward: WalkForwardConfig = WalkForwardConfig(10, 3, 3, 3, 300, 300)

    def __post_init__(self) -> None:
        if self.horizon_seconds <= 0 or self.decision_interval_seconds <= 0:
            raise ValueError("horizon and decision interval must be positive")
        if self.decision_interval_seconds < self.horizon_seconds:
            raise ValueError("decisions must not overlap their outcome horizons")
        if self.maximum_spread_bps <= 0 or self.final_holdout_days <= 0:
            raise ValueError("spread and holdout must be positive")


@dataclass(frozen=True)
class BaselineMetrics:
    strategy_id: str
    trade_count: int
    fold_count: int
    mean_net_bps: float
    confidence_low_bps: float
    confidence_high_bps: float
    win_rate: float
    profit_factor: float | None
    maximum_drawdown_bps: float


@dataclass(frozen=True)
class BaselineReport:
    report_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    outcome_dataset_id: str
    config: dict[str, object]
    folds: list[dict[str, object]]
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    metrics: list[BaselineMetrics]
    research_only: bool = True


def run_baselines(
    feature_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: BaselineConfig | None = None,
) -> BaselineReport:
    baseline_config = config or BaselineConfig()
    feature_meta = json.loads(feature_manifest.read_text(encoding="utf-8"))
    outcome_meta = json.loads(outcome_manifest.read_text(encoding="utf-8"))
    if outcome_meta.get("source_feature_dataset_id") != feature_meta.get("dataset_id"):
        raise ValueError("outcome dataset does not belong to feature dataset")
    horizon = str(baseline_config.horizon_seconds)
    if int(outcome_meta["valid_counts"].get(horizon, 0)) == 0:
        raise ValueError("requested horizon has no valid outcomes")

    features = _read_manifest_table(feature_manifest, feature_meta)
    outcomes = _read_outcome_horizon(outcome_manifest, outcome_meta, horizon)
    if features.num_rows != outcomes.num_rows:
        raise ValueError("feature and outcome row counts differ")
    feature_times = _utc_timestamps(features["occurred_at"])
    outcome_times = _utc_timestamps(outcomes["occurred_at"])
    if feature_times != outcome_times:
        raise ValueError("feature and outcome timestamps do not align")

    start = feature_times[0].astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    end = feature_times[-1].astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=baseline_config.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, baseline_config.walk_forward)
    if not folds:
        raise ValueError("not enough pre-holdout history for a walk-forward fold")

    results = _evaluate(features, outcomes, folds, baseline_config)
    serialized_config = json.loads(json.dumps(asdict(baseline_config), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized_config,
        },
        sort_keys=True,
    ).encode()
    report_id = "baseline-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / report_id
    path = root / "report.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metrics"] = [BaselineMetrics(**item) for item in payload["metrics"]]
        return BaselineReport(**payload)
    report = BaselineReport(
        report_id=report_id,
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        feature_dataset_id=str(feature_meta["dataset_id"]),
        outcome_dataset_id=str(outcome_meta["dataset_id"]),
        config=serialized_config,
        folds=[_serialize_fold(item) for item in folds],
        final_holdout_start=holdout_start.isoformat(),
        final_holdout_end_exclusive=end.isoformat(),
        holdout_evaluated=False,
        metrics=results,
    )
    root.mkdir(parents=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return report


def _evaluate(
    features: pa.Table,
    outcomes: pa.Table,
    folds: list[WalkForwardFold],
    config: BaselineConfig,
) -> list[BaselineMetrics]:
    timestamps = _utc_timestamps(features["occurred_at"])
    spread = features["spread_bps"].to_pylist()
    momentum = features["return_30s"].to_pylist()
    short_momentum = features["return_5s"].to_pylist()
    volatility = features["realized_volatility_60s"].to_pylist()
    intensity = features["tick_intensity_ratio"].to_pylist()
    spread_shock = features["spread_shock_ratio"].to_pylist()
    gap_start = features["is_gap_start"].to_pylist()
    prefix = f"h{config.horizon_seconds}"
    valid = outcomes[f"{prefix}_valid"].to_pylist()
    long_return = outcomes[f"{prefix}_long_net_bps"].to_pylist()
    short_return = outcomes[f"{prefix}_short_net_bps"].to_pylist()
    strategies = (
        "always_abstain",
        "deterministic_random",
        "simple_momentum",
        "simple_mean_reversion",
        "compression_breakout_proxy",
    )
    values: dict[str, list[float]] = {name: [] for name in strategies}
    folds_seen: dict[str, set[int]] = {name: set() for name in strategies}
    last_decision: dict[str, datetime | None] = {name: None for name in strategies}

    for index, timestamp in enumerate(timestamps):
        fold_number = _test_fold(timestamp, folds)
        if fold_number is None or not valid[index] or gap_start[index]:
            continue
        if spread[index] > config.maximum_spread_bps:
            continue
        for strategy in strategies[1:]:
            previous = last_decision[strategy]
            if (
                previous
                and (timestamp - previous).total_seconds() < config.decision_interval_seconds
            ):
                continue
            side = _decision(
                strategy,
                timestamp,
                momentum[index],
                short_momentum[index],
                volatility[index],
                intensity[index],
                spread_shock[index],
            )
            if side == 0:
                continue
            last_decision[strategy] = timestamp
            values[strategy].append(long_return[index] if side > 0 else short_return[index])
            folds_seen[strategy].add(fold_number)
    return [_metrics(name, values[name], len(folds_seen[name])) for name in strategies]


def _decision(
    strategy: str,
    timestamp: datetime,
    momentum: float | None,
    short_momentum: float | None,
    volatility: float,
    intensity: float,
    spread_shock: float,
) -> int:
    if strategy == "deterministic_random":
        digest = hashlib.sha256(timestamp.isoformat().encode()).digest()
        return 1 if digest[0] % 2 else -1
    if momentum is None or momentum == 0:
        return 0
    if strategy == "simple_momentum":
        return 1 if momentum > 0 else -1
    if strategy == "simple_mean_reversion":
        return -1 if momentum > 0 else 1
    if strategy == "compression_breakout_proxy":
        if short_momentum is None or intensity < 1.5 or spread_shock > 1.5:
            return 0
        if abs(short_momentum) <= max(volatility, 1e-12):
            return 0
        return 1 if short_momentum > 0 else -1
    return 0


def _metrics(strategy: str, returns: list[float], fold_count: int) -> BaselineMetrics:
    if not returns:
        return BaselineMetrics(strategy, 0, fold_count, 0, 0, 0, 0, None, 0)
    count = len(returns)
    mean = sum(returns) / count
    variance = sum((value - mean) ** 2 for value in returns) / max(count - 1, 1)
    margin = 1.96 * math.sqrt(variance / count)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    equity = peak = maximum_drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    return BaselineMetrics(
        strategy_id=strategy,
        trade_count=count,
        fold_count=fold_count,
        mean_net_bps=mean,
        confidence_low_bps=mean - margin,
        confidence_high_bps=mean + margin,
        win_rate=sum(value > 0 for value in returns) / count,
        profit_factor=gains / losses if losses else None,
        maximum_drawdown_bps=maximum_drawdown,
    )


def _test_fold(timestamp: datetime, folds: list[WalkForwardFold]) -> int | None:
    for fold in folds:
        if fold.test_start <= timestamp < fold.test_end_exclusive:
            return fold.fold
    return None


def _serialize_fold(fold: WalkForwardFold) -> dict[str, object]:
    payload = asdict(fold)
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in payload.items()
    }


def _read_manifest_table(path: Path, meta: dict[str, object]) -> pa.Table:
    root = path.resolve().parent
    tables: list[pa.Table] = []
    partitions = meta.get("partitions", [])
    if not isinstance(partitions, list):
        raise ValueError("manifest partitions must be a list")
    for stored in partitions:
        partition = Path(str(stored)).resolve()
        if not partition.is_relative_to(root):
            raise ValueError("partition escapes manifest directory")
        tables.append(pq.read_table(partition))
    if not tables:
        raise ValueError("manifest has no partitions")
    return pa.concat_tables(tables)


def _read_outcome_horizon(path: Path, meta: dict[str, object], horizon: str) -> pa.Table:
    mapping = meta.get("horizon_partitions")
    if not isinstance(mapping, dict) or horizon not in mapping:
        # Schema-v1 compatibility for the original single-file prototype and tests.
        return _read_manifest_table(path, meta)
    root = path.resolve().parent
    partition = Path(str(mapping[horizon])).resolve()
    if not partition.is_relative_to(root):
        raise ValueError("outcome partition escapes manifest directory")
    return pq.read_table(partition)


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    unit = column.type.unit
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
