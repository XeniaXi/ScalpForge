from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class SessionStrategyConfig:
    analysis_revision: int = 1
    horizons_seconds: tuple[int, ...] = (60, 300)
    maximum_spread_bps: float = 4.0
    minimum_activity_ratio: float = 1.5
    failure_window_seconds: int = 30
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(30, 7, 7, 7, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1:
            raise ValueError("unsupported session-strategy analysis revision")
        if self.horizons_seconds != (60, 300):
            raise ValueError("only preregistered executable horizons 60 and 300 are supported")
        if self.maximum_spread_bps <= 0 or self.minimum_activity_ratio <= 0:
            raise ValueError("spread and activity thresholds must be positive")
        if self.failure_window_seconds <= 0:
            raise ValueError("failure window must be positive")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades <= 0:
            raise ValueError("bootstrap configuration is too small")


@dataclass(frozen=True)
class SessionPolicyMetrics:
    window: str
    policy: str
    horizon_seconds: int
    trade_count: int
    fold_count: int
    profitable_fold_ratio: float
    month_count: int
    profitable_month_ratio: float
    mean_gross_bps: float
    mean_net_bps: float
    mean_net_1_5x_cost_bps: float
    mean_net_2x_cost_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    family_adjusted_fold_low_bps: float
    win_rate: float
    profit_factor: float | None
    maximum_drawdown_bps: float
    passes_research_gate: bool


@dataclass(frozen=True)
class SessionStrategyReport:
    report_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    session_range_dataset_id: str
    outcome_dataset_id: str
    config: dict[str, object]
    hypothesis_count: int
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    fold_count: int
    metrics: list[SessionPolicyMetrics]
    research_only: bool = True
    real_money_enabled: bool = False


@dataclass(frozen=True)
class _Trade:
    window: str
    policy: str
    horizon: int
    fold: int
    timestamp: datetime
    gross: float
    net: float


@dataclass
class _WindowState:
    side: int = 0
    breakout_at: datetime | None = None
    traded_keys: set[tuple[str, str, int]] | None = None

    def __post_init__(self) -> None:
        self.traded_keys = set() if self.traded_keys is None else self.traded_keys


def run_session_strategy_lab(
    feature_manifest: Path,
    session_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: SessionStrategyConfig | None = None,
) -> SessionStrategyReport:
    cfg = config or SessionStrategyConfig()
    feature_meta = _meta(feature_manifest)
    session_meta = _meta(session_manifest)
    outcome_meta = _meta(outcome_manifest)
    _validate(feature_meta, session_meta, outcome_meta, cfg)
    start, end = _dataset_bounds(feature_manifest, feature_meta)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    windows = [item["name"] for item in session_meta["session_config"]["windows"]]
    trades = _collect_trades(
        feature_manifest,
        feature_meta,
        session_manifest,
        session_meta,
        outcome_manifest,
        outcome_meta,
        windows,
        folds,
        cfg,
    )
    hypothesis_count = len(windows) * 4 * len(cfg.horizons_seconds)
    metrics = _metrics(trades, hypothesis_count, cfg)
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "sessions": session_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "session-strategy-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = SessionStrategyReport(
        report_id=report_id,
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        feature_dataset_id=str(feature_meta["dataset_id"]),
        session_range_dataset_id=str(session_meta["dataset_id"]),
        outcome_dataset_id=str(outcome_meta["dataset_id"]),
        config=serialized,
        hypothesis_count=hypothesis_count,
        final_holdout_start=holdout_start.isoformat(),
        final_holdout_end_exclusive=end.isoformat(),
        holdout_evaluated=False,
        fold_count=len(folds),
        metrics=metrics,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="session-strategy-lab",
        dataset_ids=(
            report.feature_dataset_id,
            report.session_range_dataset_id,
            report.outcome_dataset_id,
        ),
        hypothesis_count=hypothesis_count,
        holdout_evaluated=False,
    )
    return report


