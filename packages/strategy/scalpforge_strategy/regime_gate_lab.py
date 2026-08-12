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

GATES = (
    "all",
    "asia_only",
    "london_only",
    "new_york_only",
    "tight_spread",
    "high_activity",
    "low_volatility",
    "high_volatility",
    "narrow_range",
    "wide_range",
    "trend_aligned_30s",
    "trend_aligned_60s",
    "weekday_tue_thu",
)


@dataclass(frozen=True)
class RegimeGateConfig:
    analysis_revision: int = 1
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 10
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1:
            raise ValueError("unsupported regime-gate analysis revision")
        if self.bootstrap_samples < 40 or self.bootstrap_block_trades < 1:
            raise ValueError("bootstrap configuration is too small")


def run_regime_gate_lab(
    episode_manifest: Path,
    output_root: Path,
    config: RegimeGateConfig | None = None,
) -> dict[str, object]:
    cfg = config or RegimeGateConfig()
    meta, rows = _load_rows(episode_manifest)
    start = min(row["occurred_at"] for row in rows).replace(hour=0, minute=0, second=0)
    end = max(row["occurred_at"] for row in rows).replace(
        hour=0, minute=0, second=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    trades: dict[str, list[dict[str, object]]] = {gate: [] for gate in GATES}
    fold_results: dict[str, list[tuple[int, float]]] = {gate: [] for gate in GATES}
    for fold in folds:
        train = [
            row
            for row in rows
            if fold.train_start <= row["occurred_at"] < fold.train_end_exclusive
        ]
        test = [
            row
            for row in rows
            if fold.test_start <= row["occurred_at"] < fold.test_end_exclusive
        ]
        if not train or not test:
            continue
        thresholds = _thresholds(train)
        for gate in GATES:
            chosen = [row for row in test if _passes(row, gate, thresholds)]
            trades[gate].extend(chosen)
            fold_results[gate].append(
                (len(chosen), _mean([float(row["net_bps"]) for row in chosen]))
            )
    metrics = [_metrics(gate, trades[gate], fold_results[gate], cfg) for gate in GATES]
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {"episodes": meta["dataset_id"], "config": serialized, "gates": GATES},
        sort_keys=True,
    ).encode()
    report_id = "regime-gate-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "episode_dataset_id": meta["dataset_id"],
        "config": serialized,
        "hypothesis_count": len(GATES),
        "gate_ids": list(GATES),
        "threshold_policy": "training_fold_medians_only",
        "final_holdout_start": holdout_start.isoformat(),
        "final_holdout_end_exclusive": end.isoformat(),
        "holdout_evaluated": False,
        "fold_count": len(folds),
        "metrics": metrics,
        "research_only": True,
        "real_money_enabled": False,
    }
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="regime-gate-lab",
        dataset_ids=(str(meta["dataset_id"]),),
        hypothesis_count=len(GATES),
        holdout_evaluated=False,
    )
    return report


def _thresholds(rows):
    return {
        name: _median([float(row[name]) for row in rows if row[name] is not None])
        for name in (
            "spread_bps",
            "tick_intensity_ratio",
            "realized_volatility_60s",
            "range_width_bps",
        )
    }


def _passes(row, gate, thresholds):
    if gate == "all":
        return True
    if gate == "asia_only":
        return row["window"] == "asia"
    if gate == "london_only":
        return row["window"] == "london_open"
    if gate == "new_york_only":
        return row["window"] == "new_york_open"
    if gate == "tight_spread":
        return float(row["spread_bps"]) <= thresholds["spread_bps"]
    if gate == "high_activity":
        return float(row["tick_intensity_ratio"]) >= thresholds["tick_intensity_ratio"]
    if gate == "low_volatility":
        return float(row["realized_volatility_60s"]) <= thresholds["realized_volatility_60s"]
    if gate == "high_volatility":
        return float(row["realized_volatility_60s"]) > thresholds["realized_volatility_60s"]
    if gate == "narrow_range":
        return (
            row["range_width_bps"] is not None
            and float(row["range_width_bps"]) <= thresholds["range_width_bps"]
        )
    if gate == "wide_range":
        return (
            row["range_width_bps"] is not None
            and float(row["range_width_bps"]) > thresholds["range_width_bps"]
        )
    if gate == "trend_aligned_30s":
        return row["return_30s_signed"] is not None and float(row["return_30s_signed"]) > 0
    if gate == "trend_aligned_60s":
        return row["return_60s_signed"] is not None and float(row["return_60s_signed"]) > 0
    if gate == "weekday_tue_thu":
        return row["occurred_at"].weekday() in (1, 2, 3)
    raise ValueError(f"unknown gate: {gate}")


