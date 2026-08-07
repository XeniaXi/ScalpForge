from datetime import UTC, datetime, timedelta

from scalpforge_backtest import BacktestConfig, BacktestEngine, TradeIntent
from scalpforge_backtest.models import ExitReason
from scalpforge_core.models import MarketTick, Side


def tick(at: datetime, bid: float, ask: float) -> MarketTick:
    return MarketTick(
        occurred_at=at,
        received_at=at,
        bid=bid,
        ask=ask,
        source="fixture",
    )


def test_long_fill_waits_for_latency_and_includes_costs() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ticks = [
        tick(start, 2400.0, 2400.2),
        tick(start + timedelta(seconds=1), 2404.5, 2404.7),
        tick(start + timedelta(seconds=2), 2409.0, 2409.2),
    ]
    intent = TradeIntent(start, Side.LONG, stop_distance=2, take_profit_distance=4)
    config = BacktestConfig(
        entry_latency_ms=250,
        slippage_bps=0.5,
        commission_per_lot_per_side=1.0,
    )
    result = BacktestEngine(config).run(ticks, [intent])
    trade = result.trades[0]
    assert trade.opened_at == start + timedelta(seconds=1)
    assert trade.entry_price > 2404.7
    assert trade.exit_price < 2409.0
    assert trade.costs == 0.2
    assert trade.net_pnl == round(trade.gross_pnl - trade.costs, 2)
    assert trade.exit_reason is ExitReason.TAKE_PROFIT


def test_stop_loss_and_drawdown_are_recorded() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ticks = [
        tick(start, 2400.0, 2400.2),
        tick(start + timedelta(seconds=1), 2400.0, 2400.2),
        tick(start + timedelta(seconds=2), 2397.5, 2397.7),
    ]
    intent = TradeIntent(start, Side.LONG, stop_distance=2, take_profit_distance=4)
    result = BacktestEngine(BacktestConfig(entry_latency_ms=1)).run(ticks, [intent])
    assert result.trades[0].exit_reason is ExitReason.STOP
    assert result.trades[0].net_pnl < 0
    assert result.metrics is not None
    assert result.metrics.max_drawdown_pct > 0
    assert result.metrics.expectancy == result.trades[0].net_pnl


def test_intent_after_last_tick_is_rejected() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ticks = [tick(start, 2400, 2400.2)]
    intent = TradeIntent(start, Side.LONG, stop_distance=2, take_profit_distance=4)
    result = BacktestEngine(BacktestConfig(entry_latency_ms=1_000)).run(ticks, [intent])
    assert result.rejected_intents == 1
    assert result.trades == []
