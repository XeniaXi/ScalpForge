from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .experiment_registry import register_experiment
from .gold_strategy_tournament import _add_months, _maximum_drawdown, _next_month


@dataclass(frozen=True)
class TrendCandidateAuditConfig:
    family: str = "trend_continuation"
    horizon_seconds: int = 3600
    warmup_months: int = 3
    sealed_holdout_months: int = 3
    required_distinct_days: int = 100
    schema_revision: int = 1


@dataclass(frozen=True)
class TrendCandidateAuditReport:
    report_id: str
    schema_version: int
    created_at: str
    episode_dataset_id: str
    outcome_dataset_id: str
    feature_dataset_id: str
    candidate_id: str
    development_start: str
    development_end_exclusive: str
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    eligible_episode_count: int
    analyzed_trade_count: int
    distinct_day_count: int
    additional_distinct_days_required: int
    invalid_reason_counts: dict[str, int]
    overlap_rejection_count: int
    maximum_concurrent_positions_before_control: int
    monthly_metrics: list[dict[str, object]]
    session_metrics: list[dict[str, object]]
    direction_metrics: list[dict[str, object]]
    causal_feature_comparison: dict[str, dict[str, float | int | None]]
    aggregate_metrics: dict[str, float | int | None]
    candidate_frozen: bool = True
    research_only: bool = True
    real_money_enabled: bool = False