def _collect_trades(
    feature_manifest,
    feature_meta,
    session_manifest,
    session_meta,
    outcome_manifest,
    outcome_meta,
    windows,
    folds,
    cfg,
) -> list[_Trade]:
    states = {window: _WindowState() for window in windows}
    trades: list[_Trade] = []
    current_day: str | None = None
    for features, sessions, outcomes in _aligned_batches(
        feature_manifest,
        feature_meta,
        session_manifest,
        session_meta,
        outcome_manifest,
        outcome_meta,
        windows,
        cfg,
    ):
        timestamps = _utc_timestamps(features["occurred_at"])
        spreads = features["spread_bps"].to_pylist()
        activity = features["tick_intensity_ratio"].to_pylist()
        returns = features["return_5s"].to_pylist()
        days = sessions["session_day_utc"].to_pylist()
        for index, timestamp in enumerate(timestamps):
            fold = _fold(timestamp, folds)
            if days[index] != current_day:
                current_day = str(days[index])
                states = {window: _WindowState() for window in windows}
            for window in windows:
                state = states[window]
                side = int(sessions[f"{window}_breakout_side"][index].as_py() or 0)
                policies: list[tuple[str, int]] = []
                if side and state.side == 0:
                    state.breakout_at = timestamp
                    policies.append(("immediate_breakout", side))
                    momentum = float(returns[index] or 0.0) * side > 0
                    if momentum:
                        policies.append(("momentum_confirmed", side))
                    if momentum and float(activity[index]) >= cfg.minimum_activity_ratio:
                        policies.append(("activity_confirmed", side))
                elif (
                    side == 0
                    and state.side
                    and state.breakout_at is not None
                    and (timestamp - state.breakout_at).total_seconds()
                    <= cfg.failure_window_seconds
                ):
                    policies.append(("false_breakout_fade", -state.side))
                if fold is not None and float(spreads[index]) <= cfg.maximum_spread_bps:
                    for policy, trade_side in policies:
                        for horizon in cfg.horizons_seconds:
                            key = (window, policy, horizon)
                            if key in state.traded_keys:
                                continue
                            trade = _outcome_trade(
                                outcomes[horizon],
                                index,
                                window,
                                policy,
                                trade_side,
                                horizon,
                                fold.fold,
                                timestamp,
                            )
                            if trade is not None:
                                trades.append(trade)
                                state.traded_keys.add(key)
                state.side = side
    return trades


def _outcome_trade(table, index, window, policy, side, horizon, fold, timestamp):
    prefix = f"h{horizon}"
    if not table[f"{prefix}_valid"][index].as_py():
        return None
    direction = "long" if side > 0 else "short"
    return _Trade(
        window,
        policy,
        horizon,
        fold,
        timestamp,
        float(table[f"{prefix}_{direction}_gross_bps"][index].as_py()),
        float(table[f"{prefix}_{direction}_net_bps"][index].as_py()),
    )


def _metrics(trades: list[_Trade], family_size: int, cfg) -> list[SessionPolicyMetrics]:
    keys = sorted({(trade.window, trade.policy, trade.horizon) for trade in trades})
    results: list[SessionPolicyMetrics] = []
    for window, policy, horizon in keys:
        group = [
            trade
            for trade in trades
            if (trade.window, trade.policy, trade.horizon) == (window, policy, horizon)
        ]
        nets = [trade.net for trade in group]
        gross = [trade.gross for trade in group]
        costs = [trade.gross - trade.net for trade in group]
        folds = sorted({trade.fold for trade in group})
        fold_means = [_mean([trade.net for trade in group if trade.fold == fold]) for fold in folds]
        months = sorted({trade.timestamp.strftime("%Y-%m") for trade in group})
        month_sums = [
            math.fsum(trade.net for trade in group if trade.timestamp.strftime("%Y-%m") == month)
            for month in months
        ]
        low, high = stationary_block_interval(
            nets, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260811
        )
        gains = math.fsum(value for value in nets if value > 0)
        losses = -math.fsum(value for value in nets if value < 0)
        stressed_1_5 = _mean([value - 1.5 * cost for value, cost in zip(gross, costs, strict=True)])
        stressed_2 = _mean([value - 2.0 * cost for value, cost in zip(gross, costs, strict=True)])
        mean_gross = _mean(gross)
        profitable_fold_ratio = sum(value > 0 for value in fold_means) / len(fold_means)
        profitable_month_ratio = sum(value > 0 for value in month_sums) / len(month_sums)
        family_low = _family_low(fold_means, family_size)
        equity = peak = drawdown = 0.0
        for value in nets:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        profit_factor = gains / losses if losses else None
        gate = (
            len(group) >= 200
            and len(folds) >= 8
            and len(months) >= 6
            and low > 0
            and family_low > 0
            and mean_gross >= 4.0
            and stressed_1_5 > 0
            and stressed_2 >= 0
            and profitable_fold_ratio >= 0.6
            and profitable_month_ratio >= 0.6
            and profit_factor is not None
            and profit_factor >= 1.15
        )
        results.append(
            SessionPolicyMetrics(
                window, policy, horizon, len(group), len(folds), profitable_fold_ratio,
                len(months), profitable_month_ratio, mean_gross, _mean(nets), stressed_1_5,
                stressed_2, low, high, family_low,
                sum(value > 0 for value in nets) / len(nets), profit_factor, drawdown, gate,
            )
        )
    return results


