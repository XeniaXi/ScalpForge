from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from scalpforge_news.models import NormalizedEvent


@dataclass(frozen=True)
class NewsQualityReport:
    generated_at: str
    event_count: int
    unique_event_count: int
    duplicate_count: int
    source_count: int
    raw_payload_count: int
    invalid_manifest_count: int
    reaction_eligible_count: int
    average_relevance_score: float
    timing_quality: dict[str, int]
    relevance_categories: dict[str, int]
    current_health: str


def build_news_quality_report(events_path: Path, raw_root: Path) -> NewsQualityReport:
    events = (
        [
            NormalizedEvent.model_validate_json(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if events_path.exists()
        else []
    )
    invalid = 0
    payloads = 0
    for manifest_path in raw_root.rglob("*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload = manifest_path.with_name(manifest_path.name.replace(".manifest.json", ".json"))
            if (
                not payload.is_file()
                or hashlib.sha256(payload.read_bytes().rstrip(b"\n")).hexdigest()
                != manifest["sha256"]
            ):
                invalid += 1
            else:
                payloads += 1
        except (KeyError, OSError, json.JSONDecodeError):
            invalid += 1
    keys = [event.event_key for event in events]
    reasons = Counter(reason for event in events for reason in event.relevance_reasons)
    timing = Counter(event.timing_quality for event in events)
    health_path = events_path.parent / "health.latest.json"
    health = "missing"
    if health_path.exists():
        health = str(json.loads(health_path.read_text(encoding="utf-8")).get("status", "unknown"))
    return NewsQualityReport(
        generated_at=datetime.now(UTC).isoformat(),
        event_count=len(events),
        unique_event_count=len(set(keys)),
        duplicate_count=len(keys) - len(set(keys)),
        source_count=len({event.source for event in events}),
        raw_payload_count=payloads,
        invalid_manifest_count=invalid,
        reaction_eligible_count=sum(event.reaction_eligible for event in events),
        average_relevance_score=round(
            sum(event.relevance_score for event in events) / len(events), 4
        )
        if events
        else 0.0,
        timing_quality=dict(timing),
        relevance_categories=dict(reasons),
        current_health=health,
    )


def write_news_quality_report(report: NewsQualityReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
