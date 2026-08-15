from __future__ import annotations

import hashlib
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .avatrade_candidate_replay import _read_quotes, _sha256, _signals, _trades
from .demo_shadow_engine import _bars, _candidates
from .demo_shadow_protocol import verify_protocol

REGISTRY = {
    "registry_version": 1,
    "evaluation_role": "exploratory_hypothesis_generation_only",
    "arms": {
        "candidate_a_frozen_control": {
            "mechanism": "multi_hour_trend_continuation",
            "holding_seconds": 3600,
            "cooldown_seconds": 28800,
        },
        "shock_persistence_1h_v1": {
            "mechanism": "causal_abnormal_15m_repricing_continuation",
            "minimum_shock_bps": 15.0,
            "mad_multiple": 6.0,
            "minimum_efficiency": 0.60,
            "lookback_bars": 288,
            "holding_seconds": 3600,
            "cooldown_seconds": 14400,
        },
        "failed_shock_reversal_1h_v1": {
            "mechanism": "abnormal_15m_repricing_failure",
            "confirmation_bars": 3,
            "minimum_retracement_fraction": 0.50,
            "holding_seconds": 3600,
            "cooldown_seconds": 14400,
        },
        "boundary_rejection_1h_v1": {
            "mechanism": "prior_4h_boundary_sweep_and_close_back_inside",
            "boundary_lookback_bars": 48,
            "holding_seconds": 3600,
            "cooldown_seconds": 14400,
        },
    },
    "execution": {
        "entry": "first_executable_quote_after_signal",
        "exit": "first_executable_quote_at_or_after_fixed_hold",
        "maximum_quote_delay_seconds": 5.0,
        "one_position_per_arm": True,
        "extra_cost_stress_bps_round_trip": [0.0, 0.5, 1.0, 2.0],
    },
    "news_policy": {
        "role": "schedule_only_attribution_not_direction_prediction",
        "macro_window_minutes": [-30, 15],
        "fomc_window_minutes": [-60, 120],
    },
}


