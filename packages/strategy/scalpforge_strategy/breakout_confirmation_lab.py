from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.controlled_breakout_lab import (
    VARIANTS,
    ControlledBreakoutConfig,
    _quote_paths,
    simulate_episode,
)
from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class BreakoutConfirmationConfig:
    analysis_revision: int = 1
    minimum_persistence_bps: float = 0.25
    maximum_spread_bps: float = 3.0
    minimum_activity_ratio: float = 1.0
    minimum_breakout_distance_bps: float = 0.25
    maximum_compression_ratio: float = 1.0
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 10
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 900, 900)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1:
            raise ValueError("unsupported confirmation analysis revision")
        if (
            min(
                self.minimum_persistence_bps,
                self.maximum_spread_bps,
                self.minimum_activity_ratio,
                self.minimum_breakout_distance_bps,
                self.maximum_compression_ratio,
            )
            <= 0
        ):
            raise ValueError("confirmation thresholds must be positive")


RULES = ("persistence", "market_quality", "alignment", "full_confirmation")


def confirmation_passes(rule_id, row, quotes, cfg, execution_cfg):
    persistence = _persistence_bps(row, quotes, execution_cfg.confirmation_delay_seconds)
    if persistence is None or persistence < cfg.minimum_persistence_bps:
        return False
    if rule_id == "persistence":
        return True
    market_quality = (
        _number(row.get("spread_bps"), math.inf) <= cfg.maximum_spread_bps
        and _number(row.get("tick_intensity_ratio"), 0.0) >= cfg.minimum_activity_ratio
        and _number(row.get("breakout_distance_bps"), 0.0) >= cfg.minimum_breakout_distance_bps
    )
    if rule_id == "market_quality":
        return market_quality
    alignment = (
        _number(row.get("return_30s_signed"), -math.inf) > 0
        and _number(row.get("distance_from_tick_vwap_signed_bps"), -math.inf) > 0
    )
    if rule_id == "alignment":
        return alignment
    if rule_id == "full_confirmation":
        return (
            market_quality
            and alignment
            and _number(row.get("compression_60_to_300"), math.inf) <= cfg.maximum_compression_ratio
        )
    raise ValueError(f"unknown confirmation rule: {rule_id}")


