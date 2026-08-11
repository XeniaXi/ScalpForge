from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.execution_clock import CausalExecutionConfig, CausalQuoteSeries
from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class SequenceLabConfig:
    analysis_revision: int = 4
    breakout_window_seconds: int = 300
    hold_delays_seconds: tuple[int, ...] = (5, 15)
    retest_window_seconds: int = 30
    sweep_window_seconds: int = 15
    confirmation_bps: float = 0.25
    retest_tolerance_bps: float = 0.5
    minimum_activity_ratio: float = 1.5
    minimum_quote_efficiency_ratio: float = 0.5
    maximum_spread_shock_ratio: float = 1.5
    maximum_spread_bps: float = 8.0
    maximum_gap_seconds: int = 5
    decision_latency_ms: int = 50
    maximum_entry_delay_seconds: int = 2
    time_exit_seconds: int = 60
    additional_exit_horizons_seconds: tuple[int, ...] = (300, 900)
    trailing_activation_bps: float = 3.0
    trailing_distance_bps: float = 1.5
    slippage_bps_per_side: float = 0.5
    decision_interval_seconds: int = 60
    minimum_episode_spacing_seconds: int = 960
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(10, 3, 3, 3, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision < 4:
            raise ValueError("sequence analysis revision must include annual cost stress")
        if min(self.hold_delays_seconds) <= 0 or self.time_exit_seconds <= 0:
            raise ValueError("delays and exit horizon must be positive")
        if self.maximum_gap_seconds <= 0 or self.maximum_spread_bps <= 0:
            raise ValueError("gap and spread limits must be positive")
        if self.minimum_activity_ratio <= 0 or not 0 <= self.minimum_quote_efficiency_ratio <= 1:
            raise ValueError("activity and quote-efficiency limits are invalid")
        if self.maximum_spread_shock_ratio <= 0:
            raise ValueError("spread-shock limit must be positive")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades <= 0:
            raise ValueError("bootstrap configuration is too small")
        horizons = (self.time_exit_seconds, *self.additional_exit_horizons_seconds)
        if min(horizons) <= 0 or len(set(horizons)) != len(horizons):
            raise ValueError("exit horizons must be positive and unique")
        maximum_signal_delay = max(
            *self.hold_delays_seconds,
            self.retest_window_seconds,
            self.sweep_window_seconds,
        )
        required_spacing = (
            maximum_signal_delay
            + max(horizons)
            + self.maximum_entry_delay_seconds
            + self.decision_latency_ms / 1000
        )
        if self.minimum_episode_spacing_seconds < required_spacing:
            raise ValueError(
                "episode spacing must cover signal delay, execution, and maximum horizon"
            )


@dataclass(frozen=True)
class PolicyMetrics:
    policy_id: str
    exit_id: str
    trade_count: int
    eligible_episode_count: int
    policy_trigger_count: int
    signal_count: int
    abstention_rate: float
    policy_abstention_rate: float
    execution_rejection_rate: float
    fold_count: int
    profitable_fold_count: int
    profitable_fold_ratio: float
    month_count: int
    profitable_month_count: int
    profitable_month_ratio: float
    largest_positive_month_share: float
    mean_gross_bps: float
    mean_cost_drag_bps: float
    mean_net_bps: float
    mean_net_1_5x_cost_bps: float
    mean_net_2x_cost_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    family_adjusted_fold_low_bps: float
    win_rate: float
    profit_factor: float | None
    maximum_drawdown_bps: float
    mean_holding_seconds: float
    passes_research_gate: bool


@dataclass(frozen=True)
class SequenceLabReport:
    report_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    structural_dataset_id: str
    config: dict[str, object]
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    fold_count: int
    metrics: list[PolicyMetrics]
    research_only: bool = True
    real_money_enabled: bool = False


@dataclass(frozen=True)
class _Trade:
    policy: str
    exit_id: str
    fold: int
    gross: float
    net: float
    holding_seconds: float
    entry_timestamp: datetime


def run_sequence_lab(
    feature_manifest: Path,
    structural_manifest: Path,
    output_root: Path,
    config: SequenceLabConfig | None = None,
) -> SequenceLabReport:
    cfg = config or SequenceLabConfig()
    feature_meta = _meta(feature_manifest)
    structure_meta = _meta(structural_manifest)
    _require_causal_manifest(feature_meta, "feature")
    _require_causal_manifest(structure_meta, "structural")
    if structure_meta.get("source_feature_dataset_id") != feature_meta.get("dataset_id"):
        raise ValueError("structural dataset does not belong to feature dataset")
    start, end = _dataset_bounds(feature_manifest, feature_meta)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    windows = _aligned_windows(
        feature_manifest, feature_meta, structural_manifest, structure_meta, cfg
    )
    simulation_counts: dict[str, object] = {}
    trades = _simulate_windows(windows, folds, cfg, simulation_counts)
    metrics = _metrics(trades, cfg, simulation_counts)
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "structure": structure_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "sequence-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / report_id
    path = root / "report.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metrics"] = [PolicyMetrics(**item) for item in payload["metrics"]]
        report = SequenceLabReport(**payload)
        _register(output_root, report)
        return report
    report = SequenceLabReport(
        report_id=report_id,
        schema_version=2,
        created_at=datetime.now(UTC).isoformat(),
        feature_dataset_id=str(feature_meta["dataset_id"]),
        structural_dataset_id=str(structure_meta["dataset_id"]),
        config=serialized,
        final_holdout_start=holdout_start.isoformat(),
        final_holdout_end_exclusive=end.isoformat(),
        holdout_evaluated=False,
        fold_count=len(folds),
        metrics=metrics,
    )
    root.mkdir(parents=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    _register(output_root, report)
    return report


def _register(output_root: Path, report: SequenceLabReport) -> None:
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report.report_id,
        experiment_family="sequence-lab",
        dataset_ids=(report.feature_dataset_id, report.structural_dataset_id),
        hypothesis_count=max(len(report.metrics), 1),
        holdout_evaluated=report.holdout_evaluated,
    )


