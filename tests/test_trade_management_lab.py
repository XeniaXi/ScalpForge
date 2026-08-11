from datetime import UTC, datetime, timedelta

from scalpforge_strategy.trade_management_lab import match_trades


def _trade(ticket: str, second: int, side: str, entry: float, exit_price: float) -> dict:
    opened = datetime(2026, 1, 1, 10, 0, tzinfo=UTC) + timedelta(seconds=second)
    return {
        "ticket": ticket,
        "symbol": "XAUUSD",
        "side": side,
        "volume_lots": 0.1,
        "opened_at": opened,
        "entry_price": entry,
        "closed_at": opened + timedelta(seconds=60),
        "exit_price": exit_price,
    }


def test_matcher_is_one_to_one_and_does_not_use_outcomes() -> None:
    provider = [
        _trade("p1", 0, "buy", 100.0, 101.0),
        _trade("p2", 10, "buy", 100.0, 50.0),
    ]
    copied = [
        _trade("c1", 1, "buy", 100.1, 101.0),
        _trade("c2", 11, "buy", 100.1, 200.0),
    ]
    pairs = match_trades(provider, copied, tolerance=5)
    assert [(pair["provider_ticket"], pair["copied_ticket"]) for pair in pairs] == [
        ("p1", "c1"),
        ("p2", "c2"),
    ]
    assert pairs[0]["entry_lag_seconds"] == 1
    assert round(pairs[0]["adverse_entry_bps"], 6) == 10.0


def test_counterfactuals_separate_entry_and_exit_effects() -> None:
    provider = [_trade("p", 0, "buy", 100.0, 102.0)]
    copied = [_trade("c", 2, "buy", 101.0, 103.0)]
    pair = match_trades(provider, copied, tolerance=5)[0]
    assert pair["provider_path_bps"] == 200.0
    assert round(pair["copied_entry_provider_exit_bps"], 6) == round(10_000 / 101, 6)
    assert pair["provider_entry_copied_exit_bps"] == 300.0
    assert round(pair["copied_path_bps"], 6) == round(20_000 / 101, 6)
    assert pair["entry_effect_bps"] < 0
    assert pair["exit_effect_bps"] > 0


def test_matcher_rejects_wrong_side_and_out_of_window() -> None:
    provider = [_trade("p", 0, "buy", 100.0, 101.0)]
    copied = [
        _trade("wrong-side", 1, "sell", 100.0, 99.0),
        _trade("late", 31, "buy", 100.0, 101.0),
    ]
    assert match_trades(provider, copied, tolerance=30) == []
