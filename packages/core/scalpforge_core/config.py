from enum import StrEnum

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCALPFORGE_", env_file=".env", extra="ignore")

    environment: str = "local"
    trading_mode: TradingMode = TradingMode.PAPER
    database_url: str = "postgresql+asyncpg://scalpforge:scalpforge@localhost:5432/scalpforge"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    max_risk_per_trade_pct: float = 0.25
    max_daily_loss_pct: float = 1.0
    max_drawdown_pct: float = 5.0
    max_spread_bps: float = 8.0
    max_price_age_ms: int = 1500
    min_signal_score: float = 0.65
    mt4_bridge_enabled: bool = False

    @model_validator(mode="after")
    def deny_live_trading(self) -> "Settings":
        if self.trading_mode is TradingMode.LIVE or self.mt4_bridge_enabled:
            raise ValueError("Live/MT4 execution is intentionally disabled in this release")
        return self