def _simulate(features, structure, timestamps, folds, cfg) -> list[_Trade]:
    return _simulate_windows([(features, structure, features.num_rows)], folds, cfg)


def _simulate_windows(windows, folds, cfg, counts=None) -> list[_Trade]:
    counters = counts if counts is not None else {}
    trades: list[_Trade] = []
    previous_state: int | None = None
    previous_timestamp: datetime | None = None
    for features, structure, core_rows in windows:
        timestamps = _utc_timestamps(features["occurred_at"])
        if timestamps != _utc_timestamps(structure["occurred_at"]):
            raise ValueError("feature and structural timestamps do not align")
        batch, previous_state, previous_timestamp = _simulate_window(
            features,
            structure,
            timestamps,
            core_rows,
            folds,
            cfg,
            previous_state,
            previous_timestamp,
            counters,
        )
        trades.extend(batch)
    return trades


def _simulate_window(
    features,
    structure,
    timestamps,
    core_rows,
    folds,
    cfg,
    previous_state,
    previous_timestamp,
    counters,
):
    quotes = CausalQuoteSeries.from_feature_table(features, cfg.maximum_gap_seconds)
    execution_entries = quotes.entry_indices(
        CausalExecutionConfig(
            decision_latency_ms=cfg.decision_latency_ms,
            maximum_quote_delay_seconds=cfg.maximum_entry_delay_seconds,
            maximum_continuity_gap_seconds=cfg.maximum_gap_seconds,
        )
    )
    mids = [float(value) for value in features["mid"].to_pylist()]
    spreads = [float(value) for value in features["spread_bps"].to_pylist()]
    activity = [float(value) for value in features["tick_intensity_ratio"].to_pylist()]
    quote_changes = [int(value) for value in features["quote_change_count"].to_pylist()]
    ticks = [int(value) for value in features["tick_count"].to_pylist()]
    spread_shocks = [float(value) for value in features["spread_shock_ratio"].to_pylist()]
    return_5s = features["return_5s"].to_pylist()
    return_30s = features["return_30s"].to_pylist()
    compression = structure["compression_60_to_300"].to_pylist()
    sides = structure[f"breakout_side_{cfg.breakout_window_seconds}s"].to_pylist()
    highs = structure[f"prior_high_{cfg.breakout_window_seconds}s"].to_pylist()
    lows = structure[f"prior_low_{cfg.breakout_window_seconds}s"].to_pylist()
    candidates = [
        (f"hold_{delay}s", delay) for delay in cfg.hold_delays_seconds
    ] + [
        ("retest_resume", None),
        ("sweep_fade", None),
        ("compression_activity_hold", 5),
        ("quote_pressure_hold", 5),
        ("trend_alignment_hold", 5),
    ]
    trades: list[_Trade] = []
    for index, timestamp in enumerate(timestamps[:core_rows]):
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("episode timestamps must be strictly increasing")
        side = int(sides[index]) if sides[index] else 0
        state = side or None
        discontinuity = (
            previous_timestamp is None
            or (timestamp - previous_timestamp).total_seconds()
            > cfg.decision_interval_seconds
        )
        episode_start = state is not None and (
            discontinuity or state != previous_state
        )
        previous_state = state
        previous_timestamp = timestamp
        if not episode_start:
            continue
        fold = _fold(timestamp, folds)
        if (
            fold is None
            or side == 0
            or spreads[index] > cfg.maximum_spread_bps
        ):
            continue
        last_eligible = counters.get("last_eligible_timestamp")
        if (
            isinstance(last_eligible, datetime)
            and (timestamp - last_eligible).total_seconds()
            < cfg.minimum_episode_spacing_seconds
        ):
            continue
        counters["last_eligible_timestamp"] = timestamp
        counters["eligible_episodes"] = int(counters.get("eligible_episodes", 0)) + 1
        level = float(highs[index] if side > 0 else lows[index])
        for policy, delay in candidates:
            entry = _entry(
                policy,
                index,
                side,
                level,
                delay,
                timestamps,
                mids,
                spreads,
                activity,
                quote_changes,
                ticks,
                spread_shocks,
                return_5s,
                return_30s,
                compression,
                cfg,
            )
            if entry is None:
                continue
            signal_index, trade_side = entry
            trigger_key = f"trigger:{policy}"
            counters[trigger_key] = int(counters.get(trigger_key, 0)) + 1
            entry_index = execution_entries[signal_index]
            if entry_index is None or quotes.quote_at[entry_index] >= fold.test_end_exclusive:
                continue
            key = f"policy:{policy}"
            counters[key] = int(counters.get(key, 0)) + 1
            horizons = (cfg.time_exit_seconds, *cfg.additional_exit_horizons_seconds)
            for horizon in horizons:
                for exit_style in ("time", "structural_or_time", "trailing_or_time"):
                    exit_id = f"{exit_style}_{horizon}s"
                    trade = _exit(
                        policy,
                        exit_id,
                        fold.fold,
                        fold.test_end_exclusive,
                        entry_index,
                        trade_side,
                        level,
                        timestamps,
                        mids,
                        quotes,
                        execution_entries,
                        cfg,
                        horizon_seconds=horizon,
                        exit_style=exit_style,
                    )
                    if trade is not None:
                        trades.append(trade)
    return trades, previous_state, previous_timestamp


