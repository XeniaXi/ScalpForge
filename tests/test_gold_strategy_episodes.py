from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
from scalpforge_strategy.gold_strategy_episodes import (
    GoldEpisodeConfig,
    build_gold_strategy_episodes,
)


def _states() -> pa.Table:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return pa.Table.from_pydict(
        {
            "occurred_at": [start + timedelta(minutes=5 * i) for i in range(5)],
            "feature_available_at": [start + timedelta(minutes=5 * (i + 1)) for i in range(5)],
            "h1_trend_side": [1, 1, 1, 1, 1],
            "h4_return_bps": [10.0] * 5,
            "m15_displacement_atr": [0.0, 1.6, 1.7, 0.0, 1.8],
            "volatility_expansion_ratio": [1.0, 1.6, 1.7, 1.0, 1.8],
            "path_efficiency_1800s": [0.5] * 5,
            "fvg_active": [False] * 5,
            "fvg_side": [0] * 5,
            "fvg_mitigated": [False] * 5,
            "boundary_rejection_side_4h": [0, 0, -1, 0, 0],
        }
    )


def test_contiguous_signals_collapse_and_family_attribution_is_preserved() -> None:
    table = build_gold_strategy_episodes(
        _states(), GoldEpisodeConfig(family_cooldown_seconds=300)
    )
    families = table["family"].to_pylist()
    assert families.count("trend_continuation") == 1
    assert families.count("volatility_expansion") == 2
    assert families.count("displacement_persistence") == 2
    assert families.count("boundary_rejection") == 1
    assert max(table["simultaneous_family_count"].to_pylist()) >= 3


def test_episode_features_never_follow_their_availability_clock() -> None:
    table = build_gold_strategy_episodes(_states())
    assert all(
        available >= occurred
        for occurred, available in zip(
            table["occurred_at"].cast(pa.int64()).to_pylist(),
            table["feature_available_at"].cast(pa.int64()).to_pylist(),
            strict=True,
        )
    )
