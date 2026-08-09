from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class ExperimentRecord:
    sequence: int
    recorded_at: str
    report_id: str
    experiment_family: str
    dataset_ids: tuple[str, ...]
    hypothesis_count: int
    family_wise_alpha: float
    per_hypothesis_alpha: float
    holdout_evaluated: bool
    previous_hash: str | None
    record_hash: str


def bonferroni_alpha(hypothesis_count: int, family_wise_alpha: float = 0.05) -> float:
    if hypothesis_count <= 0:
        raise ValueError("hypothesis count must be positive")
    if not 0 < family_wise_alpha < 1:
        raise ValueError("family-wise alpha must be between zero and one")
    return family_wise_alpha / hypothesis_count


def register_experiment(
    registry_path: Path,
    *,
    report_id: str,
    experiment_family: str,
    dataset_ids: tuple[str, ...],
    hypothesis_count: int,
    holdout_evaluated: bool,
    family_wise_alpha: float = 0.05,
) -> ExperimentRecord:
    records = read_registry(registry_path)
    existing = next((record for record in records if record.report_id == report_id), None)
    if existing is not None:
        return existing
    payload: dict[str, object] = {
        "sequence": len(records) + 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "report_id": report_id,
        "experiment_family": experiment_family,
        "dataset_ids": list(dataset_ids),
        "hypothesis_count": hypothesis_count,
        "family_wise_alpha": family_wise_alpha,
        "per_hypothesis_alpha": bonferroni_alpha(hypothesis_count, family_wise_alpha),
        "holdout_evaluated": holdout_evaluated,
        "previous_hash": records[-1].record_hash if records else None,
    }
    payload["record_hash"] = _hash(payload)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")
    return _record(payload)


def read_registry(registry_path: Path) -> list[ExperimentRecord]:
    if not registry_path.exists():
        return []
    records: list[ExperimentRecord] = []
    previous_hash: str | None = None
    for line_number, line in enumerate(
        registry_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        payload = json.loads(line)
        record_hash = payload.pop("record_hash", None)
        if record_hash != _hash(payload):
            raise ValueError(f"experiment registry hash mismatch at line {line_number}")
        if payload.get("previous_hash") != previous_hash:
            raise ValueError(f"experiment registry chain mismatch at line {line_number}")
        payload["record_hash"] = record_hash
        record = _record(payload)
        if record.sequence != line_number:
            raise ValueError(f"experiment registry sequence mismatch at line {line_number}")
        records.append(record)
        previous_hash = record.record_hash
    return records


def _hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _record(payload: dict[str, object]) -> ExperimentRecord:
    normalized = dict(payload)
    normalized["dataset_ids"] = tuple(str(value) for value in normalized["dataset_ids"])
    return ExperimentRecord(**normalized)  # type: ignore[arg-type]
