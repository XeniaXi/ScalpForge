from datetime import UTC, datetime
from pathlib import Path

from scalpforge_collector import collect_once


def test_collects_hashes_and_reports_health(tmp_path: Path) -> None:
    source = tmp_path / "ticks.csv"
    source.write_text(
        "record_type,received_utc,server_time,monotonic_ms,session_id,source_sequence,broker,server,symbol,bid,ask,spread_points\n"
        "tick,2026.08.07 14:00:00,2026.08.07 17:00:00,10,s1,0,Ava,Ava-Demo,GOLD,2000,2000.2,20\n",
        encoding="utf-8",
    )
    result = collect_once(
        source, tmp_path / "archive", now=datetime(2026, 8, 7, 14, 0, 30, tzinfo=UTC)
    )
    assert result.status == "healthy"
    assert result.rows == 1
    assert result.sha256
    assert Path(result.snapshot or "").is_file()
    assert Path(result.snapshot or "").with_suffix(".manifest.json").is_file()


def test_missing_source_is_visible(tmp_path: Path) -> None:
    result = collect_once(tmp_path / "missing.csv", tmp_path / "archive")
    assert result.status == "missing"
    assert (tmp_path / "archive" / "health.latest.json").is_file()
