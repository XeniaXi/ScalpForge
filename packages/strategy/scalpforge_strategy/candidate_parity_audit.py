from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .avatrade_candidate_replay import _read_quotes, _sha256, _signals
from .demo_shadow_engine import Quote, _bars, _candidates
from .demo_shadow_protocol import verify_protocol


def audit_candidate_parity(
    protocol_path: Path, source_dir: Path, output_root: Path
) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("demo-shadow protocol verification failed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["order_submission_enabled"] or protocol["real_money_enabled"]:
        raise ValueError("unsafe protocol flags")
    spec = protocol["frozen_specification"]
    if spec["candidate_id"] != "trend_continuation_1h_v1":
        raise ValueError("parity audit only supports frozen Candidate A")

    files = sorted(source_dir.glob("scalpforge_GOLD_*_ticks.csv"))
    if not files:
        raise ValueError(f"no AvaTrade tick exports found in {source_dir}")
    hashes_before = {str(path.resolve()): _sha256(path) for path in files}
    quotes, duplicates, invalid = _read_quotes(files, None, None)
    hashes_after = {str(path.resolve()): _sha256(path) for path in files}
    if hashes_before != hashes_after:
        raise ValueError("AvaTrade export changed during parity audit; use a stable snapshot")
    if not quotes:
        raise ValueError("no valid quotes in parity snapshot")

    bars = _bars(quotes)
    incremental_bars = _incremental_bars(quotes)
    bar_parity = bars == incremental_bars
    minimum_efficiency = float(spec["minimum_path_efficiency"])
    cooldown_seconds = int(spec["family_cooldown_seconds"])
    batch = _signals(_candidates(bars, minimum_efficiency), cooldown_seconds)
    incremental = _incremental_signals(incremental_bars, minimum_efficiency, cooldown_seconds)
    batch_signatures = [_signature(row) for row in batch]
    incremental_signatures = [_signature(row) for row in incremental]
    batch_only = [row for row in batch_signatures if row not in incremental_signatures]
    incremental_only = [row for row in incremental_signatures if row not in batch_signatures]

    live_rows = _jsonl(Path(protocol["ledgers"]["signals"]))
    live_attribution = _attribute_live(
        live_rows, batch_signatures, quotes[0].at, quotes[-1].at
    )
    key = json.dumps(
        {
            "candidate_specification_hash": spec["candidate_specification_hash"],
            "source_hashes": hashes_after,
            "audit_revision": 2,
        },
        sort_keys=True,
    )
    report_id = "candidate-parity-audit-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    destination = output_root / report_id
    destination.mkdir(parents=True, exist_ok=True)
    signal_path = destination / "canonical-signals.jsonl"
    signal_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in batch_signatures),
        encoding="utf-8",
    )
    report: dict[str, object] = {
        "report_id": report_id,
        "schema_version": 1,
        "audit_revision": 2,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate_id": spec["candidate_id"],
        "candidate_specification_hash": spec["candidate_specification_hash"],
        "protocol_id": protocol["protocol_id"],
        "source_scope": "stable_locally_gathered_avatrade_snapshot",
        "first_quote_at": quotes[0].at.isoformat(),
        "last_quote_at": quotes[-1].at.isoformat(),
        "source_files": len(files),
        "source_hashes": hashes_after,
        "valid_quotes": len(quotes),
        "duplicate_rows_skipped": duplicates,
        "invalid_rows_skipped": invalid,
        "five_minute_bars": len(bars),
        "incremental_five_minute_bars": len(incremental_bars),
        "incremental_bar_parity_passed": bar_parity,
        "batch_signal_count": len(batch_signatures),
        "incremental_signal_count": len(incremental_signatures),
        "incremental_replay_parity_passed": (
            bar_parity and batch_signatures == incremental_signatures
        ),
        "batch_only_signals": batch_only,
        "incremental_only_signals": incremental_only,
        "live_ledger_attribution": live_attribution,
        "canonical_signal_ledger": {
            "path": str(signal_path),
            "sha256": _sha256(signal_path),
        },
        "interpretation": "signal_semantics_and_state_transition_audit_only",
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


def _incremental_signals(
    bars: list[dict[str, object]], minimum_efficiency: float, cooldown_seconds: int
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    previous_active = False
    last_signal_at: datetime | None = None
    cooldown = timedelta(seconds=cooldown_seconds)
    for state in _candidates(bars, minimum_efficiency):
        active = bool(state["active"])
        rising = active and not previous_active
        previous_active = active
        available_at = state["available_at"]
        if not rising or (last_signal_at and available_at - last_signal_at < cooldown):
            continue
        result.append(state)
        last_signal_at = available_at
    return result


def _incremental_bars(quotes: list[Quote]) -> list[dict[str, object]]:
    groups: list[list[Quote]] = []
    current: list[Quote] = []
    current_open: datetime | None = None
    for quote in quotes:
        minute = quote.at.minute - quote.at.minute % 5
        opened = quote.at.replace(minute=minute, second=0, microsecond=0)
        if current_open is not None and opened != current_open:
            groups.append(current)
            current = []
        current_open = opened
        current.append(quote)
    if current:
        groups.append(current)
    return [bar for group in groups for bar in _bars(group)]


def _signature(row: dict[str, object]) -> dict[str, object]:
    return {
        "feature_available_at": row["available_at"].isoformat(),
        "side": int(row["side"]),
        "h4_return_bps": round(float(row["h4_return_bps"]), 12),
        "path_efficiency_1800s": round(float(row["path_efficiency_1800s"]), 12),
    }


def _attribute_live(
    rows: list[dict[str, object]],
    canonical: list[dict[str, object]],
    snapshot_start: datetime,
    snapshot_end: datetime,
) -> dict[str, object]:
    canonical_keys = {
        (str(row["feature_available_at"]), int(row["side"])) for row in canonical
    }
    details = []
    matched = 0
    comparable = 0
    outside = 0
    for row in rows:
        key = (str(row.get("feature_available_at")), int(row.get("side", 0)))
        try:
            feature_at = datetime.fromisoformat(str(row.get("feature_available_at")))
            in_snapshot = snapshot_start <= feature_at <= snapshot_end
        except ValueError:
            in_snapshot = False
        is_match = in_snapshot and key in canonical_keys
        comparable += int(in_snapshot)
        outside += int(not in_snapshot)
        matched += int(is_match)
        details.append(
            {
                "feature_available_at": row.get("feature_available_at"),
                "side": row.get("side"),
                "disposition": row.get("disposition"),
                "engine_observed_at": row.get("engine_observed_at"),
                "comparison_scope": "in_snapshot" if in_snapshot else "outside_snapshot",
                "canonical_match": is_match,
            }
        )
    return {
        "live_signal_count": len(rows),
        "comparable_live_signals": comparable,
        "out_of_snapshot_live_signals": outside,
        "canonical_matches": matched,
        "unmatched_comparable_live_signals": comparable - matched,
        "details": details,
        "note": "legacy and late-reconstructed live rows are attribution only",
    }


def _jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
