import hashlib
import json
from datetime import UTC, datetime

import pytest
from scalpforge_strategy.market_session_calendar import (
    load_jforex_offline_manifest,
    load_session_calendar,
)


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
    ) == "expected_open_quote_silence"
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


def test_jforex_manifest_is_checksum_verified_and_classifies_offline(tmp_path) -> None:
    csv_text = (
        "start_utc,end_utc,duration_seconds,instrument,scope\n"
        "2026-01-05T22:00:00.000Z,2026-01-05T23:00:00.000Z,3600.000,XAUUSD,"
        "jforex_instrument_offline_domain\n"
    )
    csv_path = tmp_path / "offline.csv"
    csv_path.write_text(csv_text, encoding="utf-8")
    payload = {
        "provider": "dukascopy", "venue": "SWFX", "instrument": "XAUUSD",
        "source": "IDataService.getOfflineTimeDomains(from,to,instrument)",
        "source_scope": "instrument_specific_offline_domains",
        "start_utc": "2026-01-01T00:00:00.000Z",
        "end_utc_exclusive": "2026-02-01T00:00:00.000Z", "interval_count": 1,
        "csv": "offline.csv", "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "read_only": True, "external_non_executable": True,
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    calendar = load_jforex_offline_manifest(manifest)
    assert calendar.classify(
        datetime(2026, 1, 5, 22, 30, tzinfo=UTC),
        datetime(2026, 1, 5, 22, 31, tzinfo=UTC),
    ) == "scheduled_closed"
    assert calendar.classify(
        datetime(2026, 1, 5, 21, 30, tzinfo=UTC),
        datetime(2026, 1, 5, 21, 31, tzinfo=UTC),
    ) == "expected_open_quote_silence"
