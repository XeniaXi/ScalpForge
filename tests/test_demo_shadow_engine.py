from datetime import UTC, datetime, timedelta

import pytest
from scalpforge_strategy.demo_shadow_engine import (
    Quote,
    _bars,
    _completed,
    _ema,
    _incremental_quotes,
    _live_entry_quote,
    _merge_bars,
)


def test_live_bar_aggregation_is_causal_and_five_minute() -> None:
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    quotes = [Quote(start + timedelta(minutes=i), 2000 + i, 2000.2 + i) for i in range(10)]
    bars = _bars(quotes)
    assert len(bars) == 2
    assert bars[0]["available_at"] == start + timedelta(minutes=5)
    assert bars[0]["close"] == 2004.1


def test_completed_hour_requires_twelve_contiguous_bars() -> None:
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    quotes = [Quote(start + timedelta(minutes=5 * i), 2000 + i, 2000.2 + i) for i in range(12)]
    assert len(_completed(_bars(quotes), 12)) == 1


def test_ema_uses_only_trailing_period() -> None:
    assert _ema([1.0, 2.0, 3.0], 2) == pytest.approx(2.6666666666666665)


def test_incremental_reader_only_reads_appended_rows(tmp_path) -> None:
    source = tmp_path / "scalpforge_GOLD_20260813_ticks.csv"
    header = (
        "record_type,received_utc,server_time,monotonic_ms,session_id,source_sequence,"
        "broker,server,symbol,bid,ask,spread_points\n"
    )
    first = "tick,2026.08.13 10:00:00,x,1,s,1,A,B,GOLD,2000,2000.2,20\n"
    second = "tick,2026.08.13 10:00:01,x,2,s,2,A,B,GOLD,2001,2001.2,20\n"
    source.write_text(header + first, encoding="utf-8")
    cursor = {str(source.resolve()): source.stat().st_size}
    source.write_text(header + first + second, encoding="utf-8")
    quotes, updated = _incremental_quotes(tmp_path, cursor)
    assert len(quotes) == 1
    assert quotes[0].bid == 2001
    assert updated[str(source.resolve())] == source.stat().st_size


def test_incremental_bar_merge_preserves_original_open() -> None:
    start = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    cached = _bars([Quote(start, 2000, 2000.2)])
    incoming = _bars([Quote(start + timedelta(minutes=1), 1999, 1999.2)])
    merged = _merge_bars(cached, incoming)
    assert merged[0]["open"] == 2000.1
    assert merged[0]["close"] == 1999.1


def test_live_entry_rejects_late_reconstruction() -> None:
    signal = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    quotes = [Quote(signal, 2000, 2000.2)]
    assert _live_entry_quote(quotes, signal, signal + timedelta(minutes=2), 5, 60) is None


def test_live_entry_uses_fresh_quote_observed_at_runtime() -> None:
    signal = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    observed = signal + timedelta(seconds=20)
    old = Quote(signal, 2000, 2000.2)
    fresh = Quote(observed - timedelta(seconds=2), 2001, 2001.2)
    assert _live_entry_quote([old, fresh], signal, observed, 5, 60) == fresh
