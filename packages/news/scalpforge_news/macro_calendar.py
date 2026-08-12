from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

FAMILIES = ("cpi", "employment_situation")


@dataclass(frozen=True)
class MacroEvent:
    event_id: str
    event_family: str
    scheduled_at_utc: datetime
    released_at_utc: datetime
    consensus_as_of_utc: datetime
    consensus: float
    initial_actual: float
    previous_as_displayed: float | None
    revised_previous: float | None
    unit: str
    source_url: str
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if self.event_family not in FAMILIES:
            raise ValueError("only preregistered CPI and Employment Situation events are allowed")
        timestamps = (
            self.scheduled_at_utc,
            self.released_at_utc,
            self.consensus_as_of_utc,
        )
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("macro timestamps must include timezone information")
        if self.consensus_as_of_utc >= self.released_at_utc:
            raise ValueError("consensus must be demonstrably available before release")
        if self.released_at_utc < self.scheduled_at_utc:
            raise ValueError("release cannot precede the scheduled time")
        if not self.source_url.startswith("https://"):
            raise ValueError("macro event requires an HTTPS primary or licensed source")
        if len(self.snapshot_sha256) != 64:
            raise ValueError("macro event requires a SHA-256 source snapshot")


def import_macro_events(source_csv: Path, output_root: Path) -> dict[str, object]:
    source_hash = _sha256(source_csv)
    with source_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    events = [_event(row) for row in rows]
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("macro event IDs must be unique")
    events.sort(key=lambda event: event.released_at_utc)
    dataset_id = "macro-events-" + source_hash[:16]
    root = output_root / dataset_id
    root.mkdir(parents=True, exist_ok=True)
    event_path = root / "events.jsonl"
    event_path.write_text(
        "".join(json.dumps(_json(asdict(event)), sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    coverage = audit_macro_coverage(events)
    manifest = {
        "dataset_id": dataset_id,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_file": source_csv.name,
        "source_sha256": source_hash,
        "row_count": len(events),
        "families": list(FAMILIES),
        "events_partition": str(event_path),
        "coverage": coverage,
        "point_in_time": True,
        "consensus_vintage_required": True,
        "strategy_eligible": coverage["strategy_eligible"],
        "external_non_executable": True,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def audit_macro_coverage(events: list[MacroEvent]) -> dict[str, object]:
    counts = {family: sum(event.event_family == family for event in events) for family in FAMILIES}
    dates = {event.released_at_utc.date() for event in events}
    valid = all(
        event.consensus_as_of_utc < event.released_at_utc
        and event.released_at_utc >= event.scheduled_at_utc
        and len(event.snapshot_sha256) == 64
        for event in events
    )
    eligible = valid and len(events) >= 20 and min(counts.values(), default=0) >= 8
    return {
        "event_count": len(events),
        "release_date_count": len(dates),
        "family_counts": counts,
        "vintage_timing_valid": valid,
        "minimum_total_events": 20,
        "minimum_events_per_family": 8,
        "strategy_eligible": eligible,
    }


def _event(row):
    required = (
        "event_id", "event_family", "scheduled_at_utc", "released_at_utc",
        "consensus_as_of_utc", "consensus", "initial_actual", "unit", "source_url",
        "snapshot_sha256",
    )
    missing = [name for name in required if not row.get(name)]
    if missing:
        raise ValueError(f"macro event is missing required fields: {', '.join(missing)}")
    return MacroEvent(
        event_id=row["event_id"],
        event_family=row["event_family"],
        scheduled_at_utc=_timestamp(row["scheduled_at_utc"]),
        released_at_utc=_timestamp(row["released_at_utc"]),
        consensus_as_of_utc=_timestamp(row["consensus_as_of_utc"]),
        consensus=float(row["consensus"]),
        initial_actual=float(row["initial_actual"]),
        previous_as_displayed=_optional_float(row.get("previous_as_displayed")),
        revised_previous=_optional_float(row.get("revised_previous")),
        unit=row["unit"],
        source_url=row["source_url"],
        snapshot_sha256=row["snapshot_sha256"].lower(),
    )


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("macro timestamp is missing timezone information")
    return parsed.astimezone(UTC)


def _optional_float(value):
    return float(value) if value not in (None, "") else None


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value):
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value
