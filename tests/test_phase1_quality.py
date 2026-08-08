import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
from scalpforge_collector.collector import collect_once
from scalpforge_collector.schedule import is_gold_market_open
from scalpforge_news.models import NormalizedEvent
from scalpforge_quality.dataset import build_parquet_dataset
from scalpforge_quality.news_report import build_news_quality_report
from scalpforge_quality.prune import prune_legacy_snapshots
from scalpforge_quality.tick_report import build_tick_quality_report

HEADER = (
    "record_type,received_utc,server_time,monotonic_ms,session_id,source_sequence,"
    "broker,server,symbol,bid,ask,spread_points\n"
)


def _archive_snapshot(root: Path, day: str, rows: list[str]) -> Path:
    folder = root / day.replace("-", "/")
    folder.mkdir(parents=True, exist_ok=True)
    source_name = f"scalpforge_GOLD_{day.replace('-', '')}_ticks.csv"
    snapshot = folder / f"{Path(source_name).stem}_120000_hash.csv"
    snapshot.write_text(HEADER + "".join(rows), encoding="utf-8")
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    snapshot.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "source": f"C:/Common/Files/{source_name}",
                "snapshot": str(snapshot),
                "rows": len(rows),
                "sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return snapshot


def _row(timestamp: str, session: str, sequence: int, bid: float = 2000) -> str:
    return (
        f"tick,{timestamp},{timestamp},10,{session},{sequence},Ava,Ava-Demo,GOLD,"
        f"{bid},{bid + 0.2},20\n"
    )


def test_gold_weekend_schedule() -> None:
    assert not is_gold_market_open(datetime(2026, 8, 8, 12, tzinfo=UTC))
    assert not is_gold_market_open(datetime(2026, 8, 9, 21, 59, tzinfo=UTC))
    assert is_gold_market_open(datetime(2026, 8, 9, 22, 0, tzinfo=UTC))


def test_collector_reports_healthy_closed_market(tmp_path: Path) -> None:
    source = tmp_path / "ticks.csv"
    heartbeat = (
        "heartbeat,2026.08.08 12:00:00,2026.08.08 12:00:00,10,s,0,"
        "Ava,Ava-Demo,GOLD,2000,2000.2,20\n"
    )
    source.write_text(
        HEADER + heartbeat,
        encoding="utf-8",
    )
    result = collect_once(
        source,
        tmp_path / "archive",
        now=datetime(2026, 8, 8, 12, 0, 5, tzinfo=UTC),
    )
    assert result.status == "market_closed_heartbeat_healthy"


def test_tick_report_and_parquet_dataset(tmp_path: Path) -> None:
    archive = tmp_path / "raw"
    _archive_snapshot(
        archive,
        "2026-08-06",
        [
            _row("2026.08.06 10:00:00", "s1", 0),
            _row("2026.08.06 10:00:01", "s1", 1, 2001),
        ],
    )
    _archive_snapshot(
        archive,
        "2026-08-07",
        [
            _row("2026.08.07 10:00:00", "s2", 0),
            _row("2026.08.07 10:00:01", "s2", 1, 2002),
        ],
    )
    report = build_tick_quality_report(archive)
    assert report.status == "collecting_under_24h"
    assert report.active_day_count == 2
    assert report.total_ticks == 4
    manifest = build_parquet_dataset(archive, tmp_path / "curated")
    assert manifest.row_count == 4
    assert len(manifest.partitions) == 2
    assert sum(pq.read_table(path).num_rows for path in manifest.partitions) == 4


def test_news_quality_report(tmp_path: Path) -> None:
    events_path = tmp_path / "normalized" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    event = NormalizedEvent(
        event_key="gdelt:1",
        source="example.test",
        event_type="world_news",
        title="Gold and Treasury yields",
        occurred_at=datetime(2026, 8, 7, tzinfo=UTC),
        published_at=datetime(2026, 8, 7, tzinfo=UTC),
        relevance_score=0.68,
        relevance_reasons=["gold", "usd_yields"],
        timing_quality="gdelt_seen_time",
    )
    events_path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    (events_path.parent / "health.latest.json").write_text('{"status":"healthy"}', encoding="utf-8")
    report = build_news_quality_report(events_path, tmp_path / "raw")
    assert report.unique_event_count == 1
    assert report.relevance_categories == {"gold": 1, "usd_yields": 1}
    assert report.current_health == "healthy"


def test_legacy_prune_is_dry_run_and_preserves_chunks(tmp_path: Path) -> None:
    archive = tmp_path / "raw"
    folder = archive / "2026" / "08" / "07"
    folder.mkdir(parents=True)
    source = "C:/Common/Files/scalpforge_GOLD_20260807_ticks.csv"
    legacy_paths = []
    for index, row_count in enumerate((10, 20, 30)):
        snapshot = folder / f"legacy_{index}.csv"
        snapshot.write_text(HEADER + _row("2026.08.07 10:00:00", "s", index), encoding="utf-8")
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        snapshot.with_suffix(".manifest.json").write_text(
            json.dumps(
                {"source": source, "snapshot": str(snapshot), "rows": row_count, "sha256": digest}
            ),
            encoding="utf-8",
        )
        legacy_paths.append(snapshot)
    chunk = folder / "ticks_chunk_120000_hash.csv"
    chunk.write_text(HEADER + _row("2026.08.07 10:00:01", "s", 4), encoding="utf-8")
    chunk.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "format": "incremental_chunk_v1",
                "source": source,
                "snapshot": str(chunk),
                "rows": 1,
                "sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    dry_run = prune_legacy_snapshots(archive)
    assert dry_run.redundant_snapshots == 2
    assert all(path.exists() for path in legacy_paths)
    applied = prune_legacy_snapshots(archive, apply=True)
    assert applied.deleted_files == 4
    assert legacy_paths[-1].exists()
    assert chunk.exists()
