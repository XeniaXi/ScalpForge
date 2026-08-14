from datetime import UTC, datetime, timedelta

from scalpforge_strategy.avatrade_candidate_replay import _signals
from scalpforge_strategy.candidate_parity_audit import (
    _attribute_live,
    _incremental_bars,
    _incremental_signals,
)
from scalpforge_strategy.demo_shadow_engine import Quote, _bars, _candidates


def test_incremental_and_batch_candidate_signals_are_identical() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    quotes = []
    for index in range(12 * 32):
        at = start + timedelta(minutes=5 * index)
        price = 2000 + index * 0.1 + (0.2 if index % 7 else -0.1)
        quotes.append(Quote(at, price, price + 0.2))
    bars = _bars(quotes)
    assert _incremental_bars(quotes) == bars
    batch = _signals(_candidates(bars, 0.35), 8 * 3600)
    incremental = _incremental_signals(bars, 0.35, 8 * 3600)
    assert [row["available_at"] for row in incremental] == [
        row["available_at"] for row in batch
    ]
    assert [row["side"] for row in incremental] == [row["side"] for row in batch]


def test_live_attribution_does_not_promote_unmatched_legacy_signal() -> None:
    canonical = [{"feature_available_at": "2026-08-14T10:30:00+00:00", "side": -1}]
    live = [
        {
            "feature_available_at": "2026-08-14T07:45:00+00:00",
            "side": -1,
            "disposition": "rejected_late_engine_processing",
        }
    ]
    result = _attribute_live(
        live,
        canonical,
        datetime(2026, 8, 14, tzinfo=UTC),
        datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    assert result["canonical_matches"] == 0
    assert result["unmatched_comparable_live_signals"] == 1


def test_live_attribution_separates_signal_after_snapshot() -> None:
    live = [{"feature_available_at": "2026-08-14T16:00:00+00:00", "side": 1}]
    result = _attribute_live(
        live,
        [],
        datetime(2026, 8, 14, tzinfo=UTC),
        datetime(2026, 8, 14, 13, 51, 48, tzinfo=UTC),
    )
    assert result["comparable_live_signals"] == 0
    assert result["out_of_snapshot_live_signals"] == 1
    assert result["unmatched_comparable_live_signals"] == 0
