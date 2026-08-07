from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReplayEvent[PayloadT]:
    occurred_at: datetime
    sequence: int
    payload: PayloadT


class VirtualClock:
    def __init__(self) -> None:
        self._now: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("replay clock has not started")
        return self._now

    def advance_to(self, instant: datetime) -> None:
        if self._now is not None and instant < self._now:
            raise ValueError("virtual clock cannot move backwards")
        self._now = instant


class ReplayEngine[PayloadT]:
    def __init__(self, clock: VirtualClock | None = None) -> None:
        self.clock = clock or VirtualClock()

    async def run(
        self,
        events: Iterable[ReplayEvent[PayloadT]],
        handler: Callable[[ReplayEvent[PayloadT], datetime], Awaitable[None]],
    ) -> int:
        ordered = sorted(events, key=lambda event: (event.occurred_at, event.sequence))
        processed = 0
        for event in ordered:
            self.clock.advance_to(event.occurred_at)
            await handler(event, self.clock.now)
            processed += 1
        return processed
