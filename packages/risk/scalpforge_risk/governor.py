from dataclasses import dataclass
from datetime import UTC, datetime

from scalpforge_core.models import MarketTick, PortfolioState, RiskDecision, Side, SignalCandidate


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: float = 0.25
    max_daily_loss_pct: float = 1.0
    max_drawdown_pct: float = 5.0
    max_spread_bps: float = 8.0
    max_price_age_ms: int = 1500
    min_signal_score: float = 0.65
    contract_ounces_per_lot: float = 100.0


class RiskGovernor:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate(
        self, signal: SignalCandidate, tick: MarketTick, portfolio: PortfolioState
    ) -> RiskDecision:
        reasons: list[str] = []
        age_ms = (datetime.now(UTC) - tick.received_at).total_seconds() * 1000
        drawdown_pct = max(
            0.0,
            (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100,
        )
        daily_loss_pct = max(0.0, -portfolio.daily_pnl / portfolio.balance * 100)

        if signal.side is Side.FLAT:
            reasons.append("flat signal")
        if signal.score < self.limits.min_signal_score:
            reasons.append("signal score below threshold")
        if age_ms > self.limits.max_price_age_ms:
            reasons.append("price is stale")
        if tick.spread_bps > self.limits.max_spread_bps:
            reasons.append("spread exceeds limit")
        if daily_loss_pct >= self.limits.max_daily_loss_pct:
            reasons.append("daily loss limit reached")
        if drawdown_pct >= self.limits.max_drawdown_pct:
            reasons.append("drawdown limit reached")
        stop_distance = abs(signal.entry_price - signal.stop_price)
        if stop_distance <= 0:
            reasons.append("invalid stop distance")
        if reasons:
            return RiskDecision(approved=False, reasons=reasons)

        max_loss = portfolio.equity * self.limits.max_risk_per_trade_pct / 100
        quantity = max_loss / (stop_distance * self.limits.contract_ounces_per_lot)
        return RiskDecision(
            approved=True,
            reasons=["all independent risk checks passed"],
            quantity_lots=round(quantity, 2),
            max_loss_amount=round(max_loss, 2),
        )
