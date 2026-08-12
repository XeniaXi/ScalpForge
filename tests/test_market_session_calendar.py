import hashlib
import json
from datetime import UTC, datetime

import pytest
from scalpforge_strategy.market_session_calendar import load_session_calendar


def _calendar(tmp_path, *, verified=True):
    payload = {
        "calendar_id": "test-xau-v1", "instrument": "XAUUSD", "venue": "TEST",
        "timezone": "Etc/UTC", "effective_from": "2026-01-01",
        "effective_to_exclusive": "2027-01-01",
        "weekly_open_windows": [
            {"weekday": day, "start": "01:00:00", "end": "23:00:00"}
            for day in range(5)
        ],
        "closed_intervals": [
            {"start": "2026-01-05T12:00:00Z", "end": "2026-01-05T13:00:00Z"}
        ],
        "source_url": "https://example.com/schedule", "source_retrieved_at": "2026-01-01T00:00:00Z",
        "source_sha256": hashlib.sha256(b"source").hexdigest(), "verified": verified,
    }
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_calendar_distinguishes_closure_from_open_time_outage(tmp_path) -> None:
    calendar = load_session_calendar(_calendar(tmp_path))
    assert calendar.classify(
        datetime(2026, 1, 5, 12, 10, tzinfo=UTC),
        datetime(2026, 1, 5, 12, 20, tzinfo=UTC),
    ) == "scheduled_closed"
    assert calendar.classify(
        datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 14, 1, tzinfo=UTC),
    ) == "unexpected_open_time_interruption"
    assert calendar.classify(
        datetime(2026, 1, 10, 14, 0, tzinfo=UTC),
        datetime(2026, 1, 10, 14, 1, tzinfo=UTC),
    ) == "scheduled_closed"
    assert calendar.classify(
        datetime(2026, 1, 5, 22, 59, tzinfo=UTC),
        datetime(2026, 1, 6, 1, 1, tzinfo=UTC),
    ) == "scheduled_closed"


def test_calendar_refuses_unverified_schedule(tmp_path) -> None:
    with pytest.raises(ValueError, match="explicitly verified"):
        load_session_calendar(_calendar(tmp_path, verified=False))
