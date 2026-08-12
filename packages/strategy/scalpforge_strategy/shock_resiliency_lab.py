from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval

POLICIES = ("classified", "fade_all", "continue_all", "random_direction")


@dataclass(frozen=True)
class ShockResiliencyConfig:
    analysis_revision: int = 1
    shock_mad_multiplier: float = 6.0
    threshold_sample_interval_seconds: int = 60
    horizons_seconds: tuple[int, ...] = (30, 60, 300)
    minimum_episode_spacing_seconds: int = 300
    normalized_spread_ceiling: float = 1.25
    retracement_fraction: float = 0.10
    minimum_test_trades: int = 100
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1 or self.shock_mad_multiplier <= 0:
            raise ValueError("invalid shock analysis configuration")
        if self.threshold_sample_interval_seconds < 1:
            raise ValueError("threshold sample interval must be positive")
        if self.horizons_seconds != (30, 60, 300):
            raise ValueError("only preregistered 30, 60 and 300 second horizons are supported")
        if self.minimum_episode_spacing_seconds < max(self.horizons_seconds):
            raise ValueError("shock episodes cannot overlap the longest outcome")


def classify_shock(row, config: ShockResiliencyConfig) -> int:
    """Return fade=-1, continue=1 or abstain=0 using decision-time features only."""
    shock = float(row["return_5s"])
    recent = float(row["return_1s"] or 0.0)
    spread = float(row["spread_shock_ratio"])
    activity = float(row["tick_intensity_ratio"])
    aligned = shock * recent > 0
    retracing = shock * recent < 0 and abs(recent) >= abs(shock) * config.retracement_fraction
    normalized = spread <= config.normalized_spread_ceiling
    if normalized and retracing:
        return -1
    if normalized and aligned and activity >= 1.0:
        return 1
    return 0


def run_shock_resiliency_lab(
    feature_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: ShockResiliencyConfig | None = None,
) -> dict[str, object]:
    cfg = config or ShockResiliencyConfig()
    feature_meta = _meta(feature_manifest)
    outcome_meta = _meta(outcome_manifest)
    _validate(feature_meta, outcome_meta, cfg)
    start, end = _bounds(feature_meta)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    results: dict[tuple[str, int], list[dict[str, object]]] = {
        (policy, horizon): [] for policy in POLICIES for horizon in cfg.horizons_seconds
    }
    fold_results: dict[tuple[str, int], list[tuple[int, float]]] = {
        key: [] for key in results
    }
    thresholds = _fold_thresholds(feature_manifest, feature_meta, folds, cfg)
    rows_by_fold = _test_shocks(
        feature_manifest,
        feature_meta,
        outcome_manifest,
        outcome_meta,
        folds,
        thresholds,
        cfg,
    )
    for fold in folds:
        fold_rows = rows_by_fold[fold.fold]
        for key in results:
            chosen = [
                row
                for row in fold_rows
                if row["policy"] == key[0] and row["horizon"] == key[1]
            ]
            results[key].extend(chosen)
            fold_results[key].append(
                (len(chosen), _mean([float(row["net_bps"]) for row in chosen]))
            )
    metrics = [
        _metrics(policy, horizon, results[(policy, horizon)], fold_results[(policy, horizon)], cfg)
        for policy in POLICIES
        for horizon in cfg.horizons_seconds
    ]
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "shock-resiliency-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "feature_dataset_id": feature_meta["dataset_id"],
        "outcome_dataset_id": outcome_meta["dataset_id"],
        "config": serialized,
        "policy_count": len(POLICIES),
        "hypothesis_count": len(POLICIES) * len(cfg.horizons_seconds),
        "final_holdout_start": holdout_start.isoformat(),
        "final_holdout_end_exclusive": end.isoformat(),
        "holdout_evaluated": False,
        "fold_count": len(folds),
        "metrics": metrics,
        "quote_pressure_not_true_order_flow": True,
        "second_feed_replication_required": True,
        "research_only": True,
        "real_money_enabled": False,
    }
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="shock-resiliency-lab",
        dataset_ids=(str(feature_meta["dataset_id"]), str(outcome_meta["dataset_id"])),
        hypothesis_count=len(POLICIES) * len(cfg.horizons_seconds),
        holdout_evaluated=False,
    )
    return report


