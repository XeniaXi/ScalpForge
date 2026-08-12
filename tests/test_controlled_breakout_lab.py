from datetime import UTC, datetime, timedelta

from scalpforge_strategy.controlled_breakout_lab import (
    VARIANTS,
    ControlledBreakoutConfig,
    simulate_episode,
)


def _quotes(prices):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [(start + timedelta(seconds=i), bid, ask) for i, (bid, ask) in enumerate(prices)]


def test_factorial_family_is_frozen() -> None:
    assert [variant.variant_id for variant in VARIANTS] == [
        "single_fixed",
        "single_runner",
        "staged_fixed",
        "staged_runner",
    ]


def test_single_entry_never_stages() -> None:
    cfg = ControlledBreakoutConfig(confirmation_delay_seconds=0, quick_failure_bps=50)
    prices = [(100.0, 100.01)] * 2 + [(99.9, 99.91)] * 301
    result = simulate_episode(1, _quotes(prices), VARIANTS[0], cfg)
    assert result is not None
    assert result["ticket_count"] == 1


def test_staged_entry_is_bounded_and_charged_per_ticket() -> None:
    cfg = ControlledBreakoutConfig(confirmation_delay_seconds=0, quick_failure_bps=50)
    prices = [(100.0, 100.01), (99.95, 99.96), (99.90, 99.91)] + [(100.0, 100.01)] * 301
    result = simulate_episode(1, _quotes(prices), VARIANTS[2], cfg)
    assert result is not None
    assert result["ticket_count"] == 3
    assert result["round_trip_cost_bps"] == 3.0


def test_runner_takes_partial_then_trails() -> None:
    cfg = ControlledBreakoutConfig(confirmation_delay_seconds=0, quick_failure_bps=50)
    prices = [(100.0, 100.01), (100.10, 100.11), (100.02, 100.03)]
    result = simulate_episode(1, _quotes(prices), VARIANTS[1], cfg)
    assert result is not None
    assert result["partial_taken"] is True
    assert result["exit_reason"] == "trailing"


def test_basket_stop_is_hard() -> None:
    cfg = ControlledBreakoutConfig(confirmation_delay_seconds=0, quick_failure_bps=50)
    prices = [(100.0, 100.01), (99.5, 99.51)]
    result = simulate_episode(1, _quotes(prices), VARIANTS[3], cfg)
    assert result is not None
    assert result["exit_reason"] == "basket_stop"


def test_quick_failure_waits_for_confirmation_window() -> None:
    cfg = ControlledBreakoutConfig(confirmation_delay_seconds=0, basket_stop_bps=50)
    prices = [(100.0, 100.01)] + [(99.97, 99.98)] * 61
    result = simulate_episode(1, _quotes(prices), VARIANTS[0], cfg)
    assert result is not None
    assert result["exit_reason"] == "quick_failure"
