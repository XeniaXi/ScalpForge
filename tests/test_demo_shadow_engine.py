from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_strategy.demo_shadow_engine import Quote, _bars, _completed, _ema


def test_live_bar_aggregation_is_causal_and_five_minute() -> None:
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    quotes = [Quote(start + timedelta(minutes=i), 2000 + i, 2000.2 + i) for i in range(10)]
    bars = _bars(quotes)
    assert len(bars) == 2
    assert bars[0]["available_at"] == start + timedelta(minutes=5)
    assert bars[0]["close"] == 2004.1


def test_completed_hour_requires_twelve_contiguous_bars() -> None:
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    quotes = [Quote(start + timedelta(minutes=5 * i), 2000 + i, 2000.2 + i) for i in range(12)]
    assert len(_completed(_bars(quotes), 12)) == 1


def test_ema_uses_only_trailing_period() -> None:
    assert _ema([1.0, 2.0, 3.0], 2) == pytest.approx(2.6666666666666665)
