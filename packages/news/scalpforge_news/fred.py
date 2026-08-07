from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import NormalizedEvent
from .relevance import score_gold_relevance

ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"


def fetch_series(
    series_id: str, api_key: str, *, realtime_start: str, realtime_end: str, timeout: int = 30
) -> dict[str, object]:
    query = urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
        }
    )
    request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "ScalpForge/0.1 research-only"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        return json.load(response)


def normalize_series(
    series_id: str, title: str, payload: dict[str, object], *, received_at: datetime | None = None
) -> list[NormalizedEvent]:
    received_at = received_at or datetime.now(UTC)
    score, reasons = score_gold_relevance(title)
    events: list[NormalizedEvent] = []
    for observation in payload.get("observations", []):
        if not isinstance(observation, dict) or observation.get("value") in (None, "."):
            continue
        event_time = datetime.fromisoformat(str(observation["date"])).replace(tzinfo=UTC)
        vintage = observation.get("realtime_start", "")
        events.append(
            NormalizedEvent(
                event_key=f"fred:{series_id}:{observation['date']}:{vintage}",
                source="FRED/ALFRED",
                event_type="macro_observation",
                title=title,
                occurred_at=event_time,
                published_at=event_time,
                received_at=received_at,
                actual=float(str(observation["value"])),
                relevance_score=score,
                relevance_reasons=reasons,
                timing_quality="observation_period_not_release_time",
                reaction_eligible=False,
                raw=observation,
            )
        )
    return events
