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
    return pa.Table.from_pydict(
        {
            "occurred_at": [start + timedelta(seconds=value) for value in seconds],
            "bid": bids,
            "ask": [value + 0.5 for value in bids],
        }
    )


def test_outcomes_use_executable_sides_and_future_path_extrema() -> None:
    columns, counts = build_outcome_columns(
        feature_table([0, 1, 2, 3], [100, 101, 102, 103]),
        OutcomeConfig((2,), slippage_bps_per_side=0, maximum_endpoint_delay_seconds=0),
    )
    assert counts == {"2": 2}
    assert columns["h2_valid"] == [True, True, False, False]
    assert columns["h2_long_net_bps"][0] == (102 / 100.5 - 1) * 10_000
    assert columns["h2_short_net_bps"][0] == (100 / 102.5 - 1) * 10_000
    assert columns["h2_long_mfe_bps"][0] == (102 / 100.5 - 1) * 10_000
    assert columns["h2_long_mae_bps"][0] == (101 / 100.5 - 1) * 10_000


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
