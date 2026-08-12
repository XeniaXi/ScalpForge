import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.multi_hour_outcomes import (
    MultiHourOutcomeConfig,
    build_multi_hour_outcomes,
    write_multi_hour_outcomes,
)


def _bars(prices: list[float], *, gap_at: int | None = None) -> pa.Table:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    times = [start + timedelta(seconds=300 * index) for index in range(len(prices))]
    gaps = [False] * len(prices)
    if gap_at is not None:
        gaps[gap_at] = True
    return pa.Table.from_pydict(
        {
            "occurred_at": [value + timedelta(seconds=299) for value in times],
            "feature_available_at": [value + timedelta(seconds=300) for value in times],
            "bar_open_at": times,
            "bar_open_bid": [value - 0.1 for value in prices],
            "bar_open_ask": [value + 0.1 for value in prices],
            "bar_high": [value + 0.5 for value in prices],
            "bar_low": [value - 0.5 for value in prices],
            "is_gap_start": gaps,
        }
    )


def test_outcomes_enter_next_bar_and_use_executable_sides() -> None:
    result, counts = build_multi_hour_outcomes(
        _bars([100.0, 101.0, 102.0, 103.0]),
        MultiHourOutcomeConfig((600,), (), slippage_bps_per_side=0),
    )
    assert counts == {"600": 1}
    assert result["h600_valid"].to_pylist() == [True, False, False, False]
    assert result["h600_entry_delay_seconds"][0].as_py() == 0
    assert result["h600_long_net_base_bps"][0].as_py() == (102.9 / 101.1 - 1) * 10_000
    assert result["h600_short_net_base_bps"][0].as_py() == (100.9 / 103.1 - 1) * 10_000
    assert result["h600_long_time_to_mfe_seconds"][0].as_py() == 600
    assert result["h600_long_time_to_mae_seconds"][0].as_py() == 0


def test_outcomes_reject_paths_crossing_a_gap() -> None:
    result, counts = build_multi_hour_outcomes(
        _bars([100.0, 101.0, 102.0, 103.0], gap_at=2),
        MultiHourOutcomeConfig((600,), (), slippage_bps_per_side=0),
    )
    assert counts == {"600": 0}
    assert result["h600_valid"].to_pylist() == [False] * 4


def test_outcome_artifact_is_physically_separate_and_non_executable(tmp_path) -> None:
    source = tmp_path / "features"
    source.mkdir()
    partition = source / "multi-hour.parquet"
    pq.write_table(_bars([100.0, 101.0, 102.0, 103.0]), partition)
    source_manifest = source / "manifest.json"
    source_manifest.write_text(
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
    manifest = write_multi_hour_outcomes(
        source_manifest,
        tmp_path / "outcomes",
        MultiHourOutcomeConfig((600,), (), slippage_bps_per_side=0),
    )
    assert manifest.future_information and manifest.physically_separate_from_features
    assert manifest.evaluation_role == "development_only"
    assert not manifest.holdout_eligible and not manifest.real_money_enabled
    assert any("swap" in limitation for limitation in manifest.limitations)
