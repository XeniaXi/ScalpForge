from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .demo_shadow_protocol import verify_protocol


@dataclass(frozen=True)
class Quote:
    at: datetime
    bid: float
    ask: float


def run_demo_shadow(
    protocol_path: Path, source_dir: Path, lookback_days: int = 10
) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["order_submission_enabled"] or protocol["real_money_enabled"]:
        raise ValueError("unsafe protocol flags")
    root = protocol_path.resolve().parent
    state_path = root / "engine-state.json"
    quotes = _quotes(source_dir, lookback_days)
    if not quotes:
        return _health(protocol, "no_quotes", None, 0, state_path)
    bars = _bars(quotes)
    complete = [bar for bar in bars if bar["available_at"] <= quotes[-1].at]
    now = datetime.now(UTC)
    if not state_path.exists():
        state = {
            "schema_version": 1,
            "started_at": now.isoformat(),
            "last_processed_bar": complete[-1]["open_at"].isoformat() if complete else None,
            "last_signal_at": None,
            "open_position": None,
        }
        _atomic_json(state_path, state)
        return _health(protocol, "warmup_initialized", quotes[-1].at, 0, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    last_processed = _time(state.get("last_processed_bar"))
    candidates = _candidates(
        complete, float(protocol["frozen_specification"]["minimum_path_efficiency"])
    )
    previous_candidates = [
        item
        for item in candidates
        if last_processed is not None and item["open_at"] <= last_processed
    ]
    new_candidates = [
        item for item in candidates if last_processed is None or item["open_at"] > last_processed
    ]
    events = 0
    open_position = state.get("open_position")
    occupied_until = None
    if open_position:
        exit_due = _time(open_position["exit_due"])
        exit_quote = _first_quote(quotes, exit_due)
        if exit_quote:
            side = int(open_position["side"])
            exit_price = exit_quote.bid if side == 1 else exit_quote.ask
            entry_price = float(open_position["entry_price"])
            gross_bps = ((exit_price / entry_price) - 1.0) * 10_000 * side
            _append(
                protocol["ledgers"]["fills"],
                {
                    "event": "hypothetical_exit",
                    "signal_id": open_position["signal_id"],
                    "occurred_at": exit_quote.at.isoformat(),
                    "scheduled_at": exit_due.isoformat(),
                    "side": side,
                    "price": exit_price,
                    "gross_bps": gross_bps,
                    "quote_delay_seconds": (exit_quote.at - exit_due).total_seconds(),
                    "boundary_valid": (exit_quote.at - exit_due).total_seconds()
                    <= float(protocol["risk_limits"]["maximum_quote_age_seconds"]),
                    "order_submitted": False,
                },
            )
            occupied_until = exit_quote.at
            open_position = None
            events += 1
    previous_active = bool(previous_candidates and previous_candidates[-1]["active"])
    last_signal_at = _time(state.get("last_signal_at"))
    cooldown = timedelta(seconds=int(protocol["frozen_specification"]["family_cooldown_seconds"]))
    for candidate in new_candidates:
        active = bool(candidate["active"])
        rising = active and not previous_active
        previous_active = active
        if not rising or (last_signal_at and candidate["available_at"] - last_signal_at < cooldown):
            continue
        key = (
            f"{protocol['protocol_id']}|{candidate['available_at'].isoformat()}|{candidate['side']}"
        )
        signal_id = hashlib.sha256(key.encode()).hexdigest()[:20]
        occupied = open_position or (
            occupied_until is not None and candidate["available_at"] < occupied_until
        )
        disposition = "ignored_open_position" if occupied else "entry_quote_timeout"
        entry_quote = None if occupied else _first_quote(quotes, candidate["available_at"])
        max_age = float(protocol["risk_limits"]["maximum_quote_age_seconds"])
        if entry_quote and (entry_quote.at - candidate["available_at"]).total_seconds() <= max_age:
            side = int(candidate["side"])
            entry_price = entry_quote.ask if side == 1 else entry_quote.bid
            disposition = "hypothetical_entry"
            open_position = {
                "signal_id": signal_id,
                "side": side,
                "entry_at": entry_quote.at.isoformat(),
                "entry_price": entry_price,
                "exit_due": (
                    entry_quote.at
                    + timedelta(seconds=int(protocol["frozen_specification"]["holding_seconds"]))
                ).isoformat(),
            }
            _append(
                protocol["ledgers"]["fills"],
                {
                    "event": "hypothetical_entry",
                    "signal_id": signal_id,
                    "occurred_at": entry_quote.at.isoformat(),
                    "side": side,
                    "price": entry_price,
                    "quote_delay_seconds": (
                        entry_quote.at - candidate["available_at"]
                    ).total_seconds(),
                    "order_submitted": False,
                },
            )
        _append(
            protocol["ledgers"]["signals"],
            {
                "signal_id": signal_id,
                "candidate_id": protocol["frozen_specification"]["candidate_id"],
                "feature_available_at": candidate["available_at"].isoformat(),
                "side": candidate["side"],
                "h1_trend_side": candidate["side"],
                "h4_return_bps": candidate["h4_return_bps"],
                "path_efficiency_1800s": candidate["path_efficiency_1800s"],
                "disposition": disposition,
                "order_submitted": False,
            },
        )
        last_signal_at = candidate["available_at"]
        events += 1
    if complete:
        state["last_processed_bar"] = complete[-1]["open_at"].isoformat()
    state["last_signal_at"] = last_signal_at.isoformat() if last_signal_at else None
    state["open_position"] = open_position
    _atomic_json(state_path, state)
    return _health(protocol, "healthy", quotes[-1].at, events, state_path)


def _quotes(source_dir: Path, lookback_days: int) -> list[Quote]:
    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))[-lookback_days:]
    seen: set[tuple[str, str]] = set()
    result: list[Quote] = []
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("record_type") != "tick":
                    continue
                identity = (row.get("session_id", ""), row.get("source_sequence", ""))
                if identity in seen:
                    continue
                seen.add(identity)
                bid, ask = float(row["bid"]), float(row["ask"])
                if bid <= 0 or ask < bid:
                    continue
                result.append(
                    Quote(
                        datetime.strptime(row["received_utc"], "%Y.%m.%d %H:%M:%S").replace(
                            tzinfo=UTC
                        ),
                        bid,
                        ask,
                    )
                )
    return sorted(result, key=lambda quote: quote.at)


