from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class VirtualStrategyMetrics:
    strategy_id: str
    initial_equity: float
    equity: float
    net_pnl: float
    opportunity_count: int
    executed_count: int
    rejected_count: int
    expectancy: float
    maximum_drawdown_pct: float


@dataclass
class VirtualStrategyLedger:
    """An isolated research ledger; capital and outcomes never leak between strategies."""

    strategy_id: str
    initial_equity: float = 50.0
    equity: float = field(init=False)
    peak_equity: float = field(init=False)
    opportunity_count: int = 0
    executed_count: int = 0
    rejected_count: int = 0
    net_results: list[float] = field(default_factory=list)
    _recorded: set[UUID] = field(default_factory=set)
    _maximum_drawdown_pct: float = 0.0

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id must be non-empty")
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        self.equity = self.initial_equity
        self.peak_equity = self.initial_equity

    def record(self, opportunity_id: UUID, *, executed: bool, net_pnl: float = 0.0) -> None:
        if opportunity_id in self._recorded:
            raise ValueError("opportunity already recorded")
        if not executed and net_pnl != 0:
            raise ValueError("rejected opportunities cannot change virtual equity")
        self._recorded.add(opportunity_id)
        self.opportunity_count += 1
        if executed:
            self.executed_count += 1
            self.net_results.append(net_pnl)
            self.equity += net_pnl
            self.peak_equity = max(self.peak_equity, self.equity)
            drawdown = (self.peak_equity - self.equity) / self.peak_equity * 100
            self._maximum_drawdown_pct = max(self._maximum_drawdown_pct, drawdown)
        else:
            self.rejected_count += 1

    def metrics(self) -> VirtualStrategyMetrics:
        expectancy = sum(self.net_results) / len(self.net_results) if self.net_results else 0.0
        return VirtualStrategyMetrics(
            strategy_id=self.strategy_id,
            initial_equity=self.initial_equity,
            equity=self.equity,
            net_pnl=self.equity - self.initial_equity,
            opportunity_count=self.opportunity_count,
            executed_count=self.executed_count,
            rejected_count=self.rejected_count,
            expectancy=expectancy,
            maximum_drawdown_pct=self._maximum_drawdown_pct,
        )


class StrategyPortfolioLab:
    def __init__(self, strategy_ids: list[str], *, initial_equity: float = 50.0) -> None:
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("strategy IDs must be unique")
        self.ledgers = {
            strategy_id: VirtualStrategyLedger(strategy_id, initial_equity)
            for strategy_id in strategy_ids
        }

    def ledger(self, strategy_id: str) -> VirtualStrategyLedger:
        try:
            return self.ledgers[strategy_id]
        except KeyError as exc:
            raise ValueError(f"unknown strategy: {strategy_id}") from exc

    def metrics(self) -> list[VirtualStrategyMetrics]:
        return [self.ledgers[key].metrics() for key in sorted(self.ledgers)]
