from scalpforge_strategy.feasibility import (
    FeasibilityConfig,
    _breakout_side,
    _net,
)


def test_breakout_uses_only_prior_level() -> None:
    assert _breakout_side(101.0, 100.0, 99.0, 0.25) == 1
    assert _breakout_side(98.0, 100.0, 99.0, 0.25) == -1
    assert _breakout_side(99.5, 100.0, 99.0, 0.25) == 0


def test_oracle_components_still_pay_executable_costs() -> None:
    bids = [100.0, 101.0]
    asks = [100.5, 101.5]
    long_without_slip = _net(0, 1, 1, bids, asks, 0)
    long_with_slip = _net(0, 1, 1, bids, asks, 0.5)
    assert long_with_slip < long_without_slip


def test_feasibility_has_no_live_trading_configuration() -> None:
    assert "real_money" not in FeasibilityConfig.__dataclass_fields__
