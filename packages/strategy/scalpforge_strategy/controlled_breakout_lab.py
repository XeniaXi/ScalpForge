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


@dataclass(frozen=True)
class ControlledBreakoutConfig:
    analysis_revision: int = 1
    confirmation_delay_seconds: int = 5
    maximum_quote_delay_seconds: int = 2
    stage_after_adverse_bps: tuple[float, ...] = (4.0, 8.0)
    maximum_tickets: int = 3
    quick_failure_seconds: int = 60
    quick_failure_bps: float = 2.0
    partial_target_bps: float = 8.0
    runner_trailing_bps: float = 6.0
    fixed_holding_seconds: int = 300
    runner_holding_seconds: int = 900
    basket_stop_bps: float = 20.0
    slippage_bps_per_side: float = 0.5
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 10
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 900, 900)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1 or self.maximum_tickets != 3:
            raise ValueError("unsupported controlled-breakout configuration")
        if len(self.stage_after_adverse_bps) != self.maximum_tickets - 1:
            raise ValueError("staging thresholds must match maximum tickets")
        if tuple(sorted(self.stage_after_adverse_bps)) != self.stage_after_adverse_bps:
            raise ValueError("staging thresholds must be ordered")
        positive = (
            self.maximum_quote_delay_seconds,
            self.quick_failure_seconds,
            self.quick_failure_bps,
            self.partial_target_bps,
            self.runner_trailing_bps,
            self.fixed_holding_seconds,
            self.runner_holding_seconds,
            self.basket_stop_bps,
        )
        if min(positive) <= 0 or self.slippage_bps_per_side < 0:
            raise ValueError("risk and execution controls must be positive")


@dataclass(frozen=True)
class Variant:
    variant_id: str
    staged_entries: bool
    runner_management: bool


VARIANTS = (
    Variant("single_fixed", False, False),
    Variant("single_runner", False, True),
    Variant("staged_fixed", True, False),
    Variant("staged_runner", True, True),
)


def simulate_episode(
    side: int,
    quotes: list[tuple[datetime, float, float]],
    variant: Variant,
    config: ControlledBreakoutConfig,
) -> dict[str, object] | None:
    if not quotes or side not in (-1, 1):
        return None
    signal_at = quotes[0][0]
    executable_from = signal_at + timedelta(seconds=config.confirmation_delay_seconds)
    deadline = executable_from + timedelta(seconds=config.maximum_quote_delay_seconds)
    entry_index = next(
        (i for i, (ts, _, _) in enumerate(quotes) if executable_from <= ts <= deadline), None
    )
    if entry_index is None:
        return None
    ts, bid, ask = quotes[entry_index]
    entries: list[tuple[datetime, float]] = [(ts, ask if side > 0 else bid)]
    initial_price = entries[0][1]
    next_stage = 0
    peak_bps = -math.inf
    partial_taken = False
    realized_gross = 0.0
    open_weight = 1.0
    reason = "time"
    exit_at = None

    horizon = (
        config.runner_holding_seconds if variant.runner_management else config.fixed_holding_seconds
    )
    end_at = entries[0][0] + timedelta(seconds=horizon)
    for timestamp, quote_bid, quote_ask in quotes[entry_index:]:
        executable = quote_bid if side > 0 else quote_ask
        initial_move = side * (executable - initial_price) / initial_price * 10_000
        if variant.staged_entries and next_stage < len(config.stage_after_adverse_bps):
            threshold = config.stage_after_adverse_bps[next_stage]
            if initial_move <= -threshold:
                entries.append((timestamp, quote_ask if side > 0 else quote_bid))
                next_stage += 1
        gross_by_entry = [side * (executable - price) / price * 10_000 for _, price in entries]
        basket_gross = math.fsum(gross_by_entry) / len(gross_by_entry)
        if basket_gross <= -config.basket_stop_bps:
            reason, exit_at = "basket_stop", timestamp
            break
        age = (timestamp - entries[0][0]).total_seconds()
        if age >= config.quick_failure_seconds and initial_move <= -config.quick_failure_bps:
            reason, exit_at = "quick_failure", timestamp
            break
        peak_bps = max(peak_bps, basket_gross)
        if (
            variant.runner_management
            and not partial_taken
            and basket_gross >= config.partial_target_bps
        ):
            realized_gross = basket_gross * 0.5
            open_weight = 0.5
            partial_taken = True
        if (
            variant.runner_management
            and partial_taken
            and peak_bps - basket_gross >= config.runner_trailing_bps
        ):
            reason, exit_at = "trailing", timestamp
            break
        if timestamp >= end_at:
            exit_at = timestamp
            break
    if exit_at is None:
        return None
    gross = realized_gross + open_weight * basket_gross
    tickets = len(entries)
    cost = tickets * 2 * config.slippage_bps_per_side
    return {
        "gross_bps": gross,
        "net_bps": gross - cost,
        "round_trip_cost_bps": cost,
        "ticket_count": tickets,
        "partial_taken": partial_taken,
        "exit_reason": reason,
    }


