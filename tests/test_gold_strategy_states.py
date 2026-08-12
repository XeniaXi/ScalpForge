import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.gold_strategy_states import (
    GoldStateConfig,
    build_gold_strategy_states,
    write_gold_strategy_states,
)


def _source(prices: list[float]) -> pa.Table:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    times = [start + timedelta(seconds=300 * index) for index in range(len(prices))]
    return pa.Table.from_pydict(
        {
            "bar_open_at": times,
            "occurred_at": [time + timedelta(seconds=299) for time in times],
            "feature_available_at": [time + timedelta(seconds=300) for time in times],
            "bar_open": prices,
            "bar_high": [price + 0.5 for price in prices],
            "bar_low": [price - 0.5 for price in prices],
            "bar_close": prices,
            "spread_bps": [2.0] * len(prices),
            "volatility_expansion_ratio": [1.0] * len(prices),
            "path_efficiency_1800s": [0.5] * len(prices),
            "prior_high_14400s": [200.0] * len(prices),
            "prior_low_14400s": [50.0] * len(prices),
        }
    )


def test_completed_timeframes_do_not_use_forming_bar() -> None:
    config = GoldStateConfig(ema_fast_bars=2, ema_slow_bars=3, atr_bars=2)
    before = build_gold_strategy_states(_source([100.0] * 12), config)
    after = build_gold_strategy_states(_source([100.0] * 12 + [999.0]), config)
    for name in before.column_names:
        if name not in {"occurred_at", "feature_available_at"}:
            assert before[name].to_pylist() == after[name].slice(0, 12).to_pylist()
    assert after["h1_close"][11].as_py() is None
    assert after["h1_close"][12].as_py() == 100.0


def test_state_artifact_is_label_free_and_non_executable(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    partition = source / "multi-hour.parquet"
    pq.write_table(_source([100.0] * 20), partition)
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "multi-hour-1",
                "point_in_time": True,
                "labels_included": False,
                "partitions": [str(partition)],
            }
        ),
        encoding="utf-8",
    )
    result = write_gold_strategy_states(manifest, tmp_path / "states")
    assert result.point_in_time and not result.labels_included
    assert result.research_only and not result.real_money_enabled
