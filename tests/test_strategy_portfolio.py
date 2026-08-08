from uuid import uuid4

import pytest
from scalpforge_strategy import StrategyPortfolioLab


def test_virtual_strategy_ledgers_are_isolated() -> None:
    lab = StrategyPortfolioLab(["momentum", "news_reaction"], initial_equity=50.0)
    lab.ledger("momentum").record(uuid4(), executed=True, net_pnl=2.0)
    lab.ledger("momentum").record(uuid4(), executed=False)
    lab.ledger("news_reaction").record(uuid4(), executed=True, net_pnl=-1.0)

    momentum = lab.ledger("momentum").metrics()
    news = lab.ledger("news_reaction").metrics()
    assert momentum.equity == 52.0
    assert momentum.rejected_count == 1
    assert news.equity == 49.0
    assert news.maximum_drawdown_pct == 2.0


def test_virtual_ledger_rejects_duplicate_opportunity() -> None:
    ledger = StrategyPortfolioLab(["breakout"]).ledger("breakout")
    opportunity_id = uuid4()
    ledger.record(opportunity_id, executed=True, net_pnl=1.0)
    with pytest.raises(ValueError, match="already recorded"):
        ledger.record(opportunity_id, executed=True, net_pnl=1.0)
