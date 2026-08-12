from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scalpforge_broker.copy_trader import (
    audit_copy_history,
    normalize_mql5_history,
    read_copy_trades,
)

HEADER = "provider_id,opened_at,closed_at,symbol,side,volume,net_profit\n"


def test_copy_trader_audit_stays_paper_only(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    start = datetime(2023, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(240):
        opened = start + timedelta(days=index * 4)
        closed = opened + timedelta(hours=1)
        pnl = 12 if index % 3 else -5
        rows.append(
            f"alpha,{opened.isoformat()},{closed.isoformat()},XAUUSD,buy,0.01,{pnl}"
        )
    source.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    report = audit_copy_history(source, starting_equity=10_000)
    assert report["paper_shadow_eligible"] is True
    assert report["real_money_enabled"] is False
    assert report["metrics"]["closed_trades"] == 240


def test_copy_trader_flags_loss_size_escalation(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        HEADER
        + "p,2024-01-01T00:00:00Z,2024-01-01T01:00:00Z,GOLD,buy,1,-10\n"
        + "p,2024-01-02T00:00:00Z,2024-01-02T01:00:00Z,GOLD,buy,2,12\n",
        encoding="utf-8",
    )
    report = audit_copy_history(source, starting_equity=1000)
    assert report["checks"]["loss_size_escalation"] is False
    assert report["paper_shadow_eligible"] is False


def test_copy_trader_rejects_naive_timestamps(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        HEADER + "p,2024-01-01T00:00:00,2024-01-01T01:00:00,GOLD,buy,1,10\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="UTC offset"):
        read_copy_trades(source)


def test_normalize_mql5_history_handles_duplicate_headers_and_balance(tmp_path: Path) -> None:
    source = tmp_path / "mql5.csv"
    destination = tmp_path / "normalized.csv"
    source.write_text(
        "Time;Type;Volume;Symbol;Price;Volume;Time;Price;Commission;Swap;Profit\n"
        "2026.08.10 19:01:45;Buy;0.01;XAUUSD;4360.54;0.01;"
        "2026.08.10 19:02:58;4362.64;-0.08;;2.1\n"
        "2026.03.02 00:53:36;Balance;;;;;;;;;100\n",
        encoding="utf-8",
    )
    result = normalize_mql5_history(
        source,
        destination,
        provider_id="mql5-signal-1",
        source_utc_offset_hours=2,
    )
    trades = read_copy_trades(destination)
    assert result["closed_trades"] == 1
    assert result["skipped_non_trade_rows"] == 1
    assert trades[0].opened_at.hour == 17
    assert trades[0].net_profit == pytest.approx(2.02)


def test_normalize_detailed_mql5_history_skips_pending_orders(tmp_path: Path) -> None:
    source = tmp_path / "mql5-detailed.csv"
    destination = tmp_path / "normalized.csv"
    source.write_text(
        "Time;Type;Volume;Symbol;Price;S/L;T/P;Time;Price;Commission;Swap;Profit;Comment\n"
        "2026.08.10 01:00:01;Sell Stop;0.01;XAUUSD;4 020.83;4 107.22;4 006.16;"
        "2026.08.11 07:15:00;4 413.92;;;;cancelled\n"
        "2026.08.10 21:31:23;Buy;0.01;XAUUSD;4 372.69;4 385.52;4 394.49;"
        "2026.08.10 22:35:42;4 385.29;-0.02;;12.60;[sl]\n",
        encoding="utf-8",
    )
    result = normalize_mql5_history(
        source,
        destination,
        provider_id="detailed",
        source_utc_offset_hours=0,
    )
    trades = read_copy_trades(destination)
    assert result["closed_trades"] == 1
    assert result["skipped_non_trade_rows"] == 1
    assert trades[0].net_profit == pytest.approx(12.58)
