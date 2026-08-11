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

NUMERIC_FEATURES = (
    "side",
    "spread_bps",
    "spread_shock_ratio",
    "tick_intensity_ratio",
    "realized_volatility_60s",
    "return_5s_signed",
    "return_30s_signed",
    "return_60s_signed",
    "range_width_bps",
    "breakout_distance_bps",
    "distance_from_tick_vwap_signed_bps",
    "compression_60_to_300",
)
WINDOWS = ("asia", "london_open", "new_york_open")


@dataclass(frozen=True)
class AbstentionLabConfig:
    analysis_revision: int = 1
    ridge_penalty: float = 10.0
    utility_thresholds_bps: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0)
    minimum_validation_trades: int = 10
    minimum_test_trades: int = 5
    final_holdout_days: int = 4
    bootstrap_samples: int = 1000
    bootstrap_block_trades: int = 10
    walk_forward: WalkForwardConfig = WalkForwardConfig(90, 21, 21, 21, 300, 300)

    def __post_init__(self) -> None:
        if self.analysis_revision != 1 or self.ridge_penalty <= 0:
            raise ValueError("invalid abstention model configuration")
        if not self.utility_thresholds_bps or min(self.utility_thresholds_bps) < 0:
            raise ValueError("utility thresholds must be non-negative")
        if self.minimum_validation_trades < 1 or self.minimum_test_trades < 1:
            raise ValueError("minimum trade counts must be positive")


@dataclass(frozen=True)
class SelectedFold:
    fold: int
    threshold_bps: float | None
    train_rows: int
    validation_rows: int
    validation_trades: int
    validation_mean_net_bps: float
    test_rows: int
    test_trades: int
    test_mean_gross_bps: float
    test_mean_net_bps: float


@dataclass(frozen=True)
class AbstentionLabReport:
    report_id: str
    schema_version: int
    created_at: str
    episode_dataset_id: str
    config: dict[str, object]
    feature_names: list[str]
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    fold_count: int
    selected_folds: list[SelectedFold]
    aggregate_test_trades: int
    aggregate_abstention_rate: float
    aggregate_mean_gross_bps: float
    aggregate_mean_net_bps: float
    aggregate_mean_net_1_5x_cost_bps: float
    aggregate_mean_net_2x_cost_bps: float
    bootstrap_low_bps: float
    bootstrap_high_bps: float
    profitable_fold_ratio: float
    profit_factor: float | None
    research_gate_passed: bool
    research_only: bool = True
    real_money_enabled: bool = False


