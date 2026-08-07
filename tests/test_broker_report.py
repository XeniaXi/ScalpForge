from pathlib import Path

from scalpforge_broker.report import build_feed_report


def test_mt4_feed_report_preserves_same_second_ticks(tmp_path: Path) -> None:
    source = tmp_path / "broker_ticks.csv"
    rows = [
        "record_type,received_utc,server_time,monotonic_ms,broker,server,symbol,bid,ask,spread_points,source_sequence",
        "tick,2026.01.01 12:00:00,2026.01.01 14:00:00,1000,"
        "Demo Broker,Demo-1,XAUUSD,2400.00,2400.10,10,1",
        "tick,2026.01.01 12:00:00,2026.01.01 14:00:00,1001,"
        "Demo Broker,Demo-1,XAUUSD,2400.01,2400.11,10,2",
        "heartbeat,2026.01.01 12:00:10,2026.01.01 14:00:10,1100,"
        "Demo Broker,Demo-1,XAUUSD,2400.01,2400.11,10,3",
    ]
    source.write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    report = build_feed_report(source)
    assert report.tick_count == 2
    assert report.duplicate_count == 0
    assert report.broker == "Demo Broker"
    assert report.median_spread_bps is not None


def test_feed_report_detects_gap(tmp_path: Path) -> None:
    source = tmp_path / "broker_ticks.csv"
    source.write_text(
        "record_type,received_utc,server_time,monotonic_ms,broker,server,symbol,bid,ask,spread_points\n"
        "tick,2026.01.01 12:00:00,2026.01.01 14:00:00,1000,A,S,XAUUSD,2400,2400.1,10\n"
        "tick,2026.01.01 12:01:00,2026.01.01 14:01:00,2000,A,S,XAUUSD,2401,2401.1,10\n",
        encoding="utf-8",
    )
    report = build_feed_report(source, gap_threshold_seconds=30)
    assert report.gap_count == 1
    assert report.maximum_gap_seconds == 60
