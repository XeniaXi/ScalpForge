from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import NormalizedEvent
from .relevance import score_gold_relevance

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def build_url(query: str, *, max_records: int = 250) -> str:
    parameters = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_records,
        "sort": "datedesc",
    }
    return f"{ENDPOINT}?{urlencode(parameters)}"


def fetch(query: str, *, max_records: int = 250, timeout: int = 30) -> dict[str, object]:
    request = Request(
        build_url(query, max_records=max_records),
        headers={"User-Agent": "ScalpForge/0.1 research-only"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        return json.load(response)


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def normalize(
    payload: dict[str, object], *, received_at: datetime | None = None
) -> list[NormalizedEvent]:
    received_at = received_at or datetime.now(UTC)
    events: list[NormalizedEvent] = []
    for article in payload.get("articles", []):
        if not isinstance(article, dict) or not article.get("title") or not article.get("seendate"):
            continue
        title = str(article["title"])
        url = str(article.get("url", "")) or None
        score, reasons = score_gold_relevance(title)
        key_material = f"{url}|{article['seendate']}|{title}".encode()
        events.append(
            NormalizedEvent(
                event_key="gdelt:" + hashlib.sha256(key_material).hexdigest(),
                source=str(article.get("domain", "gdelt")),
                event_type="world_news",
                title=title,
                occurred_at=_parse_time(str(article["seendate"])),
                published_at=_parse_time(str(article["seendate"])),
                received_at=received_at,
                url=url,
                language=str(article.get("language", "")) or None,
                source_country=str(article.get("sourcecountry", "")) or None,
                relevance_score=score,
                relevance_reasons=reasons,
                timing_quality="gdelt_seen_time",
                reaction_eligible=False,
                raw=article,
            )
        )
    return events
