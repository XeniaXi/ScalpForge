from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pyarrow as pa  # type: ignore[import-untyped]
from scalpforge_strategy.execution_clock import CausalQuoteSeries
from scalpforge_strategy.sequence_lab import (
    SequenceLabConfig,
    _at,
    _exit,
    _gross_return_bps,
    _require_causal_manifest,
    _simulate,
    _simulate_windows,
)


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
    quotes = CausalQuoteSeries(timestamps, timestamps, timestamps, bids, asks, [0, 0, 0])
    trade = _exit(
        "hold_5s",
        "time_60s",
        1,
        start + timedelta(seconds=61),
        0,
        1,
        99.0,
        timestamps,
        mids,
        quotes,
        [1, 2, None],
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


def test_episode_spacing_must_cover_the_longest_complete_trade() -> None:
    try:
        SequenceLabConfig(
            hold_delays_seconds=(1,),
            retest_window_seconds=3,
            sweep_window_seconds=2,
            time_exit_seconds=5,
            additional_exit_horizons_seconds=(10,),
            minimum_episode_spacing_seconds=15,
        )
    except ValueError as error:
        assert "episode spacing" in str(error)
    else:
        raise AssertionError("unsafe overlapping episode spacing was accepted")


def test_short_gross_return_uses_reciprocal_return() -> None:
    assert _gross_return_bps(100.0, 80.0, -1) == 2500.0


def test_input_manifests_fail_closed_without_causality_declarations() -> None:
    for metadata in ({}, {"point_in_time": True}, {"labels_included": False}):
        try:
            _require_causal_manifest(metadata, "feature")
        except ValueError:
            pass
        else:
            raise AssertionError("non-causal manifest was accepted")


def test_streamed_sequence_matches_full_simulation_across_boundary() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=value) for value in range(100)]
    mids = [100 + value * 0.02 for value in range(50)] + [
        101 - value * 0.02 for value in range(50)
    ]
    features = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "feature_available_at": [value + timedelta(seconds=1) for value in timestamps],
            "bar_open_at": [value + timedelta(milliseconds=100) for value in timestamps],
            "bar_open_bid": [value - 0.01 for value in mids],
            "bar_open_ask": [value + 0.01 for value in mids],
            "mid": mids,
            "spread_bps": [2.0] * 100,
            "tick_intensity_ratio": [2.0] * 100,
            "tick_count": [10] * 100,
            "quote_change_count": [8] * 100,
            "spread_shock_ratio": [1.0] * 100,
            "return_5s": [0.001] * 50 + [-0.001] * 50,
            "return_30s": [0.001] * 50 + [-0.001] * 50,
        }
    )
    sides = [1] * 50 + [-1] * 50
    structure = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "compression_60_to_300": [0.4] * 100,
            "breakout_side_300s": sides,
            "prior_high_300s": [value - 0.05 for value in mids],
            "prior_low_300s": [value + 0.05 for value in mids],
        }
    )
    fold = SimpleNamespace(
        fold=1,
        test_start=start,
        test_end_exclusive=start + timedelta(minutes=2),
    )
    config = SequenceLabConfig(
        hold_delays_seconds=(1, 2),
        retest_window_seconds=3,
        sweep_window_seconds=2,
        time_exit_seconds=5,
        additional_exit_horizons_seconds=(10,),
        confirmation_bps=0,
        minimum_episode_spacing_seconds=16,
        bootstrap_samples=40,
    )
    full = _simulate(features, structure, timestamps, [fold], config)
    streamed = _simulate_windows(
        [
            (features.slice(0, 60), structure.slice(0, 60), 40),
            (features.slice(40), structure.slice(40), 60),
        ],
        [fold],
        config,
    )
    assert streamed == full
    assert any(trade.entry_timestamp >= timestamps[50] for trade in streamed)
