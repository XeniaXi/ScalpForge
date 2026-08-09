from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.episodes import episode_start_mask
from scalpforge_strategy.execution_clock import CausalExecutionConfig, CausalQuoteSeries
from scalpforge_strategy.experiment_registry import register_experiment
from scalpforge_strategy.research_dataset import WalkForwardConfig, anchored_walk_forward_folds
from scalpforge_strategy.structural_lab import stationary_block_interval


@dataclass(frozen=True)
class FeasibilityConfig:
    analysis_revision: int = 3
    level_windows_seconds: tuple[int, ...] = (60, 300, 900, 3600)
    horizons_seconds: tuple[int, ...] = (5, 15, 30, 60, 300)
    slippage_bps_per_side: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0)
    clearance_thresholds_bps: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)
    minimum_breakout_bps: float = 0.25
    maximum_spread_bps: float = 8.0
    maximum_gap_seconds: int = 5
    decision_latency_ms: int = 50
    maximum_entry_delay_seconds: int = 2
    event_interval_seconds: int = 60
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 20
    walk_forward: WalkForwardConfig = WalkForwardConfig(10, 3, 3, 3, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision < 3:
            raise ValueError("feasibility revision must include causal execution")
        if min(self.level_windows_seconds) <= 1 or min(self.horizons_seconds) <= 0:
            raise ValueError("windows and horizons must be positive")
        if min(self.slippage_bps_per_side) < 0 or min(self.clearance_thresholds_bps) <= 0:
            raise ValueError("costs cannot be negative and thresholds must be positive")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades <= 0:
            raise ValueError("bootstrap configuration is too small")


@dataclass(frozen=True)
class FeasibilityMetrics:
    direction_model: str
    level_window_seconds: int
    horizon_seconds: int
    slippage_bps_per_side: float
    event_count: int
    fold_count: int
    profitable_fold_count: int
    mean_gross_bps: float
    mean_net_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    positive_net_rate: float


@dataclass(frozen=True)
class ClearanceMetrics:
    direction_model: str
    level_window_seconds: int
    threshold_bps: float
    event_count: int
    clearance_rate: float
    median_seconds_to_clear: float | None


@dataclass(frozen=True)
class FeasibilityReport:
    report_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    structural_dataset_id: str
    config: dict[str, object]
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    metrics: list[FeasibilityMetrics]
    clearance: list[ClearanceMetrics]
    oracle_is_non_tradable: bool = True
    research_only: bool = True
    real_money_enabled: bool = False


@dataclass(frozen=True)
class _Observation:
    window: int
    horizon: int
    fold: int
    continuation_gross: float
    reversal_gross: float
    continuation_net: dict[float, float]
    reversal_net: dict[float, float]
    oracle_net: dict[float, float]


def run_feasibility_map(
    feature_manifest: Path,
    structural_manifest: Path,
    output_root: Path,
    config: FeasibilityConfig | None = None,
) -> FeasibilityReport:
    cfg = config or FeasibilityConfig()
    feature_meta = _meta(feature_manifest)
    structure_meta = _meta(structural_manifest)
    if structure_meta.get("source_feature_dataset_id") != feature_meta.get("dataset_id"):
        raise ValueError("structural dataset does not belong to feature dataset")
    features = _read_all(feature_manifest, feature_meta)
    structure = _read_all(structural_manifest, structure_meta)
    timestamps = _utc_timestamps(features["occurred_at"])
    if timestamps != _utc_timestamps(structure["occurred_at"]):
        raise ValueError("feature and structural timestamps do not align")
    start = timestamps[0].replace(hour=0, minute=0, second=0, microsecond=0)
    end = timestamps[-1].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    observations, clearance = _observe(features, structure, timestamps, folds, cfg)
    metrics = _summaries(observations, cfg)
    clearance_metrics = _clearance_summaries(clearance, cfg)
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "structure": structure_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "feasibility-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / report_id
    path = root / "report.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metrics"] = [FeasibilityMetrics(**item) for item in payload["metrics"]]
        payload["clearance"] = [ClearanceMetrics(**item) for item in payload["clearance"]]
        report = FeasibilityReport(**payload)
        _register(output_root, report)
        return report
    report = FeasibilityReport(
        report_id,
        1,
        datetime.now(UTC).isoformat(),
        str(feature_meta["dataset_id"]),
        str(structure_meta["dataset_id"]),
        serialized,
        holdout_start.isoformat(),
        end.isoformat(),
        False,
        metrics,
        clearance_metrics,
    )
    root.mkdir(parents=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    _register(output_root, report)
    return report


def _register(output_root: Path, report: FeasibilityReport) -> None:
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report.report_id,
        experiment_family="feasibility-map",
        dataset_ids=(report.feature_dataset_id, report.structural_dataset_id),
        hypothesis_count=max(len(report.metrics) + len(report.clearance), 1),
        holdout_evaluated=report.holdout_evaluated,
    )


