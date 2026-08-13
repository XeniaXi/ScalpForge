from __future__ import annotations

import csv
import hashlib
import io
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


CSV_COLUMNS = [
    "record_type",
    "received_utc",
    "server_time",
    "monotonic_ms",
    "session_id",
    "source_sequence",
    "broker",
    "server",
    "symbol",
    "bid",
    "ask",
    "spread_points",
]
MAX_ENGINE_PROCESSING_DELAY_SECONDS = 60.0


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
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    if state and state.get("source_cursors") is not None and state.get("bar_cache") is not None:
        quotes, cursors = _incremental_quotes(source_dir, state["source_cursors"])
        bars = _merge_bars(_restore_bars(state["bar_cache"]), _bars(quotes))
        latest_quote = quotes[-1].at if quotes else _time(state.get("latest_quote_at"))
    else:
        quotes = _quotes(source_dir, lookback_days)
        cursors = _source_cursors(source_dir, lookback_days)
        bars = _bars(quotes)
        latest_quote = quotes[-1].at if quotes else None
    if not quotes:
        if latest_quote is None:
            return _health(protocol, "no_quotes", None, 0, state_path)
        state["source_cursors"] = cursors
        state["bar_cache"] = _store_bars(bars)
        _atomic_json(state_path, state)
        return _health(protocol, "healthy_no_new_quotes", latest_quote, 0, state_path)
    complete = [bar for bar in bars if bar["available_at"] <= latest_quote]
    now = datetime.now(UTC)
    if state is None:
        state = {
            "schema_version": 2,
            "started_at": now.isoformat(),
            "last_processed_bar": complete[-1]["open_at"].isoformat() if complete else None,
            "last_signal_at": None,
            "open_position": None,
            "source_cursors": cursors,
            "bar_cache": _store_bars(bars),
            "latest_quote_at": latest_quote.isoformat(),
        }
        _atomic_json(state_path, state)
        return _health(protocol, "warmup_initialized", latest_quote, 0, state_path)
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
        max_age = float(protocol["risk_limits"]["maximum_quote_age_seconds"])
        entry_quote = (
            None
            if occupied
            else _live_entry_quote(
                quotes,
                candidate["available_at"],
                now,
                max_age,
                MAX_ENGINE_PROCESSING_DELAY_SECONDS,
            )
        )
        processing_delay = (now - candidate["available_at"]).total_seconds()
        if not occupied and processing_delay > MAX_ENGINE_PROCESSING_DELAY_SECONDS:
            disposition = "rejected_late_engine_processing"
        elif entry_quote:
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
                    "engine_observed_at": now.isoformat(),
                    "engine_processing_delay_seconds": processing_delay,
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
                "engine_observed_at": now.isoformat(),
                "engine_processing_delay_seconds": processing_delay,
                "order_submitted": False,
            },
        )
        last_signal_at = candidate["available_at"]
        events += 1
    if complete:
        state["last_processed_bar"] = complete[-1]["open_at"].isoformat()
    state["last_signal_at"] = last_signal_at.isoformat() if last_signal_at else None
    state["open_position"] = open_position
    state["schema_version"] = 2
    state["source_cursors"] = cursors
    state["bar_cache"] = _store_bars(_trim_bars(bars, latest_quote))
    state["latest_quote_at"] = latest_quote.isoformat()
    _atomic_json(state_path, state)
    return _health(protocol, "healthy", latest_quote, events, state_path)


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


def _source_cursors(source_dir: Path, lookback_days: int) -> dict[str, int]:
    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))[-lookback_days:]
    return {str(path.resolve()): path.stat().st_size for path in files}


def _incremental_quotes(
    source_dir: Path, stored_cursors: dict[str, int]
) -> tuple[list[Quote], dict[str, int]]:
    cursors = dict(stored_cursors)
    result: list[Quote] = []
    for path in sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv")):
        resolved = str(path.resolve())
        size = path.stat().st_size
        offset = int(cursors.get(resolved, 0))
        if size < offset:
            offset = 0
        if size == offset:
            continue
        with path.open("rb") as handle:
            handle.seek(offset)
            payload = handle.read()
        rows = csv.DictReader(
            io.StringIO(payload.decode("utf-8-sig")),
            fieldnames=CSV_COLUMNS,
        )
        for row in rows:
            quote = _quote(row)
            if quote is not None:
                result.append(quote)
        cursors[resolved] = size
    return sorted(result, key=lambda quote: quote.at), cursors


