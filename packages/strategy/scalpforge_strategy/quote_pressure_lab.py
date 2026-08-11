from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scalpforge_strategy.execution_clock import CausalExecutionConfig, CausalQuoteSeries
from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.sequence_lab import (
    SequenceLabConfig,
    _aligned_windows,
    _at,
    _dataset_bounds,
    _gross_return_bps,
    _meta,
    _require_causal_manifest,
    _utc_timestamps,
)


@dataclass(frozen=True)
class QuotePressureConfig:
    analysis_revision: int = 1
    horizons_seconds: tuple[int, ...] = (900, 1800, 3600)
    pressure_thresholds_bps: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0)
    activity_thresholds: tuple[float, ...] = (1.5, 2.0, 3.0)
    spread_caps_bps: tuple[float, ...] = (2.0, 3.0, 4.0)
    sessions: tuple[str, ...] = ("all", "london", "new_york", "london_new_york_overlap")
    signal_delay_seconds: int = 5
    limit_wait_seconds: int = 5
    decision_latency_ms: int = 50
    maximum_quote_delay_seconds: int = 2
    maximum_gap_seconds: int = 5
    slippage_bps_per_side: float = 0.5
    minimum_episode_spacing_seconds: int = 3660
    minimum_train_trades: int = 30
    minimum_validation_trades: int = 15
    minimum_test_trades: int = 10
    final_holdout_days: int = 4
    walk_forward: WalkForwardConfig = WalkForwardConfig(30, 7, 7, 7, 3600, 3600)

    def __post_init__(self) -> None:
        if self.analysis_revision < 1:
            raise ValueError("quote-pressure analysis revision is invalid")
        if min(self.horizons_seconds) <= 0 or len(set(self.horizons_seconds)) != len(
            self.horizons_seconds
        ):
            raise ValueError("horizons must be positive and unique")
        required = (
            self.signal_delay_seconds
            + max(self.horizons_seconds)
            + self.maximum_quote_delay_seconds
            + self.decision_latency_ms / 1000
        )
        if self.minimum_episode_spacing_seconds < required:
            raise ValueError("episode spacing must cover the complete maximum-horizon trade")


@dataclass(frozen=True)
class SelectedFold:
    fold: int
    horizon_seconds: int
    execution_style: str
    pressure_threshold_bps: float
    activity_threshold: float
    spread_cap_bps: float
    session: str
    train_trades: int
    validation_trades: int
    test_trades: int
    train_mean_net_bps: float
    validation_mean_net_bps: float
    test_mean_net_bps: float
    test_mean_gross_bps: float
    test_fill_rate: float


@dataclass(frozen=True)
class QuotePressureReport:
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
    selected_folds: list[SelectedFold]
    aggregate_test_trades: int
    aggregate_mean_gross_bps: float
    aggregate_mean_net_bps: float
    aggregate_profit_factor: float | None
    profitable_fold_ratio: float
    research_gate_passed: bool
    passive_limit_is_optimistic_upper_bound: bool = True
    research_only: bool = True
    real_money_enabled: bool = False


@dataclass(frozen=True)
class _Event:
    occurred_at: datetime
    side: int
    session: str
    pressure_bps: float
    activity: float
    spread_bps: float
    horizon: int
    execution_style: str
    filled: bool
    gross_bps: float
    net_bps: float


