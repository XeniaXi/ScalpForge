from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class SequenceLabConfig:
    breakout_window_seconds: int = 300
    hold_delays_seconds: tuple[int, ...] = (5, 15)
    retest_window_seconds: int = 30
    sweep_window_seconds: int = 15
    confirmation_bps: float = 0.25
    retest_tolerance_bps: float = 0.5
    maximum_spread_bps: float = 8.0
    maximum_gap_seconds: int = 5
    time_exit_seconds: int = 60
    trailing_activation_bps: float = 3.0
    trailing_distance_bps: float = 1.5
    slippage_bps_per_side: float = 0.5
    decision_interval_seconds: int = 60
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(10, 3, 3, 3, 300, 300)

    def __post_init__(self) -> None:
        if min(self.hold_delays_seconds) <= 0 or self.time_exit_seconds <= 0:
            raise ValueError("delays and exit horizon must be positive")
        if self.maximum_gap_seconds <= 0 or self.maximum_spread_bps <= 0:
            raise ValueError("gap and spread limits must be positive")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades <= 0:
            raise ValueError("bootstrap configuration is too small")


@dataclass(frozen=True)
class PolicyMetrics:
    policy_id: str
    exit_id: str
    trade_count: int
    fold_count: int
    profitable_fold_count: int
    mean_gross_bps: float
    mean_cost_drag_bps: float
    mean_net_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    win_rate: float
    profit_factor: float | None
    maximum_drawdown_bps: float
    mean_holding_seconds: float


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


def run_sequence_lab(
    feature_manifest: Path,
    structural_manifest: Path,
    output_root: Path,
    config: SequenceLabConfig | None = None,
) -> SequenceLabReport:
    cfg = config or SequenceLabConfig()
    feature_meta = _meta(feature_manifest)
    structure_meta = _meta(structural_manifest)
    if structure_meta.get("source_feature_dataset_id") != feature_meta.get("dataset_id"):
        raise ValueError("structural dataset does not belong to feature dataset")
    features = _read_all(feature_manifest, feature_meta)
    structure = _read_all(structural_manifest, structure_meta)
    if features.num_rows != structure.num_rows:
        raise ValueError("feature and structure row counts differ")
    timestamps = _utc_timestamps(features["occurred_at"])
    if timestamps != _utc_timestamps(structure["occurred_at"]):
        raise ValueError("feature and structural timestamps do not align")
    start = timestamps[0].replace(hour=0, minute=0, second=0, microsecond=0)
    end = timestamps[-1].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    trades = _simulate(features, structure, timestamps, folds, cfg)
    metrics = _metrics(trades, cfg)
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
        return SequenceLabReport(**payload)
    report = SequenceLabReport(
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
        metrics=metrics,
    )
    root.mkdir(parents=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return report


def _simulate(features, structure, timestamps, folds, cfg) -> list[_Trade]:
    bids = [float(value) for value in features["bid"].to_pylist()]
    asks = [float(value) for value in features["ask"].to_pylist()]
    mids = [float(value) for value in features["mid"].to_pylist()]
    spreads = [float(value) for value in features["spread_bps"].to_pylist()]
    activity = [float(value) for value in features["tick_intensity_ratio"].to_pylist()]
    compression = structure["compression_60_to_300"].to_pylist()
    sides = structure[f"breakout_side_{cfg.breakout_window_seconds}s"].to_pylist()
    highs = structure[f"prior_high_{cfg.breakout_window_seconds}s"].to_pylist()
    lows = structure[f"prior_low_{cfg.breakout_window_seconds}s"].to_pylist()
    candidates = [
        (f"hold_{delay}s", delay) for delay in cfg.hold_delays_seconds
    ] + [("retest_resume", None), ("sweep_fade", None), ("compression_activity_hold", 5)]
    trades: list[_Trade] = []
    last_entry: dict[str, datetime | None] = {name: None for name, _ in candidates}
    for index, timestamp in enumerate(timestamps):
        fold = _fold(timestamp, folds)
        side = int(sides[index])
        if fold is None or side == 0 or spreads[index] > cfg.maximum_spread_bps:
            continue
        level = float(highs[index] if side > 0 else lows[index])
        for policy, delay in candidates:
            previous = last_entry[policy]
            if previous and (timestamp - previous).total_seconds() < cfg.decision_interval_seconds:
                continue
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
                compression,
                cfg,
            )
            if entry is None:
                continue
            entry_index, trade_side = entry
            if timestamps[entry_index] >= fold.test_end_exclusive:
                continue
            for exit_id in ("time_60s", "structural_or_time", "trailing_or_time"):
                trade = _exit(
                    policy,
                    exit_id,
                    fold.fold,
                    fold.test_end_exclusive,
                    entry_index,
                    trade_side,
                    level,
                    timestamps,
                    bids,
                    asks,
                    mids,
                    cfg,
                )
                if trade is not None:
                    trades.append(trade)
            last_entry[policy] = timestamps[entry_index]
    return trades


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
    compression,
    cfg,
):
    if policy.startswith("hold_") or policy == "compression_activity_hold":
        if policy == "compression_activity_hold" and (
            compression[index] is None or compression[index] > 0.5 or activity[index] < 1.5
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
    bids,
    asks,
    mids,
    cfg,
):
    final = _at(timestamps, entry, cfg.time_exit_seconds, cfg.maximum_gap_seconds)
    if final is None:
        return None
    if timestamps[final] >= test_end_exclusive:
        return None
    exit_index = final
    best_bps = -math.inf
    for cursor in range(entry + 1, final + 1):
        gross = (mids[cursor] / mids[entry] - 1) * 10_000 * side
        best_bps = max(best_bps, gross)
        if exit_id == "structural_or_time":
            crossed = mids[cursor] < level if side > 0 else mids[cursor] > level
            if crossed:
                exit_index = cursor
                break
        if (
            exit_id == "trailing_or_time"
            and best_bps >= cfg.trailing_activation_bps
            and gross <= best_bps - cfg.trailing_distance_bps
        ):
            exit_index = cursor
            break
    slip = cfg.slippage_bps_per_side / 10_000
    gross = (mids[exit_index] / mids[entry] - 1) * 10_000 * side
    if side > 0:
        entry_price = asks[entry] * (1 + slip)
        exit_price = bids[exit_index] * (1 - slip)
        net = (exit_price / entry_price - 1) * 10_000
    else:
        entry_price = bids[entry] * (1 - slip)
        exit_price = asks[exit_index] * (1 + slip)
        net = (entry_price / exit_price - 1) * 10_000
    holding = (timestamps[exit_index] - timestamps[entry]).total_seconds()
    return _Trade(policy, exit_id, fold, gross, net, holding)


def _metrics(trades: list[_Trade], cfg: SequenceLabConfig) -> list[PolicyMetrics]:
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
        equity = peak = drawdown = 0.0
        for value in nets:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        results.append(
            PolicyMetrics(
                policy,
                exit_id,
                len(group),
                len(folds),
                profitable_folds,
                _mean([trade.gross for trade in group]),
                _mean([trade.gross - trade.net for trade in group]),
                _mean(nets),
                low,
                high,
                sum(value > 0 for value in nets) / len(nets),
                gains / losses if losses else None,
                drawdown,
                _mean([trade.holding_seconds for trade in group]),
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


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
