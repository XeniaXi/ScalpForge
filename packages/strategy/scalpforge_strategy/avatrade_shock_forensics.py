from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from .avatrade_candidate_replay import _read_quotes, _sha256, _trades
from .avatrade_discovery_lab import (
    REGISTRY,
    _digest,
    _news_attribution,
    _read_news,
    _shock_signals,
)
from .demo_shadow_engine import _bars

GATES = {
    "minimum_trades": 50,
    "minimum_active_days": 20,
    "minimum_trades_per_direction": 15,
    "both_directions_positive_after_2bps": True,
    "leave_best_day_positive": True,
    "maximum_best_day_positive_profit_share": 0.25,
}


def audit_avatrade_shock_forensics(
    discovery_report_path: Path,
    source_dir: Path,
    output_root: Path,
    news_events_path: Path | None = None,
) -> dict[str, object]:
    discovery = _json(discovery_report_path)
    if discovery.get("registry_hash") != _digest(REGISTRY):
        raise ValueError("discovery report does not match the current frozen registry")
    if discovery.get("promotion_allowed_from_this_report") is not False:
        raise ValueError("source discovery report has unsafe promotion semantics")
    arm = "shock_persistence_1h_v1"
    ledger_meta = discovery["ledgers"][arm]
    if _sha256(Path(ledger_meta["path"])) != ledger_meta["sha256"]:
        raise ValueError("source shock ledger hash changed")

    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))
    hashes = {str(path.resolve()): _sha256(path) for path in files}
    if hashes != discovery["source_hashes"]:
        raise ValueError("source snapshot does not match the discovery report")
    quotes, duplicates, invalid = _read_quotes(files, None, None)
    bars = _bars(quotes)
    signals = _shock_signals(bars)
    trades, rejected = _trades(signals, quotes, 3600, 5.0)
    signal_map = {(row["available_at"].isoformat(), int(row["side"])): row for row in signals}
    news = _read_news(news_events_path) if news_events_path else []
    enriched = []
    for trade in trades:
        signal = signal_map[(trade["signal_at"], int(trade["side"]))]
        path = _path_metrics(trade, quotes)
        enriched.append(
            {
                **trade,
                "shock_bps": signal["h4_return_bps"],
                "shock_threshold_bps": signal["shock_threshold_bps"],
                "shock_margin_ratio": signal["shock_margin_ratio"],
                "signal_efficiency": signal["path_efficiency_1800s"],
                "entry_hour_utc": datetime.fromisoformat(trade["entry_at"]).hour,
                "news_attribution": _news_attribution(trade["signal_at"], news),
                **path,
            }
        )

    registry_hash = _digest(REGISTRY)
    identity = {
        "discovery_report_sha256": _sha256(discovery_report_path),
        "registry_hash": registry_hash,
        "news_sha256": _sha256(news_events_path) if news_events_path else None,
    }
    report_id = "avatrade-shock-forensics-" + _digest(identity)[:16]
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    ledger = root / "forensic-trades.jsonl"
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in enriched),
        encoding="utf-8",
    )
    metrics = _metrics(enriched)
    gates = _gates(metrics)
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "arm": arm,
        "registry_hash": registry_hash,
        "source_discovery_report": str(discovery_report_path.resolve()),
        "source_discovery_report_sha256": _sha256(discovery_report_path),
        "source_hashes": hashes,
        "valid_quotes": len(quotes),
        "duplicate_rows_skipped": duplicates,
        "invalid_rows_skipped": invalid,
        "rejected": rejected,
        "metrics": metrics,
        "evidence_gates": GATES,
        "gate_results": gates,
        "news": {
            "status": "official_schedule_attribution_available" if news else "not_supplied",
            "event_count": len(news),
            "directional_surprise_used": False,
        },
        "forensic_ledger": {"path": str(ledger.resolve()), "sha256": _sha256(ledger)},
        "decision": (
            "eligible_for_transport_audit"
            if all(gates.values())
            else "insufficient_evidence"
        ),
        "thresholds_modified": False,
        "candidate_b_frozen": False,
        "candidate_a_modified": False,
        "promotion_allowed": False,
        "holdout_evaluated": False,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    report_path = root / "report.json"
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _path_metrics(trade, quotes):
    entry_at = datetime.fromisoformat(trade["entry_at"])
    exit_at = datetime.fromisoformat(trade["exit_at"])
    side = int(trade["side"])
    entry = float(trade["entry_price"])
    path = [
        quote.bid if side == 1 else quote.ask
        for quote in quotes
        if entry_at <= quote.at <= exit_at
    ]
    returns = [((price / entry) - 1) * 10_000 * side for price in path]
    return {
        "mfe_executable_bps": max(returns) if returns else None,
        "mae_executable_bps": min(returns) if returns else None,
    }


