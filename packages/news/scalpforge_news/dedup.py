from __future__ import annotations

from collections.abc import Iterable

from .models import NormalizedEvent


def deduplicate(events: Iterable[NormalizedEvent]) -> list[NormalizedEvent]:
    unique: dict[str, NormalizedEvent] = {}
    for event in events:
        existing = unique.get(event.event_key)
        if existing is None or event.received_at < existing.received_at:
            unique[event.event_key] = event
    return sorted(unique.values(), key=lambda event: (event.occurred_at, event.event_key))
