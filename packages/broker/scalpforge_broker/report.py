import csv
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from scalpforge_core.models import MarketTick
from scalpforge_data.quality import Severity, TickQualityValidator


@dataclass(frozen=True)
class BrokerFeedReport:
    file: str
    broker: str
    server: str
    symbol: str
    first_tick_utc: str | None
    last_tick_utc: str | None
    tick_count: int
    observed_hours: float
    ticks_per_minute: float
    median_spread_bps: float | None
    p95_spread_bps: float | None
    p99_spread_bps: float | None
    maximum_gap_seconds: float
    gap_count: int
    duplicate_count: int
    error_count: int
    warning_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _parse_mt4_time(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y.%m.%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty list")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def read_recorder_file(path: Path) -> tuple[list[MarketTick], dict[str, str]]:
    ticks: list[MarketTick] = []
    metadata = {"broker": "unknown", "server": "unknown", "symbol": "unknown"}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            metadata = {key: row.get(key) or "unknown" for key in metadata}
            if row.get("record_type") != "tick":
                continue
            received = _parse_mt4_time(row["received_utc"])
            ticks.append(
                MarketTick(
                    instrument=row["symbol"],
                    occurred_at=received,
                    received_at=received,
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    source=f"{row['broker']}:{row['server']}",
                    source_sequence=row.get("source_sequence") or row.get("monotonic_ms") or "",
                )
            )
    return ticks, metadata


def build_feed_report(path: Path, *, gap_threshold_seconds: float = 30.0) -> BrokerFeedReport:
    ticks, metadata = read_recorder_file(path)
    validator = TickQualityValidator(gap_threshold_seconds=gap_threshold_seconds)
    issues = validator.validate(ticks)
    spreads = [tick.spread_bps for tick in ticks]
    gaps = [
        (current.occurred_at - previous.occurred_at).total_seconds()
        for previous, current in zip(ticks, ticks[1:], strict=False)
    ]
    observed_seconds = (
        (ticks[-1].occurred_at - ticks[0].occurred_at).total_seconds() if len(ticks) > 1 else 0
    )
    return BrokerFeedReport(
        file=path.name,
        broker=metadata["broker"],
        server=metadata["server"],
        symbol=metadata["symbol"],
        first_tick_utc=ticks[0].occurred_at.isoformat() if ticks else None,
        last_tick_utc=ticks[-1].occurred_at.isoformat() if ticks else None,
        tick_count=len(ticks),
        observed_hours=round(observed_seconds / 3600, 4),
        ticks_per_minute=round(len(ticks) / (observed_seconds / 60), 4)
        if observed_seconds
        else 0.0,
        median_spread_bps=round(statistics.median(spreads), 4) if spreads else None,
        p95_spread_bps=round(_percentile(spreads, 0.95), 4) if spreads else None,
        p99_spread_bps=round(_percentile(spreads, 0.99), 4) if spreads else None,
        maximum_gap_seconds=round(max(gaps, default=0.0), 3),
        gap_count=sum(gap > gap_threshold_seconds for gap in gaps),
        duplicate_count=sum(issue.code == "duplicate" for issue in issues),
        error_count=sum(issue.severity is Severity.ERROR for issue in issues),
        warning_count=sum(issue.severity is Severity.WARNING for issue in issues),
    )


def compare_feed_files(paths: list[Path]) -> list[BrokerFeedReport]:
    return [build_feed_report(path) for path in paths]
