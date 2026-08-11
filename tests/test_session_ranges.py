import json
from datetime import UTC, datetime

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.session_ranges import (
    CausalSessionRangeBuilder,
    SessionRangeConfig,
    write_session_range_dataset,
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 5, hour, minute, tzinfo=UTC)


def test_range_is_hidden_until_window_closes() -> None:
    builder = CausalSessionRangeBuilder(SessionRangeConfig())
    builder.row(_at(0), 100.0)
    inside = builder.row(_at(6, 59), 102.0)
    assert inside["asia_high"] is None
    assert inside["asia_breakout_side"] == 0
    complete = builder.row(_at(7), 103.0)
    assert complete["asia_high"] == 102.0
    assert complete["asia_low"] == 100.0
    assert complete["asia_available_at"] == _at(7)


def test_current_price_does_not_mutate_completed_range() -> None:
    builder = CausalSessionRangeBuilder()
    builder.row(_at(0), 100.0)
    builder.row(_at(6), 101.0)
    breakout = builder.row(_at(7), 105.0)
    assert breakout["asia_high"] == 101.0
    assert breakout["asia_breakout_side"] == 1


def test_ranges_reset_at_utc_day_boundary() -> None:
    builder = CausalSessionRangeBuilder()
    builder.row(_at(0), 100.0)
    assert builder.row(_at(7), 101.0)["asia_high"] == 100.0
    next_day = datetime(2026, 1, 6, 7, tzinfo=UTC)
    assert builder.row(next_day, 200.0)["asia_high"] is None


def test_streaming_dataset_is_causal_and_non_executable(tmp_path) -> None:
    source_file = tmp_path / "features.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"occurred_at": _at(0), "mid": 100.0},
                {"occurred_at": _at(6), "mid": 102.0},
                {"occurred_at": _at(7), "mid": 103.0},
            ]
        ),
        source_file,
    )
    source_manifest = tmp_path / "manifest.json"
    source_manifest.write_text(
        json.dumps(
            {
                "dataset_id": "features-test",
                "point_in_time": True,
                "labels_included": False,
                "partitions": [str(source_file)],
            }
        ),
        encoding="utf-8",
    )
    result = write_session_range_dataset(
        source_manifest, tmp_path / "output", batch_rows=1
    )
    rows = pq.read_table(result.partitions[0], columns=["asia_high"]).to_pylist()
    assert rows[1]["asia_high"] is None
    assert rows[2]["asia_high"] == 102.0
    assert result.point_in_time is True
    assert result.labels_included is False
    assert result.external_non_executable is True
