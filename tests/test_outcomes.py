import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.outcomes import (
    OutcomeConfig,
    build_outcome_columns,
    write_outcome_dataset,
)


def feature_table(seconds: list[int], bids: list[float]) -> pa.Table:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    occurred = [start + timedelta(seconds=value) for value in seconds]
    return pa.Table.from_pydict(
        {
            "occurred_at": occurred,
            "feature_available_at": [value + timedelta(seconds=1) for value in occurred],
            "bar_open_at": [value + timedelta(milliseconds=100) for value in occurred],
            "bar_open_bid": bids,
            "bar_open_ask": [value + 0.5 for value in bids],
        }
    )


def test_outcomes_use_executable_sides_and_future_path_extrema() -> None:
    columns, counts = build_outcome_columns(
        feature_table([0, 1, 2, 3, 4], [100, 101, 102, 103, 104]),
        OutcomeConfig((2,), slippage_bps_per_side=0, maximum_endpoint_delay_seconds=0),
    )
    assert counts == {"2": 2}
    assert columns["h2_valid"] == [True, True, False, False, False]
    assert columns["h2_long_net_bps"][0] == (103 / 101.5 - 1) * 10_000
    assert columns["h2_short_net_bps"][0] == (101 / 103.5 - 1) * 10_000
    assert columns["h2_long_mfe_bps"][0] == (103 / 101.5 - 1) * 10_000
    assert columns["h2_long_mae_bps"][0] == (101 / 101.5 - 1) * 10_000
    assert columns["h2_entry_delay_seconds"][0] == 0.05
    assert columns["h2_long_gross_bps"][0] == (103.25 / 101.25 - 1) * 10_000
    assert columns["h2_short_gross_bps"][0] == (101.25 / 103.25 - 1) * 10_000


def test_outcome_is_invalid_when_horizon_crosses_a_market_gap() -> None:
    columns, _ = build_outcome_columns(
        feature_table([0, 1, 10, 11], [100, 101, 102, 103]),
        OutcomeConfig(
            (5,),
            slippage_bps_per_side=0,
            maximum_endpoint_delay_seconds=10,
            maximum_continuity_gap_seconds=5,
        ),
    )
    assert columns["h5_valid"] == [False, False, False, False]


def test_outcomes_are_separate_content_addressed_artifact(tmp_path) -> None:
    features = tmp_path / "features"
    features.mkdir()
    partition = features / "features.parquet"
    pq.write_table(feature_table([0, 1, 2], [100, 101, 102]), partition)
    manifest = features / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "features-abc",
                "point_in_time": True,
                "labels_included": False,
                "partitions": [str(partition)],
            }
        ),
        encoding="utf-8",
    )
    first = write_outcome_dataset(
        manifest,
        tmp_path / "outcomes",
        OutcomeConfig((1,), slippage_bps_per_side=0),
        write_batch_rows=1,
    )
    second = write_outcome_dataset(
        manifest,
        tmp_path / "outcomes",
        OutcomeConfig((1,), slippage_bps_per_side=0),
    )
    assert first == second
    assert first.future_information
    assert first.external_non_executable
    assert first.row_count == 3
    assert first.horizon_partitions["1"].endswith("horizon=1\\outcomes.parquet")
    assert len(first.partitions) == 1


def test_streamed_outcomes_match_in_memory_columns(tmp_path) -> None:
    features = tmp_path / "features-streamed"
    features.mkdir()
    source = feature_table(list(range(12)), [100 + value for value in range(12)])
    partition = features / "features.parquet"
    pq.write_table(source, partition, row_group_size=3)
    manifest = features / "manifest.json"
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
    config = OutcomeConfig((2,), slippage_bps_per_side=0)
    expected, _ = build_outcome_columns(source, config)
    result = write_outcome_dataset(
        manifest, tmp_path / "outcomes-streamed", config, write_batch_rows=3
    )
    actual_table = pq.read_table(result.horizon_partitions["2"])
    assert actual_table["occurred_at"].cast(pa.int64()).to_pylist() == source[
        "occurred_at"
    ].cast(pa.int64()).to_pylist()
    for name, values in expected.items():
        if name != "occurred_at":
            assert actual_table[name].to_pylist() == values
