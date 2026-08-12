from datetime import UTC, datetime

from scalpforge_strategy.gold_strategy_tournament import (
    _add_months,
    _maximum_drawdown,
)


def test_month_arithmetic_crosses_year_boundary() -> None:
    assert _add_months(datetime(2025, 11, 1, tzinfo=UTC), 3) == datetime(
        2026, 2, 1, tzinfo=UTC
    )


def test_drawdown_uses_chronological_daily_equity() -> None:
    assert _maximum_drawdown([2.0, -3.0, 1.0, -4.0]) == 6.0