def _metrics(trades):
    values = [float(row["net_bps"]) for row in trades if row["boundary_valid"]]
    daily = _groups(trades, lambda row: str(row["signal_at"])[:10])
    direction = _groups(trades, lambda row: "long" if int(row["side"]) == 1 else "short")
    hourly = _groups(trades, lambda row: f"{int(row['entry_hour_utc']):02d}")
    positive_days = sorted(
        (item["total_bps"] for item in daily.values() if item["total_bps"] > 0),
        reverse=True,
    )
    positive_total = sum(positive_days)
    best = positive_days[0] if positive_days else 0.0
    return {
        "trades": len(values),
        "active_days": len(daily),
        "mean_bps": statistics.mean(values) if values else None,
        "median_bps": statistics.median(values) if values else None,
        "total_bps": sum(values),
        "mean_after_extra_2bps": (
            statistics.mean(value - 2.0 for value in values) if values else None
        ),
        "best_day_positive_profit_share": best / positive_total if positive_total else None,
        "leave_best_day_total_bps": sum(values) - best,
        "direction": direction,
        "daily": daily,
        "entry_hour_utc": hourly,
        "median_shock_bps": _median(trades, "shock_bps", absolute=True),
        "median_shock_margin_ratio": _median(trades, "shock_margin_ratio"),
        "median_mfe_bps": _median(trades, "mfe_executable_bps"),
        "median_mae_bps": _median(trades, "mae_executable_bps"),
        "official_news_window_trades": sum(
            row["news_attribution"] != "outside" for row in trades
        ),
    }


def _median(rows, field, *, absolute=False):
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    if absolute:
        values = [abs(value) for value in values]
    return statistics.median(values) if values else None


def _groups(trades, key):
    groups = {}
    for row in trades:
        if not row["boundary_valid"]:
            continue
        groups.setdefault(key(row), []).append(float(row["net_bps"]))
    return {
        name: {
            "trades": len(values),
            "mean_bps": statistics.mean(values),
            "total_bps": sum(values),
            "mean_after_extra_2bps": statistics.mean(value - 2.0 for value in values),
        }
        for name, values in sorted(groups.items())
    }


def _gates(metrics):
    direction = metrics["direction"]
    long = direction.get("long", {"trades": 0, "mean_after_extra_2bps": float("-inf")})
    short = direction.get("short", {"trades": 0, "mean_after_extra_2bps": float("-inf")})
    return {
        "minimum_trades": metrics["trades"] >= GATES["minimum_trades"],
        "minimum_active_days": metrics["active_days"] >= GATES["minimum_active_days"],
        "minimum_trades_per_direction": min(long["trades"], short["trades"])
        >= GATES["minimum_trades_per_direction"],
        "both_directions_positive_after_2bps": min(
            long["mean_after_extra_2bps"], short["mean_after_extra_2bps"]
        ) > 0,
        "leave_best_day_positive": metrics["leave_best_day_total_bps"] > 0,
        "maximum_best_day_positive_profit_share": (
            metrics["best_day_positive_profit_share"] is not None
            and metrics["best_day_positive_profit_share"]
            <= GATES["maximum_best_day_positive_profit_share"]
        ),
    }


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
