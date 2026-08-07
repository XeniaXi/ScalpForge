from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_replay import ReplayEngine, ReplayEvent


@pytest.mark.asyncio
async def test_replay_is_ordered_and_uses_event_time() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    observed: list[tuple[str, datetime]] = []

    async def capture(event: ReplayEvent[str], now: datetime) -> None:
        observed.append((event.payload, now))

    events = [
        ReplayEvent(start + timedelta(seconds=2), 2, "later"),
        ReplayEvent(start, 1, "first"),
        ReplayEvent(start + timedelta(seconds=2), 1, "same-time-first"),
    ]
    count = await ReplayEngine[str]().run(events, capture)
    assert count == 3
    assert [value for value, _ in observed] == ["first", "same-time-first", "later"]
    expected_times = [start, start + timedelta(seconds=2), start + timedelta(seconds=2)]
    assert all(
        now == event_time
        for (_, now), event_time in zip(observed, expected_times, strict=True)
    )
