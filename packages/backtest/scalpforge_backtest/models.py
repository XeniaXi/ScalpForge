from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from scalpforge_core.models import Side


class ExitReason(StrEnum):
    STOP = "stop"
    TAKE_PROFIT = "take_profit"
    END_OF_DATA = "end_of_data"


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    quantity_lots: float = 0.10
    contract_ounces_per_lot: float = 100.0
    entry_latency_ms: int = 250
    slippage_bps: float = 0.5
    commission_per_lot_per_side: float = 0.0

    def __post_init__(self) -> None:
        positive = (
            self.initial_equity,
            self.quantity_lots,
            self.contract_ounces_per_lot,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("equity, quantity, and contract size must be positive")
        negative_cost = (
            self.entry_latency_ms < 0
            or self.slippage_bps < 0
            or self.commission_per_lot_per_side < 0
        )
        if negative_cost:
            raise ValueError("execution costs and latency cannot be negative")


@dataclass(frozen=True)
class TradeIntent:
    generated_at: datetime
    side: Side
    stop_distance: float
    take_profit_distance: float
    score: float = 1.0

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between zero and one")


@dataclass
class SimulatedPosition:
    side: Side
    quantity_lots: float
    signal_at: datetime
    opened_at: datetime
    entry_price: float
    stop_price: float
    take_profit_price: float
    entry_commission: float


@dataclass(frozen=True)
class SimulatedTrade:
    side: Side
    signal_at: datetime
    opened_at: datetime
    closed_at: datetime
    entry_price: float
    exit_price: float
    quantity_lots: float
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: ExitReason


@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: float
    final_equity: float
    net_pnl: float
    return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float
    profit_factor: float | None
    expectancy: float
    return_over_drawdown: float | None


@dataclass(frozen=True)
class BacktestResult:
    trades: list[SimulatedTrade] = field(default_factory=list)
    metrics: BacktestMetrics | None = None
    rejected_intents: int = 0
