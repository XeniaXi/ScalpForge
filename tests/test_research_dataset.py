import json
from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from scalpforge_core.models import MarketTick
from scalpforge_strategy.research_dataset import (
    PointInTimeFeatureBuilder,
    WalkForwardConfig,
    anchored_walk_forward_folds,
    write_feature_dataset,
)


def tick(second: int, bid: float, *, milliseconds: int = 0) -> MarketTick:
    return MarketTick(
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC)
        + timedelta(seconds=second, milliseconds=milliseconds),
        bid=bid,
        ask=bid + 0.5,
        source="test",
    )


def test_features_are_point_in_time_and_aggregate_each_second() -> None:
    rows = list(
        PointInTimeFeatureBuilder().rows(
            [tick(0, 100), tick(0, 101, milliseconds=500), tick(1, 102), tick(5, 200)]
        )
    )
    assert len(rows) == 3
    assert rows[0]["tick_count"] == 2
    assert rows[0]["mid"] == 101.25
    assert rows[0]["bar_open_bid"] == 100
    assert rows[0]["bar_open_ask"] == 100.5
    assert rows[0]["bar_open_at"] == datetime(2026, 7, 1, tzinfo=UTC)
    assert rows[0]["feature_available_at"] == datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC)
    assert rows[1]["return_1s"] == 102.25 / 101.25 - 1
    assert rows[1]["return_5s"] is None
    assert rows[2]["is_gap_start"] is False


def test_future_tick_cannot_change_prior_feature_row() -> None:
    builder = PointInTimeFeatureBuilder()
    before = list(builder.rows([tick(0, 100), tick(1, 101)]))
    after = list(builder.rows([tick(0, 100), tick(1, 101), tick(2, 999)]))
    assert before == after[:2]


def test_walk_forward_folds_have_explicit_purge_and_embargo() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    folds = anchored_walk_forward_folds(
        start,
        start + timedelta(days=30),
        WalkForwardConfig(10, 3, 3, 3, 60, 120),
    )
    assert len(folds) == 5
    first = folds[0]
    assert first.validation_start - first.train_end_exclusive == timedelta(seconds=60)
    assert first.test_start - first.validation_end_exclusive == timedelta(seconds=120)
    assert folds[1].train_start == start
    assert folds[1].train_end_exclusive == start + timedelta(days=13)


def test_feature_dataset_is_content_addressed_and_has_no_labels(tmp_path) -> None:
    source = tmp_path / "source" / "manifest.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"dataset_id": "ticks-abc"}), encoding="utf-8")
    output = tmp_path / "features"
    first = write_feature_dataset([tick(0, 100), tick(1, 101)], source, output)
    second = write_feature_dataset([tick(0, 999)], source, output)

    assert first == second
    assert first.row_count == 2
    assert first.point_in_time
    assert not first.labels_included
    table = pq.read_table(first.partitions[0])
    assert table.num_rows == 2
    assert "return_1s" in table.column_names


def test_feature_writer_uses_small_batches_and_cleans_failed_staging(tmp_path) -> None:
    source = tmp_path / "source" / "manifest.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"dataset_id": "ticks-failure"}), encoding="utf-8")

    def broken_ticks():
        yield tick(0, 100)
        yield tick(1, 101)
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        write_feature_dataset(
            broken_ticks(),
            source,
            tmp_path / "features",
            write_batch_rows=1,
        )
    assert not list((tmp_path / "features").glob("*.partial"))