def _entry(
    policy,
    index,
    side,
    level,
    delay,
    timestamps,
    mids,
    spreads,
    activity,
    quote_changes,
    ticks,
    spread_shocks,
    return_5s,
    return_30s,
    compression,
    cfg,
):
    if policy.startswith("hold_") or policy in {
        "compression_activity_hold",
        "quote_pressure_hold",
        "trend_alignment_hold",
    }:
        if policy == "compression_activity_hold" and (
            compression[index] is None
            or compression[index] > 0.5
            or activity[index] < cfg.minimum_activity_ratio
        ):
            return None
        if policy == "quote_pressure_hold":
            efficiency = quote_changes[index] / max(ticks[index], 1)
            pressure = float(return_5s[index] or 0.0) * side * 10_000
            if (
                activity[index] < cfg.minimum_activity_ratio
                or efficiency < cfg.minimum_quote_efficiency_ratio
                or pressure < cfg.confirmation_bps
                or spread_shocks[index] > cfg.maximum_spread_shock_ratio
            ):
                return None
        if policy == "trend_alignment_hold":
            short_pressure = float(return_5s[index] or 0.0) * side * 10_000
            long_pressure = float(return_30s[index] or 0.0) * side * 10_000
            if (
                short_pressure < cfg.confirmation_bps
                or long_pressure < cfg.confirmation_bps
                or spread_shocks[index] > cfg.maximum_spread_shock_ratio
            ):
                return None
        entry = _at(timestamps, index, int(delay), cfg.maximum_gap_seconds)
        if entry is None or spreads[entry] > cfg.maximum_spread_bps:
            return None
        distance = (mids[entry] / level - 1) * 10_000 * side
        return (entry, side) if distance >= cfg.confirmation_bps else None
    window = cfg.retest_window_seconds if policy == "retest_resume" else cfg.sweep_window_seconds
    end = _at(timestamps, index, window, cfg.maximum_gap_seconds)
    if end is None:
        return None
    tolerance = cfg.retest_tolerance_bps / 10_000
    if policy == "sweep_fade":
        for cursor in range(index + 1, end + 1):
            inside = (
                mids[cursor] <= level * (1 - tolerance)
                if side > 0
                else mids[cursor] >= level * (1 + tolerance)
            )
            if inside and spreads[cursor] <= cfg.maximum_spread_bps:
                return cursor, -side
        return None
    touched = False
    for cursor in range(index + 1, end + 1):
        if abs(mids[cursor] / level - 1) * 10_000 <= cfg.retest_tolerance_bps:
            touched = True
        resumed = (mids[cursor] / level - 1) * 10_000 * side >= cfg.confirmation_bps
        if touched and resumed and spreads[cursor] <= cfg.maximum_spread_bps:
            return cursor, side
    return None


