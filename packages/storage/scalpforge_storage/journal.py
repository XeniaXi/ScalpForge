from typing import Protocol

from scalpforge_core.models import DecisionRecord


class DecisionJournal(Protocol):
    async def append(self, record: DecisionRecord) -> None: ...


class InMemoryDecisionJournal:
    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    async def append(self, record: DecisionRecord) -> None:
        self.records.append(record.model_copy(deep=True))