def run_abstention_lab(
    episode_manifest: Path,
    output_root: Path,
    config: AbstentionLabConfig | None = None,
) -> AbstentionLabReport:
    cfg = config or AbstentionLabConfig()
    meta, rows = _load_rows(episode_manifest)
    start = min(row["occurred_at"] for row in rows).replace(hour=0, minute=0, second=0)
    end = max(row["occurred_at"] for row in rows).replace(
        hour=0, minute=0, second=0
    ) + timedelta(days=1)
    holdout_start = end - timedelta(days=cfg.final_holdout_days)
    folds = anchored_walk_forward_folds(start, holdout_start, cfg.walk_forward)
    selected: list[SelectedFold] = []
    test_results: list[tuple[float, float]] = []
    total_test_rows = 0
    for fold in folds:
        train = [
            row
            for row in rows
            if fold.train_start <= row["occurred_at"] < fold.train_end_exclusive
        ]
        validation = [
            row
            for row in rows
            if fold.validation_start <= row["occurred_at"] < fold.validation_end_exclusive
        ]
        test = [
            row
            for row in rows
            if fold.test_start <= row["occurred_at"] < fold.test_end_exclusive
        ]
        if len(train) < 50 or not validation or not test:
            continue
        model = _fit(train, cfg.ridge_penalty)
        threshold, validation_trades, validation_mean = _select_threshold(
            model, validation, cfg
        )
        chosen = [] if threshold is None else _select(model, test, threshold)
        total_test_rows += len(test)
        if len(chosen) < cfg.minimum_test_trades:
            chosen = []
        test_results.extend((row["gross_bps"], row["net_bps"]) for row in chosen)
        selected.append(
            SelectedFold(
                fold.fold,
                threshold,
                len(train),
                len(validation),
                len(validation_trades),
                validation_mean,
                len(test),
                len(chosen),
                _mean([row["gross_bps"] for row in chosen]),
                _mean([row["net_bps"] for row in chosen]),
            )
        )
    gross = [item[0] for item in test_results]
    nets = [item[1] for item in test_results]
    costs = [left - right for left, right in test_results]
    stressed_1_5 = _mean([value - 1.5 * cost for value, cost in zip(gross, costs, strict=True)])
    stressed_2 = _mean([value - 2.0 * cost for value, cost in zip(gross, costs, strict=True)])
    low, high = stationary_block_interval(
        nets, cfg.bootstrap_samples, cfg.bootstrap_block_trades, 20260811
    )
    gains = math.fsum(value for value in nets if value > 0)
    losses = -math.fsum(value for value in nets if value < 0)
    profitable_folds = sum(
        item.test_trades >= cfg.minimum_test_trades and item.test_mean_net_bps > 0
        for item in selected
    )
    active_folds = sum(item.test_trades >= cfg.minimum_test_trades for item in selected)
    profitable_ratio = profitable_folds / active_folds if active_folds else 0.0
    profit_factor = gains / losses if losses else None
    gate = (
        len(nets) >= 100
        and active_folds >= 6
        and low > 0
        and _mean(gross) >= 4.0
        and stressed_1_5 > 0
        and stressed_2 >= 0
        and profitable_ratio >= 0.6
        and profit_factor is not None
        and profit_factor >= 1.15
    )
    serialized = json.loads(json.dumps(asdict(cfg), default=str))
    identity = json.dumps(
        {"episodes": meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    report_id = "abstention-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    report = AbstentionLabReport(
        report_id,
        1,
        datetime.now(UTC).isoformat(),
        str(meta["dataset_id"]),
        serialized,
        [*NUMERIC_FEATURES, *[f"window_{value}" for value in WINDOWS]],
        holdout_start.isoformat(),
        end.isoformat(),
        False,
        len(folds),
        selected,
        len(nets),
        1 - len(nets) / total_test_rows if total_test_rows else 1.0,
        _mean(gross),
        _mean(nets),
        stressed_1_5,
        stressed_2,
        low,
        high,
        profitable_ratio,
        profit_factor,
        gate,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="abstention-lab",
        dataset_ids=(report.episode_dataset_id,),
        hypothesis_count=len(cfg.utility_thresholds_bps),
        holdout_evaluated=False,
    )
    return report


def _load_rows(manifest):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if not meta.get("point_in_time") or not meta.get("labels_physically_separate"):
        raise ValueError("episode dataset is not leakage-safe")
    root = manifest.resolve().parent
    feature_path = Path(meta["feature_partition"]).resolve()
    label_path = Path(meta["label_partition"]).resolve()
    if not feature_path.is_relative_to(root) or not label_path.is_relative_to(root):
        raise ValueError("episode partitions escape their dataset")
    features = pq.read_table(feature_path)
    labels = pq.read_table(label_path)
    feature_rows = _table_rows(features)
    label_by_id = {row["episode_id"]: row for row in _table_rows(labels)}
    rows = []
    for row in feature_rows:
        label = label_by_id.get(row["episode_id"])
        if label is None or label["occurred_at"] != row["occurred_at"]:
            raise ValueError("episode feature and label joins do not align")
        rows.append({**row, **{key: label[key] for key in ("gross_bps", "net_bps")}})
    if len(rows) != len(label_by_id):
        raise ValueError("episode labels are duplicated or unmatched")
    return meta, rows


def _fit(rows, penalty):
    medians = [_median([_value(row, name) for row in rows]) for name in NUMERIC_FEATURES]
    raw = [_vector(row, medians) for row in rows]
    means = [_mean([vector[index] for vector in raw]) for index in range(len(raw[0]))]
    scales = [
        math.sqrt(_mean([(vector[index] - means[index]) ** 2 for vector in raw])) or 1.0
        for index in range(len(raw[0]))
    ]
    matrix = [
        [1.0, *[(value - means[i]) / scales[i] for i, value in enumerate(vector)]]
        for vector in raw
    ]
    targets = [row["net_bps"] for row in rows]
    size = len(matrix[0])
    normal = [
        [math.fsum(row[i] * row[j] for row in matrix) for j in range(size)]
        for i in range(size)
    ]
    for index in range(1, size):
        normal[index][index] += penalty
    rhs = [
        math.fsum(
            row[i] * target for row, target in zip(matrix, targets, strict=True)
        )
        for i in range(size)
    ]
    return (_solve(normal, rhs), medians, means, scales)


def _predict(model, row):
    coefficients, medians, means, scales = model
    raw = _vector(row, medians)
    vector = [1.0, *[(value - means[i]) / scales[i] for i, value in enumerate(raw)]]
    return math.fsum(left * right for left, right in zip(coefficients, vector, strict=True))


def _select_threshold(model, rows, cfg):
    best = None
    best_rows = []
    best_mean = -math.inf
    for threshold in cfg.utility_thresholds_bps:
        chosen = _select(model, rows, threshold)
        if len(chosen) < cfg.minimum_validation_trades:
            continue
        mean = _mean([row["net_bps"] for row in chosen])
        if mean > best_mean:
            best, best_rows, best_mean = threshold, chosen, mean
    if best is None or best_mean <= 0:
        return None, [], 0.0
    return best, best_rows, best_mean


def _select(model, rows, threshold):
    return [row for row in rows if _predict(model, row) >= threshold]


def _vector(row, medians):
    numeric = [
        _value(row, name) if _value(row, name) is not None else medians[index]
        for index, name in enumerate(NUMERIC_FEATURES)
    ]
    return [*numeric, *[float(row["window"] == value) for value in WINDOWS]]


def _value(row, name):
    value = row.get(name)
    return float(value) if value is not None else None


def _solve(matrix, vector):
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    size = len(augmented)
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("ridge system is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                left - factor * right
                for left, right in zip(augmented[row], augmented[column], strict=True)
            ]
    return [row[-1] for row in augmented]


def _median(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


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
        for value in column.cast("int64").to_pylist()
    ]
