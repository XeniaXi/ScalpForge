from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .experiment_registry import register_experiment
from .gold_strategy_tournament import _add_months, _next_month
from .trend_candidate_audit import _meta, _read, _timestamps


@dataclass(frozen=True)
class GapSemanticsConfig:
    family: str = "trend_continuation"
    horizon_seconds: int = 3600
    warmup_months: int = 3
    sealed_holdout_months: int = 3
    short_gap_max_seconds: int = 30
    medium_gap_max_seconds: int = 300
    schema_revision: int = 1


@dataclass(frozen=True)
class GapSemanticsReport:
    report_id: str
    schema_version: int
    created_at: str
    episode_dataset_id: str
    outcome_dataset_id: str
    multi_hour_dataset_id: str
    source_feature_dataset_id: str
    candidate_id: str
    development_start: str
    development_end_exclusive: str
    final_holdout_start: str
    final_holdout_end_exclusive: str
    holdout_evaluated: bool
    rejected_candidate_count: int
    classification_counts: dict[str, int]
    gap_duration_bucket_counts: dict[str, int]
    interruption_metrics: dict[str, float | int | None]
    recommendation: str
    automatic_rule_change_applied: bool = False
    candidate_frozen: bool = True
    research_only: bool = True
    real_money_enabled: bool = False


def run_gap_semantics_audit(
    episode_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: GapSemanticsConfig | None = None,
) -> GapSemanticsReport:
    cfg = config or GapSemanticsConfig()
    episode_meta, outcome_meta = _meta(episode_manifest), _meta(outcome_manifest)
    multi_manifest = Path(str(outcome_meta["source_multi_hour_manifest"])).resolve()
    multi_meta = _meta(multi_manifest)
    source_manifest = Path(str(multi_meta["source_feature_manifest"])).resolve()
    source_meta = _meta(source_manifest)
    _validate(episode_meta, outcome_meta, multi_meta, source_meta, cfg)

    episodes = _read(episode_manifest, episode_meta)
    outcomes = _read(outcome_manifest, outcome_meta)
    multi = _read(multi_manifest, multi_meta)
    outcome_times = _timestamps(outcomes["occurred_at"])
    start = min(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _next_month(max(outcome_times).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    development_start = _add_months(start, cfg.warmup_months)
    holdout_start = _add_months(end, -cfg.sealed_holdout_months)

    multi_times = _timestamps(multi["occurred_at"])
    multi_gaps = multi["is_gap_start"].to_pylist()
    multi_index = {value: index for index, value in enumerate(multi_times)}
    outcome_valid = outcomes[f"h{cfg.horizon_seconds}_valid"].to_pylist()
    outcome_index = {value: index for index, value in enumerate(outcome_times)}
    episode_times = _timestamps(episodes["occurred_at"])
    families = episodes["family"].to_pylist()
    rejected: list[datetime] = []
    for index, timestamp in enumerate(episode_times):
        if not (development_start <= timestamp < holdout_start):
            continue
        if families[index] != cfg.family:
            continue
        oindex = outcome_index.get(timestamp)
        if oindex is not None and not outcome_valid[oindex]:
            rejected.append(timestamp)

    raw = _read_development_source(source_manifest, source_meta, development_start, holdout_start)
    raw_times = _timestamps(raw["occurred_at"])
    raw_seconds = raw["seconds_since_previous_active_bar"].to_pylist()
    bids, asks = raw["bid"].to_pylist(), raw["ask"].to_pylist()

    classifications: dict[str, int] = {}
    buckets = {"gt_5_to_30s": 0, "gt_30_to_300s": 0, "gt_300s": 0}
    observed_durations: list[float] = []
    executable_boundaries = 0
    inspected_interruptions = 0
    for timestamp in rejected:
        mindex = multi_index.get(timestamp)
        if mindex is None:
            classification = "missing_alignment"
        else:
            first, last = mindex + 1, mindex + 1 + cfg.horizon_seconds // 300
            actual_discontinuity = any(
                (multi_times[i] - multi_times[i - 1]).total_seconds() != 300
                for i in range(first, min(last + 1, len(multi_times)))
            )
            path_start = timestamp
            path_end = timestamp + timedelta(seconds=cfg.horizon_seconds + 300)
            raw_first = bisect_right(raw_times, path_start)
            raw_last = bisect_right(raw_times, path_end)
            gaps = [
                (i, float(raw_seconds[i]))
                for i in range(raw_first, raw_last)
                if raw_seconds[i] is not None and float(raw_seconds[i]) > 5
            ]
            for i, duration in gaps:
                inspected_interruptions += 1
                observed_durations.append(duration)
                if duration <= cfg.short_gap_max_seconds:
                    buckets["gt_5_to_30s"] += 1
                elif duration <= cfg.medium_gap_max_seconds:
                    buckets["gt_30_to_300s"] += 1
                else:
                    buckets["gt_300s"] += 1
                if i > 0 and bids[i - 1] is not None and asks[i - 1] is not None \
                        and bids[i] is not None and asks[i] is not None \
                        and float(asks[i - 1]) >= float(bids[i - 1]) \
                        and float(asks[i]) >= float(bids[i]):
                    executable_boundaries += 1
            maximum = max((duration for _, duration in gaps), default=0.0)
            inherited = any(multi_gaps[i] for i in range(first, min(last + 1, len(multi_gaps))))
            classification = _classify(actual_discontinuity, inherited, maximum)
        classifications[classification] = classifications.get(classification, 0) + 1

    short_only = classifications.get("short_intraday_interruption_only", 0)
    recommendation = (
        "research_revised_continuity_rule_on_new_development_data"
        if rejected and short_only / len(rejected) >= 0.5
        else "retain_current_continuity_rule_pending_more_evidence"
    )
    payload = {
        "episodes": episode_meta["dataset_id"], "outcomes": outcome_meta["dataset_id"],
        "config": asdict(cfg),
    }
    report_id = "gap-semantics-audit-" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]
    report = GapSemanticsReport(
        report_id, 1, datetime.now(UTC).isoformat(), str(episode_meta["dataset_id"]),
        str(outcome_meta["dataset_id"]), str(multi_meta["dataset_id"]),
        str(source_meta["dataset_id"]), "trend_continuation_1h_v1",
        development_start.isoformat(), holdout_start.isoformat(), holdout_start.isoformat(),
        end.isoformat(), False, len(rejected), classifications, buckets,
        {
            "interruption_count": inspected_interruptions,
            "maximum_duration_seconds": max(observed_durations, default=None),
            "median_duration_seconds": _median(observed_durations),
            "executable_quote_boundary_ratio": (
                executable_boundaries / inspected_interruptions if inspected_interruptions else None
            ),
        }, recommendation,
    )
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl", report_id=report_id,
        experiment_family="gap-semantics-forensics",
        dataset_ids=(report.episode_dataset_id, report.outcome_dataset_id),
        hypothesis_count=1, holdout_evaluated=False,
    )
    return report