def run_quote_pressure_lab(
    feature_manifest: Path,
    structural_manifest: Path,
    output_root: Path,
    config: QuotePressureConfig | None = None,
) -> QuotePressureReport:
    cfg = config or QuotePressureConfig()
    feature_meta = _meta(feature_manifest)
    structure_meta = _meta(structural_manifest)
    _require_causal_manifest(feature_meta, "feature")
    _require_causal_manifest(structure_meta, "structural")
    if structure_meta.get("source_feature_dataset_id") != feature_meta.get("dataset_id"):
        raise ValueError("structural dataset does not belong to feature dataset")
    start, end = _dataset_bounds(feature_manifest, feature_meta)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    sequence_cfg = SequenceLabConfig(
        hold_delays_seconds=(cfg.signal_delay_seconds,),
        retest_window_seconds=cfg.signal_delay_seconds,
        sweep_window_seconds=cfg.signal_delay_seconds,
        time_exit_seconds=cfg.horizons_seconds[0],
        additional_exit_horizons_seconds=cfg.horizons_seconds[1:],
        maximum_gap_seconds=cfg.maximum_gap_seconds,
        decision_latency_ms=cfg.decision_latency_ms,
        maximum_entry_delay_seconds=cfg.maximum_quote_delay_seconds,
        minimum_episode_spacing_seconds=cfg.minimum_episode_spacing_seconds,
    )
    windows = _aligned_windows(
        feature_manifest, feature_meta, structural_manifest, structure_meta, sequence_cfg
    )
    events = _collect_events(windows, holdout_start, cfg)
    selected = [
        _select_fold(events, fold, cfg, style)
        for fold in folds
        for style in ("market", "passive_limit")
    ]
    selected = [item for item in selected if item is not None]
    test_events = [
        event
        for event in _selected_test_events(events, folds, selected)
        if event.execution_style == "market"
    ]
    nets = [event.net_bps for event in test_events]
    gross = [event.gross_bps for event in test_events]
    gains = math.fsum(value for value in nets if value > 0)
    losses = -math.fsum(value for value in nets if value < 0)
    profitable_ratio = (
        sum(item.test_mean_net_bps > 0 for item in selected) / len(selected) if selected else 0.0
    )
    aggregate_net = _mean(nets)
    aggregate_gross = _mean(gross)
    gate = (
        len(test_events) >= 500
        and aggregate_gross >= 4.0
        and aggregate_net > 0
        and profitable_ratio >= 0.6
        and losses > 0
        and gains / losses >= 1.15
    )
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "structure": structure_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "quote-pressure-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = QuotePressureReport(
        report_id=report_id,
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        feature_dataset_id=str(feature_meta["dataset_id"]),
        structural_dataset_id=str(structure_meta["dataset_id"]),
        config=serialized,
        final_holdout_start=holdout_start.isoformat(),
        final_holdout_end_exclusive=end.isoformat(),
        holdout_evaluated=False,
        fold_count=len(folds),
        selected_folds=selected,
        aggregate_test_trades=len(test_events),
        aggregate_mean_gross_bps=aggregate_gross,
        aggregate_mean_net_bps=aggregate_net,
        aggregate_profit_factor=gains / losses if losses else None,
        profitable_fold_ratio=profitable_ratio,
        research_gate_passed=gate,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="quote-pressure-lab",
        dataset_ids=(report.feature_dataset_id, report.structural_dataset_id),
        hypothesis_count=len(cfg.horizons_seconds) * 2,
        holdout_evaluated=False,
    )
    return report


def _collect_events(windows, holdout_start: datetime, cfg: QuotePressureConfig) -> list[_Event]:
    events: list[_Event] = []
    previous_state: int | None = None
    previous_timestamp: datetime | None = None
    last_event: datetime | None = None
    for features, structure, core_rows in windows:
        timestamps = _utc_timestamps(features["occurred_at"])
        sides = structure["breakout_side_300s"].to_pylist()
        returns = features["return_5s"].to_pylist()
        activity = features["tick_intensity_ratio"].to_pylist()
        spreads = features["spread_bps"].to_pylist()
        sessions = features["session"].to_pylist()
        quotes = CausalQuoteSeries.from_feature_table(features, cfg.maximum_gap_seconds)
        market_entries = quotes.entry_indices(
            CausalExecutionConfig(
                cfg.decision_latency_ms,
                cfg.maximum_quote_delay_seconds,
                cfg.maximum_gap_seconds,
            )
        )
        for index, timestamp in enumerate(timestamps[:core_rows]):
            if timestamp >= holdout_start:
                return events
            side = int(sides[index] or 0)
            state = side or None
            discontinuity = previous_timestamp is None or (
                timestamp - previous_timestamp
            ).total_seconds() > 60
            episode_start = state is not None and (discontinuity or state != previous_state)
            previous_state = state
            previous_timestamp = timestamp
            if not episode_start or side == 0:
                continue
            if (
                last_event
                and (timestamp - last_event).total_seconds()
                < cfg.minimum_episode_spacing_seconds
            ):
                continue
            signal = _at(timestamps, index, cfg.signal_delay_seconds, cfg.maximum_gap_seconds)
            if signal is None:
                continue
            pressure = float(returns[signal] or 0.0) * side * 10_000
            if pressure < min(cfg.pressure_thresholds_bps):
                continue
            last_event = timestamp
            for style in ("market", "passive_limit"):
                if style == "market":
                    entry = market_entries[signal]
                    limit_price = None
                else:
                    limit_fill = _limit_fill(quotes, signal, side, cfg)
                    entry = limit_fill[0] if limit_fill else None
                    limit_price = limit_fill[1] if limit_fill else None
                filled = entry is not None
                for horizon in cfg.horizons_seconds:
                    gross_bps = net_bps = 0.0
                    if entry is not None:
                        result = _event_return(
                            quotes, entry, side, horizon, style, cfg, limit_price
                        )
                        if result is None:
                            filled = False
                        else:
                            gross_bps, net_bps = result
                    events.append(
                        _Event(
                            timestamp,
                            side,
                            str(sessions[signal]),
                            pressure,
                            float(activity[signal]),
                            float(spreads[signal]),
                            horizon,
                            style,
                            filled,
                            gross_bps,
                            net_bps,
                        )
                    )
    return events


