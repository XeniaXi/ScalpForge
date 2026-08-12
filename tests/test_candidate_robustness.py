from datetime import UTC, date, datetime

from scalpforge_strategy.candidate_robustness import (
    _block_bootstrap,
    _concentration,
    _daily,
    _effective_sample_size,
    _time_underwater,
)


def _row(day: int, value: float):
    return {
        "timestamp": datetime(2026, 1, day, tzinfo=UTC),
        "base": value,
        "stress_1_5": value - 0.5,
        "stress_2": value - 1.0,
    }


def test_daily_and_concentration_are_deterministic() -> None:
    rows = [_row(1, 5), _row(1, -1), _row(2, 2), _row(3, -1)]
    daily = _daily(rows)
    months = {"2026-01": rows}
    result = _concentration(daily, months)
    assert daily[date(2026, 1, 1)]["base"] == 4
    assert result["leave_best_day_total_base_bps"] == 1
    assert result["leave_best_month_total_base_bps"] == 0


def test_block_bootstrap_constant_series_has_exact_interval() -> None:
    daily = {date(2026, 1, day): {"base": 2.0} for day in range(1, 11)}
    result = _block_bootstrap(daily, 100, 5, 7)
    assert result == {"low_bps": 2.0, "high_bps": 2.0, "probability_mean_le_zero": 0.0}


def test_underwater_and_effective_sample_size_bounds() -> None:
    assert _time_underwater([2, -1, -2, 5]) == 2
    assert 1 <= _effective_sample_size([1, -1, 1, -1, 1, -1]) <= 6
