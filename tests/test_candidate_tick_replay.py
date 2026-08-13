from datetime import UTC, datetime, timedelta

from scalpforge_strategy.candidate_tick_replay import (
    TickReplayConfig,
    _execute,
    _metrics,
)


def test_long_uses_ask_entry_and_bid_exit_after_latency() -> None:
    start = datetime(2026, 1, 2, tzinfo=UTC)
    trade = {
        "timestamp": start,
        "entry_at": start,
        "exit_at": start + timedelta(seconds=10),
        "side": 1,
        "base": 0.0,
    }
    times = [start, start + timedelta(seconds=1), start + timedelta(seconds=11)]
    row = _execute(
        trade,
        1.0,
        times,
        [100.0, 101.0, 102.0],
        [100.5, 101.5, 102.5],
        TickReplayConfig(latency_seconds=(1.0,)),
    )
    expected = (102.0 / 101.5 - 1) * 10_000 - 1.0
    assert row["valid"] is True
    assert row["net_bps"] == expected


def test_stale_boundary_is_rejected() -> None:
    start = datetime(2026, 1, 2, tzinfo=UTC)
    trade = {
        "timestamp": start,
        "entry_at": start,
        "exit_at": start + timedelta(seconds=10),
        "side": -1,
        "base": 0.0,
    }
    row = _execute(
        trade,
        0.0,
        [start + timedelta(seconds=6), start + timedelta(seconds=11)],
        [100.0, 99.0],
        [100.5, 99.5],
        TickReplayConfig(latency_seconds=(0.0,), maximum_quote_delay_seconds=5.0),
    )
    assert row["valid"] is False
    assert row["invalid_reason"] == "stale_boundary_quote"


def test_metrics_keep_rejections_visible() -> None:
    metrics = _metrics(
        [
            {
                "valid": True,
                "invalid_reason": None,
                "net_bps": 2.0,
                "difference_from_proxy_bps": -0.5,
            },
            {"valid": False, "invalid_reason": "stale_boundary_quote"},
        ]
    )
    assert metrics["execution_coverage_ratio"] == 0.5
    assert metrics["mean_net_bps"] == 2.0
    assert metrics["rejection_reasons"] == {"stale_boundary_quote": 1}
