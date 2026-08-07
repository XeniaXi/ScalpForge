from collections.abc import Sequence

from scalpforge_core.models import EventEnvelope, MarketTick
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scalpforge_storage.database import EventEnvelopeRow, MarketTickRow


class MarketDataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append_ticks(self, ticks: Sequence[MarketTick]) -> int:
        if not ticks:
            return 0
        statement = insert(MarketTickRow).values([tick.model_dump() for tick in ticks])
        statement = statement.on_conflict_do_nothing(
            constraint="uq_market_tick_source"
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount or 0


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, event: EventEnvelope) -> bool:
        values = event.model_dump(mode="python")
        values["kind"] = event.kind.value
        statement = insert(EventEnvelopeRow).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["idempotency_key"])
        result = await self.session.execute(statement)
        await self.session.commit()
        return (result.rowcount or 0) == 1
