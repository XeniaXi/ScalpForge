from datetime import UTC, datetime, timedelta

from scalpforge_strategy.broker_economics_audit import (
    BrokerEconomicsConfig,
    _crosses_hour,
    _daily_swap_bps,
)


def test_interest_swap_is_conservative_and_directional() -> None:
    spec = {"swap_type": 2, "swap_long": -9.5, "swap_short": 0.5}
    config = BrokerEconomicsConfig()
    assert _daily_swap_bps(1, spec, config) == -9.5 * 100 / 360
    assert _daily_swap_bps(-1, spec, config) == 0.0


def test_rollover_crossing_uses_wall_clock_boundary() -> None:
    start = datetime(2026, 1, 2, 21, 30, tzinfo=UTC)
    assert _crosses_hour(start, start + timedelta(hours=1), 22) is True
    assert _crosses_hour(start, start + timedelta(minutes=20), 22) is False
