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
        }
    )


def _config() -> MultiHourConfig:
    return MultiHourConfig(300, (600, 900), (600, 900), 300, 600)


def test_prior_boundary_excludes_current_bar() -> None:
    rows = multi_hour_rows(_features([100.0, 101.0, 105.0]), _config())
    assert rows[2]["prior_high_600s"] == 101.0
    assert rows[2]["breakout_side_600s"] == 1


def test_future_quote_cannot_change_prior_rows() -> None:
    before = multi_hour_rows(_features([100.0, 101.0]), _config())
    after = multi_hour_rows(_features([100.0, 101.0, 999.0]), _config())
    assert before == after[:2]


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
