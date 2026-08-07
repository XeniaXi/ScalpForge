from __future__ import annotations

from collections.abc import Iterable

from scalpforge_core.models import MarketTick

from .models import NormalizedEvent, ReactionMeasurement


def measure_reactions(
    event: NormalizedEvent,
    ticks: Iterable[MarketTick],
    windows: Iterable[int] = (5, 30, 60, 300, 900),
) -> list[ReactionMeasurement]:
    if not event.reaction_eligible:
        return []
    ordered = sorted(ticks, key=lambda tick: tick.occurred_at)
    before = [tick for tick in ordered if tick.occurred_at <= event.occurred_at]
    if not before:
        return []
    anchor = before[-1]
    results: list[ReactionMeasurement] = []
    for seconds in windows:
        target = event.occurred_at.timestamp() + seconds
        after = next((tick for tick in ordered if tick.occurred_at.timestamp() >= target), None)
        if after is None:
            continue
        return_bps = (after.mid / anchor.mid - 1) * 10_000
        results.append(
            ReactionMeasurement(
                event_key=event.event_key,
                instrument=anchor.instrument,
                event_time=event.occurred_at,
                window_seconds=seconds,
                before_time=anchor.occurred_at,
                after_time=after.occurred_at,
                before_mid=anchor.mid,
                after_mid=after.mid,
                return_bps=return_bps,
            )
        )
    return results
