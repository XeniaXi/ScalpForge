from datetime import UTC, datetime, timedelta

from scalpforge_strategy.avatrade_discovery_lab import (
    _boundary_rejections,
    _cooldown,
    _news_attribution,
)


def _bar(at, open_, high, low, close):
    return {
        "open_at": at,
        "available_at": at + timedelta(minutes=5),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def test_boundary_rejection_uses_only_prior_boundary() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    bars = [_bar(start + timedelta(minutes=5 * i), 100, 101, 99, 100) for i in range(48)]
    bars.append(_bar(start + timedelta(minutes=240), 100, 103, 100, 100.5))
    result = _boundary_rejections(bars)
    assert len(result) == 1
    assert result[0]["side"] == -1


def test_cooldown_keeps_first_signal_without_looking_at_returns() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    signals = [
        {"available_at": start, "side": 1},
        {"available_at": start + timedelta(hours=1), "side": -1},
        {"available_at": start + timedelta(hours=4), "side": 1},
    ]
    assert _cooldown(signals, 14400) == [signals[0], signals[2]]


def test_news_attribution_is_schedule_only() -> None:
    at = datetime(2026, 8, 14, 12, 30, tzinfo=UTC)
    events = [{"at": at, "family": "cpi"}]
    assert _news_attribution((at - timedelta(minutes=20)).isoformat(), events) \
        == "inside_official_event_window"
    assert _news_attribution((at + timedelta(minutes=20)).isoformat(), events) == "outside"
