import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.structural import (
    StructuralConfig,
    structural_rows,
    write_structural_dataset,
)


def _features(prices: list[float]) -> pa.Table:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    return pa.Table.from_pydict(
        {
            "occurred_at": [start + timedelta(seconds=i) for i in range(len(prices))],
            "mid": prices,
            "tick_count": [2] * len(prices),
            "is_gap_start": [False] * len(prices),
        }
    )


def test_structural_levels_exclude_current_price() -> None:
    rows = structural_rows(
        _features([100.0, 101.0, 102.0]),
        StructuralConfig((2,), breakout_window_seconds=2, minimum_breakout_bps=0),
    )
    assert rows[0]["prior_high_2s"] is None
    assert rows[1]["prior_high_2s"] == 100.0
    assert rows[2]["prior_high_2s"] == 101.0
    assert rows[2]["breakout_side_2s"] == 1


def test_future_price_cannot_change_prior_structural_rows() -> None:
    config = StructuralConfig((2,), breakout_window_seconds=2)
    before = structural_rows(_features([100.0, 101.0]), config)
    after = structural_rows(_features([100.0, 101.0, 999.0]), config)
    assert before == after[:2]


def test_structural_artifact_is_research_only(tmp_path) -> None:
    folder = tmp_path / "features"
    folder.mkdir()
    partition = folder / "features.parquet"
    pq.write_table(_features([100.0, 101.0]), partition)
    manifest = folder / "manifest.json"
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
    result = write_structural_dataset(
        manifest,
        tmp_path / "structure",
        StructuralConfig((2,), breakout_window_seconds=2),
    )
    assert result.point_in_time
    assert not result.labels_included
    assert result.external_non_executable


def test_streamed_structure_preserves_windows_across_batches(tmp_path) -> None:
    folder = tmp_path / "features-streamed"
    folder.mkdir()
    source = _features([100.0, 101.0, 102.0, 99.0, 103.0, 98.0])
    partition = folder / "features.parquet"
    pq.write_table(source, partition, row_group_size=2)
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "features-streamed",
                "point_in_time": True,
                "labels_included": False,
                "partitions": [str(partition)],
            }
        ),
        encoding="utf-8",
    )
    config = StructuralConfig((2,), breakout_window_seconds=2)
    expected = structural_rows(source, config)
    result = write_structural_dataset(
        manifest, tmp_path / "structure-streamed", config, write_batch_rows=2
    )
    actual = pq.read_table(result.partitions[0])
    assert actual["occurred_at"].cast(pa.int64()).to_pylist() == source[
        "occurred_at"
    ].cast(pa.int64()).to_pylist()
    for name in actual.column_names:
        if name != "occurred_at":
            assert actual[name].to_pylist() == [row[name] for row in expected]
