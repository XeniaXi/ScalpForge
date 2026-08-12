from datetime import UTC, datetime, timedelta

from scalpforge_strategy.gap_semantics_audit import (
    GapSemanticsConfig,
    _classify,
    _continuity_intervals,
    _median,
)


def test_gap_classification_distinguishes_inherited_interruptions() -> None:
    assert _classify(False, True, 12) == "short_quote_interruption_only"
    assert _classify(False, True, 60) == "short_quote_interruption_only"
    assert _classify(False, True, 61) == "long_quote_interruption_or_closure_unknown"
    assert _classify(True, True, 12) == "five_minute_bar_discontinuity"
    assert _classify(False, False, 0) == "outcome_invalid_without_continuity_evidence"


def test_gap_median() -> None:
    assert _median([]) is None
    assert _median([8, 6, 10]) == 8
    assert _median([6, 8]) == 7


def test_continuity_interval_preserves_executable_boundaries() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = _continuity_intervals(
        [start, start + timedelta(seconds=59), start + timedelta(seconds=120)],
        [None, 59, 61],
        [100.0, 101.0, 102.0],
        [100.2, 101.2, 102.2],
        GapSemanticsConfig(),
    )
    assert [row["interruption_class"] for row in rows] == [
        "short_quote_interruption", "long_quote_interruption"
    ]
    assert all(row["valid_post_gap_quote"] for row in rows)
    assert all(row["synthetic_fill_permitted"] is False for row in rows)
