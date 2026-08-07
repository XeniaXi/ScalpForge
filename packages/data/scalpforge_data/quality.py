from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from scalpforge_core.models import MarketTick


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: Severity
    message: str
    row_number: int | None = None


class TickQualityValidator:
    def __init__(self, max_spread_bps: float = 50.0, gap_threshold_seconds: float = 30.0) -> None:
        self.max_spread_bps = max_spread_bps
        self.gap_threshold = timedelta(seconds=gap_threshold_seconds)

    def validate(self, ticks: list[MarketTick]) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        previous: MarketTick | None = None
        seen: set[tuple[str, object, str, str]] = set()
        for row, tick in enumerate(ticks, start=2):
            key = (tick.instrument, tick.occurred_at, tick.source, tick.source_sequence)
            if key in seen:
                issues.append(QualityIssue("duplicate", Severity.ERROR, "duplicate tick key", row))
            seen.add(key)
            if tick.spread_bps > self.max_spread_bps:
                issues.append(
                    QualityIssue(
                        "wide_spread", Severity.WARNING, "spread exceeds data-quality limit", row
                    )
                )
            if previous is not None:
                if tick.occurred_at < previous.occurred_at:
                    issues.append(
                        QualityIssue(
                            "out_of_order", Severity.ERROR, "timestamp moved backwards", row
                        )
                    )
                elif tick.occurred_at - previous.occurred_at > self.gap_threshold:
                    issues.append(
                        QualityIssue(
                            "time_gap", Severity.WARNING, "tick gap exceeds threshold", row
                        )
                    )
            previous = tick
        return issues
