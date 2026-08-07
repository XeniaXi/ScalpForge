from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class NormalizedEvent(BaseModel):
    event_key: str
    source: str
    event_type: str
    title: str
    occurred_at: datetime
    published_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    url: str | None = None
    language: str | None = None
    source_country: str | None = None
    relevance_score: float = Field(default=0, ge=0, le=1)
    relevance_reasons: list[str] = Field(default_factory=list)
    timing_quality: str = "approximate"
    reaction_eligible: bool = False
    forecast: float | None = None
    actual: float | None = None
    previous: float | None = None
    raw: dict[str, object] = Field(default_factory=dict)


class ReactionMeasurement(BaseModel):
    event_key: str
    instrument: str
    event_time: datetime
    window_seconds: int
    before_time: datetime
    after_time: datetime
    before_mid: float = Field(gt=0)
    after_mid: float = Field(gt=0)
    return_bps: float
    attribution_status: str = "candidate_only"
