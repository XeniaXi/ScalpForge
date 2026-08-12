from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class PathManagementConfig:
    analysis_revision: int = 1
    entry_wait_seconds: tuple[int, ...] = (0, 5, 15, 30)
    pullback_bps: tuple[float, ...] = (0.0, 0.5, 1.0)
    holding_seconds: tuple[int, ...] = (60, 180, 300, 900)
    stop_bps: tuple[float, ...] = (4.0, 8.0, 12.0)
    target_bps: tuple[float, ...] = (4.0, 8.0, 12.0)
    decision_latency_ms: int = 50
    maximum_quote_delay_seconds: int = 2
    slippage_bps_per_side: float = 0.5
    minimum_validation_trades: int = 10
    minimum_test_trades: int = 5
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 10
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 900, 900)

    def __post_init__(self) -> None:
        if (
            self.analysis_revision != 1
            or self.decision_latency_ms < 0
            or self.maximum_quote_delay_seconds < 1
        ):
            raise ValueError("invalid path-management configuration")
        if min(self.entry_wait_seconds) < 0 or min(self.pullback_bps) < 0:
            raise ValueError("entry controls must be non-negative")
        if min(self.holding_seconds) < 1 or min(self.stop_bps) <= 0 or min(self.target_bps) <= 0:
            raise ValueError("exit controls must be positive")


@dataclass(frozen=True)
class Policy:
    entry_wait_seconds: int
    pullback_bps: float
    holding_seconds: int
    stop_bps: float
    target_bps: float

    @property
    def policy_id(self) -> str:
        return (
            f"wait{self.entry_wait_seconds}_pull{self.pullback_bps:g}_"
            f"hold{self.holding_seconds}_stop{self.stop_bps:g}_target{self.target_bps:g}"
        )


def policies(config: PathManagementConfig) -> list[Policy]:
    """The fixed policy family is declared before any labels are inspected."""
    return [
        Policy(wait, pullback, hold, stop, target)
        for wait in config.entry_wait_seconds
        for pullback in config.pullback_bps
        for hold in config.holding_seconds
        for stop in config.stop_bps
        for target in config.target_bps
        if (pullback == 0.0 and wait == 0) or (pullback > 0.0 and wait > 0)
    ]


def simulate_path(
    side: int,
    quotes: list[tuple[datetime, float, float]],
    policy: Policy,
    slippage_bps_per_side: float,
    decision_latency_ms: int = 50,
    maximum_quote_delay_seconds: int = 2,
) -> dict[str, object] | None:
    if not quotes:
        return None
    signal_mid = (quotes[0][1] + quotes[0][2]) / 2
    entry = None
    executable_from = quotes[0][0] + timedelta(milliseconds=decision_latency_ms)
    deadline = quotes[0][0] + timedelta(
        seconds=(
            policy.entry_wait_seconds
            if policy.entry_wait_seconds > 0
            else maximum_quote_delay_seconds
        ),
    )
    for timestamp, bid, ask in quotes:
        if timestamp < executable_from:
            continue
        if timestamp > deadline:
            break
        executable = ask if side > 0 else bid
        improvement = side * (signal_mid - executable) / signal_mid * 10_000
        if policy.pullback_bps == 0 or improvement >= policy.pullback_bps:
            entry = (timestamp, executable)
            break
    if entry is None:
        return None
    entered_at, entry_price = entry
    end = entered_at + timedelta(seconds=policy.holding_seconds)
    exit_price = None
    exited_at = None
    reason = "time"
    for timestamp, bid, ask in quotes:
        if timestamp < entered_at:
            continue
        executable = bid if side > 0 else ask
        gross = side * (executable - entry_price) / entry_price * 10_000
        if gross <= -policy.stop_bps:
            exit_price, exited_at, reason = executable, timestamp, "stop"
            break
        if gross >= policy.target_bps:
            exit_price, exited_at, reason = executable, timestamp, "target"
            break
        if timestamp >= end:
            exit_price, exited_at = executable, timestamp
            break
    if exit_price is None:
        return None
    gross = side * (exit_price - entry_price) / entry_price * 10_000
    net = gross - 2 * slippage_bps_per_side
    return {
        "entered_at": entered_at,
        "exited_at": exited_at,
        "gross_bps": gross,
        "net_bps": net,
        "round_trip_cost_bps": 2 * slippage_bps_per_side,
        "exit_reason": reason,
    }