def _test_shocks(fm, fmeta, om, ometa, folds, thresholds, cfg):
    selected = {fold.fold: [] for fold in folds}
    last_episode: dict[int, datetime | None] = {fold.fold: None for fold in folds}
    for features, outcomes in _aligned_batches(fm, fmeta, om, ometa, cfg):
        timestamps = _utc_timestamps(features["occurred_at"])
        returns = features["return_5s"].to_pylist()
        gaps = features["is_gap_start"].to_pylist()
        for index, timestamp in enumerate(timestamps):
            fold = next(
                (
                    candidate
                    for candidate in folds
                    if candidate.test_start
                    <= timestamp
                    < candidate.test_end_exclusive
                ),
                None,
            )
            if fold is None:
                continue
            value = returns[index]
            if (
                value is None
                or abs(float(value)) < thresholds[fold.fold]
                or gaps[index]
            ):
                continue
            if (
                last_episode[fold.fold]
                and (timestamp - last_episode[fold.fold]).total_seconds()
                < cfg.minimum_episode_spacing_seconds
            ):
                continue
            last_episode[fold.fold] = timestamp
            row = {name: features[name][index].as_py() for name in features.column_names}
            shock_side = 1 if float(value) > 0 else -1
            classification = classify_shock(row, cfg)
            directions = {
                "classified": shock_side * classification,
                "fade_all": -shock_side,
                "continue_all": shock_side,
                "random_direction": _random_side(timestamp),
            }
            for policy, direction in directions.items():
                if direction == 0:
                    continue
                for horizon in cfg.horizons_seconds:
                    outcome = outcomes[horizon]
                    prefix = f"h{horizon}"
                    if not outcome[f"{prefix}_valid"][index].as_py():
                        continue
                    side = "long" if direction > 0 else "short"
                    selected[fold.fold].append(
                        {
                            "policy": policy,
                            "horizon": horizon,
                            "occurred_at": timestamp,
                            "gross_bps": float(
                                outcome[f"{prefix}_{side}_gross_bps"][index].as_py()
                            ),
                            "net_bps": float(
                                outcome[f"{prefix}_{side}_net_bps"][index].as_py()
                            ),
                        }
                    )
    return selected


def _fold_thresholds(manifest, meta, folds, cfg):
    values = {fold.fold: [] for fold in folds}
    for path in _paths(manifest, meta):
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=100_000, columns=["occurred_at", "return_5s", "is_gap_start"]
        ):
            timestamps = _utc_timestamps(batch.column("occurred_at"))
            returns = batch.column("return_5s").to_pylist()
            gaps = batch.column("is_gap_start").to_pylist()
            for timestamp, value, gap in zip(timestamps, returns, gaps, strict=True):
                if value is None or gap:
                    continue
                if int(timestamp.timestamp()) % cfg.threshold_sample_interval_seconds:
                    continue
                for fold in folds:
                    if fold.train_start <= timestamp < fold.train_end_exclusive:
                        values[fold.fold].append(abs(float(value)))
    return {
        fold.fold: _robust_threshold(values[fold.fold], cfg.shock_mad_multiplier)
        for fold in folds
    }


def _robust_threshold(values, multiplier):
    if len(values) < 100:
        raise ValueError("insufficient training returns for robust shock threshold")
    center = _median(values)
    mad = _median([abs(value - center) for value in values])
    if mad <= 0:
        raise ValueError("training shock dispersion is zero")
    return center + multiplier * 1.4826 * mad


