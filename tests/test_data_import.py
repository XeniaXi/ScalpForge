from pathlib import Path

from scalpforge_data import TickCsvImporter


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
