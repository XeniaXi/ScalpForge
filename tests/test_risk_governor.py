from datetime import UTC, datetime, timedelta

from scalpforge_core.models import MarketTick, PortfolioState, Side, SignalCandidate
from scalpforge_risk.governor import RiskGovernor, RiskLimits


def signal(score: float = 0.8) -> SignalCandidate:
    return SignalCandidate(
        side=Side.LONG,
        score=score,
        regime="directional",
        regime_confidence=0.8,
        entry_price=2400,
        stop_price=2398,
        take_profit_price=2404,
        model_version="test",
    )


def portfolio(**changes: float) -> PortfolioState:
    values = dict(equity=10_000, balance=10_000, daily_pnl=0, peak_equity=10_000)
    values.update(changes)
    return PortfolioState(**values)


def test_approves_and_sizes_from_max_loss() -> None:
    tick = MarketTick(occurred_at=datetime.now(UTC), bid=2400, ask=2400.2, source="test")
    decision = RiskGovernor(RiskLimits()).evaluate(signal(), tick, portfolio())
    assert decision.approved
    assert decision.max_loss_amount == 25
    assert decision.quantity_lots == 0.12


def test_fails_closed_for_stale_price_and_low_score() -> None:
    old = datetime.now(UTC) - timedelta(seconds=10)
    tick = MarketTick(occurred_at=old, received_at=old, bid=2400, ask=2400.2, source="test")
    decision = RiskGovernor(RiskLimits()).evaluate(signal(0.2), tick, portfolio())
    assert not decision.approved
    assert "price is stale" in decision.reasons
    assert "signal score below threshold" in decision.reasons


def test_denies_after_daily_loss_limit() -> None:
    tick = MarketTick(occurred_at=datetime.now(UTC), bid=2400, ask=2400.2, source="test")
    decision = RiskGovernor(RiskLimits()).evaluate(signal(), tick, portfolio(daily_pnl=-100))
    assert not decision.approved
    assert "daily loss limit reached" in decision.reasons