def _bars(quotes: list[Quote]) -> list[dict[str, object]]:
    grouped: dict[datetime, list[Quote]] = {}
    for quote in quotes:
        minute = quote.at.minute - quote.at.minute % 5
        opened = quote.at.replace(minute=minute, second=0, microsecond=0)
        grouped.setdefault(opened, []).append(quote)
    output = []
    for opened, items in sorted(grouped.items()):
        mids = [(item.bid + item.ask) / 2 for item in items]
        output.append(
            {
                "open_at": opened,
                "available_at": opened + timedelta(minutes=5),
                "open": mids[0],
                "close": mids[-1],
                "high": max(mids),
                "low": min(mids),
            }
        )
    return output


def _candidates(
    bars: list[dict[str, object]], minimum_efficiency: float
) -> list[dict[str, object]]:
    h1 = _completed(bars, 12)
    h4 = _completed(bars, 48)
    result = []
    for index, bar in enumerate(bars):
        available = bar["available_at"]
        past_h1 = [item for item in h1 if item["available_at"] <= available]
        past_h4 = [item for item in h4 if item["available_at"] <= available]
        if len(past_h1) < 26 or len(past_h4) < 2 or index < 6:
            continue
        fast = _ema([float(item["close"]) for item in past_h1], 12)
        slow = _ema([float(item["close"]) for item in past_h1], 26)
        side = 1 if fast > slow else -1 if fast < slow else 0
        h4_return = (float(past_h4[-1]["close"]) / float(past_h4[-2]["open"]) - 1) * 10_000
        closes = [float(item["close"]) for item in bars[index - 6 : index + 1]]
        efficiency = (
            abs(closes[-1] - closes[0])
            / sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
            if len(set(closes)) > 1
            else 0.0
        )
        result.append(
            {
                **bar,
                "side": side,
                "h4_return_bps": h4_return,
                "path_efficiency_1800s": efficiency,
                "active": bool(side and h4_return * side > 0 and efficiency >= minimum_efficiency),
            }
        )
    return result


def _completed(bars: list[dict[str, object]], count: int) -> list[dict[str, object]]:
    output = []
    for index in range(count - 1, len(bars)):
        group = bars[index - count + 1 : index + 1]
        if all(
            (group[i]["open_at"] - group[i - 1]["open_at"]).total_seconds() == 300
            for i in range(1, len(group))
        ):
            end = group[-1]["available_at"]
            epoch = int(end.timestamp())
            if epoch % (count * 300) == 0:
                output.append(
                    {"open": group[0]["open"], "close": group[-1]["close"], "available_at": end}
                )
    return output


def _ema(values: list[float], period: int) -> float:
    value = values[-period]
    alpha = 2 / (period + 1)
    for current in values[-period + 1 :]:
        value = alpha * current + (1 - alpha) * value
    return value


def _first_quote(quotes: list[Quote], at: datetime) -> Quote | None:
    return next((quote for quote in quotes if quote.at >= at), None)


def _health(protocol, status, latest, events, state_path):
    record = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": status,
        "latest_quote_at": latest.isoformat() if latest else None,
        "events_written": events,
        "state_path": str(state_path),
        "order_submission_enabled": False,
    }
    _append(protocol["ledgers"]["health"], record)
    return record


def _append(path, value):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def _atomic_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _time(value):
    return datetime.fromisoformat(value) if value else None
