import hashlib
import json
from datetime import UTC, datetime, timedelta

from scalpforge_strategy.demo_shadow_evaluator import evaluate_demo_shadow


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_evaluator_excludes_invalidated_trade_and_reports_collecting(tmp_path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("evidence", encoding="utf-8")
    ledgers = {}
    for name in ("signals", "fills", "health", "events", "weekly-evaluations"):
        path = tmp_path / f"{name}.jsonl"
        path.write_text("", encoding="utf-8")
        ledgers[name] = str(path)
    protocol = {
        "protocol_id": "demo-shadow-test",
        "protocol_hash": "frozen",
        "created_at": datetime.now(UTC).isoformat(),
        "frozen_specification": {"candidate_id": "trend_continuation_1h_v1"},
        "acceptance_gates": {
            "minimum_calendar_weeks": 12,
            "minimum_closed_trades": 150,
            "minimum_active_days": 45,
            "mean_net_bps_above_zero": True,
            "minimum_profit_factor": 1.1,
            "minimum_execution_coverage_ratio": 0.98,
            "maximum_drawdown_pct": 6.0,
            "maximum_best_day_positive_profit_share": 0.2,
            "maximum_top_five_days_positive_profit_share": 0.5,
            "both_directions_nonnegative": True,
            "first_and_second_halves_nonnegative": True,
            "unresolved_data_or_execution_failures": 0,
        },
        "evidence": {
            "one": {
                "path": str(evidence),
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
        },
        "ledgers": ledgers,
        "order_submission_enabled": False,
        "real_money_enabled": False,
        "holdout_evaluated": False,
    }
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    now = datetime.now(UTC)
    _write_jsonl(
        tmp_path / "signals.jsonl",
        [
            {"signal_id": "keep", "disposition": "hypothetical_entry"},
            {"signal_id": "drop", "disposition": "hypothetical_entry"},
        ],
    )
    _write_jsonl(
        tmp_path / "fills.jsonl",
        [
            {
                "event": "hypothetical_entry",
                "signal_id": "keep",
                "side": 1,
                "occurred_at": now.isoformat(),
            },
            {
                "event": "hypothetical_exit",
                "signal_id": "keep",
                "gross_bps": 4.0,
                "occurred_at": (now + timedelta(hours=1)).isoformat(),
                "boundary_valid": True,
            },
            {
                "event": "hypothetical_entry",
                "signal_id": "drop",
                "side": -1,
                "occurred_at": now.isoformat(),
            },
            {
                "event": "hypothetical_exit",
                "signal_id": "drop",
                "gross_bps": -99.0,
                "occurred_at": (now + timedelta(hours=1)).isoformat(),
                "boundary_valid": True,
            },
        ],
    )
    _write_jsonl(
        tmp_path / "events.jsonl",
        [
            {
                "event": "shadow_signal_invalidated",
                "signal_id": "drop",
                "exclude_from_prospective_metrics": True,
            }
        ],
    )
    _write_jsonl(
        tmp_path / "health.jsonl",
        [{"checked_at": now.isoformat(), "status": "healthy"}],
    )

    report = evaluate_demo_shadow(protocol_path)
    assert report["status"] == "collecting"
    assert report["metrics"]["closed_trades"] == 1
    assert report["metrics"]["mean_net_bps"] == 4.0
    assert report["metrics"]["invalidated_signals_excluded"] == 1
    assert report["gate_results"]["maximum_drawdown_pct"] is None
    assert report["order_submission_enabled"] is False
    assert len((tmp_path / "weekly-evaluations.jsonl").read_text().splitlines()) == 1

    evaluate_demo_shadow(protocol_path)
    assert len((tmp_path / "weekly-evaluations.jsonl").read_text().splitlines()) == 1
