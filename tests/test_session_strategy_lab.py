from datetime import UTC, datetime

import pyarrow as pa  # type: ignore[import-untyped]
from scalpforge_strategy.session_strategy_lab import (
    SessionStrategyConfig,
    _metrics,
    _outcome_trade,
    _Trade,
)


def test_session_lab_cannot_enable_real_money() -> None:
    assert "real_money" not in SessionStrategyConfig.__dataclass_fields__


def test_outcome_selects_direction_without_recomputing_execution() -> None:
    table = pa.Table.from_pylist(
        [
            {
                "h60_valid": True,
                "h60_long_gross_bps": 5.0,
                "h60_long_net_bps": 2.0,
                "h60_short_gross_bps": -5.0,
                "h60_short_net_bps": -8.0,
            }
        ]
    )
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    long_trade = _outcome_trade(
        table, 0, "asia", "immediate_breakout", 1, 60, 1, timestamp
    )
    short_trade = _outcome_trade(
        table, 0, "asia", "false_breakout_fade", -1, 60, 1, timestamp
    )
    assert long_trade.gross == 5.0
    assert long_trade.net == 2.0
    assert short_trade.gross == -5.0
    assert short_trade.net == -8.0


def test_metrics_apply_cost_stress_and_require_broad_evidence() -> None:
    trades = [
        _Trade(
            "asia",
            "immediate_breakout",
            60,
            index % 3,
            datetime(2026, 1, 1, index, tzinfo=UTC),
            5.0,
            2.0,
        )
        for index in range(20)
    ]
    metric = _metrics(
        trades,
        family_size=24,
        cfg=SessionStrategyConfig(bootstrap_samples=40, bootstrap_block_trades=5),
    )[0]
    assert metric.mean_gross_bps == 5.0
    assert metric.mean_net_bps == 2.0
    assert metric.mean_net_1_5x_cost_bps == 0.5
    assert metric.mean_net_2x_cost_bps == -1.0
    assert metric.passes_research_gate is False
