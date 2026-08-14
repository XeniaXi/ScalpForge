from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .demo_shadow_engine import Quote, _bars, _candidates, _first_quote
from .demo_shadow_protocol import verify_protocol


def replay_avatrade_candidate(
    protocol_path: Path,
    source_dir: Path,
    output_root: Path,
    start: datetime | None = None,
    end_exclusive: datetime | None = None,
) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["order_submission_enabled"] or protocol["real_money_enabled"]:
        raise ValueError("unsafe protocol flags")
    spec = protocol["frozen_specification"]
    if spec["candidate_id"] != "trend_continuation_1h_v1":
        raise ValueError("replay only supports frozen Candidate A")

    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))
    if not files:
        raise ValueError(f"no AvaTrade tick exports found in {source_dir}")
    source_hashes_before = {str(path.resolve()): _sha256(path) for path in files}
    quotes, duplicates, invalid_rows = _read_quotes(files, start, end_exclusive)
    source_hashes = {str(path.resolve()): _sha256(path) for path in files}
    if source_hashes != source_hashes_before:
        raise ValueError("AvaTrade export changed during replay; retry against a stable snapshot")
    if not quotes:
        raise ValueError("no valid quotes in the requested replay interval")

    bars = _bars(quotes)
    candidates = _candidates(bars, float(spec["minimum_path_efficiency"]))
    signals = _signals(candidates, int(spec["family_cooldown_seconds"]))
    trades, rejected = _trades(
        signals,
        quotes,
        int(spec["holding_seconds"]),
        float(protocol["risk_limits"]["maximum_quote_age_seconds"]),
    )

    replay_key = json.dumps(
        {
            "candidate_specification_hash": spec["candidate_specification_hash"],
            "source_hashes": source_hashes,
            "start": start.isoformat() if start else None,
            "end_exclusive": end_exclusive.isoformat() if end_exclusive else None,
        },
        sort_keys=True,
    )
    replay_id = "avatrade-candidate-replay-" + hashlib.sha256(replay_key.encode()).hexdigest()[:16]
    destination = output_root / replay_id
    destination.mkdir(parents=True, exist_ok=True)
    ledger_path = destination / "trade-ledger.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in trades), encoding="utf-8"
    )

    net = [float(row["net_bps"]) for row in trades]
    wins = sum(value > 0 for value in net)
    gains = sum(value for value in net if value > 0)
    losses = -sum(value for value in net if value < 0)
    report: dict[str, object] = {
        "report_id": replay_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": spec["candidate_id"],
        "candidate_specification_hash": spec["candidate_specification_hash"],
        "protocol_id": protocol["protocol_id"],
        "data_scope": "locally_gathered_avatrade_exports_only",
        "requested_start": start.isoformat() if start else None,
        "requested_end_exclusive": end_exclusive.isoformat() if end_exclusive else None,
        "first_quote_at": quotes[0].at.isoformat(),
        "last_quote_at": quotes[-1].at.isoformat(),
        "source_files": len(files),
        "source_hashes": source_hashes,
        "valid_quotes": len(quotes),
        "duplicate_rows_skipped": duplicates,
        "invalid_rows_skipped": invalid_rows,
        "five_minute_bars": len(bars),
        "candidate_state_rows": len(candidates),
        "rising_edge_signals": len(signals),
        "closed_trades": len(trades),
        "rejected_signals": rejected,
        "metrics": {
            "mean_net_bps": sum(net) / len(net) if net else None,
            "total_net_bps": sum(net),
            "win_rate": wins / len(net) if net else None,
            "profit_factor": gains / losses if losses else (None if not gains else "infinite"),
            "long_trades": sum(int(row["side"]) == 1 for row in trades),
            "short_trades": sum(int(row["side"]) == -1 for row in trades),
        },
        "trade_ledger": {"path": str(ledger_path), "sha256": _sha256(ledger_path)},
        "interpretation": "diagnostic_replay_not_prospective_evidence",
        "prospective_ledgers_modified": False,
        "candidate_frozen": True,
        "holdout_evaluated": False,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    report_path = destination / "report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _read_quotes(
    files: list[Path], start: datetime | None, end_exclusive: datetime | None
) -> tuple[list[Quote], int, int]:
    seen: set[tuple[str, str]] = set()
    quotes: list[Quote] = []
    duplicates = 0
    invalid = 0
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("record_type") != "tick":
                    continue
                identity = (row.get("session_id", ""), row.get("source_sequence", ""))
                if identity in seen:
                    duplicates += 1
                    continue
                seen.add(identity)
                try:
                    at = datetime.strptime(row["received_utc"], "%Y.%m.%d %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                    bid, ask = float(row["bid"]), float(row["ask"])
                except (KeyError, TypeError, ValueError):
                    invalid += 1
                    continue
                if bid <= 0 or ask < bid:
                    invalid += 1
                    continue
                if start and at < start:
                    continue
                if end_exclusive and at >= end_exclusive:
                    continue
                quotes.append(Quote(at, bid, ask))
    return sorted(quotes, key=lambda quote: quote.at), duplicates, invalid


def _signals(candidates: list[dict[str, object]], cooldown_seconds: int) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    previous_active = False
    last_signal_at: datetime | None = None
    cooldown = timedelta(seconds=cooldown_seconds)
    for candidate in candidates:
        active = bool(candidate["active"])
        rising = active and not previous_active
        previous_active = active
        available_at = candidate["available_at"]
        if not rising or (last_signal_at and available_at - last_signal_at < cooldown):
            continue
        result.append(candidate)
        last_signal_at = available_at
    return result


def _trades(
    signals: list[dict[str, object]],
    quotes: list[Quote],
    holding_seconds: int,
    maximum_quote_delay_seconds: float,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    trades: list[dict[str, object]] = []
    rejected = {
        "open_position": 0,
        "entry_quote_timeout": 0,
        "exit_quote_unavailable": 0,
        "invalid_exit_boundary": 0,
    }
    occupied_until: datetime | None = None
    for signal in signals:
        signal_at = signal["available_at"]
        if occupied_until and signal_at < occupied_until:
            rejected["open_position"] += 1
            continue
        entry = _first_quote(quotes, signal_at)
        if entry is None or (entry.at - signal_at).total_seconds() > maximum_quote_delay_seconds:
            rejected["entry_quote_timeout"] += 1
            continue
        exit_due = entry.at + timedelta(seconds=holding_seconds)
        exit_quote = _first_quote(quotes, exit_due)
        if exit_quote is None:
            rejected["exit_quote_unavailable"] += 1
            occupied_until = exit_due
            continue
        exit_delay = (exit_quote.at - exit_due).total_seconds()
        boundary_valid = exit_delay <= maximum_quote_delay_seconds
        if not boundary_valid:
            rejected["invalid_exit_boundary"] += 1
        side = int(signal["side"])
        entry_price = entry.ask if side == 1 else entry.bid
        exit_price = exit_quote.bid if side == 1 else exit_quote.ask
        gross_bps = ((exit_price / entry_price) - 1.0) * 10_000 * side
        # The prospective evaluator currently treats executable bid/ask gross as net.
        net_bps = gross_bps
        key = f"{signal_at.isoformat()}|{side}|{entry.at.isoformat()}"
        trades.append(
            {
                "signal_id": hashlib.sha256(key.encode()).hexdigest()[:20],
                "signal_at": signal_at.isoformat(),
                "side": side,
                "h4_return_bps": signal["h4_return_bps"],
                "path_efficiency_1800s": signal["path_efficiency_1800s"],
                "entry_at": entry.at.isoformat(),
                "entry_price": entry_price,
                "exit_due": exit_due.isoformat(),
                "exit_at": exit_quote.at.isoformat(),
                "exit_price": exit_price,
                "exit_quote_delay_seconds": exit_delay,
                "boundary_valid": boundary_valid,
                "gross_bps": gross_bps,
                "net_bps": net_bps,
                "order_submitted": False,
            }
        )
        occupied_until = exit_quote.at
    return trades, rejected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