def _quote(row: dict[str, str | None]) -> Quote | None:
    if row.get("record_type") != "tick":
        return None
    try:
        bid, ask = float(row["bid"] or 0), float(row["ask"] or 0)
        if bid <= 0 or ask < bid:
            return None
        at = datetime.strptime(str(row["received_utc"]), "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return Quote(at, bid, ask)


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


def _merge_bars(
    cached: list[dict[str, object]], incoming: list[dict[str, object]]
) -> list[dict[str, object]]:
    merged = {bar["open_at"]: bar for bar in cached}
    for bar in incoming:
        existing = merged.get(bar["open_at"])
        if existing is None:
            merged[bar["open_at"]] = bar
        else:
            merged[bar["open_at"]] = {
                "open_at": existing["open_at"],
                "available_at": existing["available_at"],
                "open": existing["open"],
                "close": bar["close"],
                "high": max(float(existing["high"]), float(bar["high"])),
                "low": min(float(existing["low"]), float(bar["low"])),
            }
    return [merged[key] for key in sorted(merged)]


def _trim_bars(bars: list[dict[str, object]], latest_quote: datetime) -> list[dict[str, object]]:
    cutoff = latest_quote - timedelta(days=10)
    return [bar for bar in bars if bar["open_at"] >= cutoff]


def _store_bars(bars: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            **bar,
            "open_at": bar["open_at"].isoformat(),
            "available_at": bar["available_at"].isoformat(),
        }
        for bar in bars
    ]


def _restore_bars(stored: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            **bar,
            "open_at": _time(bar["open_at"]),
            "available_at": _time(bar["available_at"]),
        }
        for bar in stored
    ]


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


def _live_entry_quote(
    quotes: list[Quote],
    signal_at: datetime,
    observed_at: datetime,
    maximum_quote_age_seconds: float,
    maximum_processing_delay_seconds: float,
) -> Quote | None:
    if (observed_at - signal_at).total_seconds() > maximum_processing_delay_seconds:
        return None
    eligible = [quote for quote in quotes if signal_at <= quote.at <= observed_at]
    if not eligible:
        return None
    latest = eligible[-1]
    if (observed_at - latest.at).total_seconds() > maximum_quote_age_seconds:
        return None
    return latest


def invalidate_shadow_signal(protocol_path: Path, signal_id: str, reason: str) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    if not reason.strip():
        raise ValueError("an explicit invalidation reason is required")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    state_path = protocol_path.resolve().parent / "engine-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    prior = _jsonl(Path(protocol["ledgers"]["events"]))
    if any(
        item.get("event") == "shadow_signal_invalidated" and item.get("signal_id") == signal_id
        for item in prior
    ):
        return {"signal_id": signal_id, "status": "already_invalidated"}
    signals = _jsonl(Path(protocol["ledgers"]["signals"]))
    if not any(item.get("signal_id") == signal_id for item in signals):
        raise ValueError("signal_id is not present in the frozen protocol ledger")
    now = datetime.now(UTC).isoformat()
    correction = {
        "event": "shadow_signal_invalidated",
        "signal_id": signal_id,
        "invalidated_at": now,
        "reason": reason.strip(),
        "original_records_preserved": True,
        "exclude_from_prospective_metrics": True,
        "order_submitted": False,
    }
    _append(protocol["ledgers"]["events"], correction)
    open_position = state.get("open_position")
    if open_position and open_position.get("signal_id") == signal_id:
        _append(
            protocol["ledgers"]["fills"],
            {
                "event": "hypothetical_entry_invalidated",
                "signal_id": signal_id,
                "invalidated_at": now,
                "reason": reason.strip(),
                "cash_or_order_effect": False,
                "order_submitted": False,
            },
        )
        state["open_position"] = None
    state.setdefault("invalidated_signal_ids", []).append(signal_id)
    _atomic_json(state_path, state)
    return {"signal_id": signal_id, "status": "invalidated", "state_path": str(state_path)}


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


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _atomic_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _time(value):
    return datetime.fromisoformat(value) if value else None
