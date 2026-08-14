from datetime import UTC, datetime, timedelta

from scalpforge_strategy.avatrade_candidate_replay import _signals, _trades
from scalpforge_strategy.demo_shadow_engine import Quote


def test_replay_signals_use_rising_edges_and_frozen_cooldown() -> None:
    start = datetime(2026, 8, 10, tzinfo=UTC)
    candidates = [
        {"available_at": start, "active": True},
        {"available_at": start + timedelta(minutes=5), "active": True},
        {"available_at": start + timedelta(minutes=10), "active": False},
        {"available_at": start + timedelta(hours=1), "active": True},
        {"available_at": start + timedelta(hours=9), "active": False},
        {"available_at": start + timedelta(hours=9, minutes=5), "active": True},
    ]
    signals = _signals(candidates, 8 * 3600)
    assert [row["available_at"] for row in signals] == [
        start,
        start + timedelta(hours=9, minutes=5),
    ]


def test_replay_uses_executable_sides_and_one_hour_exit() -> None:
    start = datetime(2026, 8, 10, tzinfo=UTC)
    signals = [
        {
            "available_at": start,
            "side": 1,
            "h4_return_bps": 10.0,
            "path_efficiency_1800s": 0.5,
        }
    ]
    quotes = [
        Quote(start + timedelta(seconds=1), 2000.0, 2000.2),
        Quote(start + timedelta(hours=1, seconds=1), 2002.0, 2002.2),
    ]
    trades, rejected = _trades(signals, quotes, 3600, 5)
    assert len(trades) == 1
    assert trades[0]["entry_price"] == 2000.2
    assert trades[0]["exit_price"] == 2002.0
    assert trades[0]["net_bps"] == trades[0]["gross_bps"]
    assert rejected == {
        "open_position": 0,
        "entry_quote_timeout": 0,
        "exit_quote_unavailable": 0,
        "invalid_exit_boundary": 0,
    }
