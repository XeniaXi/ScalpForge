from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_news.macro_calendar import MacroEvent, audit_macro_coverage


def _event(index, family):
    released = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return MacroEvent(
        f"event-{index}", family, released, released, released - timedelta(hours=1),
        1.0, 2.0, 0.5, None, "percent", "https://example.com/release", "a" * 64,
    )


def test_coverage_requires_both_families_and_twenty_events() -> None:
    events = [_event(index, "cpi" if index < 10 else "employment_situation") for index in range(20)]
    assert audit_macro_coverage(events)["strategy_eligible"] is True


def test_consensus_must_predate_release() -> None:
    released = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="consensus"):
        MacroEvent(
            "bad", "cpi", released, released, released, 1.0, 2.0, None, None,
            "percent", "https://example.com", "a" * 64,
        )
