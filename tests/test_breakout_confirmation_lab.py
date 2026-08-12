from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_strategy.breakout_confirmation_lab import (
    RULES,
    BreakoutConfirmationConfig,
    confirmation_passes,
)
from scalpforge_strategy.controlled_breakout_lab import ControlledBreakoutConfig


def _row(**overrides):
    values = {
        "side": 1,
        "spread_bps": 2.0,
        "tick_intensity_ratio": 1.5,
        "breakout_distance_bps": 0.5,
        "return_30s_signed": 1.0,
        "distance_from_tick_vwap_signed_bps": 1.0,
        "compression_60_to_300": 0.8,
    }
    values.update(overrides)
    return values


def _quotes():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        (start, 100.0, 100.01),
        (start + timedelta(seconds=5), 100.01, 100.02),
    ]


def test_confirmation_family_is_small_and_frozen() -> None:
    assert RULES == ("persistence", "market_quality", "alignment", "full_confirmation")


def test_full_confirmation_requires_every_component() -> None:
    assert confirmation_passes(
        "full_confirmation",
        _row(),
        _quotes(),
        BreakoutConfirmationConfig(),
        ControlledBreakoutConfig(),
    )
    assert not confirmation_passes(
        "full_confirmation",
        _row(return_30s_signed=-1.0),
        _quotes(),
        BreakoutConfirmationConfig(),
        ControlledBreakoutConfig(),
    )


def test_opposite_direction_does_not_persist() -> None:
    assert not confirmation_passes(
        "persistence",
        _row(side=-1),
        _quotes(),
        BreakoutConfirmationConfig(),
        ControlledBreakoutConfig(),
    )


def test_unknown_rule_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown confirmation rule"):
        confirmation_passes(
            "unknown",
            _row(),
            _quotes(),
            BreakoutConfirmationConfig(),
            ControlledBreakoutConfig(),
        )
