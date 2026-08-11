import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.session_episodes import write_session_episode_dataset


def _write_dataset(root, rows, manifest):
    root.mkdir()
    partition = root / "data.parquet"
    pq.write_table(pa.Table.from_pylist(rows), partition)
    payload = {**manifest, "partitions": [str(partition)]}
    path = root / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, partition


def test_episode_features_and_future_labels_are_physically_separate(tmp_path) -> None:
    start = datetime(2026, 1, 5, 7, tzinfo=UTC)
    timestamps = [start, start + timedelta(seconds=1)]
    feature_rows = [
        {
            "occurred_at": timestamp,
            "spread_bps": 1.0,
            "spread_shock_ratio": 1.0,
            "tick_intensity_ratio": 2.0,
            "realized_volatility_60s": 0.001,
            "return_5s": 0.001,
            "return_30s": 0.002,
            "return_60s": 0.003,
        }
        for timestamp in timestamps
    ]
    feature_manifest, _ = _write_dataset(
        tmp_path / "features",
        feature_rows,
        {"dataset_id": "features", "point_in_time": True, "labels_included": False},
    )
    session_rows = [
        {
            "occurred_at": timestamps[0],
            "session_day_utc": "2026-01-05",
            "asia_width_bps": 10.0,
            "asia_breakout_side": 0,
            "asia_breakout_distance_bps": 0.0,
        },
        {
            "occurred_at": timestamps[1],
            "session_day_utc": "2026-01-05",
            "asia_width_bps": 10.0,
            "asia_breakout_side": 1,
            "asia_breakout_distance_bps": 0.5,
        },
    ]
    session_manifest, _ = _write_dataset(
        tmp_path / "sessions",
        session_rows,
        {
            "dataset_id": "sessions",
            "source_feature_dataset_id": "features",
            "point_in_time": True,
            "labels_included": False,
            "session_config": {"windows": [{"name": "asia"}]},
        },
    )
    structure_rows = [
        {
            "occurred_at": timestamp,
            "distance_from_tick_vwap_bps": 1.0,
            "compression_60_to_300": 0.5,
        }
        for timestamp in timestamps
    ]
    structure_manifest, _ = _write_dataset(
        tmp_path / "structure",
        structure_rows,
        {
            "dataset_id": "structure",
            "source_feature_dataset_id": "features",
            "point_in_time": True,
            "labels_included": False,
        },
    )
    outcome_rows = [
        {
            "occurred_at": timestamp,
            "h300_valid": True,
            "h300_long_gross_bps": 5.0,
            "h300_short_gross_bps": -5.0,
            "h300_long_net_bps": 2.0,
            "h300_short_net_bps": -8.0,
            "h300_long_mfe_bps": 7.0,
            "h300_short_mfe_bps": 1.0,
            "h300_long_mae_bps": -1.0,
            "h300_short_mae_bps": -7.0,
        }
        for timestamp in timestamps
    ]
    outcome_manifest, outcome_partition = _write_dataset(
        tmp_path / "outcomes",
        outcome_rows,
        {"dataset_id": "outcomes", "source_feature_dataset_id": "features"},
    )
    outcome_payload = json.loads(outcome_manifest.read_text(encoding="utf-8"))
    outcome_payload["horizon_partitions"] = {"300": str(outcome_partition)}
    outcome_manifest.write_text(json.dumps(outcome_payload), encoding="utf-8")

    result = write_session_episode_dataset(
        feature_manifest,
        session_manifest,
        structure_manifest,
        outcome_manifest,
        tmp_path / "episode-output",
        batch_size=1,
    )
    features = pq.read_table(result.feature_partition)
    labels = pq.read_table(result.label_partition)
    assert result.row_count == 1
    assert "net_bps" not in features.column_names
    assert "spread_bps" not in labels.column_names
    assert labels["net_bps"][0].as_py() == 2.0
    assert result.labels_physically_separate is True