def run_breakout_confirmation_lab(
    episode_manifest: Path,
    feature_manifest: Path,
    output_root: Path,
    config: BreakoutConfirmationConfig | None = None,
) -> dict[str, object]:
    cfg = config or BreakoutConfirmationConfig()
    episode_meta, episodes = _episodes(episode_manifest)
    feature_meta = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if feature_meta.get("dataset_id") != episode_meta.get("feature_dataset_id"):
        raise ValueError("features do not belong to episode dataset")
    execution_cfg = ControlledBreakoutConfig()
    variant = next(item for item in VARIANTS if item.variant_id == "staged_runner")
    paths = _quote_paths(
        feature_manifest,
        feature_meta,
        episodes,
        execution_cfg.runner_holding_seconds + 10,
    )
    start = min(row["occurred_at"] for row in episodes).replace(hour=0, minute=0, second=0)
    end = max(row["occurred_at"] for row in episodes).replace(
        hour=0, minute=0, second=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    metrics = []
    for rule_id in RULES:
        aggregate = []
        fold_metrics = []
        total_candidates = 0
        for fold in folds:
            test = [
                row
                for row in episodes
                if fold.test_start <= row["occurred_at"] < fold.test_end_exclusive
            ]
            total_candidates += len(test)
            results = _evaluate(test, paths, rule_id, cfg, execution_cfg, variant)
            aggregate.extend(results)
            fold_metrics.append(
                {
                    "fold": fold.fold,
                    "candidate_count": len(test),
                    "trade_count": len(results),
                    "mean_net_bps": _mean([float(row["net_bps"]) for row in results]),
                }
            )
        net = [float(row["net_bps"]) for row in aggregate]
        gross = [float(row["gross_bps"]) for row in aggregate]
        costs = [float(row["round_trip_cost_bps"]) for row in aggregate]
        low, high = stationary_block_interval(
            net, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260813
        )
        active = [fold for fold in fold_metrics if fold["trade_count"]]
        profitable = (
            sum(float(fold["mean_net_bps"]) > 0 for fold in active) / len(active) if active else 0.0
        )
        gains = math.fsum(value for value in net if value > 0)
        losses = -math.fsum(value for value in net if value < 0)
        stressed_1_5 = _mean([g - 1.5 * c for g, c in zip(gross, costs, strict=True)])
        stressed_2 = _mean([g - 2 * c for g, c in zip(gross, costs, strict=True)])
        gate = (
            len(net) >= 100
            and len(active) >= 6
            and low > 0
            and stressed_1_5 > 0
            and stressed_2 >= 0
            and profitable >= 0.6
            and losses > 0
            and gains / losses >= 1.15
        )
        metrics.append(
            {
                "rule_id": rule_id,
                "candidate_count": total_candidates,
                "trade_count": len(net),
                "abstention_rate": 1 - len(net) / total_candidates,
                "mean_gross_bps": _mean(gross),
                "mean_net_bps": _mean(net),
                "mean_net_1_5x_cost_bps": stressed_1_5,
                "mean_net_2x_cost_bps": stressed_2,
                "bootstrap_low_bps": low,
                "bootstrap_high_bps": high,
                "profitable_fold_ratio": profitable,
                "profit_factor": gains / losses if losses else None,
                "passes_research_gate": gate,
                "folds": fold_metrics,
            }
        )
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {"episodes": episode_meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    report_id = "breakout-confirmation-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "episode_dataset_id": episode_meta["dataset_id"],
        "feature_dataset_id": feature_meta["dataset_id"],
        "config": serialized,
        "execution_variant": variant.variant_id,
        "rule_count": len(RULES),
        "final_holdout_start": holdout_start.isoformat(),
        "final_holdout_end_exclusive": end.isoformat(),
        "holdout_evaluated": False,
        "fold_count": len(folds),
        "metrics": metrics,
        "winner_selected": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="breakout-confirmation-lab",
        dataset_ids=(str(episode_meta["dataset_id"]), str(feature_meta["dataset_id"])),
        hypothesis_count=len(RULES),
        holdout_evaluated=False,
    )
    return report


def _evaluate(rows, paths, rule_id, cfg, execution_cfg, variant):
    output = []
    for row in rows:
        quotes = paths.get(row["episode_id"], [])
        if not confirmation_passes(rule_id, row, quotes, cfg, execution_cfg):
            continue
        result = simulate_episode(int(row["side"]), quotes, variant, execution_cfg)
        if result is not None:
            output.append({"episode_id": row["episode_id"], **result})
    return output


def _persistence_bps(row, quotes, delay):
    if not quotes:
        return None
    signal_mid = (quotes[0][1] + quotes[0][2]) / 2
    target = quotes[0][0] + timedelta(seconds=delay)
    quote = next((value for value in quotes if value[0] >= target), None)
    if quote is None:
        return None
    mid = (quote[1] + quote[2]) / 2
    return int(row["side"]) * (mid - signal_mid) / signal_mid * 10_000


def _episodes(manifest):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if not meta.get("point_in_time") or not meta.get("labels_physically_separate"):
        raise ValueError("episode dataset is not leakage-safe")
    root = manifest.resolve().parent
    path = Path(str(meta["feature_partition"])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("episode partition escapes its dataset")
    table = pq.read_table(path)
    timestamps = _utc_timestamps(table["occurred_at"])
    columns = [name for name in table.column_names if name != "occurred_at"]
    values = {name: table[name].to_pylist() for name in columns}
    return meta, [
        {
            "occurred_at": timestamp,
            **{name: values[name][index] for name in columns},
        }
        for index, timestamp in enumerate(timestamps)
    ]


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[column.type.unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _number(value, fallback):
    return float(value) if value is not None and math.isfinite(float(value)) else fallback


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
