import json
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_data.trade_history import TradeHistoryCsvNormalizer


def test_trade_history_import_is_anonymized_and_research_only(tmp_path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "Ticket,Open Time,Type,Size,Symbol,Open Price,S / L,T / P,Close Time,"
        "Close Price,Commission,Swap,Profit,Magic Number,Comment\n"
        "1001,2026.08.01 10:00:00,buy,0.07,XAUUSD,2400.10,2395.10,2410.10,"
        "2026.08.01 10:03:00,2401.10,-0.20,0,6.80,42,copied\n",
        encoding="utf-8",
    )
    result = TradeHistoryCsvNormalizer().normalize(
        source,
        tmp_path / "normalized",
        source_system="mt4",
        source_role="copied_account",
        account_alias="friend-copy-a",
        broker="demo-broker",
        source_timezone="UTC",
        entry_origin="copier",
        exit_origin="manual",
    )
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["contains_credentials"] is False
    assert manifest["research_only"] is True
    assert manifest["external_non_executable"] is True
    table = pq.read_table(result.manifest.partitions[0])
    row = table.select(
        [
            "side",
            "volume_lots",
            "entry_origin",
            "exit_origin",
            "reported_net_profit",
        ]
    ).to_pylist()[0]
    assert row["side"] == "buy"
    assert row["volume_lots"] == 0.07
    assert row["entry_origin"] == "copier"
    assert row["exit_origin"] == "manual"
    assert row["reported_net_profit"] == 6.6


def test_duplicate_ticket_is_counted_and_not_duplicated(tmp_path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "ticket,open_time,type,lots,symbol,open_price\n"
        "1,2026-08-01 10:00:00,sell,0.01,GOLD,2400\n"
        "1,2026-08-01 10:00:00,sell,0.01,GOLD,2400\n",
        encoding="utf-8",
    )
    result = TradeHistoryCsvNormalizer().normalize(
        source,
        tmp_path / "normalized",
        source_system="mt4",
        source_role="unknown",
        account_alias="sample",
        broker="unknown",
        source_timezone="UTC",
    )
    assert result.manifest.row_count == 1
    assert result.manifest.duplicate_ticket_count == 1
