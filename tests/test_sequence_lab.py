from datetime import UTC, datetime, timedelta

from scalpforge_strategy.sequence_lab import SequenceLabConfig, _at, _exit


def test_at_rejects_path_crossing_a_market_gap() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start, start + timedelta(seconds=1), start + timedelta(seconds=10)]
    assert _at(timestamps, 0, 10, 5) is None


def test_long_exit_crosses_spread_and_slippage() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=value) for value in (0, 1, 60)]
    bids = [100.0, 100.0, 101.0]
    asks = [100.5, 100.5, 101.5]
    mids = [100.25, 100.25, 101.25]
    trade = _exit(
        "hold_5s",
        "time_60s",
        1,
        start + timedelta(seconds=61),
        0,
        1,
        99.0,
        timestamps,
        bids,
        asks,
        mids,
        SequenceLabConfig(maximum_gap_seconds=60),
    )
    assert trade is not None
    gross = (101.25 / 100.25 - 1) * 10_000
    expected_net = (101.0 * 0.99995 / (100.5 * 1.00005) - 1) * 10_000
    assert trade.gross == gross
    assert trade.net == expected_net
    assert trade.net < trade.gross


def test_sequence_lab_cannot_enable_real_money() -> None:
    assert "real_money" not in SequenceLabConfig.__dataclass_fields__
