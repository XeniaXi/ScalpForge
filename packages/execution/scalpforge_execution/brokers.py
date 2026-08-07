from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from scalpforge_core.models import RiskDecision, SignalCandidate


@dataclass(frozen=True)
class ExecutionReceipt:
    order_id: UUID
    status: str
    mode: str
    submitted_at: datetime


class Broker(Protocol):
    async def submit(self, signal: SignalCandidate, risk: RiskDecision) -> ExecutionReceipt: ...


class PaperBroker:
    async def submit(self, signal: SignalCandidate, risk: RiskDecision) -> ExecutionReceipt:
        if not risk.approved:
            raise PermissionError("Risk governor denied this order")
        return ExecutionReceipt(uuid4(), "paper_accepted", "paper", datetime.now(UTC))


class MT4Bridge:
    """Future adapter boundary. Deliberately cannot transmit orders."""

    async def submit(self, signal: SignalCandidate, risk: RiskDecision) -> ExecutionReceipt:
        raise RuntimeError("MT4 execution is disabled; use PaperBroker or a demo-only adapter")
