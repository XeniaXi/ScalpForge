from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from scalpforge_core.config import Settings
from scalpforge_core.models import DecisionRecord, MarketTick, PortfolioState, SignalCandidate
from scalpforge_execution.brokers import PaperBroker
from scalpforge_risk.governor import RiskGovernor, RiskLimits
from scalpforge_storage.journal import InMemoryDecisionJournal

settings = Settings()
journal = InMemoryDecisionJournal()
broker = PaperBroker()
governor = RiskGovernor(
    RiskLimits(
        max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
        max_spread_bps=settings.max_spread_bps,
        max_price_age_ms=settings.max_price_age_ms,
        min_signal_score=settings.min_signal_score,
    )
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="ScalpForge", version="0.1.0", lifespan=lifespan)


class EvaluationRequest(BaseModel):
    signal: SignalCandidate
    tick: MarketTick
    portfolio: PortfolioState


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "trading_mode": settings.trading_mode.value}


@app.post("/v1/decisions/evaluate", response_model=DecisionRecord)
async def evaluate(request: EvaluationRequest) -> DecisionRecord:
    risk = governor.evaluate(request.signal, request.tick, request.portfolio)
    status = "risk_denied"
    if risk.approved:
        receipt = await broker.submit(request.signal, risk)
        status = receipt.status
    record = DecisionRecord(
        signal=request.signal,
        risk=risk,
        execution_mode=settings.trading_mode.value,
        config_version="scaffold-v1",
        status=status,
    )
    await journal.append(record)
    return record
