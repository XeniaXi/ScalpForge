from __future__ import annotations

from datetime import UTC, datetime, time


def is_gold_market_open(moment: datetime) -> bool:
    """Conservative weekly GOLD schedule; holidays remain an external calendar concern."""
    utc = moment.astimezone(UTC)
    weekday = utc.weekday()
    clock = utc.time()
    if weekday == 5:
        return False
    if weekday == 6:
        return clock >= time(22, 0)
    return not (weekday == 4 and clock >= time(22, 0))