def _exit(
    policy,
    exit_id,
    fold,
    test_end_exclusive,
    entry,
    side,
    level,
    timestamps,
    mids,
    quotes,
    execution_entries,
    cfg,
    horizon_seconds=None,
    exit_style=None,
):
    horizon = horizon_seconds or cfg.time_exit_seconds
    style = exit_style or exit_id.replace(f"_{horizon}s", "")
    if style == f"time_{horizon}s":
        style = "time"
    final = _at(quotes.quote_at, entry, horizon, cfg.maximum_gap_seconds)
    if final is None:
        return None
    if quotes.quote_at[final] >= test_end_exclusive:
        return None
    exit_index = final
    best_bps = -math.inf
    for cursor in range(entry + 1, final + 1):
        entry_mid = (quotes.open_bid[entry] + quotes.open_ask[entry]) / 2
        gross = _gross_return_bps(entry_mid, mids[cursor], side)
        best_bps = max(best_bps, gross)
        exit_signal = False
        if style == "structural_or_time":
            crossed = mids[cursor] < level if side > 0 else mids[cursor] > level
            if crossed:
                exit_signal = True
        if (
            style == "trailing_or_time"
            and best_bps >= cfg.trailing_activation_bps
            and gross <= best_bps - cfg.trailing_distance_bps
        ):
            exit_signal = True
        if exit_signal:
            executable = execution_entries[cursor]
            if executable is not None and executable <= final:
                exit_index = executable
                break
    slip = cfg.slippage_bps_per_side / 10_000
    entry_mid = (quotes.open_bid[entry] + quotes.open_ask[entry]) / 2
    exit_mid = (quotes.open_bid[exit_index] + quotes.open_ask[exit_index]) / 2
    gross = _gross_return_bps(entry_mid, exit_mid, side)
    if side > 0:
        entry_price = quotes.open_ask[entry] * (1 + slip)
        exit_price = quotes.open_bid[exit_index] * (1 - slip)
        net = (exit_price / entry_price - 1) * 10_000
    else:
        entry_price = quotes.open_bid[entry] * (1 - slip)
        exit_price = quotes.open_ask[exit_index] * (1 + slip)
        net = (entry_price / exit_price - 1) * 10_000
    holding = (quotes.quote_at[exit_index] - quotes.quote_at[entry]).total_seconds()
    return _Trade(policy, exit_id, fold, gross, net, holding, quotes.quote_at[entry])


