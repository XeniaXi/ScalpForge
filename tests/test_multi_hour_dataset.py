import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.multi_hour_dataset import (
    MultiHourConfig,
    multi_hour_rows,
    write_multi_hour_dataset,
)


def _features(prices: list[float]) -> pa.Table:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return pa.Table.from_pydict(
        {
            "occurred_at": [start + timedelta(seconds=300 * i) for i in range(len(prices))],
            "bid": [p - 0.1 for p in prices],
            "ask": [p + 0.1 for p in prices],
            "mid": prices,
            "spread_bps": [2.0] * len(prices),
            "tick_count": [10] * len(prices),
            "quote_change_count": [5] * len(prices),
            "is_gap_start": [False] * len(prices),
            "seconds_since_previous_active_bar": [None] + [1] * (len(prices) - 1),
        }
    )


def _config() -> MultiHourConfig:
    return MultiHourConfig(300, (600, 900), (600, 900), 300, 600, 60)


def test_prior_boundary_excludes_current_bar() -> None:
    rows = multi_hour_rows(_features([100.0, 101.0, 105.0]), _config())
    assert rows[0]["volatility_expansion_ratio"] is None
    assert rows[0]["bar_open_bid"] == 99.9
    assert rows[0]["bar_open_ask"] == 100.1
    assert rows[2]["prior_high_600s"] == 101.0
    assert rows[2]["breakout_side_600s"] == 1


def test_future_quote_cannot_change_prior_rows() -> None:
    before = multi_hour_rows(_features([100.0, 101.0]), _config())
    after = multi_hour_rows(_features([100.0, 101.0, 999.0]), _config())
    assert before == after[:2]


def test_volatility_expansion_compares_normalized_windows() -> None:
    config = MultiHourConfig(300, (600,), (600,), 600, 1200, 60)
    prices = [100.0, 100.0, 100.0, 100.0, 102.0]
    rows = multi_hour_rows(_features(prices), config)
    assert rows[-1]["volatility_expansion_ratio"] > 1.0


def test_short_quote_silence_is_provenance_not_a_broken_bar() -> None:
    features = _features([100.0, 101.0])
    features = features.set_column(
        features.schema.get_field_index("seconds_since_previous_active_bar"),
        "seconds_since_previous_active_bar",
        pa.array([None, 7.0]),
    )
    rows = multi_hour_rows(features, _config())
    assert rows[1]["maximum_quote_silence_seconds"] == 7.0
    assert rows[1]["bar_complete"] is True
    assert rows[1]["is_gap_start"] is False


def test_long_open_quote_silence_breaks_bar_without_synthesis() -> None:
    features = _features([100.0, 101.0])
    features = features.set_column(
        features.schema.get_field_index("seconds_since_previous_active_bar"),
        "seconds_since_previous_active_bar",
        pa.array([None, 61.0]),
    )
    rows = multi_hour_rows(features, _config())
    assert rows[1]["underlying_observation_count"] == 1
    assert rows[1]["bar_complete"] is False
    assert rows[1]["is_gap_start"] is True


def test_manifest_is_label_free_and_development_only(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    partition = source / "features.parquet"
    pq.write_table(_features([100.0, 101.0, 102.0]), partition)
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "features-1",
                "point_in_time": True,
                "labels_included": False,
                "partitions": [str(partition)],
            }
        ),
        encoding="utf-8",
    )
    result = write_multi_hour_dataset(manifest, tmp_path / "output", _config())
    assert result.point_in_time and not result.labels_included
    assert result.evaluation_role == "development_only"
    assert not result.holdout_eligible and result.external_non_executable