def run_controlled_breakout_lab(
    episode_manifest: Path,
    feature_manifest: Path,
    output_root: Path,
    config: ControlledBreakoutConfig | None = None,
) -> dict[str, object]:
    cfg = config or ControlledBreakoutConfig()
    episode_meta, episodes = _episodes(episode_manifest)
    feature_meta = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if feature_meta.get("dataset_id") != episode_meta.get("feature_dataset_id"):
        raise ValueError("features do not belong to episode dataset")
    paths = _quote_paths(feature_manifest, feature_meta, episodes, cfg.runner_holding_seconds + 10)
    start = min(row["occurred_at"] for row in episodes).replace(hour=0, minute=0, second=0)
    end = max(row["occurred_at"] for row in episodes).replace(
        hour=0, minute=0, second=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    metrics = []
    for variant in VARIANTS:
        fold_metrics = []
        aggregate = []
        for fold in folds:
            test = [
                r for r in episodes if fold.test_start <= r["occurred_at"] < fold.test_end_exclusive
            ]
            evaluated = _evaluate(test, paths, variant, cfg)
            aggregate.extend(evaluated)
            fold_metrics.append(
                {
                    "fold": fold.fold,
                    "trade_count": len(evaluated),
                    "mean_net_bps": _mean([float(r["net_bps"]) for r in evaluated]),
                }
            )
        net = [float(r["net_bps"]) for r in aggregate]
        gross = [float(r["gross_bps"]) for r in aggregate]
        costs = [float(r["round_trip_cost_bps"]) for r in aggregate]
        low, high = stationary_block_interval(
            net, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260812
        )
        active = [f for f in fold_metrics if f["trade_count"]]
        profitable = (
            sum(float(f["mean_net_bps"]) > 0 for f in active) / len(active) if active else 0.0
        )
        stressed_1_5 = _mean([g - 1.5 * c for g, c in zip(gross, costs, strict=True)])
        stressed_2 = _mean([g - 2 * c for g, c in zip(gross, costs, strict=True)])
        gate = (
            len(net) >= 100
            and len(active) >= 6
            and low > 0
            and stressed_1_5 > 0
            and stressed_2 >= 0
            and profitable >= 0.6
        )
        metrics.append(
            {
                "variant_id": variant.variant_id,
                "staged_entries": variant.staged_entries,
                "runner_management": variant.runner_management,
                "trade_count": len(net),
                "mean_gross_bps": _mean(gross),
                "mean_net_bps": _mean(net),
                "mean_net_1_5x_cost_bps": stressed_1_5,
                "mean_net_2x_cost_bps": stressed_2,
                "bootstrap_low_bps": low,
                "bootstrap_high_bps": high,
                "profitable_fold_ratio": profitable,
                "average_ticket_count": _mean([float(r["ticket_count"]) for r in aggregate]),
                "partial_take_ratio": _mean([float(bool(r["partial_taken"])) for r in aggregate]),
                "passes_research_gate": gate,
                "folds": fold_metrics,
            }
        )
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {"episodes": episode_meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    report_id = "controlled-breakout-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "episode_dataset_id": episode_meta["dataset_id"],
        "feature_dataset_id": feature_meta["dataset_id"],
        "config": serialized,
        "variant_count": len(VARIANTS),
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
        experiment_family="controlled-breakout-lab",
        dataset_ids=(str(episode_meta["dataset_id"]), str(feature_meta["dataset_id"])),
        hypothesis_count=len(VARIANTS),
        holdout_evaluated=False,
    )
    return report


def _evaluate(rows, paths, variant, cfg):
    output = []
    for row in rows:
        result = simulate_episode(int(row["side"]), paths.get(row["episode_id"], []), variant, cfg)
        if result is not None:
            output.append({"episode_id": row["episode_id"], **result})
    return output


def _episodes(manifest):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if not meta.get("point_in_time") or not meta.get("labels_physically_separate"):
        raise ValueError("episode dataset is not leakage-safe")
    root = manifest.resolve().parent
    path = Path(str(meta["feature_partition"])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("episode partition escapes its dataset")
    table = pq.read_table(path, columns=["episode_id", "occurred_at", "side"])
    return meta, [
        {"episode_id": i, "occurred_at": t, "side": s}
        for i, t, s in zip(
            table["episode_id"].to_pylist(),
            _utc_timestamps(table["occurred_at"]),
            table["side"].to_pylist(),
            strict=True,
        )
    ]


def _quote_paths(manifest, meta, episodes, horizon):
    root = manifest.resolve().parent
    partitions = [Path(str(value)).resolve() for value in meta.get("partitions", [])]
    if not partitions or any(not path.is_relative_to(root) for path in partitions):
        raise ValueError("feature partitions are missing or escape their dataset")
    ordered = sorted(episodes, key=lambda row: row["occurred_at"])
    output = {row["episode_id"]: [] for row in ordered}
    next_episode = 0
    active = []
    for path in partitions:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=100_000, columns=["occurred_at", "bid", "ask"]
        ):
            for timestamp, bid, ask in zip(
                _utc_timestamps(batch.column("occurred_at")),
                batch.column("bid").to_pylist(),
                batch.column("ask").to_pylist(),
                strict=True,
            ):
                while (
                    next_episode < len(ordered)
                    and ordered[next_episode]["occurred_at"] <= timestamp
                ):
                    active.append(ordered[next_episode])
                    next_episode += 1
                active = [
                    episode
                    for episode in active
                    if (timestamp - episode["occurred_at"]).total_seconds() <= horizon
                ]
                for episode in active:
                    output[episode["episode_id"]].append((timestamp, float(bid), float(ask)))
    return output


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[column.type.unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