def _metrics(
    trades: list[_Trade], cfg: SequenceLabConfig, counts: dict[str, object] | None = None
) -> list[PolicyMetrics]:
    counters = counts or {}
    keys = sorted({(trade.policy, trade.exit_id) for trade in trades})
    results = []
    for policy, exit_id in keys:
        group = [trade for trade in trades if trade.policy == policy and trade.exit_id == exit_id]
        nets = [trade.net for trade in group]
        low, high = stationary_block_interval(
            nets, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260809
        )
        folds = sorted({trade.fold for trade in group})
        profitable_folds = sum(
            _mean([trade.net for trade in group if trade.fold == fold]) > 0 for fold in folds
        )
        gains = math.fsum(value for value in nets if value > 0)
        losses = -math.fsum(value for value in nets if value < 0)
        profit_factor = gains / losses if losses else None
        profitable_fold_ratio = profitable_folds / len(folds) if folds else 0.0
        months = sorted({trade.entry_timestamp.strftime("%Y-%m") for trade in group})
        monthly_net = {
            month: math.fsum(
                trade.net
                for trade in group
                if trade.entry_timestamp.strftime("%Y-%m") == month
            )
            for month in months
        }
        profitable_months = sum(value > 0 for value in monthly_net.values())
        profitable_month_ratio = profitable_months / len(months) if months else 0.0
        positive_month_total = math.fsum(
            value for value in monthly_net.values() if value > 0
        )
        largest_positive_month_share = (
            max((value for value in monthly_net.values() if value > 0), default=0.0)
            / positive_month_total
            if positive_month_total
            else 0.0
        )
        eligible_episodes = int(counters.get("eligible_episodes", 0))
        policy_trigger_count = int(counters.get(f"trigger:{policy}", 0))
        signal_count = int(counters.get(f"policy:{policy}", 0))
        abstention_rate = 1 - signal_count / eligible_episodes if eligible_episodes else 1.0
        policy_abstention_rate = (
            1 - policy_trigger_count / eligible_episodes if eligible_episodes else 1.0
        )
        execution_rejection_rate = (
            1 - signal_count / policy_trigger_count if policy_trigger_count else 0.0
        )
        stressed_1_5x = _mean(
            [trade.gross - 1.5 * (trade.gross - trade.net) for trade in group]
        )
        stressed_2x = _mean(
            [trade.gross - 2.0 * (trade.gross - trade.net) for trade in group]
        )
        mean_gross = _mean([trade.gross for trade in group])
        fold_means = [
            _mean([trade.net for trade in group if trade.fold == fold]) for fold in folds
        ]
        family_adjusted_fold_low = _family_adjusted_mean_low(fold_means, 63)
        equity = peak = drawdown = 0.0
        for value in nets:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        results.append(
            PolicyMetrics(
                policy_id=policy,
                exit_id=exit_id,
                trade_count=len(group),
                eligible_episode_count=eligible_episodes,
                policy_trigger_count=policy_trigger_count,
                signal_count=signal_count,
                abstention_rate=abstention_rate,
                policy_abstention_rate=policy_abstention_rate,
                execution_rejection_rate=execution_rejection_rate,
                fold_count=len(folds),
                profitable_fold_count=profitable_folds,
                profitable_fold_ratio=profitable_fold_ratio,
                month_count=len(months),
                profitable_month_count=profitable_months,
                profitable_month_ratio=profitable_month_ratio,
                largest_positive_month_share=largest_positive_month_share,
                mean_gross_bps=mean_gross,
                mean_cost_drag_bps=_mean([trade.gross - trade.net for trade in group]),
                mean_net_bps=_mean(nets),
                mean_net_1_5x_cost_bps=stressed_1_5x,
                mean_net_2x_cost_bps=stressed_2x,
                bootstrap_low_bps=low,
                bootstrap_high_bps=high,
                family_adjusted_fold_low_bps=family_adjusted_fold_low,
                win_rate=sum(value > 0 for value in nets) / len(nets),
                profit_factor=profit_factor,
                maximum_drawdown_bps=drawdown,
                mean_holding_seconds=_mean([trade.holding_seconds for trade in group]),
                passes_research_gate=(
                    low > 0
                    and family_adjusted_fold_low > 0
                    and mean_gross >= 4.0
                    and stressed_1_5x >= 0
                    and stressed_2x >= 0
                    and len(folds) >= 4
                    and profitable_fold_ratio >= 0.6
                    and len(months) >= 6
                    and profitable_month_ratio >= 0.6
                    and largest_positive_month_share <= 0.5
                    and profit_factor is not None
                    and profit_factor >= 1.15
                    and len(group) >= 500
                ),
            )
        )
    return results


