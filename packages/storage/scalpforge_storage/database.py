from datetime import datetime
from uuid import UUID

from scalpforge_core.models import EventEnvelope, MarketTick
from sqlalchemy import DateTime, Float, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MarketTickRow(Base):
    __tablename__ = "market_ticks"
    __table_args__ = (
        UniqueConstraint("instrument", "occurred_at", "source", name="uq_market_tick_source"),
        Index("ix_market_ticks_instrument_time", "instrument", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument: Mapped[str] = mapped_column(String(32), nullable=False)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    @classmethod
    def from_domain(cls, tick: MarketTick) -> "MarketTickRow":
        return cls(**tick.model_dump())


class EventEnvelopeRow(Base):
    __tablename__ = "event_envelopes"

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    @classmethod
    def from_domain(cls, event: EventEnvelope) -> "EventEnvelopeRow":
        values = event.model_dump(mode="python")
        values["kind"] = event.kind.value
        return cls(**values)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
