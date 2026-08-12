from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .experiment_registry import read_registry, register_experiment
from .gold_strategy_tournament import _add_months, _maximum_drawdown, _next_month
from .trend_candidate_audit import _invalid_reason, _meta, _read, _timestamps


@dataclass(frozen=True)
class RobustnessConfig:
    family: str = "trend_continuation"
    horizon_seconds: int = 3600
    warmup_months: int = 3
    sealed_holdout_months: int = 3
    bootstrap_samples: int = 10_000
    bootstrap_block_days: tuple[int, ...] = (3, 5, 10)
    additional_adverse_fill_bps: tuple[float, ...] = (0.5, 1.0, 2.0)
    schema_revision: int = 1


def run_candidate_robustness(
    episode_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    registry_path: Path | None = None,
    config: RobustnessConfig | None = None,
) -> dict[str, object]:
    cfg = config or RobustnessConfig()
    if cfg.family != "trend_continuation" or cfg.horizon_seconds != 3600:
        raise ValueError("Candidate A is frozen as one-hour trend continuation")
    episode_meta, outcome_meta = _meta(episode_manifest), _meta(outcome_manifest)
    feature_manifest = Path(str(outcome_meta["source_multi_hour_manifest"])).resolve()
    feature_meta = _meta(feature_manifest)
    _validate(episode_meta, outcome_meta, feature_meta)
    episodes, outcomes, features = (
        _read(episode_manifest, episode_meta),
        _read(outcome_manifest, outcome_meta),
        _read(feature_manifest, feature_meta),
    )
    outcome_times = _timestamps(outcomes["occurred_at"])
    beginning = min(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ending = _next_month(
        max(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    )
    development_start = _add_months(beginning, cfg.warmup_months)
    holdout_start = _add_months(ending, -cfg.sealed_holdout_months)
    ledger, rejected = _ledger(
        episodes, outcomes, features, development_start, holdout_start, cfg.horizon_seconds
    )
    ledger, overlap_rejections, maximum_concurrent = _remove_executable_overlaps(ledger)
    ledger = sorted(ledger, key=lambda row: row["timestamp"])
    daily = _daily(ledger)
    months = _group(ledger, lambda row: row["timestamp"].strftime("%Y-%m"))
    directions = _group(ledger, lambda row: "long" if row["side"] == 1 else "short")
    halves = {
        "first": ledger[: len(ledger) // 2],
        "second": ledger[len(ledger) // 2 :],
    }
    aggregate = _metrics(ledger)
    concentration = _concentration(daily, months)
    bootstrap = {
        str(block): _block_bootstrap(daily, cfg.bootstrap_samples, block, 20260812 + block)
        for block in cfg.bootstrap_block_days
    }
    adverse = {
        str(value): _mean([float(row["base"]) - value for row in ledger])
        for value in cfg.additional_adverse_fill_bps
    }
    registry = _registry(registry_path)
    specification = {
        "candidate_id": "trend_continuation_1h_v1",
        "config": asdict(cfg),
        "episode_dataset_id": episode_meta["dataset_id"],
        "outcome_dataset_id": outcome_meta["dataset_id"],
        "feature_dataset_id": feature_meta["dataset_id"],
    }
    specification_hash = _hash(specification)
    identity = {**specification, "specification_hash": specification_hash}
    report_id = "candidate-robustness-" + _hash(identity)[:16]
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    ledger_rows = [_serializable(row) for row in ledger]
    ledger_path = root / "trade-ledger.jsonl"
    ledger_bytes = "".join(json.dumps(row, sort_keys=True) + "\n" for row in ledger_rows).encode()
    ledger_path.write_bytes(ledger_bytes)
    ledger_hash = hashlib.sha256(ledger_bytes).hexdigest()
    slices = {
        "months": {key: _metrics(value) for key, value in months.items()},
        "directions": {key: _metrics(value) for key, value in directions.items()},
        "chronological_halves": {key: _metrics(value) for key, value in halves.items()},
    }
    gates = _gates(ledger, daily, months, directions, halves, aggregate, concentration, bootstrap)
    decision = (
        "green"
        if all(gates.values())
        else ("red" if any(not gates[name] for name in _red_gates()) else "yellow")
    )
    report: dict[str, object] = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": "trend_continuation_1h_v1",
        "specification_hash": specification_hash,
        "source_manifest_hashes": {
            "episodes": _file_hash(episode_manifest),
            "outcomes": _file_hash(outcome_manifest),
            "features": _file_hash(feature_manifest),
        },
        "development_start": development_start.isoformat(),
        "development_end_exclusive": holdout_start.isoformat(),
        "final_holdout_start": holdout_start.isoformat(),
        "final_holdout_end_exclusive": ending.isoformat(),
        "holdout_evaluated": False,
        "counts": {
            "trades": len(ledger),
            "distinct_days": len(daily),
            "months": len(months),
            "invalid_reasons": rejected,
            "overlap_rejections": overlap_rejections,
            "maximum_concurrent_positions_before_control": maximum_concurrent,
        },
        "trade_ledger": {"path": str(ledger_path.resolve()), "sha256": ledger_hash},
        "aggregate_metrics": aggregate,
        "slices": slices,
        "concentration": concentration,
        "block_bootstrap_daily_mean_bps": bootstrap,
        "additional_adverse_fill_mean_bps": adverse,
        "autocorrelation": {
            "trade_lags_1_to_10": _autocorrelations([float(row["base"]) for row in ledger]),
            "daily_lags_1_to_10": _autocorrelations([value["base"] for value in daily.values()]),
            "trade_effective_sample_size": _effective_sample_size(
                [float(row["base"]) for row in ledger]
            ),
            "daily_effective_sample_size": _effective_sample_size(
                [value["base"] for value in daily.values()]
            ),
        },
        "experiment_registry": registry,
        "gate_results": gates,
        "decision": decision,
        "news_attribution_included": False,
        "news_attribution_status": "pending_official_point_in_time_calendar",
        "limitations": [
            "outcomes use five-minute executable-side proxies, not raw-tick latency replay",
            "broker commission and rollover swap are not modeled",
            "MFE and MAE remain five-minute proxies",
            "development inference cannot remove prior strategy-selection bias",
            "session slices are diagnostic and may not be promoted as filters",
        ],
        "candidate_frozen": True,
        "research_only": True,
        "real_money_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="candidate-a-robustness-gate",
        dataset_ids=(str(episode_meta["dataset_id"]), str(outcome_meta["dataset_id"])),
        hypothesis_count=1,
        holdout_evaluated=False,
    )
    return report


def _ledger(episodes, outcomes, features, start, end, horizon):
    outcome_times, feature_times = (
        _timestamps(outcomes["occurred_at"]),
        _timestamps(features["occurred_at"]),
    )
    outcome_index = {value: index for index, value in enumerate(outcome_times)}
    feature_index = {value: index for index, value in enumerate(feature_times)}
    gaps, sessions = features["is_gap_start"].to_pylist(), features["session"].to_pylist()
    episode_times = _timestamps(episodes["occurred_at"])
    families, sides = episodes["family"].to_pylist(), episodes["side"].to_pylist()
    prefix = f"h{horizon}"
    names = [
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
    columns = {name: outcomes[name].to_pylist() for name in names}
    rows, rejected = [], {}
    for index, timestamp in enumerate(episode_times):
        if families[index] != "trend_continuation" or not start <= timestamp < end:
            continue
        oindex, findex = outcome_index.get(timestamp), feature_index.get(timestamp)
        reason = _invalid_reason(
            timestamp,
            oindex,
            findex,
            columns[f"{prefix}_valid"],
            feature_times,
            gaps,
            horizon,
        )
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        assert oindex is not None and findex is not None
        direction = "long" if int(sides[index]) == 1 else "short"
        rows.append(
            {
                "timestamp": timestamp,
                "entry_at": feature_times[findex + 1],
                "exit_at": feature_times[findex + 1] + timedelta(seconds=horizon),
                "side": int(sides[index]),
                "session": sessions[findex],
                "gross": float(columns[f"{prefix}_{direction}_gross_bps"][oindex]),
                "base": float(columns[f"{prefix}_{direction}_net_base_bps"][oindex]),
                "stress_1_5": float(columns[f"{prefix}_{direction}_net_cost_1_5x_bps"][oindex]),
                "stress_2": float(columns[f"{prefix}_{direction}_net_cost_2x_bps"][oindex]),
            }
        )
    return rows, rejected


def _metrics(rows):
    if not rows:
        return {
            "trade_count": 0,
            "mean_base_bps": 0.0,
            "mean_1_5x_bps": 0.0,
            "mean_2x_bps": 0.0,
            "profit_factor_base": None,
            "total_base_bps": 0.0,
        }
    base = [float(row["base"]) for row in rows]
    stress_2 = [float(row["stress_2"]) for row in rows]
    return {
        "trade_count": len(rows),
        "mean_base_bps": _mean(base),
        "mean_1_5x_bps": _mean([float(row["stress_1_5"]) for row in rows]),
        "mean_2x_bps": _mean(stress_2),
        "median_base_bps": _median(base),
        "win_rate_base": sum(value > 0 for value in base) / len(base),
        "profit_factor_base": _profit_factor(base),
        "profit_factor_1_5x": _profit_factor([float(row["stress_1_5"]) for row in rows]),
        "profit_factor_2x": _profit_factor(stress_2),
        "total_base_bps": math.fsum(base),
    }


def _daily(rows):
    result: dict[date, dict[str, float]] = {}
    for row in rows:
        day = row["timestamp"].date()
        values = result.setdefault(day, {"base": 0.0, "stress_1_5": 0.0, "stress_2": 0.0})
        for name in values:
            values[name] += float(row[name])
    return dict(sorted(result.items()))


def _group(rows, key_fn):
    result = {}
    for row in rows:
        result.setdefault(key_fn(row), []).append(row)
    return dict(sorted(result.items()))


def _concentration(daily, months):
    positive_days = sorted(
        (value["base"] for value in daily.values() if value["base"] > 0), reverse=True
    )
    positive_total = math.fsum(positive_days)
    month_totals = {
        key: math.fsum(float(row["base"]) for row in rows) for key, rows in months.items()
    }
    best_month = max(month_totals, key=month_totals.get) if month_totals else None
    all_total = math.fsum(value["base"] for value in daily.values())
    best_day = positive_days[0] if positive_days else 0.0
    top_five = math.fsum(positive_days[:5])
    return {
        "best_day_positive_profit_share": best_day / positive_total if positive_total else None,
        "top_five_days_positive_profit_share": top_five / positive_total
        if positive_total
        else None,
        "best_month_positive_profit_share": (
            max(month_totals.values()) / math.fsum(v for v in month_totals.values() if v > 0)
            if month_totals and any(v > 0 for v in month_totals.values())
            else None
        ),
        "leave_best_day_total_base_bps": all_total - best_day,
        "leave_top_five_days_total_base_bps": all_total - top_five,
        "leave_best_month_total_base_bps": all_total - month_totals.get(best_month, 0.0),
        "best_month": best_month,
    }


def _block_bootstrap(daily, samples, block, seed):
    values = [row["base"] for row in daily.values()]
    if not values:
        return {"low_bps": 0.0, "high_bps": 0.0, "probability_mean_le_zero": 1.0}
    rng, means, count = random.Random(seed), [], len(values)
    for _ in range(samples):
        sample = []
        while len(sample) < count:
            start = rng.randrange(count)
            sample.extend(values[(start + offset) % count] for offset in range(block))
        means.append(_mean(sample[:count]))
    means.sort()
    return {
        "low_bps": means[int(samples * 0.025)],
        "high_bps": means[min(int(samples * 0.975), samples - 1)],
        "probability_mean_le_zero": sum(value <= 0 for value in means) / samples,
    }


def _gates(ledger, daily, months, directions, halves, aggregate, concentration, bootstrap):
    positive_months_base = sum(_metrics(rows)["mean_base_bps"] > 0 for rows in months.values())
    positive_months_2x = sum(_metrics(rows)["mean_2x_bps"] > 0 for rows in months.values())
    direction_ok = all(
        len(rows) >= 75
        and _metrics(rows)["mean_1_5x_bps"] > 0
        and (_metrics(rows)["profit_factor_1_5x"] or 0) >= 1.05
        for rows in directions.values()
    ) and {"long", "short"}.issubset(directions)
    daily_values = [row["base"] for row in daily.values()]
    max_dd = _maximum_drawdown(daily_values)
    return {
        "minimum_sample": len(ledger) >= 250 and len(daily) >= 120 and len(months) >= 6,
        "positive_all_cost_levels": aggregate["mean_base_bps"] > 0
        and aggregate["mean_1_5x_bps"] > 0
        and aggregate["mean_2x_bps"] > 0,
        "profit_factor_base_at_least_1_20": (aggregate["profit_factor_base"] or 0) >= 1.20,
        "profit_factor_2x_at_least_1_10": (aggregate["profit_factor_2x"] or 0) >= 1.10,
        "five_day_bootstrap_lower_above_zero": bootstrap["5"]["low_bps"] > 0,
        "month_stability": positive_months_base >= 4 and positive_months_2x >= 3,
        "chronological_halves_positive": all(
            _metrics(rows)["mean_base_bps"] > 0 and _metrics(rows)["mean_1_5x_bps"] > 0
            for rows in halves.values()
        ),
        "direction_balance": direction_ok,
        "leave_out_robustness": concentration["leave_best_day_total_base_bps"] > 0
        and concentration["leave_top_five_days_total_base_bps"] > 0
        and concentration["leave_best_month_total_base_bps"] > 0,
        "concentration_limits": concentration["best_day_positive_profit_share"] <= 0.15
        and concentration["top_five_days_positive_profit_share"] <= 0.40
        and concentration["best_month_positive_profit_share"] <= 0.50,
        "total_to_drawdown_at_least_2": aggregate["total_base_bps"] / max_dd >= 2
        if max_dd
        else True,
        "maximum_time_underwater_60_days": _time_underwater(daily_values) <= 60,
    }


def _red_gates():
    return {
        "positive_all_cost_levels",
        "profit_factor_2x_at_least_1_10",
        "chronological_halves_positive",
        "direction_balance",
        "leave_out_robustness",
    }


def _remove_executable_overlaps(rows):
    ordered = sorted(rows, key=lambda row: row["entry_at"])
    accepted = []
    active_until = None
    rejected = 0
    maximum_concurrent = 0
    exits = []
    for row in ordered:
        entry_at = row["entry_at"]
        exits = [value for value in exits if value > entry_at]
        exits.append(row["exit_at"])
        maximum_concurrent = max(maximum_concurrent, len(exits))
        if active_until is not None and entry_at < active_until:
            rejected += 1
            continue
        accepted.append(row)
        active_until = row["exit_at"]
    return accepted, rejected, maximum_concurrent


def _time_underwater(values):
    peak = equity = 0.0
    current = longest = 0
    for value in values:
        equity += value
        if equity >= peak:
            peak, current = equity, 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _autocorrelations(values):
    return {str(lag): _autocorrelation(values, lag) for lag in range(1, 11)}


def _autocorrelation(values, lag):
    if len(values) <= lag:
        return None
    mean = _mean(values)
    denominator = math.fsum((value - mean) ** 2 for value in values)
    return (
        math.fsum((values[i] - mean) * (values[i - lag] - mean) for i in range(lag, len(values)))
        / denominator
        if denominator
        else 0.0
    )


def _effective_sample_size(values):
    correlations = [
        value
        for value in (_autocorrelation(values, lag) for lag in range(1, min(11, len(values))))
        if value is not None
    ]
    denominator = 1 + 2 * math.fsum(correlations)
    return min(float(len(values)), max(1.0, len(values) / denominator)) if values else 0.0


def _registry(path):
    if path is None or not path.exists():
        return {"path": str(path) if path else None, "record_count": 0, "sha256": None}
    records = read_registry(path)
    return {
        "path": str(path.resolve()),
        "record_count": len(records),
        "sha256": _file_hash(path),
        "total_hypotheses": sum(record.hypothesis_count for record in records),
    }


def _validate(episodes, outcomes, features):
    if episodes.get("point_in_time") is not True or episodes.get("labels_included") is not False:
        raise ValueError("episode artifact is not causal")
    if outcomes.get("future_information") is not True:
        raise ValueError("outcomes are not a separate future-information artifact")
    if outcomes.get("source_multi_hour_dataset_id") != features.get("dataset_id"):
        raise ValueError("feature and outcome lineage does not align")


def _serializable(row):
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def _profit_factor(values):
    gains, losses = (
        math.fsum(v for v in values if v > 0),
        abs(math.fsum(v for v in values if v < 0)),
    )
    return gains / losses if losses else None


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _mean(values):
    return math.fsum(values) / len(values) if values else 0.0


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
