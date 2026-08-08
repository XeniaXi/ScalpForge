import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from scalpforge_data import HistoricalCsvNormalizer, TickCsvImporter
from scalpforge_replay import ParquetTickReplaySource


def test_import_creates_content_addressed_manifest(tmp_path: Path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(
        "occurred_at,bid,ask\n"
        "2026-01-01T12:00:00Z,2400.00,2400.10\n"
        "2026-01-01T12:00:01Z,2400.10,2400.20\n",
        encoding="utf-8",
    )
    first = TickCsvImporter().load(source, source="fixture")
    second = TickCsvImporter().load(source, source="fixture")
    assert first.is_usable
    assert first.manifest.dataset_id == second.manifest.dataset_id
    assert first.manifest.row_count == 2


def test_import_rejects_out_of_order_ticks(tmp_path: Path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(
        "occurred_at,bid,ask\n"
        "2026-01-01T12:00:01Z,2400.00,2400.10\n"
        "2026-01-01T12:00:00Z,2400.10,2400.20\n",
        encoding="utf-8",
    )
    result = TickCsvImporter().load(source, source="fixture")
    assert not result.is_usable
    assert any(issue.code == "out_of_order" for issue in result.issues)


def test_import_accepts_mt4_style_headers_and_semicolons(tmp_path: Path) -> None:
    source = tmp_path / "mt4.csv"
    source.write_text(
        "DATE;TIME;BID;ASK\n"
        "2026.01.01;12:00:00+0000;2400.00;2400.10\n",
        encoding="utf-8",
    )
    result = TickCsvImporter().load(source, source="mt4-export")
    assert result.is_usable
    assert result.ticks[0].occurred_at.isoformat() == "2026-01-01T12:00:00+00:00"


def test_historical_normalizer_partitions_and_records_provenance(tmp_path: Path) -> None:
    source = tmp_path / "provider.csv"
    source.write_text(
        "date,time,bid,ask\n"
        "2026.01.01,23:59:59,2400.00,2400.10\n"
        "2026.01.02,00:00:01,2400.10,2400.20\n",
        encoding="utf-8",
    )
    result = HistoricalCsvNormalizer().normalize(
        source,
        tmp_path / "curated",
        provider="fixture-provider",
        venue="fixture-venue",
        source_timezone="UTC",
    )
    assert result.manifest.row_count == 2
    assert result.manifest.external_non_executable
    assert len(result.manifest.partitions) == 2
    table = pq.read_table(result.manifest.partitions[0])
    assert table.column("provider")[0].as_py() == "fixture-provider"
    assert table.column("external_non_executable")[0].as_py() is True
    stored = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert stored["timezone_assumption"] == "UTC"
    events = list(ParquetTickReplaySource(Path(result.manifest_path)).events())
    assert [event.payload.source for event in events] == [
        "fixture-provider:fixture-venue",
        "fixture-provider:fixture-venue",
    ]
    assert [event.sequence for event in events] == [1, 2]


def test_historical_normalizer_requires_ordered_ticks(tmp_path: Path) -> None:
    source = tmp_path / "unordered.csv"
    source.write_text(
        "timestamp,bid,ask\n"
        "2026-01-01T00:00:02Z,2400.00,2400.10\n"
        "2026-01-01T00:00:01Z,2400.00,2400.10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not ordered"):
        HistoricalCsvNormalizer().normalize(
            source,
            tmp_path / "curated",
            provider="fixture",
            venue="fixture",
            source_timezone="UTC",
        )
    assert not (tmp_path / "curated").exists()
