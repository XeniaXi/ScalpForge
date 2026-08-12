from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .experiment_registry import register_experiment
from .structural_lab import stationary_block_interval

ELIGIBLE_FAMILIES = (
    "boundary_rejection",
    "displacement_persistence",
    "trend_continuation",
    "volatility_expansion",
)


@dataclass(frozen=True)
class GoldTournamentConfig:
    horizons_seconds: tuple[int, ...] = (3600, 7200, 14400, 28800)
    primary_horizon_seconds: int = 14400
    warmup_months: int = 3
    sealed_holdout_months: int = 3
    minimum_trades: int = 100
    minimum_distinct_days: int = 100
    minimum_profitable_month_ratio: float = 0.6
    minimum_profit_factor: float = 1.15
    maximum_best_day_share: float = 0.15
    bootstrap_samples: int = 1000
    bootstrap_block_days: int = 5
    schema_revision: int = 1


@dataclass(frozen=True)
class VariantMetrics:
    family: str
    horizon_seconds: int
    primary_horizon: bool
    trade_count: int
    distinct_day_count: int
    month_count: int
    profitable_month_ratio: float
    mean_gross_bps: float
    mean_net_base_bps: float
    mean_net_1_5x_bps: float
    mean_net_2x_bps: float
    daily_bootstrap_low_bps: float
    daily_bootstrap_high_bps: float
    win_rate: float
    profit_factor: float | None
    maximum_drawdown_bps: float
    best_day_positive_profit_share: float
    top_five_day_positive_profit_share: float
    leave_best_day_out_net_bps: float
    long_mean_net_bps: float
    short_mean_net_bps: float
    excluded_invalid_outcome_count: int
    passes_research_gate: bool


@dataclass(frozen=True)
class GoldTournamentReport:
    report_id: str
    schema_version: int
    created_at: str
    episode_dataset_id: str
    outcome_dataset_id: str
    config: dict[str, object]
    development_start: str
    development_end_exclusive: str
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    hypothesis_count: int
    excluded_families: dict[str, str]
    metrics: list[VariantMetrics]
    research_only: bool = True
    real_money_enabled: bool = False