def _classify(discontinuity: bool, inherited: bool, maximum: float) -> str:
    if discontinuity:
        return "five_minute_bar_discontinuity"
    if not inherited:
        return "outcome_invalid_without_continuity_evidence"
    if maximum <= 30:
        return "short_intraday_interruption_only"
    if maximum <= 300:
        return "medium_intraday_interruption"
    return "long_gap_or_closure"


def _read_development_source(manifest, meta, start, end):
    root = Path(manifest).resolve().parent
    tables = []
    for stored in meta.get("partitions", []):
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("partition escapes its manifest directory")
        tables.append(pq.read_table(
            path,
            columns=["occurred_at", "seconds_since_previous_active_bar", "bid", "ask"],
            filters=[("occurred_at", ">=", start), ("occurred_at", "<", end)],
        ))
    return pa.concat_tables(tables)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _validate(episodes, outcomes, multi, source, cfg):
    if episodes.get("point_in_time") is not True or episodes.get("labels_included") is not False:
        raise ValueError("episode artifact is not causal")
    if outcomes.get("future_information") is not True:
        raise ValueError("outcome artifact is not a separate label source")
    if outcomes.get("source_multi_hour_dataset_id") != multi.get("dataset_id"):
        raise ValueError("multi-hour outcome lineage does not align")
    if multi.get("source_feature_dataset_id") != source.get("dataset_id"):
        raise ValueError("one-second feature lineage does not align")
    if cfg.family != "trend_continuation" or cfg.horizon_seconds != 3600:
        raise ValueError("Candidate A is frozen as trend continuation with a one-hour exit")
