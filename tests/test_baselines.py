import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_strategy.baselines import run_baselines


def test_baselines_use_walk_forward_tests_and_leave_holdout_untouched(tmp_path) -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    count = 34 * 24 * 60
    timestamps = [start + timedelta(minutes=index) for index in range(count)]
    momentum = [0.001 if index % 2 else -0.001 for index in range(count)]
    features = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "spread_bps": [1.0] * count,
            "return_30s": momentum,
            "return_5s": momentum,
            "realized_volatility_60s": [0.0001] * count,
            "tick_intensity_ratio": [2.0] * count,
            "spread_shock_ratio": [1.0] * count,
            "is_gap_start": [False] * count,
        }
    )
    long_returns = [2.0 if value > 0 else -2.0 for value in momentum]
    short_returns = [-value for value in long_returns]
    outcomes = pa.Table.from_pydict(
        {
            "occurred_at": timestamps,
            "h60_valid": [True] * count,
            "h60_long_net_bps": long_returns,
            "h60_short_net_bps": short_returns,
        }
    )
    feature_manifest = _dataset(tmp_path, "features", features, {"dataset_id": "features-1"})
    outcome_manifest = _dataset(
        tmp_path,
        "outcomes",
        outcomes,
        {
            "dataset_id": "outcomes-1",
            "source_feature_dataset_id": "features-1",
            "valid_counts": {"60": count},
        },
    )
    report = run_baselines(feature_manifest, outcome_manifest, tmp_path / "reports")
    metrics = {item.strategy_id: item for item in report.metrics}
    assert not report.holdout_evaluated
    assert report.final_holdout_start.startswith("2026-07-31")
    assert metrics["always_abstain"].trade_count == 0
    assert metrics["simple_momentum"].mean_net_bps == 2.0
    assert metrics["simple_mean_reversion"].mean_net_bps == -2.0
    assert metrics["simple_momentum"].fold_count >= 4


def _dataset(
    root,
    name: str,
    table: pa.Table,
    metadata: dict[str, object],
):
    folder = root / name
    folder.mkdir()
    partition = folder / f"{name}.parquet"
    pq.write_table(table, partition)
    manifest = folder / "manifest.json"
    metadata = {**metadata, "partitions": [str(partition)]}
    manifest.write_text(json.dumps(metadata), encoding="utf-8")
    return manifest
