from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scalpforge_broker.copy_trader import audit_copy_history, read_copy_trades

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
