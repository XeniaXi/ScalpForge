from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .avatrade_candidate_replay import _read_quotes, _sha256, _signals
from .demo_shadow_engine import _bars, _candidates, _restore_bars, _store_bars, _trim_bars
from .demo_shadow_protocol import verify_protocol


def rollover_demo_shadow(
    protocol_path: Path,
    parity_report_path: Path,
    snapshot_dir: Path,
    live_source_dir: Path,
    output_root: Path,
) -> dict[str, object]:
    verification = verify_protocol(protocol_path)
    if verification["ready"] is not True:
        raise ValueError("source demo-shadow protocol verification failed")
    parent = _json(protocol_path)
    parity = _json(parity_report_path)
    _validate_parity(parent, parity)

    snapshot_files = sorted(snapshot_dir.glob("scalpforge_GOLD_*_ticks.csv"))
    if not snapshot_files:
        raise ValueError("canonical snapshot contains no AvaTrade tick exports")
    snapshot_hashes = {str(path.resolve()): _sha256(path) for path in snapshot_files}
    if snapshot_hashes != parity["source_hashes"]:
        raise ValueError("snapshot files do not match the frozen parity report")
    live_files, live_cursors = _validate_exact_live_handoff(snapshot_files, live_source_dir)

    quotes, duplicates, invalid = _read_quotes(snapshot_files, None, None)
    if not quotes or duplicates or invalid:
        raise ValueError("canonical bootstrap requires non-empty, duplicate-free valid quotes")
    bars = _bars(quotes)
    complete = [bar for bar in bars if bar["available_at"] <= quotes[-1].at]
    if not complete:
        raise ValueError("canonical bootstrap has no completed five-minute bars")
    spec = parent["frozen_specification"]
    canonical_signals = _signals(
        _candidates(bars, float(spec["minimum_path_efficiency"])),
        int(spec["family_cooldown_seconds"]),
    )
    last_signal_at = canonical_signals[-1]["available_at"] if canonical_signals else None
    holding = timedelta(seconds=int(spec["holding_seconds"]))
    if last_signal_at and quotes[-1].at < last_signal_at + holding:
        raise ValueError("canonical snapshot ends while a hypothetical campaign may still be open")

    lineage = {
        "parent_protocol_id": parent["protocol_id"],
        "parent_protocol_hash": parent["protocol_hash"],
        "parity_report_id": parity["report_id"],
        "parity_report_sha256": _sha256(parity_report_path),
        "snapshot_hashes": snapshot_hashes,
        "rollover_revision": 1,
    }
    identity = {
        "frozen_specification": parent["frozen_specification"],
        "acceptance_gates": parent["acceptance_gates"],
        "evidence": parent["evidence"],
        "lineage": lineage,
    }
    protocol_hash = _digest(identity)
    protocol_id = "demo-shadow-" + protocol_hash[:16]
    root = output_root / protocol_id
    if root.exists():
        existing = _json(root / "protocol.json")
        if existing["protocol_hash"] != protocol_hash:
            raise ValueError("rollover destination contains a different protocol")
        return existing
    root.mkdir(parents=True, exist_ok=False)
    ledgers = {}
    for name in ("signals", "fills", "health", "events", "weekly-evaluations"):
        path = root / f"{name}.jsonl"
        path.write_text("", encoding="utf-8")
        ledgers[name] = str(path.resolve())

    trimmed = _trim_bars(bars, quotes[-1].at)
    bar_cache_payload = _store_bars(trimmed)
    parent_state_path = protocol_path.resolve().parent / "engine-state.json"
    parent_state = _json(parent_state_path) if parent_state_path.exists() else None
    parent_state_diagnosis = _diagnose_parent_state(parent_state, bars)
    certificate = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "protocol_id": protocol_id,
        "parity_report_id": parity["report_id"],
        "incremental_bar_parity_passed": True,
        "incremental_replay_parity_passed": True,
        "snapshot_hashes": snapshot_hashes,
        "live_source_files": [str(path.resolve()) for path in live_files],
        "last_quote_at": quotes[-1].at.isoformat(),
        "last_processed_bar": complete[-1]["open_at"].isoformat(),
        "last_canonical_signal_at": last_signal_at.isoformat() if last_signal_at else None,
        "bar_cache_sha256": _digest(bar_cache_payload),
        "parent_state_diagnosis": parent_state_diagnosis,
        "order_submission_enabled": False,
    }
    certificate_path = root / "bootstrap-certificate.json"
    certificate_path.write_text(json.dumps(certificate, indent=2) + "\n", encoding="utf-8")
    certificate_hash = _sha256(certificate_path)
    state = {
        "schema_version": 3,
        "started_at": certificate["created_at"],
        "last_processed_bar": certificate["last_processed_bar"],
        "last_signal_at": certificate["last_canonical_signal_at"],
        "open_position": None,
        "source_cursors": live_cursors,
        "bar_cache": bar_cache_payload,
        "latest_quote_at": certificate["last_quote_at"],
        "bootstrap_certificate_sha256": certificate_hash,
        "candidate_logic_revision": 1,
    }
    (root / "engine-state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    protocol = {
        **parent,
        "protocol_id": protocol_id,
        "protocol_hash": protocol_hash,
        "created_at": certificate["created_at"],
        "ledgers": ledgers,
        "lineage": lineage,
        "bootstrap_certificate": {
            "path": str(certificate_path.resolve()),
            "sha256": certificate_hash,
        },
        "initial_status": "ready_after_canonical_state_bootstrap",
        "holdout_evaluated": False,
        "candidate_frozen": True,
        "order_submission_enabled": False,
        "research_only": True,
        "real_money_enabled": False,
    }
    new_protocol_path = root / "protocol.json"
    new_protocol_path.write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    _append_event(
        Path(parent["ledgers"]["events"]),
        {
            "event": "protocol_superseded_after_state_parity_failure",
            "occurred_at": datetime.now(UTC).isoformat(),
            "successor_protocol_id": protocol_id,
            "successor_protocol_path": str(new_protocol_path.resolve()),
            "historical_records_preserved": True,
            "order_submission_enabled": False,
        },
    )
    return {
        **protocol,
        "protocol_path": str(new_protocol_path.resolve()),
        "engine_state_path": str((root / "engine-state.json").resolve()),
    }


