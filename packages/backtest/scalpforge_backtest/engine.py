from collections import deque
from datetime import timedelta

from scalpforge_core.models import MarketTick, Side

from scalpforge_backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    ExitReason,
    SimulatedPosition,
    SimulatedTrade,
    TradeIntent,
)


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(self, ticks: list[MarketTick], intents: list[TradeIntent]) -> BacktestResult:
        if not ticks:
            raise ValueError("backtest requires at least one tick")
        ordered_ticks = sorted(ticks, key=lambda tick: tick.occurred_at)
        pending = deque(sorted(intents, key=lambda intent: intent.generated_at))
        position: SimulatedPosition | None = None
        trades: list[SimulatedTrade] = []
        rejected = 0

        for tick in ordered_ticks:
            if position is not None:
                trade = self._maybe_close(position, tick)
                if trade is not None:
                    trades.append(trade)
                    position = None

            while pending and self._eligible_at(pending[0]) <= tick.occurred_at:
                intent = pending.popleft()
                if intent.generated_at < ordered_ticks[0].occurred_at:
                    rejected += 1
                    continue
                if position is not None:
                    rejected += 1
                    continue
                invalid_intent = (
                    intent.side is Side.FLAT
                    or intent.stop_distance <= 0
                    or intent.take_profit_distance <= 0
                )
                if invalid_intent:
                    rejected += 1
                    continue
                position = self._open(intent, tick)

        rejected += len(pending)
        if position is not None:
            trades.append(self._close(position, ordered_ticks[-1], ExitReason.END_OF_DATA))
        return BacktestResult(trades, self._metrics(trades), rejected)

    def _eligible_at(self, intent: TradeIntent):
        return intent.generated_at + timedelta(milliseconds=self.config.entry_latency_ms)

    def _adverse_slippage(self, price: float, side: Side, *, entry: bool) -> float:
        shift = price * self.config.slippage_bps / 10_000
        adverse_up = (side is Side.LONG and entry) or (side is Side.SHORT and not entry)
        return price + shift if adverse_up else price - shift

    def _open(self, intent: TradeIntent, tick: MarketTick) -> SimulatedPosition:
        quoted = tick.ask if intent.side is Side.LONG else tick.bid
        entry = self._adverse_slippage(quoted, intent.side, entry=True)
        direction = 1 if intent.side is Side.LONG else -1
        return SimulatedPosition(
            side=intent.side,
            quantity_lots=self.config.quantity_lots,
            signal_at=intent.generated_at,
            opened_at=tick.occurred_at,
            entry_price=entry,
            stop_price=entry - direction * intent.stop_distance,
            take_profit_price=entry + direction * intent.take_profit_distance,
            entry_commission=self.config.commission_per_lot_per_side
            * self.config.quantity_lots,
        )

    def _maybe_close(self, position: SimulatedPosition, tick: MarketTick) -> SimulatedTrade | None:
        executable = tick.bid if position.side is Side.LONG else tick.ask
        stop_hit = (
            executable <= position.stop_price
            if position.side is Side.LONG
            else executable >= position.stop_price
        )
        target_hit = (
            executable >= position.take_profit_price
            if position.side is Side.LONG
            else executable <= position.take_profit_price
        )
        if stop_hit:
            return self._close(position, tick, ExitReason.STOP)
        if target_hit:
            return self._close(position, tick, ExitReason.TAKE_PROFIT)
        return None

    def _close(
        self, position: SimulatedPosition, tick: MarketTick, reason: ExitReason
    ) -> SimulatedTrade:
        quoted = tick.bid if position.side is Side.LONG else tick.ask
        exit_price = self._adverse_slippage(quoted, position.side, entry=False)
        direction = 1 if position.side is Side.LONG else -1
        gross = (
            (exit_price - position.entry_price)
            * direction
            * position.quantity_lots
            * self.config.contract_ounces_per_lot
        )
        exit_commission = self.config.commission_per_lot_per_side * position.quantity_lots
        costs = position.entry_commission + exit_commission
        return SimulatedTrade(
            side=position.side,
            signal_at=position.signal_at,
            opened_at=position.opened_at,
            closed_at=tick.occurred_at,
            entry_price=entry_round(position.entry_price),
            exit_price=entry_round(exit_price),
            quantity_lots=position.quantity_lots,
            gross_pnl=round(gross, 2),
            costs=round(costs, 2),
            net_pnl=round(gross - costs, 2),
            exit_reason=reason,
        )

    def _metrics(self, trades: list[SimulatedTrade]) -> BacktestMetrics:
        equity = self.config.initial_equity
        peak = equity
        max_drawdown = 0.0
        wins = 0
        gross_profit = 0.0
        gross_loss = 0.0
        for trade in trades:
            equity += trade.net_pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
            if trade.net_pnl > 0:
                wins += 1
                gross_profit += trade.net_pnl
            elif trade.net_pnl < 0:
                gross_loss += abs(trade.net_pnl)
        count = len(trades)
        net = equity - self.config.initial_equity
        return BacktestMetrics(
            initial_equity=self.config.initial_equity,
            final_equity=round(equity, 2),
            net_pnl=round(net, 2),
            return_pct=round(net / self.config.initial_equity * 100, 4),
            max_drawdown_pct=round(max_drawdown, 4),
            trade_count=count,
            win_rate=round(wins / count, 4) if count else 0.0,
            profit_factor=round(gross_profit / gross_loss, 4) if gross_loss else None,
            expectancy=round(net / count, 2) if count else 0.0,
            return_over_drawdown=(
                round((net / self.config.initial_equity * 100) / max_drawdown, 4)
                if max_drawdown
                else None
            ),
        )


def entry_round(value: float) -> float:
    return round(value, 5)
