from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .demo_shadow_protocol import verify_protocol


def evaluate_demo_shadow(protocol_path: Path) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    protocol = _json(protocol_path)
    if protocol["order_submission_enabled"] or protocol["real_money_enabled"]:
        raise ValueError("unsafe protocol flags")

    root = protocol_path.resolve().parent
    ledger_paths = {name: Path(path) for name, path in protocol["ledgers"].items()}
    worker_logs = sorted((root / "logs").glob("*-worker.jsonl"))
    source_paths = [
        ledger_paths[name]
        for name in ("signals", "fills", "health", "events")
    ] + worker_logs
    source_hashes = {str(path.resolve()): _sha256(path) for path in source_paths}
    evaluated_at = datetime.now(UTC)
    identity = {
        "protocol_hash": protocol["protocol_hash"],
        "source_hashes": source_hashes,
    }
    evaluation_id = "demo-shadow-evaluation-" + _digest(identity)[:16]

    signals = _jsonl(ledger_paths["signals"])
    fills = _jsonl(ledger_paths["fills"])
    health = _jsonl(ledger_paths["health"])
    events = _jsonl(ledger_paths["events"])
    worker_rows = [row for path in worker_logs for row in _jsonl(path)]
    invalid = {
        str(row["signal_id"])
        for row in events
        if row.get("event") == "shadow_signal_invalidated"
        and row.get("exclude_from_prospective_metrics") is True
    }
    valid_signals = [row for row in signals if str(row.get("signal_id")) not in invalid]
    valid_fills = [row for row in fills if str(row.get("signal_id")) not in invalid]
    entries = {
        str(row["signal_id"]): row
        for row in valid_fills
        if row.get("event") == "hypothetical_entry"
    }
    exits = {
        str(row["signal_id"]): row
        for row in valid_fills
        if row.get("event") == "hypothetical_exit"
    }
    trades = [
        {
            "signal_id": signal_id,
            "side": int(entry["side"]),
            "entry_at": entry["occurred_at"],
            "exit_at": exits[signal_id]["occurred_at"],
            "net_bps": float(exits[signal_id]["gross_bps"]),
            "boundary_valid": bool(exits[signal_id].get("boundary_valid", False)),
        }
        for signal_id, entry in entries.items()
        if signal_id in exits
    ]
    trades.sort(key=lambda row: str(row["entry_at"]))

    audit_start = _audit_start(protocol, worker_rows, health)
    weeks = max(0.0, (evaluated_at - audit_start).total_seconds() / (7 * 86_400))
    nets = [float(row["net_bps"]) for row in trades]
    daily = _daily_values(trades)
    positive_total = math.fsum(value for value in daily.values() if value > 0)
    sorted_positive = sorted((value for value in daily.values() if value > 0), reverse=True)
    best_day_share = sorted_positive[0] / positive_total if positive_total else None
    top_five_share = math.fsum(sorted_positive[:5]) / positive_total if positive_total else None
    eligible_signals = [
        row for row in valid_signals if row.get("disposition") != "ignored_open_position"
    ]
    executable_entries = [
        row for row in valid_signals if row.get("disposition") == "hypothetical_entry"
    ]
    execution_coverage = (
        len(executable_entries) / len(eligible_signals) if eligible_signals else None
    )
    failed_worker_cycles = sum(row.get("event") == "worker_cycle_failed" for row in worker_rows)
    unhealthy_checks = sum(
        row.get("status") not in {"healthy", "healthy_no_new_quotes", "warmup_initialized"}
        for row in health
    )
    invalid_boundaries = sum(not bool(row["boundary_valid"]) for row in trades)
    unresolved_failures = failed_worker_cycles + unhealthy_checks + invalid_boundaries

    metrics = {
        "calendar_weeks": weeks,
        "signals": len(valid_signals),
        "closed_trades": len(trades),
        "active_days": len(daily),
        "mean_net_bps": _mean(nets),
        "total_net_bps": math.fsum(nets),
        "profit_factor": _profit_factor(nets),
        "win_rate": sum(value > 0 for value in nets) / len(nets) if nets else None,
        "maximum_drawdown_bps": _maximum_drawdown(nets),
        "maximum_drawdown_pct": None,
        "drawdown_pct_status": "pending_unmeasurable_without_frozen_equity_sizing",
        "execution_coverage_ratio": execution_coverage,
        "best_day_positive_profit_share": best_day_share,
        "top_five_days_positive_profit_share": top_five_share,
        "long_mean_net_bps": _side_mean(trades, 1),
        "short_mean_net_bps": _side_mean(trades, -1),
        "first_half_mean_net_bps": _mean(nets[: len(nets) // 2]) if len(nets) >= 2 else None,
        "second_half_mean_net_bps": _mean(nets[len(nets) // 2 :]) if len(nets) >= 2 else None,
        "unresolved_data_or_execution_failures": unresolved_failures,
        "invalidated_signals_excluded": len(invalid),
        "open_hypothetical_positions": len(set(entries) - set(exits)),
    }
    gate_results = _gates(protocol["acceptance_gates"], metrics)
    sampling_complete = all(
        gate_results[name] is True
        for name in ("minimum_calendar_weeks", "minimum_closed_trades", "minimum_active_days")
    )
    if not sampling_complete:
        status = "collecting"
    elif any(value is None for value in gate_results.values()):
        status = "yellow"
    elif all(gate_results.values()):
        status = "green"
    else:
        status = "red"

    report = {
        "evaluation_id": evaluation_id,
        "schema_version": 1,
        "evaluated_at": evaluated_at.isoformat(),
        "protocol_id": protocol["protocol_id"],
        "protocol_hash": protocol["protocol_hash"],
        "candidate_id": protocol["frozen_specification"]["candidate_id"],
        "audit_started_at": audit_start.isoformat(),
        "status": status,
        "metrics": metrics,
        "gate_results": gate_results,
        "source_hashes": source_hashes,
        "limitations": [
            (
                "executable bid/ask returns exclude rollover swap until a crossing "
                "is observed and modeled"
            ),
            (
                "equity drawdown cannot be computed until frozen notional or "
                "stop-risk sizing is implemented"
            ),
            "early metrics are descriptive and must not be used to tune Candidate A",
        ],
        "holdout_evaluated": False,
        "candidate_frozen": True,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    report_root = root / "weekly-reports"
    report_root.mkdir(exist_ok=True)
    report_path = report_root / f"{evaluation_id}.json"
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    weekly = ledger_paths["weekly-evaluations"]
    if not any(row.get("evaluation_id") == evaluation_id for row in _jsonl(weekly)):
        with weekly.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
    return report


def _gates(gates, metrics):
    return {
        "minimum_calendar_weeks": metrics["calendar_weeks"] >= gates["minimum_calendar_weeks"],
        "minimum_closed_trades": metrics["closed_trades"] >= gates["minimum_closed_trades"],
        "minimum_active_days": metrics["active_days"] >= gates["minimum_active_days"],
        "mean_net_bps_above_zero": _positive(metrics["mean_net_bps"]),
        "minimum_profit_factor": _at_least(
            metrics["profit_factor"], gates["minimum_profit_factor"]
        ),
        "minimum_execution_coverage_ratio": _at_least(
            metrics["execution_coverage_ratio"], gates["minimum_execution_coverage_ratio"]
        ),
        "maximum_drawdown_pct": None,
        "maximum_best_day_positive_profit_share": _at_most(
            metrics["best_day_positive_profit_share"],
            gates["maximum_best_day_positive_profit_share"],
        ),
        "maximum_top_five_days_positive_profit_share": _at_most(
            metrics["top_five_days_positive_profit_share"],
            gates["maximum_top_five_days_positive_profit_share"],
        ),
        "both_directions_nonnegative": _both_nonnegative(
            metrics["long_mean_net_bps"], metrics["short_mean_net_bps"]
        ),
        "first_and_second_halves_nonnegative": _both_nonnegative(
            metrics["first_half_mean_net_bps"], metrics["second_half_mean_net_bps"]
        ),
        "unresolved_data_or_execution_failures": (
            metrics["unresolved_data_or_execution_failures"]
            <= gates["unresolved_data_or_execution_failures"]
        ),
    }


def _audit_start(protocol, worker_rows, health):
    starts = [
        datetime.fromisoformat(str(row["invoked_at_utc"]))
        for row in worker_rows
        if row.get("event") == "worker_started"
    ]
    if starts:
        return min(starts)
    checks = [datetime.fromisoformat(str(row["checked_at"])) for row in health]
    return min(checks) if checks else datetime.fromisoformat(protocol["created_at"])


def _daily_values(trades):
    daily = defaultdict(float)
    for row in trades:
        daily[datetime.fromisoformat(str(row["entry_at"])).date().isoformat()] += float(
            row["net_bps"]
        )
    return dict(daily)


def _maximum_drawdown(values):
    equity = peak = maximum = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _profit_factor(values):
    wins = math.fsum(value for value in values if value > 0)
    losses = abs(math.fsum(value for value in values if value < 0))
    if not values:
        return None
    return wins / losses if losses else None


def _side_mean(trades, side):
    values = [float(row["net_bps"]) for row in trades if int(row["side"]) == side]
    return _mean(values) if values else None


def _mean(values):
    return math.fsum(values) / len(values) if values else None


def _positive(value):
    return None if value is None else value > 0


def _at_least(value, threshold):
    return None if value is None else value >= threshold


def _at_most(value, threshold):
    return None if value is None else value <= threshold


def _both_nonnegative(first, second):
    return None if first is None or second is None else first >= 0 and second >= 0


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
