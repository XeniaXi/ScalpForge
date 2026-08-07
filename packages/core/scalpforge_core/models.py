from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class EventKind(StrEnum):
    MARKET_TICK = "market_tick"
    NEWS = "news"


class EventEnvelope(BaseModel):
    """Versioned, idempotent transport wrapper shared by ingestion and replay."""

    event_id: UUID = Field(default_factory=uuid4)
    schema_version: int = Field(default=1, ge=1)
    kind: EventKind
    occurred_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    source_sequence: str = ""
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, object]


class MarketTick(BaseModel):
    instrument: str = "XAUUSD"
    occurred_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    source: str
    source_sequence: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000


class NewsEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    published_at: datetime
    source: str
    headline: str
    event_type: str
    forecast: float | None = None
    actual: float | None = None
    previous: float | None = None
    raw_reference: str | None = None


class CausalHypothesis(BaseModel):
    event_id: UUID | None
    label: str
    probability: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class MovementCase(BaseModel):
    case_id: UUID = Field(default_factory=uuid4)
    instrument: str
    window_started_at: datetime
    window_ended_at: datetime
    return_bps: float
    hypotheses: list[CausalHypothesis]
    primary_cause: str | None = None
    attribution_confidence: float = Field(ge=0, le=1)


class SignalCandidate(BaseModel):
    signal_id: UUID = Field(default_factory=uuid4)
    instrument: str = "XAUUSD"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    side: Side
    score: float = Field(ge=0, le=1)
    regime: str
    regime_confidence: float = Field(ge=0, le=1)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    take_profit_price: float = Field(gt=0)
    feature_snapshot: dict[str, float | str | bool] = Field(default_factory=dict)
    catalyst_case_id: UUID | None = None
    model_version: str


class PortfolioState(BaseModel):
    equity: float = Field(gt=0)
    balance: float = Field(gt=0)
    daily_pnl: float = 0
    peak_equity: float = Field(gt=0)
    open_risk: float = Field(default=0, ge=0)


class RiskDecision(BaseModel):
    approved: bool
    reasons: list[str]
    quantity_lots: float = Field(default=0, ge=0)
    max_loss_amount: float = Field(default=0, ge=0)


class DecisionRecord(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signal: SignalCandidate
    risk: RiskDecision
    execution_mode: str = "paper"
    config_version: str
    status: str