def run_gold_strategy_tournament(
    episode_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: GoldTournamentConfig | None = None,
) -> GoldTournamentReport:
    cfg = config or GoldTournamentConfig()
    episode_meta = _meta(episode_manifest)
    outcome_meta = _meta(outcome_manifest)
    _validate_lineage(episode_manifest, episode_meta, outcome_meta, cfg)
    episodes = _read(episode_manifest, episode_meta)
    outcomes = _read(outcome_manifest, outcome_meta)
    episode_times = _timestamps(episodes["occurred_at"])
    outcome_times = _timestamps(outcomes["occurred_at"])
    if not episode_times or not outcome_times:
        raise ValueError("tournament sources must not be empty")
    start = min(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _next_month(max(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    development_start = _add_months(start, cfg.warmup_months)
    holdout_start = _add_months(end, -cfg.sealed_holdout_months)
    if development_start >= holdout_start:
        raise ValueError("dataset is too short for warmup and sealed holdout")
    outcome_index = {timestamp: index for index, timestamp in enumerate(outcome_times)}
    rows = _episode_rows(episodes, episode_times)
    metrics = [
        _variant_metrics(
            family,
            horizon,
            rows,
            outcomes,
            outcome_index,
            development_start,
            holdout_start,
            cfg,
        )
        for family in ELIGIBLE_FAMILIES
        for horizon in cfg.horizons_seconds
    ]
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {
            "episodes": episode_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    report_id = "gold-strategy-tournament-" + hashlib.sha256(identity).hexdigest()[:16]
    report = GoldTournamentReport(
        report_id,
        1,
        datetime.now(UTC).isoformat(),
        str(episode_meta["dataset_id"]),
        str(outcome_meta["dataset_id"]),
        serialized,
        development_start.isoformat(),
        holdout_start.isoformat(),
        holdout_start.isoformat(),
        end.isoformat(),
        False,
        len(ELIGIBLE_FAMILIES) * len(cfg.horizons_seconds),
        {"fvg_retracement": "only two independent episodes; exploratory only"},
        metrics,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "report.json"
    path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="gold-strategy-tournament",
        dataset_ids=(report.episode_dataset_id, report.outcome_dataset_id),
        hypothesis_count=report.hypothesis_count,
        holdout_evaluated=False,
    )
    return report


def _variant_metrics(
    family: str,
    horizon: int,
    episodes: list[dict[str, object]],
    outcomes: pa.Table,
    outcome_index: dict[datetime, int],
    start: datetime,
    end: datetime,
    cfg: GoldTournamentConfig,
) -> VariantMetrics:
    prefix = f"h{horizon}"
    needed = [
        f"{prefix}_valid",
        f"{prefix}_long_gross_bps",
        f"{prefix}_short_gross_bps",
        f"{prefix}_long_net_base_bps",
        f"{prefix}_short_net_base_bps",
        f"{prefix}_long_net_cost_1_5x_bps",
        f"{prefix}_short_net_cost_1_5x_bps",
        f"{prefix}_long_net_cost_2x_bps",
        f"{prefix}_short_net_cost_2x_bps",
    ]
    if not set(needed).issubset(outcomes.column_names):
        raise ValueError(f"outcomes lack required {horizon}-second columns")
    columns = {name: outcomes[name].to_pylist() for name in needed}
    trades: list[dict[str, object]] = []
    invalid = 0
    for episode in episodes:
        timestamp = episode["occurred_at"]
        if episode["family"] != family or not start <= timestamp < end:  # type: ignore[operator]
            continue
        index = outcome_index.get(timestamp)  # type: ignore[arg-type]
        if index is None or not columns[f"{prefix}_valid"][index]:
            invalid += 1
            continue
        side = int(episode["side"])
        side_name = "long" if side == 1 else "short"
        trades.append(
            {
                "timestamp": timestamp,
                "side": side,
                "gross": float(columns[f"{prefix}_{side_name}_gross_bps"][index]),
                "base": float(columns[f"{prefix}_{side_name}_net_base_bps"][index]),
                "stress_1_5": float(
                    columns[f"{prefix}_{side_name}_net_cost_1_5x_bps"][index]
                ),
                "stress_2": float(columns[f"{prefix}_{side_name}_net_cost_2x_bps"][index]),
            }
        )
    daily = _group_sum(trades, "%Y-%m-%d", "base")
    monthly = _group_sum(trades, "%Y-%m", "base")
    nets = [float(row["base"]) for row in trades]
    gains = math.fsum(value for value in nets if value > 0)
    losses = abs(math.fsum(value for value in nets if value < 0))
    pf = gains / losses if losses else None
    positive_days = sorted((value for value in daily.values() if value > 0), reverse=True)
    positive_sum = math.fsum(positive_days)
    best_share = positive_days[0] / positive_sum if positive_sum else 0.0
    top_five_share = math.fsum(positive_days[:5]) / positive_sum if positive_sum else 0.0
    daily_values = list(daily.values())
    low, high = stationary_block_interval(
        daily_values, cfg.bootstrap_samples, cfg.bootstrap_block_days, 20260812 + horizon
    )
    drawdown = _maximum_drawdown(daily_values)
    leave_best = math.fsum(daily_values) - (max(daily_values) if daily_values else 0.0)
    profitable_month_ratio = (
        sum(value > 0 for value in monthly.values()) / len(monthly) if monthly else 0.0
    )
    long_nets = [float(row["base"]) for row in trades if row["side"] == 1]
    short_nets = [float(row["base"]) for row in trades if row["side"] == -1]
    gate = (
        len(trades) >= cfg.minimum_trades
        and len(daily) >= cfg.minimum_distinct_days
        and _mean(nets) > 0
        and _mean([float(row["stress_1_5"]) for row in trades]) > 0
        and profitable_month_ratio >= cfg.minimum_profitable_month_ratio
        and pf is not None
        and pf >= cfg.minimum_profit_factor
        and best_share <= cfg.maximum_best_day_share
        and leave_best > 0
        and _mean(long_nets) >= 0
        and _mean(short_nets) >= 0
        and low > 0
    )
    return VariantMetrics(
        family,
        horizon,
        horizon == cfg.primary_horizon_seconds,
        len(trades),
        len(daily),
        len(monthly),
        profitable_month_ratio,
        _mean([float(row["gross"]) for row in trades]),
        _mean(nets),
        _mean([float(row["stress_1_5"]) for row in trades]),
        _mean([float(row["stress_2"]) for row in trades]),
        low,
        high,
        sum(value > 0 for value in nets) / len(nets) if nets else 0.0,
        pf,
        drawdown,
        best_share,
        top_five_share,
        leave_best,
        _mean(long_nets),
        _mean(short_nets),
        invalid,
        gate,
    )


def _validate_lineage(
    episode_manifest: Path,
    episodes: dict[str, object],
    outcomes: dict[str, object],
    cfg: GoldTournamentConfig,
) -> None:
    if episodes.get("point_in_time") is not True or episodes.get("labels_included") is not False:
        raise ValueError("episodes must be point-in-time and label-free")
    if outcomes.get("future_information") is not True:
        raise ValueError("outcomes must be a physically separate future-information artifact")
    if (
        outcomes.get("holdout_eligible") is not False
        or episodes.get("holdout_eligible") is not False
    ):
        raise ValueError("tournament only accepts development-only artifacts")
    state_path = Path(str(episodes.get("source_state_manifest", ""))).resolve()
    if not state_path.exists():
        raise ValueError("episode source-state manifest is unavailable")
    state = _meta(state_path)
    if state.get("dataset_id") != episodes.get("source_state_dataset_id"):
        raise ValueError("episode state lineage is inconsistent")
    if state.get("source_multi_hour_dataset_id") != outcomes.get("source_multi_hour_dataset_id"):
        raise ValueError("episodes and outcomes do not derive from the same multi-hour dataset")
    outcome_config = outcomes.get("config", {})
    configured = {
        str(value)
        for value in outcome_config.get("primary_horizons_seconds", [])  # type: ignore[union-attr]
    }
    if not {str(value) for value in cfg.horizons_seconds}.issubset(configured):
        raise ValueError("outcome artifact lacks tournament primary horizons")
    if not episode_manifest.resolve().parent.exists():
        raise ValueError("episode manifest directory is unavailable")


def _episode_rows(table: pa.Table, timestamps: list[datetime]) -> list[dict[str, object]]:
    family = table["family"].to_pylist()
    side = table["side"].to_pylist()
    return [
        {"occurred_at": timestamp, "family": family[index], "side": side[index]}
        for index, timestamp in enumerate(timestamps)
    ]


def _group_sum(rows: list[dict[str, object]], pattern: str, field: str) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in rows:
        key = row["timestamp"].strftime(pattern)  # type: ignore[union-attr]
        output[key] = output.get(key, 0.0) + float(row[field])
    return output


def _maximum_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _mean(values: list[float]) -> float:
    return math.fsum(values) / len(values) if values else 0.0


def _add_months(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + months
    return value.replace(year=absolute // 12, month=absolute % 12 + 1, day=1)


def _next_month(value: datetime) -> datetime:
    return _add_months(value, 1)


def _meta(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(manifest: Path, meta: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    tables = []
    for stored in meta.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("partition escapes its manifest directory")
        tables.append(pq.read_table(path))
    if not tables:
        raise ValueError("manifest has no partitions")
    return pa.concat_tables(tables)


def _timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