def run_path_management_lab(
    episode_manifest: Path,
    feature_manifest: Path,
    output_root: Path,
    config: PathManagementConfig | None = None,
) -> dict[str, object]:
    cfg = config or PathManagementConfig()
    episode_meta, episodes = _episodes(episode_manifest)
    feature_meta = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if feature_meta.get("dataset_id") != episode_meta.get("feature_dataset_id"):
        raise ValueError("features do not belong to episode dataset")
    quote_paths = _quote_paths(feature_manifest, feature_meta, episodes, max(cfg.holding_seconds))
    candidates = policies(cfg)
    start = min(row["occurred_at"] for row in episodes).replace(hour=0, minute=0, second=0)
    end = max(row["occurred_at"] for row in episodes).replace(
        hour=0, minute=0, second=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    selected_folds = []
    results: list[dict[str, object]] = []
    for fold in folds:
        validation = [
            row
            for row in episodes
            if fold.validation_start
            <= row["occurred_at"]
            < fold.validation_end_exclusive
        ]
        test = [
            row
            for row in episodes
            if fold.test_start <= row["occurred_at"] < fold.test_end_exclusive
        ]
        selected = _select_policy(validation, quote_paths, candidates, cfg)
        chosen = [] if selected is None else _evaluate(test, quote_paths, selected, cfg)
        if len(chosen) < cfg.minimum_test_trades:
            chosen = []
        results.extend(chosen)
        selected_folds.append(
            {
                "fold": fold.fold,
                "policy_id": selected.policy_id if selected else None,
                "validation_rows": len(validation),
                "test_rows": len(test),
                "test_trades": len(chosen),
                "test_mean_net_bps": _mean([float(r["net_bps"]) for r in chosen]),
            }
        )
    gross = [float(row["gross_bps"]) for row in results]
    net = [float(row["net_bps"]) for row in results]
    costs = [float(row["round_trip_cost_bps"]) for row in results]
    low, high = stationary_block_interval(
        net, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260812
    )
    active = [fold for fold in selected_folds if fold["test_trades"] >= cfg.minimum_test_trades]
    profitable_ratio = (
        sum(float(fold["test_mean_net_bps"]) > 0 for fold in active) / len(active)
        if active
        else 0.0
    )
    gains = math.fsum(value for value in net if value > 0)
    losses = -math.fsum(value for value in net if value < 0)
    profit_factor = gains / losses if losses else None
    stressed_1_5 = _mean([g - 1.5 * c for g, c in zip(gross, costs, strict=True)])
    stressed_2 = _mean([g - 2 * c for g, c in zip(gross, costs, strict=True)])
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {"episodes": episode_meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    report_id = "path-management-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    gate = (
        len(net) >= 100 and len(active) >= 6 and low > 0 and stressed_1_5 > 0
        and stressed_2 >= 0 and profitable_ratio >= 0.6 and profit_factor is not None
        and profit_factor >= 1.15
    )
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "episode_dataset_id": episode_meta["dataset_id"],
        "feature_dataset_id": feature_meta["dataset_id"],
        "config": serialized,
        "policy_count": len(candidates),
        "final_holdout_start": holdout_start.isoformat(),
        "final_holdout_end_exclusive": end.isoformat(),
        "holdout_evaluated": False,
        "fold_count": len(folds),
        "selected_folds": selected_folds,
        "aggregate_test_trades": len(net),
        "aggregate_mean_gross_bps": _mean(gross),
        "aggregate_mean_net_bps": _mean(net),
        "aggregate_mean_net_1_5x_cost_bps": stressed_1_5,
        "aggregate_mean_net_2x_cost_bps": stressed_2,
        "bootstrap_low_bps": low,
        "bootstrap_high_bps": high,
        "profitable_fold_ratio": profitable_ratio,
        "profit_factor": profit_factor,
        "research_gate_passed": gate,
        "research_only": True,
        "real_money_enabled": False,
    }
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="path-management-lab",
        dataset_ids=(
            str(episode_meta["dataset_id"]),
            str(feature_meta["dataset_id"]),
        ),
        hypothesis_count=len(candidates),
        holdout_evaluated=False,
    )
    return report


def _select_policy(rows, paths, candidates, cfg):
    best = None
    best_mean = 0.0
    for candidate in candidates:
        evaluated = _evaluate(rows, paths, candidate, cfg)
        if len(evaluated) < cfg.minimum_validation_trades:
            continue
        score = _mean([float(row["net_bps"]) for row in evaluated])
        if score > best_mean:
            best, best_mean = candidate, score
    return best


def _evaluate(rows, paths, policy, cfg):
    output = []
    for row in rows:
        result = simulate_path(int(row["side"]), paths.get(row["episode_id"], []), policy,
                               cfg.slippage_bps_per_side, cfg.decision_latency_ms,
                               cfg.maximum_quote_delay_seconds)
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
    return meta, pq.read_table(path, columns=["episode_id", "occurred_at", "side"]).to_pylist()


def _quote_paths(manifest, meta, episodes, horizon):
    root = manifest.resolve().parent
    paths = [Path(str(value)).resolve() for value in meta.get("partitions", [])]
    if not paths or any(not path.is_relative_to(root) for path in paths):
        raise ValueError("feature partitions are missing or escape their dataset")
    ordered = sorted(episodes, key=lambda row: row["occurred_at"])
    output = {row["episode_id"]: [] for row in ordered}
    next_episode = 0
    active: list[dict[str, object]] = []
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=100_000, columns=["occurred_at", "bid", "ask"]
        ):
            for row in batch.to_pylist():
                timestamp = row["occurred_at"].astimezone(UTC)
                while (
                    next_episode < len(ordered)
                    and ordered[next_episode]["occurred_at"] <= timestamp
                ):
                    active.append(ordered[next_episode])
                    next_episode += 1
                active = [
                    episode
                    for episode in active
                    if (timestamp - episode["occurred_at"]).total_seconds()
                    <= horizon + 30
                ]
                for episode in active:
                    delta = (timestamp - episode["occurred_at"]).total_seconds()
                    if 0 <= delta <= horizon + 30:
                        output[episode["episode_id"]].append(
                            (timestamp, float(row["bid"]), float(row["ask"]))
                        )
    return output


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