def run_trend_candidate_audit(
    episode_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: TrendCandidateAuditConfig | None = None,
) -> TrendCandidateAuditReport:
    cfg = config or TrendCandidateAuditConfig()
    episode_meta, outcome_meta = _meta(episode_manifest), _meta(outcome_manifest)
    feature_manifest = Path(str(outcome_meta["source_multi_hour_manifest"])).resolve()
    feature_meta = _meta(feature_manifest)
    _validate(episode_meta, outcome_meta, feature_meta, cfg)
    episodes = _read(episode_manifest, episode_meta)
    outcomes = _read(outcome_manifest, outcome_meta)
    features = _read(feature_manifest, feature_meta)
    outcome_times = _timestamps(outcomes["occurred_at"])
    feature_times = _timestamps(features["occurred_at"])
    start = min(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _next_month(max(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    development_start = _add_months(start, cfg.warmup_months)
    holdout_start = _add_months(end, -cfg.sealed_holdout_months)
    development_outcome_indices = [
        index
        for index, value in enumerate(outcome_times)
        if development_start <= value < holdout_start
    ]
    development_feature_indices = [
        index
        for index, value in enumerate(feature_times)
        if development_start <= value < holdout_start
    ]
    outcomes = outcomes.take(pa.array(development_outcome_indices))
    features = features.take(pa.array(development_feature_indices))
    outcome_times = [outcome_times[index] for index in development_outcome_indices]
    feature_times = [feature_times[index] for index in development_feature_indices]
    outcome_index = {value: index for index, value in enumerate(outcome_times)}
    feature_index = {value: index for index, value in enumerate(feature_times)}
    feature_values = {
        name: features[name].to_pylist()
        for name in ("session", "spread_bps", "is_gap_start")
    }
    episode_times = _timestamps(episodes["occurred_at"])
    families, sides = episodes["family"].to_pylist(), episodes["side"].to_pylist()
    signals = [
        {"timestamp": timestamp, "side": int(sides[index])}
        for index, timestamp in enumerate(episode_times)
        if families[index] == cfg.family and development_start <= timestamp < holdout_start
    ]
    prefix = f"h{cfg.horizon_seconds}"
    outcome_columns = {
        name: outcomes[name].to_pylist()
        for name in (
            f"{prefix}_valid",
            f"{prefix}_long_gross_bps",
            f"{prefix}_short_gross_bps",
            f"{prefix}_long_net_base_bps",
            f"{prefix}_short_net_base_bps",
            f"{prefix}_long_net_cost_1_5x_bps",
            f"{prefix}_short_net_cost_1_5x_bps",
            f"{prefix}_long_net_cost_2x_bps",
            f"{prefix}_short_net_cost_2x_bps",
        )
    }
    invalid: dict[str, int] = {}
    candidate_rows: list[dict[str, object]] = []
    for signal in signals:
        timestamp = signal["timestamp"]
        oindex, findex = outcome_index.get(timestamp), feature_index.get(timestamp)
        reason = _invalid_reason(
            timestamp, oindex, findex, outcome_columns[f"{prefix}_valid"], feature_times,
            feature_values["is_gap_start"], cfg.horizon_seconds
        )
        causal = {
            "spread_bps": (
                float(feature_values["spread_bps"][findex]) if findex is not None else None
            ),
            "session": feature_values["session"][findex] if findex is not None else "missing",
        }
        if reason:
            invalid[reason] = invalid.get(reason, 0) + 1
            candidate_rows.append({**signal, **causal, "valid": False})
            continue
        assert oindex is not None
        direction = "long" if signal["side"] == 1 else "short"
        candidate_rows.append(
            {
                **signal,
                **causal,
                "valid": True,
                "gross": float(outcome_columns[f"{prefix}_{direction}_gross_bps"][oindex]),
                "base": float(outcome_columns[f"{prefix}_{direction}_net_base_bps"][oindex]),
                "stress_1_5": float(
                    outcome_columns[f"{prefix}_{direction}_net_cost_1_5x_bps"][oindex]
                ),
                "stress_2": float(
                    outcome_columns[f"{prefix}_{direction}_net_cost_2x_bps"][oindex]
                ),
            }
        )
    valid = [row for row in candidate_rows if row["valid"]]
    controlled, overlap_count, maximum_concurrent = _remove_overlaps(valid, cfg.horizon_seconds)
    distinct_days = {row["timestamp"].date() for row in controlled}  # type: ignore[union-attr]
    aggregate = _metrics(controlled)
    report_payload = {
        "episodes": episode_meta["dataset_id"],
        "outcomes": outcome_meta["dataset_id"],
        "config": asdict(cfg),
    }
    report_id = "trend-candidate-audit-" + hashlib.sha256(
        json.dumps(report_payload, sort_keys=True).encode()
    ).hexdigest()[:16]
    report = TrendCandidateAuditReport(
        report_id,
        1,
        datetime.now(UTC).isoformat(),
        str(episode_meta["dataset_id"]),
        str(outcome_meta["dataset_id"]),
        str(feature_meta["dataset_id"]),
        "trend_continuation_1h_v1",
        development_start.isoformat(),
        holdout_start.isoformat(),
        holdout_start.isoformat(),
        end.isoformat(),
        False,
        len(signals),
        len(controlled),
        len(distinct_days),
        max(cfg.required_distinct_days - len(distinct_days), 0),
        invalid,
        overlap_count,
        maximum_concurrent,
        _breakdown(controlled, lambda row: row["timestamp"].strftime("%Y-%m")),  # type: ignore[union-attr]
        _breakdown(controlled, lambda row: str(row["session"])),
        _breakdown(controlled, lambda row: "long" if row["side"] == 1 else "short"),
        {
            "valid": _causal_summary(valid),
            "invalid": _causal_summary([row for row in candidate_rows if not row["valid"]]),
        },
        aggregate,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="trend-candidate-forensics",
        dataset_ids=(report.episode_dataset_id, report.outcome_dataset_id),
        hypothesis_count=1,
        holdout_evaluated=False,
    )
    return report


def _invalid_reason(timestamp, oindex, findex, valid, feature_times, gaps, horizon):
    if oindex is None or findex is None:
        return "timestamp_not_aligned"
    if valid[oindex]:
        return None
    entry, steps = findex + 1, horizon // 300
    exit_index = entry + steps
    if entry >= len(feature_times):
        return "entry_quote_unavailable"
    if exit_index >= len(feature_times):
        return "exit_beyond_dataset"
    for index in range(entry, exit_index + 1):
        if gaps[index]:
            return "flagged_gap"
        if (feature_times[index] - feature_times[index - 1]).total_seconds() != 300:
            return "market_closure_or_missing_bar"
    return "outcome_invalid_other"


def _remove_overlaps(rows, horizon):
    accepted, rejected = [], 0
    active_until = None
    maximum_concurrent = 0
    active_ends: list[datetime] = []
    for row in sorted(rows, key=lambda item: item["timestamp"]):
        timestamp = row["timestamp"]
        active_ends = [value for value in active_ends if value > timestamp]
        active_ends.append(timestamp + timedelta(seconds=horizon))
        maximum_concurrent = max(maximum_concurrent, len(active_ends))
        if active_until is not None and timestamp < active_until:
            rejected += 1
            continue
        accepted.append(row)
        active_until = timestamp + timedelta(seconds=horizon)
    return accepted, rejected, maximum_concurrent


def _metrics(rows):
    nets = [float(row["base"]) for row in rows]
    daily: dict[str, float] = {}
    for row in rows:
        key = row["timestamp"].strftime("%Y-%m-%d")
        daily[key] = daily.get(key, 0.0) + float(row["base"])
    gains = math.fsum(value for value in nets if value > 0)
    losses = abs(math.fsum(value for value in nets if value < 0))
    return {
        "mean_gross_bps": _mean([float(row["gross"]) for row in rows]),
        "mean_net_base_bps": _mean(nets),
        "mean_net_1_5x_bps": _mean([float(row["stress_1_5"]) for row in rows]),
        "mean_net_2x_bps": _mean([float(row["stress_2"]) for row in rows]),
        "win_rate": sum(value > 0 for value in nets) / len(nets) if nets else 0.0,
        "profit_factor": gains / losses if losses else None,
        "maximum_drawdown_bps": _maximum_drawdown(list(daily.values())),
        "total_net_bps": math.fsum(nets),
    }


def _breakdown(rows, key_fn):
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)
    return [
        {"value": key, "trade_count": len(values), **_metrics(values)}
        for key, values in sorted(grouped.items())
    ]


def _causal_summary(rows):
    spreads = [float(row["spread_bps"]) for row in rows if row["spread_bps"] is not None]
    return {
        "count": len(rows),
        "mean_signal_spread_bps": _mean(spreads) if spreads else None,
        "long_ratio": sum(row["side"] == 1 for row in rows) / len(rows) if rows else None,
    }


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _validate(episodes, outcomes, features, cfg):
    if episodes.get("point_in_time") is not True or episodes.get("labels_included") is not False:
        raise ValueError("episode artifact is not causal")
    if outcomes.get("future_information") is not True:
        raise ValueError("outcome artifact is not a separate label source")
    if outcomes.get("source_multi_hour_dataset_id") != features.get("dataset_id"):
        raise ValueError("feature and outcome lineage does not align")
    if cfg.family != "trend_continuation" or cfg.horizon_seconds != 3600:
        raise ValueError("Candidate A is frozen as trend continuation with a one-hour exit")


def _meta(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read(manifest, meta):
    root = Path(manifest).resolve().parent
    tables = []
    for stored in meta.get("partitions", []):
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("partition escapes its manifest directory")
        tables.append(pq.read_table(path))
    return pa.concat_tables(tables)


def _timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[column.type.unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