def _observe(features, structure, timestamps, folds, cfg):
    quotes = CausalQuoteSeries.from_feature_table(features, cfg.maximum_gap_seconds)
    clock = CausalExecutionConfig(
        decision_latency_ms=cfg.decision_latency_ms,
        maximum_quote_delay_seconds=cfg.maximum_entry_delay_seconds,
        maximum_continuity_gap_seconds=cfg.maximum_gap_seconds,
    )
    entries = quotes.entry_indices(clock)
    exits = {
        horizon: quotes.exit_indices(
            entries,
            horizon,
            CausalExecutionConfig(
                decision_latency_ms=cfg.decision_latency_ms,
                maximum_quote_delay_seconds=cfg.maximum_gap_seconds,
                maximum_continuity_gap_seconds=cfg.maximum_gap_seconds,
            ),
        )
        for horizon in cfg.horizons_seconds
    }
    mids = [float(value) for value in features["mid"].to_pylist()]
    spreads = [float(value) for value in features["spread_bps"].to_pylist()]
    levels = {
        window: (
            structure[f"prior_high_{window}s"].to_pylist(),
            structure[f"prior_low_{window}s"].to_pylist(),
        )
        for window in cfg.level_windows_seconds
    }
    observations: list[_Observation] = []
    clearance: dict[tuple[str, int, float], list[float | None]] = {}
    episode_starts = {
        window: episode_start_mask(
            timestamps,
            [
                (_breakout_side(mid, high, low, cfg.minimum_breakout_bps) or None)
                for mid, high, low in zip(mids, levels[window][0], levels[window][1], strict=True)
            ],
            cfg.event_interval_seconds,
        )
        for window in cfg.level_windows_seconds
    }
    for index, timestamp in enumerate(timestamps):
        fold = _fold(timestamp, folds)
        if fold is None or spreads[index] > cfg.maximum_spread_bps:
            continue
        for window in cfg.level_windows_seconds:
            high = levels[window][0][index]
            low = levels[window][1][index]
            side = _breakout_side(mids[index], high, low, cfg.minimum_breakout_bps)
            if side == 0 or not episode_starts[window][index]:
                continue
            entry = entries[index]
            if entry is None:
                continue
            endpoints = {horizon: exits[horizon][index] for horizon in cfg.horizons_seconds}
            if any(
                endpoint is None or quotes.quote_at[endpoint] >= fold.test_end_exclusive
                for endpoint in endpoints.values()
            ):
                continue
            for horizon, endpoint in endpoints.items():
                assert endpoint is not None
                entry_mid = (quotes.open_bid[entry] + quotes.open_ask[entry]) / 2
                exit_mid = (quotes.open_bid[endpoint] + quotes.open_ask[endpoint]) / 2
                continuation_gross = (exit_mid / entry_mid - 1) * 10_000 * side
                continuation_net = {
                    slip: _net(entry, endpoint, side, quotes.open_bid, quotes.open_ask, slip)
                    for slip in cfg.slippage_bps_per_side
                }
                reversal_net = {
                    slip: _net(entry, endpoint, -side, quotes.open_bid, quotes.open_ask, slip)
                    for slip in cfg.slippage_bps_per_side
                }
                oracle_net = {
                    slip: max(
                        _net(entry, endpoint, 1, quotes.open_bid, quotes.open_ask, slip),
                        _net(entry, endpoint, -1, quotes.open_bid, quotes.open_ask, slip),
                    )
                    for slip in cfg.slippage_bps_per_side
                }
                observations.append(
                    _Observation(
                        window,
                        horizon,
                        fold.fold,
                        continuation_gross,
                        -continuation_gross,
                        continuation_net,
                        reversal_net,
                        oracle_net,
                    )
                )
            final = endpoints[max(cfg.horizons_seconds)]
            assert final is not None
            _record_clearance(clearance, window, entry, final, side, quotes, cfg)
    return observations, clearance


