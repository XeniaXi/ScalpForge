from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_strategy.episodes import episode_start_mask


def test_repeated_breakout_rows_form_one_episode() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=value) for value in (0, 1, 2, 3, 4, 5)]
    states = [None, (300, 1), (300, 1), None, (300, 1), (300, -1)]
    assert episode_start_mask(timestamps, states, 5) == [False, True, False, False, True, True]


def test_data_gap_starts_new_episode_even_when_state_is_unchanged() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start, start + timedelta(seconds=1), start + timedelta(seconds=10)]
    assert episode_start_mask(timestamps, [1, 1, 1], 5) == [True, False, True]


def test_episode_inputs_are_validated() -> None:
    now = datetime(2026, 7, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="align"):
        episode_start_mask([now], [], 5)
    with pytest.raises(ValueError, match="strictly increasing"):
        episode_start_mask([now, now], [1, 1], 5)
