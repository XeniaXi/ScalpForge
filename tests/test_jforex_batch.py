import csv
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from scalpforge_data import ingest_jforex_batches


def _batch(root: Path, hour: int, rows: list[tuple[str, float, float]]) -> None:
    start = f"2026-08-03T{hour:02d}:00:00.000Z"
    end = f"2026-08-03T{hour + 1:02d}:00:00.000Z"
    stem = f"scalpforge_jforex_XAUUSD_20260803_{hour:02d}0000_20260803_{hour + 1:02d}0000"
    csv_path = root / f"{stem}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["occurred_at", "bid", "ask", "bid_volume", "ask_volume", "source_sequence"]
        )
        for sequence, row in enumerate(rows, start=1):
            writer.writerow([*row, 0.00012, 0.00012, sequence])
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "provider": "dukascopy",
        "venue": "SWFX",
        "instrument": "XAUUSD",
        "source": "jforex-IHistory.getTicks",
        "start_utc": start,
        "end_utc_exclusive": end,
        "rows": len(rows),
        "csv": csv_path.name,
        "sha256": digest,
        "read_only": True,
        "external_non_executable": True,
    }
    (root / f"{stem}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_ingests_valid_and_empty_batches_as_one_dataset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _batch(source, 0, [("2026-08-03T00:00:00.058Z", 4000.0, 4000.5)])
    _batch(source, 1, [])
    result = ingest_jforex_batches(source, tmp_path / "archive", tmp_path / "curated")
    assert result.batch_count == 2
    assert result.empty_batch_count == 1
    assert result.row_count == 1
    assert result.volume_unit == "jforex_native_unknown"
    table = pq.read_table(result.partitions[0])
    assert table.column("external_non_executable")[0].as_py() is True
    assert len(result.archived_manifests) == 2


def test_refuses_checksum_mismatch_before_creating_dataset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _batch(source, 0, [("2026-08-03T00:00:00.058Z", 4000.0, 4000.5)])
    csv_path = next(source.glob("*.csv"))
    csv_path.write_text(csv_path.read_text(encoding="utf-8") + "broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        ingest_jforex_batches(source, tmp_path / "archive", tmp_path / "curated")
    assert not (tmp_path / "curated").exists()