def _record_clearance(clearance, window, entry, final, side, quotes, cfg):
    keys = [
        (model, threshold)
        for model in ("continuation", "reversal")
        for threshold in cfg.clearance_thresholds_bps
    ]
    found = {key: None for key in keys}
    for cursor in range(entry + 1, final + 1):
        elapsed = (quotes.quote_at[cursor] - quotes.quote_at[entry]).total_seconds()
        for model, direction in (("continuation", side), ("reversal", -side)):
            net = _net(entry, cursor, direction, quotes.open_bid, quotes.open_ask, 0.5)
            for threshold in cfg.clearance_thresholds_bps:
                key = (model, threshold)
                if found[key] is None and net >= threshold:
                    found[key] = elapsed
    for (model, threshold), elapsed in found.items():
        clearance.setdefault((model, window, threshold), []).append(elapsed)


def _summaries(observations, cfg):
    results = []
    for window in cfg.level_windows_seconds:
        for horizon in cfg.horizons_seconds:
            group = [o for o in observations if o.window == window and o.horizon == horizon]
            for model in ("continuation", "reversal", "oracle_endpoint"):
                for slip in cfg.slippage_bps_per_side:
                    nets = [
                        o.continuation_net[slip]
                        if model == "continuation"
                        else o.reversal_net[slip]
                        if model == "reversal"
                        else o.oracle_net[slip]
                        for o in group
                    ]
                    gross = [
                        o.continuation_gross
                        if model == "continuation"
                        else o.reversal_gross
                        if model == "reversal"
                        else abs(o.continuation_gross)
                        for o in group
                    ]
                    low, high = stationary_block_interval(
                        nets, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260809
                    )
                    fold_ids = sorted({o.fold for o in group})
                    profitable = sum(
                        _mean(
                            [
                                net
                                for observation, net in zip(group, nets, strict=True)
                                if observation.fold == fold
                            ]
                        )
                        > 0
                        for fold in fold_ids
                    )
                    results.append(
                        FeasibilityMetrics(
                            model,
                            window,
                            horizon,
                            slip,
                            len(group),
                            len(fold_ids),
                            profitable,
                            _mean(gross),
                            _mean(nets),
                            low,
                            high,
                            sum(value > 0 for value in nets) / len(nets) if nets else 0,
                        )
                    )
    return results


def _clearance_summaries(clearance, cfg):
    results = []
    for (model, window, threshold), values in sorted(clearance.items()):
        cleared = sorted(value for value in values if value is not None)
        results.append(
            ClearanceMetrics(
                model,
                window,
                threshold,
                len(values),
                len(cleared) / len(values) if values else 0,
                _median(cleared) if cleared else None,
            )
        )
    return results


def _breakout_side(mid, high, low, threshold):
    if high is not None and (mid / high - 1) * 10_000 >= threshold:
        return 1
    if low is not None and (low / mid - 1) * 10_000 >= threshold:
        return -1
    return 0


def _net(entry, exit_index, side, bids, asks, slippage_bps):
    slip = slippage_bps / 10_000
    if side > 0:
        return (bids[exit_index] * (1 - slip) / (asks[entry] * (1 + slip)) - 1) * 10_000
    return (bids[entry] * (1 - slip) / (asks[exit_index] * (1 + slip)) - 1) * 10_000


def _fold(timestamp, folds):
    for fold in folds:
        if fold.test_start <= timestamp < fold.test_end_exclusive:
            return fold
    return None


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _median(values):
    midpoint = len(values) // 2
    return (
        values[midpoint]
        if len(values) % 2
        else (values[midpoint - 1] + values[midpoint]) / 2
    )


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