def _metrics(policy, horizon, rows, fold_results, cfg):
    gross = [float(row["gross_bps"]) for row in rows]
    net = [float(row["net_bps"]) for row in rows]
    costs = [left - right for left, right in zip(gross, net, strict=True)]
    low, high = stationary_block_interval(
        net, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260812 + horizon
    )
    active = [mean for count, mean in fold_results if count > 0]
    months = sorted({row["occurred_at"].strftime("%Y-%m") for row in rows})
    month_sums = [
        math.fsum(
            float(row["net_bps"])
            for row in rows
            if row["occurred_at"].strftime("%Y-%m") == month
        )
        for month in months
    ]
    gains = math.fsum(value for value in net if value > 0)
    losses = -math.fsum(value for value in net if value < 0)
    stressed_1_5 = _mean([g - 1.5 * c for g, c in zip(gross, costs, strict=True)])
    stressed_2 = _mean([g - 2.0 * c for g, c in zip(gross, costs, strict=True)])
    profit_factor = gains / losses if losses else None
    passed = (
        policy == "classified"
        and len(net) >= cfg.minimum_test_trades
        and len(active) >= 8
        and len(months) >= 6
        and low > 0
        and _mean(gross) >= 4
        and stressed_1_5 > 0
        and stressed_2 >= 0
        and sum(value > 0 for value in active) / len(active) >= 0.6
        and sum(value > 0 for value in month_sums) / len(month_sums) >= 0.6
        and profit_factor is not None
        and profit_factor >= 1.15
    )
    return {
        "policy_id": policy,
        "horizon_seconds": horizon,
        "trade_count": len(net),
        "active_fold_count": len(active),
        "month_count": len(months),
        "mean_gross_bps": _mean(gross),
        "mean_net_bps": _mean(net),
        "mean_net_1_5x_cost_bps": stressed_1_5,
        "mean_net_2x_cost_bps": stressed_2,
        "bootstrap_low_bps": low,
        "bootstrap_high_bps": high,
        "profitable_fold_ratio": (
            sum(value > 0 for value in active) / len(active) if active else 0.0
        ),
        "profitable_month_ratio": (
            sum(value > 0 for value in month_sums) / len(month_sums)
            if month_sums
            else 0.0
        ),
        "profit_factor": profit_factor,
        "passes_research_gate": passed,
    }


def _aligned_batches(fm, fmeta, om, ometa, cfg, size=50_000):
    feature_columns = [
        "occurred_at", "return_1s", "return_5s", "spread_shock_ratio",
        "tick_intensity_ratio", "is_gap_start",
    ]
    feature_stream = _batches(_paths(fm, fmeta), feature_columns, size)
    outcome_streams = {
        horizon: _batches([_horizon_path(om, ometa, horizon)], _outcome_columns(horizon), size)
        for horizon in cfg.horizons_seconds
    }
    sentinel = object()
    while True:
        feature = next(feature_stream, sentinel)
        outcomes = {horizon: next(stream, sentinel) for horizon, stream in outcome_streams.items()}
        items = [feature, *outcomes.values()]
        if all(item is sentinel for item in items):
            return
        if any(item is sentinel for item in items):
            raise ValueError("feature and outcome datasets have different row counts")
        tables = [item for item in items if isinstance(item, pa.Table)]
        if len({table.num_rows for table in tables}) != 1:
            raise ValueError("feature and outcome batch sizes do not align")
        timestamps = [_utc_timestamps(table["occurred_at"]) for table in tables]
        if any(values != timestamps[0] for values in timestamps[1:]):
            raise ValueError("feature and outcome timestamps do not align")
        yield feature, outcomes


def _validate(features, outcomes, cfg):
    if not features.get("point_in_time") or features.get("labels_included"):
        raise ValueError("features must be point-in-time and label-free")
    if outcomes.get("source_feature_dataset_id") != features.get("dataset_id"):
        raise ValueError("outcomes do not belong to feature dataset")
    if any(
        str(horizon) not in outcomes.get("horizon_partitions", {})
        for horizon in cfg.horizons_seconds
    ):
        raise ValueError("outcome dataset lacks a preregistered horizon")


def _outcome_columns(horizon):
    prefix = f"h{horizon}"
    return [
        "occurred_at", f"{prefix}_valid", f"{prefix}_long_gross_bps",
        f"{prefix}_short_gross_bps", f"{prefix}_long_net_bps", f"{prefix}_short_net_bps",
    ]


def _paths(manifest, meta):
    root = manifest.resolve().parent
    paths = [Path(str(value)).resolve() for value in meta.get("partitions", [])]
    if not paths or any(not path.is_relative_to(root) for path in paths):
        raise ValueError("manifest partitions are missing or escape their dataset")
    return paths


def _horizon_path(manifest, meta, horizon):
    root = manifest.resolve().parent
    path = Path(str(meta["horizon_partitions"][str(horizon)])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("outcome partition escapes its dataset")
    return path


def _batches(paths, columns, size):
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=size, columns=columns):
            yield pa.Table.from_batches([batch])


def _bounds(meta):
    first = datetime.fromisoformat(str(meta["first_timestamp"])).astimezone(UTC)
    last = datetime.fromisoformat(str(meta["last_timestamp"])).astimezone(UTC)
    return (
        first.replace(hour=0, minute=0, second=0),
        last.replace(hour=0, minute=0, second=0) + timedelta(days=1),
    )


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _meta(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _random_side(timestamp):
    return 1 if hashlib.sha256(timestamp.isoformat().encode()).digest()[0] % 2 else -1


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
