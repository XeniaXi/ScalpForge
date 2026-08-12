from datetime import UTC, datetime, timedelta

from scalpforge_strategy.trend_candidate_audit import _invalid_reason, _remove_overlaps


def test_overlap_control_keeps_first_signal_only() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {"timestamp": start},
        {"timestamp": start + timedelta(minutes=30)},
        {"timestamp": start + timedelta(hours=1)},
    ]
    accepted, rejected, maximum = _remove_overlaps(rows, 3600)
    assert len(accepted) == 2 and rejected == 1 and maximum == 2


def test_invalid_reason_distinguishes_gap_from_closure() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    times = [start + timedelta(minutes=5 * index) for index in range(15)]
    gaps = [False] * 15
    gaps[5] = True
    assert _invalid_reason(start, 0, 0, [False], times, gaps, 3600) == "flagged_gap"
    times[5] += timedelta(minutes=5)
    gaps[5] = False
    assert (
        _invalid_reason(start, 0, 0, [False], times, gaps, 3600)
        == "market_closure_or_missing_bar"
    )
