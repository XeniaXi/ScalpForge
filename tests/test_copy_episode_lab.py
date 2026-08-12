from pathlib import Path

import pytest
from scalpforge_broker.copy_episode_lab import run_episode_audit


def test_episode_audit_adjusts_cash_flows_and_groups_overlap(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "Time;Type;Volume;Symbol;Price;Volume;Time;Price;Commission;Swap;Profit\n"
        "2026.01.01 00:00:00;Balance;;;;;;;;;1000\n"
        "2026.01.02 00:00:00;Buy;0.01;XAUUSD;100;0.01;"
        "2026.01.02 00:10:00;101;-1;;10\n"
        "2026.01.02 00:05:00;Buy;0.02;XAUUSD;100;0.02;"
        "2026.01.02 00:15:00;102;-1;;20\n"
        "2026.01.03 00:00:00;Balance;;;;;;;;;-500\n"
        "2026.01.04 00:00:00;Sell;0.01;XAUUSD;100;0.01;"
        "2026.01.04 00:01:00;101;;;-10\n",
        encoding="utf-8",
    )
    report = run_episode_audit(source, provider_id="provider", source_utc_offset_hours=0)
    metrics = report["metrics"]
    assert metrics["closed_trades"] == 3
    assert metrics["episode_count"] == 2
    assert metrics["maximum_concurrent_volume"] == pytest.approx(0.03)
    assert metrics["maximum_concurrent_tickets"] == 2
    assert report["cash_flows"]["net"] == 500
    assert metrics["ticket_net_profit"] == 18
    assert metrics["reconstructed_ending_closed_balance"] == 518
    assert report["paper_shadow_eligible"] is False


def test_episode_audit_skips_pending_orders(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_text(
        "Time;Type;Volume;Symbol;Price;S/L;T/P;Time;Price;Commission;Swap;Profit;Comment\n"
        "2026.01.01 00:00:00;Balance;;;;;;;;;;1000;\n"
        "2026.01.02 00:00:00;Buy Stop;0.01;XAUUSD;100;99;101;"
        "2026.01.02 00:05:00;100;;;;cancelled\n"
        "2026.01.03 00:00:00;Buy;0.01;XAUUSD;100;99;101;"
        "2026.01.03 00:01:00;101;-1;;10;[tp]\n",
        encoding="utf-8",
    )
    report = run_episode_audit(source, provider_id="provider", source_utc_offset_hours=0)
    assert report["metrics"]["closed_trades"] == 1
    assert report["metrics"]["skipped_pending_orders"] == 1
