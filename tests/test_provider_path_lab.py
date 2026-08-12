import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from scalpforge_broker.provider_path_lab import run_provider_path_lab


def test_provider_path_lab_uses_executable_sides(tmp_path: Path) -> None:
    history = tmp_path / "history.csv"
    history.write_text(
        "Time;Type;Volume;Symbol;Price;Volume;Time;Price;Commission;Swap;Profit\n"
        "2026.01.02 00:00:00;Buy;0.01;XAUUSD;100.1;0.01;"
        "2026.01.02 00:00:02;100.3;;;2\n",
        encoding="utf-8",
    )
    root = tmp_path / "quotes"
    partition = root / "year=2026" / "month=01" / "day=02" / "ticks.parquet"
    partition.parent.mkdir(parents=True)
    start = datetime(2026, 1, 2, tzinfo=UTC)
    pq.write_table(
        pa.table(
            {
                "occurred_at": [start + timedelta(seconds=i) for i in range(3)],
                "bid": [100.0, 99.8, 100.3],
                "ask": [100.1, 99.9, 100.4],
            }
        ),
        partition,
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "quotes-test",
                "provider": "test",
                "instrument": "XAUUSD",
                "start_utc": start.isoformat(),
                "end_utc_exclusive": (start + timedelta(days=1)).isoformat(),
                "partitions": [str(partition)],
            }
        ),
        encoding="utf-8",
    )
    report = run_provider_path_lab(
        history,
        manifest,
        provider_id="provider",
        source_utc_offset_hours=0,
    )
    assert report["coverage"]["analyzed_trades"] == 1
    assert report["path_metrics"]["worst_mae_bps"] < 0
    assert report["path_metrics"]["median_mfe_bps"] > 0
    assert report["real_money_enabled"] is False