def _limit_fill(
    quotes, signal: int, side: int, cfg: QuotePressureConfig
) -> tuple[int, float] | None:
    end = _at(quotes.quote_at, signal, cfg.limit_wait_seconds, cfg.maximum_gap_seconds)
    if end is None:
        return None
    limit = quotes.open_bid[signal] if side > 0 else quotes.open_ask[signal]
    for cursor in range(signal + 1, end + 1):
        if side > 0 and quotes.open_ask[cursor] < limit:
            return cursor, limit
        if side < 0 and quotes.open_bid[cursor] > limit:
            return cursor, limit
    return None


def _event_return(quotes, entry, side, horizon, style, cfg, limit_price=None):
    exit_index = _at(quotes.quote_at, entry, horizon, cfg.maximum_gap_seconds)
    if exit_index is None:
        return None
    entry_mid = (quotes.open_bid[entry] + quotes.open_ask[entry]) / 2
    exit_mid = (quotes.open_bid[exit_index] + quotes.open_ask[exit_index]) / 2
    gross = _gross_return_bps(entry_mid, exit_mid, side)
    slip = cfg.slippage_bps_per_side / 10_000
    if side > 0:
        entry_price = (
            quotes.open_ask[entry] * (1 + slip)
            if style == "market"
            else float(limit_price)
        )
        exit_price = quotes.open_bid[exit_index] * (1 - slip)
        net = (exit_price / entry_price - 1) * 10_000
    else:
        entry_price = (
            quotes.open_bid[entry] * (1 - slip)
            if style == "market"
            else float(limit_price)
        )
        exit_price = quotes.open_ask[exit_index] * (1 + slip)
        net = (entry_price / exit_price - 1) * 10_000
    return gross, net


def _select_fold(events, fold, cfg, required_style: str | None = None) -> SelectedFold | None:
    best = None
    train_pool = _between(events, fold.train_start, fold.train_end_exclusive)
    validation_pool = _between(
        events, fold.validation_start, fold.validation_end_exclusive
    )
    test_pool = _between(events, fold.test_start, fold.test_end_exclusive)
    for horizon in cfg.horizons_seconds:
        for style in ("market", "passive_limit"):
            if required_style is not None and style != required_style:
                continue
            for pressure in cfg.pressure_thresholds_bps:
                for activity in cfg.activity_thresholds:
                    for spread in cfg.spread_caps_bps:
                        for session in cfg.sessions:
                            candidate = (horizon, style, pressure, activity, spread, session)
                            train = _matching_pool(train_pool, candidate)
                            validation = _matching_pool(validation_pool, candidate)
                            if (
                                len(train) < cfg.minimum_train_trades
                                or len(validation) < cfg.minimum_validation_trades
                            ):
                                continue
                            train_mean = _mean([event.net_bps for event in train])
                            validation_mean = _mean([event.net_bps for event in validation])
                            score = min(train_mean, validation_mean)
                            if train_mean <= 0 or score <= 0:
                                continue
                            if best is None or score > best[0]:
                                best = (score, candidate, train, validation)
    if best is None:
        return None
    _, candidate, train, validation = best
    test = _matching_pool(test_pool, candidate)
    if len(test) < cfg.minimum_test_trades:
        return None
    horizon, style, pressure, activity, spread, session = candidate
    eligible_test = _matching_pool(test_pool, candidate, filled=False)
    fill_rate = len(test) / len(eligible_test) if eligible_test else 0.0
    return SelectedFold(
        fold.fold,
        horizon,
        style,
        pressure,
        activity,
        spread,
        session,
        len(train),
        len(validation),
        len(test),
        _mean([event.net_bps for event in train]),
        _mean([event.net_bps for event in validation]),
        _mean([event.net_bps for event in test]),
        _mean([event.gross_bps for event in test]),
        fill_rate,
    )


def _matching(events, candidate, start, end, filled=True):
    return _matching_pool(_between(events, start, end), candidate, filled)


def _between(events, start, end):
    return [event for event in events if start <= event.occurred_at < end]


def _matching_pool(events, candidate, filled=True):
    horizon, style, pressure, activity, spread, session = candidate
    return [
        event
        for event in events
        if event.horizon == horizon
        and event.execution_style == style
        and event.pressure_bps >= pressure
        and event.activity >= activity
        and event.spread_bps <= spread
        and (session == "all" or event.session == session)
        and (event.filled or not filled)
    ]


def _selected_test_events(events, folds, selected):
    by_fold = {(item.fold, item.execution_style): item for item in selected}
    result = []
    for fold in folds:
        for style in ("market", "passive_limit"):
            item = by_fold.get((fold.fold, style))
            if item is None:
                continue
            candidate = (
                item.horizon_seconds,
                item.execution_style,
                item.pressure_threshold_bps,
                item.activity_threshold,
                item.spread_cap_bps,
                item.session,
            )
            result.extend(
                _matching(events, candidate, fold.test_start, fold.test_end_exclusive)
            )
    return result


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
