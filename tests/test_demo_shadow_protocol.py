import json
from pathlib import Path

from scalpforge_strategy.demo_shadow_protocol import (
    DemoShadowConfig,
    _validate,
    verify_protocol,
)


def test_validation_requires_aligned_frozen_evidence() -> None:
    robustness = {
        "candidate_id": "trend_continuation_1h_v1",
        "holdout_evaluated": False,
        "specification_hash": "frozen",
    }
    replay = {
        "candidate_id": "trend_continuation_1h_v1",
        "holdout_evaluated": False,
        "candidate_specification_hash": "frozen",
        "advancement_interpretation": "execution_evidence_supportive",
    }
    economics = {
        "candidate_id": "trend_continuation_1h_v1",
        "holdout_evaluated": False,
        "candidate_specification_hash": "frozen",
        "stress_evidence_supportive": True,
    }
    _validate(DemoShadowConfig(), robustness, replay, economics)


def test_verifier_rejects_mutated_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("original", encoding="utf-8")
    ledger = tmp_path / "signals.jsonl"
    ledger.write_text("", encoding="utf-8")
    protocol = {
        "protocol_id": "demo-shadow-test",
        "evidence": {
            "one": {
                "path": str(evidence),
                "sha256": "not-the-real-hash",
            }
        },
        "ledgers": {"signals": str(ledger)},
        "order_submission_enabled": False,
        "real_money_enabled": False,
        "holdout_evaluated": False,
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    assert verify_protocol(path)["ready"] is False
