from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pyarrow as pa  # type: ignore[import-untyped]
from scalpforge_strategy.structural_lab import (
    StructuralLabConfig,
    _events_from_batches,
    stationary_block_interval,
)


def test_block_bootstrap_is_deterministic_and_preserves_constant_mean() -> None:
    first = stationary_block_interval([2.0] * 100, 100, 10, 7)
    second = stationary_block_interval([2.0] * 100, 100, 10, 7)
    assert first == second == (2.0, 2.0)


def test_block_bootstrap_handles_no_events() -> None:
    assert stationary_block_interval([], 100, 10, 7) == (0.0, 0.0)


def test_streamed_events_preserve_episode_state_across_batches() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=value) for value in range(4)]
    features = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "spread_bps": [1.0] * 4,
            "session": ["london"] * 4,
            "realized_volatility_60s": [0.0001] * 4,
            "tick_intensity_ratio": [1.0] * 4,
        }
    )
    structure = pa.Table.from_pydict(
        {"occurred_at": timestamps, "breakout_side_300s": [1, 1, -1, -1]}
    )
    outcomes = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "h60_valid": [True] * 4,
            "h60_long_gross_bps": [2.0] * 4,
            "h60_short_gross_bps": [2.0] * 4,
            "h60_long_net_bps": [1.0] * 4,
            "h60_short_net_bps": [1.0] * 4,
            "h60_long_mfe_bps": [2.0] * 4,
            "h60_long_mae_bps": [-0.5] * 4,
            "h60_short_mfe_bps": [2.0] * 4,
            "h60_short_mae_bps": [-0.5] * 4,
        }
    )
    fold = SimpleNamespace(
        test_start=start, test_end_exclusive=start + timedelta(minutes=1)
    )
    config = StructuralLabConfig(bootstrap_samples=40)
    full = _events_from_batches([(features, structure, outcomes)], [fold], config)
    streamed = _events_from_batches(
        [
            (features.slice(0, 2), structure.slice(0, 2), outcomes.slice(0, 2)),
            (features.slice(2, 2), structure.slice(2, 2), outcomes.slice(2, 2)),
        ],
        [fold],
        config,
    )
    assert streamed == full
    assert len(streamed) == 2