def verify_bootstrap(protocol_path: Path, state: dict[str, object] | None = None) -> None:
    protocol = _json(protocol_path)
    bootstrap = protocol.get("bootstrap_certificate")
    if bootstrap is None:
        return
    certificate_path = Path(str(bootstrap["path"]))
    expected = str(bootstrap["sha256"])
    if not certificate_path.exists() or _sha256(certificate_path) != expected:
        raise ValueError("canonical bootstrap certificate is missing or changed")
    current_state = state
    if current_state is None:
        current_state = _json(protocol_path.resolve().parent / "engine-state.json")
    if current_state.get("bootstrap_certificate_sha256") != expected:
        raise ValueError("engine state is not linked to the canonical bootstrap certificate")
    if int(current_state.get("schema_version", 0)) < 3:
        raise ValueError("canonical rollover requires engine state schema version 3")


def _validate_parity(parent: dict[str, object], parity: dict[str, object]) -> None:
    if parity.get("candidate_specification_hash") != parent["frozen_specification"][
        "candidate_specification_hash"
    ]:
        raise ValueError("parity report candidate does not match the frozen protocol")
    if parity.get("incremental_bar_parity_passed") is not True:
        raise ValueError("incremental bar parity did not pass")
    if parity.get("incremental_replay_parity_passed") is not True:
        raise ValueError("incremental signal parity did not pass")
    if parity.get("holdout_evaluated") is not False:
        raise ValueError("sealed holdout must remain unevaluated")


def _validate_exact_live_handoff(
    snapshot_files: list[Path], live_source_dir: Path
) -> tuple[list[Path], dict[str, int]]:
    live_files = []
    cursors = {}
    for snapshot in snapshot_files:
        live = live_source_dir / snapshot.name
        if not live.exists():
            raise ValueError(f"live source is missing {snapshot.name}")
        if live.stat().st_size != snapshot.stat().st_size or _sha256(live) != _sha256(snapshot):
            raise ValueError(
                "live exporter files changed after the snapshot; "
                "capture a new snapshot and parity report"
            )
        live_files.append(live)
        cursors[str(live.resolve())] = live.stat().st_size
    return live_files, cursors


def _diagnose_parent_state(
    state: dict[str, object] | None, canonical_bars: list[dict[str, object]]
) -> dict[str, object]:
    if state is None or state.get("bar_cache") is None:
        return {"status": "missing_parent_state", "comparable_bars": 0}
    stored = _restore_bars(state["bar_cache"])
    canonical = {bar["open_at"]: bar for bar in canonical_bars}
    mismatches = []
    missing = []
    for bar in stored:
        expected = canonical.get(bar["open_at"])
        if expected is None:
            missing.append(bar["open_at"].isoformat())
            continue
        fields = ("open", "high", "low", "close")
        if any(float(bar[field]) != float(expected[field]) for field in fields):
            mismatches.append(bar["open_at"].isoformat())
    return {
        "status": "diverged" if mismatches or missing else "matched",
        "stored_bars": len(stored),
        "comparable_bars": len(stored) - len(missing),
        "mismatched_bars": len(mismatches),
        "missing_from_snapshot": len(missing),
        "first_mismatch_at": mismatches[0] if mismatches else None,
        "first_missing_at": missing[0] if missing else None,
    }


def _append_event(path: Path, event: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