def _at(timestamps, start, seconds, maximum_delay):
    target = timestamps[start] + timedelta(seconds=seconds)
    index = bisect.bisect_left(timestamps, target, lo=start + 1)
    if index >= len(timestamps) or (timestamps[index] - target).total_seconds() > maximum_delay:
        return None
    for cursor in range(start + 1, index + 1):
        if (timestamps[cursor] - timestamps[cursor - 1]).total_seconds() > maximum_delay:
            return None
    return index


def _fold(timestamp, folds):
    for fold in folds:
        if fold.test_start <= timestamp < fold.test_end_exclusive:
            return fold
    return None


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _gross_return_bps(entry_mid: float, exit_mid: float, side: int) -> float:
    if side > 0:
        return (exit_mid / entry_mid - 1) * 10_000
    return (entry_mid / exit_mid - 1) * 10_000


def _family_adjusted_mean_low(values: list[float], family_size: int) -> float:
    if len(values) < 2:
        return -math.inf
    alpha = 0.05 / max(family_size, 1)
    z_score = statistics.NormalDist().inv_cdf(1 - alpha)
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return statistics.mean(values) - z_score * standard_error


def _meta(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_causal_manifest(meta: dict[str, object], label: str) -> None:
    if meta.get("point_in_time") is not True:
        raise ValueError(f"{label} manifest must declare point_in_time=true")
    if meta.get("labels_included") is not False:
        raise ValueError(f"{label} manifest must declare labels_included=false")


def _dataset_bounds(
    manifest: Path, meta: dict[str, object]
) -> tuple[datetime, datetime]:
    first = meta.get("first_timestamp")
    last = meta.get("last_timestamp")
    if first and last:
        start_value = datetime.fromisoformat(str(first)).astimezone(UTC)
        last_value = datetime.fromisoformat(str(last)).astimezone(UTC)
    else:
        paths = _partition_paths(manifest, meta)
        first_file = pq.ParquetFile(paths[0])
        last_file = pq.ParquetFile(paths[-1])
        first_table = first_file.read_row_group(0, columns=["occurred_at"])
        last_table = last_file.read_row_group(
            last_file.num_row_groups - 1, columns=["occurred_at"]
        )
        start_value = _utc_timestamps(first_table["occurred_at"])[0]
        last_value = _utc_timestamps(last_table["occurred_at"])[-1]
    start = start_value.replace(hour=0, minute=0, second=0, microsecond=0)
    end = last_value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
    return start, end


def _partition_paths(manifest: Path, meta: dict[str, object]) -> list[Path]:
    root = manifest.resolve().parent
    parts = meta.get("partitions")
    if not isinstance(parts, list) or not parts:
        raise ValueError("manifest has no partitions")
    paths: list[Path] = []
    for stored in parts:
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("partition escapes manifest directory")
        paths.append(path)
    return paths


def _aligned_windows(
    feature_manifest,
    feature_meta,
    structural_manifest,
    structural_meta,
    cfg,
    core_rows=50_000,
):
    feature_columns = [
        "occurred_at",
        "feature_available_at",
        "bar_open_at",
        "bar_open_bid",
        "bar_open_ask",
        "mid",
        "spread_bps",
        "tick_intensity_ratio",
        "tick_count",
        "quote_change_count",
        "spread_shock_ratio",
        "return_5s",
        "return_30s",
    ]
    structure_columns = [
        "occurred_at",
        "compression_60_to_300",
        f"breakout_side_{cfg.breakout_window_seconds}s",
        f"prior_high_{cfg.breakout_window_seconds}s",
        f"prior_low_{cfg.breakout_window_seconds}s",
    ]
    feature_batches = _table_batches(
        _partition_paths(feature_manifest, feature_meta), feature_columns, core_rows
    )
    structure_batches = _table_batches(
        _partition_paths(structural_manifest, structural_meta),
        structure_columns,
        core_rows,
    )
    features: pa.Table | None = None
    structure: pa.Table | None = None
    exhausted = False
    while features is not None or not exhausted:
        while features is None or features.num_rows < core_rows:
            pair = _next_aligned(feature_batches, structure_batches)
            if pair is None:
                exhausted = True
                break
            feature_batch, structure_batch = pair
            features = (
                feature_batch
                if features is None
                else pa.concat_tables([features, feature_batch])
            )
            structure = (
                structure_batch
                if structure is None
                else pa.concat_tables([structure, structure_batch])
            )
        if features is None or structure is None or features.num_rows == 0:
            break
        output_rows = min(core_rows, features.num_rows)
        signal_delay = max(
            *cfg.hold_delays_seconds,
            cfg.retest_window_seconds,
            cfg.sweep_window_seconds,
        )
        horizon = max(cfg.time_exit_seconds, *cfg.additional_exit_horizons_seconds)
        target = _timestamp_at(features["occurred_at"], output_rows - 1) + timedelta(
            seconds=signal_delay + horizon + cfg.maximum_entry_delay_seconds + 2,
            milliseconds=cfg.decision_latency_ms,
        )
        while not exhausted and _timestamp_at(features["bar_open_at"], -1) < target:
            pair = _next_aligned(feature_batches, structure_batches)
            if pair is None:
                exhausted = True
                break
            feature_batch, structure_batch = pair
            features = pa.concat_tables([features, feature_batch])
            structure = pa.concat_tables([structure, structure_batch])
        yield features, structure, output_rows
        features = features.slice(output_rows)
        structure = structure.slice(output_rows)
        if features.num_rows == 0:
            features = None
            structure = None


def _next_aligned(feature_batches, structure_batches):
    sentinel = object()
    feature = next(feature_batches, sentinel)
    structure = next(structure_batches, sentinel)
    if feature is sentinel and structure is sentinel:
        return None
    if feature is sentinel or structure is sentinel or feature.num_rows != structure.num_rows:
        raise ValueError("feature and structure row counts differ")
    if _utc_timestamps(feature["occurred_at"]) != _utc_timestamps(
        structure["occurred_at"]
    ):
        raise ValueError("feature and structural timestamps do not align")
    return feature, structure


def _table_batches(paths, columns, batch_size):
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=batch_size, columns=columns
        ):
            yield pa.Table.from_batches([batch])


def _timestamp_at(column: pa.ChunkedArray, index: int) -> datetime:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    value = column.cast(pa.int64())[index].as_py()
    return datetime.fromtimestamp(value / divisor, UTC)


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
