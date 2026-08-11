from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from scalpforge_strategy.execution_clock import CausalQuoteSeries
from scalpforge_strategy.quote_pressure_lab import (
    QuotePressureConfig,
    _Event,
    _limit_fill,
    _select_fold,
)


def test_quote_pressure_lab_cannot_enable_real_money() -> None:
    assert "real_money" not in QuotePressureConfig.__dataclass_fields__


def test_limit_fill_requires_price_to_become_marketable() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    timestamps = [start + timedelta(seconds=value) for value in range(7)]
    quotes = CausalQuoteSeries(
        timestamps,
        timestamps,
        timestamps,
        [100.0, 100.0, 99.9, 99.8, 99.8, 99.8, 99.8],
        [100.2, 100.2, 100.1, 99.9, 99.9, 99.9, 99.9],
        [0] * 7,
    )
    fill = _limit_fill(quotes, 0, 1, QuotePressureConfig(maximum_gap_seconds=1))
    assert fill == (3, 100.0)


def test_fold_selection_never_uses_test_returns() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    fold = SimpleNamespace(
        fold=1,
        train_start=start,
        train_end_exclusive=start + timedelta(days=1),
        validation_start=start + timedelta(days=1),
        validation_end_exclusive=start + timedelta(days=2),
        test_start=start + timedelta(days=2),
        test_end_exclusive=start + timedelta(days=3),
    )
    events = []
    for day, net in ((0, 2.0), (1, 1.0), (2, -100.0)):
        for index in range(3):
            events.append(
                _Event(
                    start + timedelta(days=day, hours=index),
                    1,
                    "london",
                    2.0,
                    3.0,
                    1.0,
                    900,
                    "market",
                    True,
                    net + 2.0,
                    net,
                )
            )
    config = QuotePressureConfig(
        horizons_seconds=(900,),
        pressure_thresholds_bps=(1.0,),
        activity_thresholds=(2.0,),
        spread_caps_bps=(2.0,),
        sessions=("all",),
        minimum_episode_spacing_seconds=910,
        minimum_train_trades=3,
        minimum_validation_trades=3,
        minimum_test_trades=3,
    )
    selected = _select_fold(events, fold, config)
    assert selected is not None
    assert selected.validation_mean_net_bps == 1.0
    assert selected.test_mean_net_bps == -100.0
