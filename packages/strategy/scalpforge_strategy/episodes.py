from __future__ import annotations

from collections.abc import Hashable, Sequence
from datetime import datetime


def episode_start_mask(
    timestamps: Sequence[datetime],
    states: Sequence[Hashable | None],
    maximum_gap_seconds: int,
) -> list[bool]:
    """Mark first observations of independent contiguous market-state episodes."""
    if len(timestamps) != len(states):
        raise ValueError("timestamps and episode states must align")
    if maximum_gap_seconds <= 0:
        raise ValueError("maximum episode gap must be positive")
    starts = [False] * len(timestamps)
    previous_state: Hashable | None = None
    previous_timestamp: datetime | None = None
    for index, (timestamp, state) in enumerate(zip(timestamps, states, strict=True)):
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("episode timestamps must be strictly increasing")
        discontinuity = (
            previous_timestamp is None
            or (timestamp - previous_timestamp).total_seconds() > maximum_gap_seconds
        )
        if state is not None and (discontinuity or state != previous_state):
            starts[index] = True
        previous_state = state
        previous_timestamp = timestamp
    return starts