def _validate(features, sessions, outcomes, cfg) -> None:
    if not features.get("point_in_time") or features.get("labels_included"):
        raise ValueError("features must be point-in-time and label-free")
    if not sessions.get("point_in_time") or sessions.get("labels_included"):
        raise ValueError("session ranges must be point-in-time and label-free")
    if sessions.get("source_feature_dataset_id") != features.get("dataset_id"):
        raise ValueError("session ranges do not belong to feature dataset")
    if outcomes.get("source_feature_dataset_id") != features.get("dataset_id"):
        raise ValueError("outcomes do not belong to feature dataset")
    available = outcomes.get("horizon_partitions", {})
    if any(str(horizon) not in available for horizon in cfg.horizons_seconds):
        raise ValueError("outcome dataset lacks a preregistered horizon")


def _aligned_batches(fm, fmeta, sm, smeta, om, ometa, windows, cfg, batch_size=50_000):
    feature_columns = ["occurred_at", "spread_bps", "tick_intensity_ratio", "return_5s"]
    session_columns = ["occurred_at", "session_day_utc"] + [
        f"{window}_breakout_side" for window in windows
    ]
    feature_batches = _batches(_paths(fm, fmeta), feature_columns, batch_size)
    session_batches = _batches(_paths(sm, smeta), session_columns, batch_size)
    outcome_batches = {
        horizon: _batches(
            [_horizon_path(om, ometa, horizon)],
            _outcome_columns(horizon),
            batch_size,
        )
        for horizon in cfg.horizons_seconds
    }
    sentinel = object()
    while True:
        feature = next(feature_batches, sentinel)
        session = next(session_batches, sentinel)
        outcome = {horizon: next(stream, sentinel) for horizon, stream in outcome_batches.items()}
        items = [feature, session, *outcome.values()]
        if all(item is sentinel for item in items):
            return
        if any(item is sentinel for item in items):
            raise ValueError("research datasets have different row counts")
        tables = [item for item in items if isinstance(item, pa.Table)]
        if len({table.num_rows for table in tables}) != 1:
            raise ValueError("research batch row counts do not align")
        timestamps = [_utc_timestamps(table["occurred_at"]) for table in tables]
        if any(values != timestamps[0] for values in timestamps[1:]):
            raise ValueError("research timestamps do not align")
        yield feature, session, outcome


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


def _dataset_bounds(manifest, meta):
    first = datetime.fromisoformat(str(meta["first_timestamp"])).astimezone(UTC)
    last = datetime.fromisoformat(str(meta["last_timestamp"])).astimezone(UTC)
    return (
        first.replace(hour=0, minute=0, second=0, microsecond=0),
        last.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
    )


def _fold(timestamp, folds):
    return next(
        (
            fold
            for fold in folds
            if fold.test_start <= timestamp < fold.test_end_exclusive
        ),
        None,
    )


def _family_low(values, family_size):
    if len(values) < 2:
        return -math.inf
    alpha = 0.05 / max(family_size, 1)
    z_score = statistics.NormalDist().inv_cdf(1 - alpha)
    return statistics.mean(values) - z_score * statistics.stdev(values) / math.sqrt(len(values))


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _meta(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
