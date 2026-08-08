from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from scalpforge_broker.report import BrokerFeedReport, _percentile, read_recorder_file
from scalpforge_data.quality import Severity, TickQualityValidator

from .snapshots import valid_snapshot_groups


@dataclass(frozen=True)
class TickQualityReport:
    generated_at: str
    source_root: str
    day_count: int
    active_day_count: int
    total_ticks: int
    total_observed_hours: float
    maximum_gap_seconds: float
    total_gap_count: int
    total_duplicate_count: int
    invalid_snapshot_count: int
    status: str
    days: list[dict[str, object]]


def build_tick_quality_report(root: Path) -> TickQualityReport:
    groups, invalid = valid_snapshot_groups(root)
    reports = [_build_group_report(name, paths) for name, paths in groups.items()]
    active = [report for report in reports if report.tick_count > 0]
    errors = sum(report.error_count for report in reports)
    observed_hours = round(sum(report.observed_hours for report in active), 4)
    if observed_hours >= 40 and len(active) >= 2:
        status = "ready_for_48h_review"
    elif observed_hours >= 20:
        status = "ready_for_24h_checkpoint"
    else:
        status = "collecting_under_24h"
    if invalid or errors:
        status = "quality_errors"
    return TickQualityReport(
        generated_at=datetime.now(UTC).isoformat(),
        source_root=str(root),
        day_count=len(reports),
        active_day_count=len(active),
        total_ticks=sum(report.tick_count for report in reports),
        total_observed_hours=observed_hours,
        maximum_gap_seconds=max((report.maximum_gap_seconds for report in active), default=0.0),
        total_gap_count=sum(report.gap_count for report in active),
        total_duplicate_count=sum(report.duplicate_count for report in active),
        invalid_snapshot_count=invalid,
        status=status,
        days=[report.to_dict() for report in reports],
    )


def _build_group_report(name: str, paths: list[Path]) -> BrokerFeedReport:
    ticks = []
    metadata = {"broker": "unknown", "server": "unknown", "symbol": "unknown"}
    for path in paths:
        part, metadata = read_recorder_file(path)
        ticks.extend(part)
    ticks.sort(key=lambda tick: (tick.occurred_at, tick.source_sequence or ""))
    issues = TickQualityValidator(gap_threshold_seconds=30).validate(ticks)
    spreads = [tick.spread_bps for tick in ticks]
    gaps = [
        (current.occurred_at - previous.occurred_at).total_seconds()
        for previous, current in zip(ticks, ticks[1:], strict=False)
    ]
    observed = (
        (ticks[-1].occurred_at - ticks[0].occurred_at).total_seconds() if len(ticks) > 1 else 0
    )
    return BrokerFeedReport(
        file=name,
        broker=metadata["broker"],
        server=metadata["server"],
        symbol=metadata["symbol"],
        first_tick_utc=ticks[0].occurred_at.isoformat() if ticks else None,
        last_tick_utc=ticks[-1].occurred_at.isoformat() if ticks else None,
        tick_count=len(ticks),
        observed_hours=round(observed / 3600, 4),
        ticks_per_minute=round(len(ticks) / (observed / 60), 4) if observed else 0.0,
        median_spread_bps=round(statistics.median(spreads), 4) if spreads else None,
        p95_spread_bps=round(_percentile(spreads, 0.95), 4) if spreads else None,
        p99_spread_bps=round(_percentile(spreads, 0.99), 4) if spreads else None,
        maximum_gap_seconds=round(max(gaps, default=0.0), 3),
        gap_count=sum(gap > 30 for gap in gaps),
        duplicate_count=sum(issue.code == "duplicate" for issue in issues),
        error_count=sum(issue.severity is Severity.ERROR for issue in issues),
        warning_count=sum(issue.severity is Severity.WARNING for issue in issues),
    )


def write_tick_quality_report(report: TickQualityReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
