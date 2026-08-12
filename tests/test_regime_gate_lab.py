from datetime import UTC, datetime

from scalpforge_strategy.regime_gate_lab import GATES, _passes, _thresholds


def _row(**updates):
    row = {
        "occurred_at": datetime(2026, 8, 12, tzinfo=UTC),
        "window": "asia",
        "spread_bps": 1.0,
        "tick_intensity_ratio": 2.0,
        "realized_volatility_60s": 0.5,
        "range_width_bps": 10.0,
        "return_30s_signed": 0.1,
        "return_60s_signed": 0.2,
    }
    return {**row, **updates}


def test_gate_family_is_fixed_and_small() -> None:
    assert len(GATES) == 13
    assert len(set(GATES)) == 13


def test_thresholds_are_derived_from_training_features() -> None:
    thresholds = _thresholds([_row(spread_bps=1.0), _row(spread_bps=3.0)])
    assert thresholds["spread_bps"] == 2.0


def test_interpretable_gates() -> None:
    thresholds = _thresholds([_row()])
    assert _passes(_row(), "asia_only", thresholds)
    assert _passes(_row(), "trend_aligned_60s", thresholds)
    assert _passes(_row(), "weekday_tue_thu", thresholds)
    assert not _passes(_row(window="new_york_open"), "asia_only", thresholds)