def run_avatrade_discovery_lab(
    protocol_path: Path,
    source_dir: Path,
    output_root: Path,
    news_events_path: Path | None = None,
) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    protocol = _json(protocol_path)
    if protocol["order_submission_enabled"] or protocol["real_money_enabled"]:
        raise ValueError("unsafe protocol flags")
    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))
    if not files:
        raise ValueError("no AvaTrade tick exports found")
    before = {str(path.resolve()): _sha256(path) for path in files}
    quotes, duplicates, invalid = _read_quotes(files, None, None)
    after = {str(path.resolve()): _sha256(path) for path in files}
    if before != after:
        raise ValueError("AvaTrade export changed during discovery; use a stable snapshot")
    bars = _bars(quotes)
    news_events = _read_news(news_events_path) if news_events_path else []

    spec = protocol["frozen_specification"]
    a_signals = _signals(
        _candidates(bars, float(spec["minimum_path_efficiency"])),
        int(spec["family_cooldown_seconds"]),
    )
    shock_signals = _shock_signals(bars)
    arms = {
        "candidate_a_frozen_control": a_signals,
        "shock_persistence_1h_v1": shock_signals,
        "failed_shock_reversal_1h_v1": _failed_shocks(bars, shock_signals),
        "boundary_rejection_1h_v1": _boundary_rejections(bars),
    }

    registry_hash = _digest(REGISTRY)
    identity = {"registry_hash": registry_hash, "source_hashes": after}
    report_id = "avatrade-discovery-" + _digest(identity)[:16]
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "hypothesis-registry.json").write_text(
        json.dumps(REGISTRY, indent=2) + "\n", encoding="utf-8"
    )

    arm_reports = {}
    ledgers = {}
    for arm, signals in arms.items():
        holding = int(REGISTRY["arms"][arm]["holding_seconds"])
        trades, rejected = _trades(signals, quotes, holding, 5.0)
        for trade in trades:
            trade["arm"] = arm
            trade["news_attribution"] = _news_attribution(trade["signal_at"], news_events)
        ledger = root / f"{arm}-trades.jsonl"
        ledger.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in trades),
            encoding="utf-8",
        )
        ledgers[arm] = {"path": str(ledger.resolve()), "sha256": _sha256(ledger)}
        arm_reports[arm] = _metrics(trades, rejected)

    overlap = _overlap(arms)
    report = {
        "report_id": report_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "protocol_id": protocol["protocol_id"],
        "candidate_a_specification_hash": spec["candidate_specification_hash"],
        "registry_hash": registry_hash,
        "data_scope": "locally_gathered_avatrade_exports_only",
        "source_hashes": after,
        "valid_quotes": len(quotes),
        "duplicate_rows_skipped": duplicates,
        "invalid_rows_skipped": invalid,
        "first_quote_at": quotes[0].at.isoformat(),
        "last_quote_at": quotes[-1].at.isoformat(),
        "five_minute_bars": len(bars),
        "news": {
            "status": "official_schedule_attribution_available" if news_events else "not_supplied",
            "event_count": len(news_events),
            "directional_surprise_used": False,
        },
        "arms": arm_reports,
        "signal_overlap_counts": overlap,
        "ledgers": ledgers,
        "interpretation": "exploratory_screen_not_candidate_validation",
        "automatic_winner_selected": False,
        "promotion_allowed_from_this_report": False,
        "next_step": "freeze_any_surviving_hypothesis_then_test_on_new_prospective_data",
        "candidate_a_modified": False,
        "prospective_ledgers_modified": False,
        "holdout_evaluated": False,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    report_path = root / "report.json"
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _shock_signals(bars: list[dict[str, object]]) -> list[dict[str, object]]:
    cfg = REGISTRY["arms"]["shock_persistence_1h_v1"]
    candidates = []
    for index in range(int(cfg["lookback_bars"]), len(bars)):
        if not _continuous(bars[index - 2 : index + 1]):
            continue
        prior_returns = [
            abs((float(bars[i]["close"]) / float(bars[i]["open"]) - 1) * 10_000)
            for i in range(index - int(cfg["lookback_bars"]), index)
        ]
        median = statistics.median(prior_returns)
        mad = statistics.median(abs(value - median) for value in prior_returns)
        threshold = max(float(cfg["minimum_shock_bps"]), float(cfg["mad_multiple"]) * 1.4826 * mad)
        start, end = float(bars[index - 2]["open"]), float(bars[index]["close"])
        shock = (end / start - 1) * 10_000
        closes = [float(item["close"]) for item in bars[index - 2 : index + 1]]
        path = sum(abs(b - a) for a, b in zip([start, *closes[:-1]], closes, strict=True))
        efficiency = abs(end - start) / path if path else 0.0
        if abs(shock) < threshold or efficiency < float(cfg["minimum_efficiency"]):
            continue
        candidate = _signal(bars[index], 1 if shock > 0 else -1, shock, efficiency)
        candidate["shock_threshold_bps"] = threshold
        candidate["shock_margin_ratio"] = abs(shock) / threshold
        candidates.append(candidate)
    return _cooldown(candidates, int(cfg["cooldown_seconds"]))


def _failed_shocks(
    bars: list[dict[str, object]], shocks: list[dict[str, object]]
) -> list[dict[str, object]]:
    by_time = {bar["available_at"]: index for index, bar in enumerate(bars)}
    result = []
    for shock in shocks:
        index = by_time[shock["available_at"]]
        confirm = index + 3
        if confirm >= len(bars) or not _continuous(bars[index : confirm + 1]):
            continue
        shock_side = int(shock["side"])
        shock_end = float(bars[index]["close"])
        shock_start = float(bars[index - 2]["open"])
        retracement = (shock_end - float(bars[confirm]["close"])) * shock_side
        magnitude = abs(shock_end - shock_start)
        if magnitude and retracement / magnitude >= 0.50:
            result.append(_signal(bars[confirm], -shock_side, float(shock["h4_return_bps"]), 0.0))
    return _cooldown(result, 14400)


def _boundary_rejections(bars: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for index in range(48, len(bars)):
        prior = bars[index - 48 : index]
        if not _continuous([*prior, bars[index]]):
            continue
        high = max(float(item["high"]) for item in prior)
        low = min(float(item["low"]) for item in prior)
        current = bars[index]
        if float(current["high"]) > high and float(current["close"]) < high:
            result.append(_signal(current, -1, 0.0, 0.0))
        elif float(current["low"]) < low and float(current["close"]) > low:
            result.append(_signal(current, 1, 0.0, 0.0))
    return _cooldown(result, 14400)


def _signal(bar, side, move, efficiency):
    return {
        **bar,
        "side": side,
        "h4_return_bps": move,
        "path_efficiency_1800s": efficiency,
        "active": True,
    }


def _cooldown(signals: list[dict[str, object]], seconds: int) -> list[dict[str, object]]:
    result = []
    last = None
    delay = timedelta(seconds=seconds)
    for signal in signals:
        if last is None or signal["available_at"] - last >= delay:
            result.append(signal)
            last = signal["available_at"]
    return result


def _continuous(bars: list[dict[str, object]]) -> bool:
    return all(
        (right["open_at"] - left["open_at"]).total_seconds() == 300
        for left, right in zip(bars, bars[1:], strict=False)
    )


def _metrics(trades, rejected):
    values = [float(row["net_bps"]) for row in trades if row["boundary_valid"]]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    days = {}
    for row in trades:
        day = str(row["signal_at"])[:10]
        days[day] = days.get(day, 0.0) + float(row["net_bps"])
    positive = sorted((value for value in days.values() if value > 0), reverse=True)
    total_positive = sum(positive)
    return {
        "signals_executed": len(trades),
        "valid_closed_trades": len(values),
        "rejected": rejected,
        "mean_executable_bps": sum(values) / len(values) if values else None,
        "total_executable_bps": sum(values),
        "profit_factor": gains / losses if losses else ("infinite" if gains else None),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "mean_after_extra_cost_bps": {
            str(cost): (sum(value - cost for value in values) / len(values) if values else None)
            for cost in (0.5, 1.0, 2.0)
        },
        "active_days": len(days),
        "best_day_positive_profit_share": positive[0] / total_positive if positive else None,
        "leave_best_day_total_bps": sum(values) - positive[0] if positive else sum(values),
        "news_window_trades": sum(row["news_attribution"] != "outside" for row in trades),
    }


def _read_news(path: Path) -> list[dict[str, object]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source = str(row.get("source_url", row.get("url", "")))
        provenance = str(row.get("provenance", row.get("source", ""))).lower()
        if not source.startswith("https://") or "official" not in provenance:
            raise ValueError("news attribution requires HTTPS official-source provenance")
        value = row.get("released_at_utc", row.get("scheduled_at_utc", row.get("occurred_at")))
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
        events.append({"at": at, "family": str(row.get("event_family", row.get("title", "")))})
    return events


def _news_attribution(signal_at: str, events: list[dict[str, object]]) -> str:
    at = datetime.fromisoformat(signal_at)
    for event in events:
        fomc = "fomc" in event["family"].lower()
        before, after = ((60, 120) if fomc else (30, 15))
        if event["at"] - timedelta(minutes=before) <= at <= event["at"] + timedelta(minutes=after):
            return "inside_official_event_window"
    return "outside"


def _overlap(arms):
    sets = {
        name: {(row["available_at"], row["side"]) for row in rows}
        for name, rows in arms.items()
    }
    names = list(sets)
    return {
        f"{left}|{right}": len(sets[left] & sets[right])
        for i, left in enumerate(names)
        for right in names[i + 1 :]
    }


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
