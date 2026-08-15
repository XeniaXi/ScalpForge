import json
from pathlib import Path

import pytest
from scalpforge_strategy.demo_shadow_rollover import (
    _diagnose_parent_state,
    _validate_exact_live_handoff,
    verify_bootstrap,
)


def test_exact_live_handoff_rejects_changed_export(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    live = tmp_path / "live"
    snapshot.mkdir()
    live.mkdir()
    name = "scalpforge_GOLD_20260814_ticks.csv"
    (snapshot / name).write_text("one", encoding="utf-8")
    (live / name).write_text("two", encoding="utf-8")
    with pytest.raises(ValueError, match="do not preserve the snapshot prefix"):
        _validate_exact_live_handoff([snapshot / name], live)


def test_exact_live_handoff_accepts_appended_export_and_uses_snapshot_cursor(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    live = tmp_path / "live"
    snapshot.mkdir()
    live.mkdir()
    name = "scalpforge_GOLD_20260814_ticks.csv"
    (snapshot / name).write_bytes(b"snapshot\n")
    (live / name).write_bytes(b"snapshot\nappended\n")

    files, cursors = _validate_exact_live_handoff([snapshot / name], live)

    assert files == [live / name]
    assert cursors[str((live / name).resolve())] == len(b"snapshot\n")


def test_bootstrap_certificate_must_match_state(tmp_path: Path) -> None:
    certificate = tmp_path / "bootstrap-certificate.json"
    certificate.write_text("{}\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(certificate.read_bytes()).hexdigest()
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps(
            {"bootstrap_certificate": {"path": str(certificate), "sha256": digest}}
        ),
        encoding="utf-8",
    )
    verify_bootstrap(
        protocol,
        {"schema_version": 3, "bootstrap_certificate_sha256": digest},
    )
    with pytest.raises(ValueError, match="not linked"):
        verify_bootstrap(protocol, {"schema_version": 3})


def test_parent_state_diagnosis_locates_first_changed_bar() -> None:
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 8, 14, tzinfo=UTC)
    canonical = [
        {
            "open_at": start,
            "available_at": start + timedelta(minutes=5),
            "open": 1.0,
            "high": 2.0,
            "low": 1.0,
            "close": 2.0,
        }
    ]
    state = {
        "bar_cache": [
            {
                **canonical[0],
                "open_at": start.isoformat(),
                "available_at": (start + timedelta(minutes=5)).isoformat(),
                "close": 3.0,
            }
        ]
    }
    result = _diagnose_parent_state(state, canonical)
    assert result["status"] == "diverged"
    assert result["first_mismatch_at"] == start.isoformat()