def _metrics(gate, rows, fold_results, cfg):
    gross = [float(row["gross_bps"]) for row in rows]
    net = [float(row["net_bps"]) for row in rows]
    costs = [left - right for left, right in zip(gross, net, strict=True)]
    low, high = stationary_block_interval(
        net, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260812
    )
    active_folds = [value for count, value in fold_results if count > 0]
    profitable_fold_ratio = (
        sum(value > 0 for value in active_folds) / len(active_folds) if active_folds else 0.0
    )
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
    profit_factor = gains / losses if losses else None
    stressed_1_5 = _mean([g - 1.5 * c for g, c in zip(gross, costs, strict=True)])
    stressed_2 = _mean([g - 2 * c for g, c in zip(gross, costs, strict=True)])
    family_low = _family_low(active_folds, len(GATES))
    gate_passed = (
        len(net) >= 100
        and len(active_folds) >= 8
        and len(months) >= 6
        and low > 0
        and family_low > 0
        and _mean(gross) >= 4
        and stressed_1_5 > 0
        and stressed_2 >= 0
        and profitable_fold_ratio >= 0.6
        and sum(value > 0 for value in month_sums) / len(month_sums) >= 0.6
        and profit_factor is not None
        and profit_factor >= 1.15
    )
    return {
        "gate_id": gate,
        "trade_count": len(net),
        "active_fold_count": len(active_folds),
        "month_count": len(months),
        "mean_gross_bps": _mean(gross),
        "mean_net_bps": _mean(net),
        "mean_net_1_5x_cost_bps": stressed_1_5,
        "mean_net_2x_cost_bps": stressed_2,
        "bootstrap_low_bps": low,
        "bootstrap_high_bps": high,
        "family_adjusted_fold_low_bps": family_low,
        "profitable_fold_ratio": profitable_fold_ratio,
        "profitable_month_ratio": (
            sum(value > 0 for value in month_sums) / len(month_sums) if month_sums else 0.0
        ),
        "profit_factor": profit_factor,
        "passes_research_gate": gate_passed,
    }


def _load_rows(manifest):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if not meta.get("point_in_time") or not meta.get("labels_physically_separate"):
        raise ValueError("episode dataset is not leakage-safe")
    root = manifest.resolve().parent
    feature_path = Path(str(meta["feature_partition"])).resolve()
    label_path = Path(str(meta["label_partition"])).resolve()
    if not feature_path.is_relative_to(root) or not label_path.is_relative_to(root):
        raise ValueError("episode partitions escape their dataset")
    features = _table_rows(pq.read_table(feature_path))
    labels = {row["episode_id"]: row for row in _table_rows(pq.read_table(label_path))}
    rows = []
    for row in features:
        label = labels.get(row["episode_id"])
        if label is None or label["occurred_at"] != row["occurred_at"]:
            raise ValueError("episode feature and label joins do not align")
        rows.append({**row, "gross_bps": label["gross_bps"], "net_bps": label["net_bps"]})
    return meta, rows


def _table_rows(table):
    timestamps = _utc_timestamps(table["occurred_at"])
    columns = [name for name in table.column_names if name != "occurred_at"]
    values = {name: table[name].to_pylist() for name in columns}
    return [
        {"occurred_at": timestamp, **{name: values[name][index] for name in columns}}
        for index, timestamp in enumerate(timestamps)
    ]


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _family_low(values, family_size):
    if len(values) < 2:
        return -math.inf
    mean = _mean(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean - math.sqrt(variance / len(values)) * math.sqrt(2 * math.log(family_size))


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0
