from datetime import UTC, datetime, timedelta

import pyarrow as pa
from scalpforge_strategy.path_management_lab import (
    PathManagementConfig,
    Policy,
    _utc_timestamps,
    policies,
    simulate_path,
)


def _quotes(values):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [(start + timedelta(seconds=i), bid, ask) for i, (bid, ask) in enumerate(values)]


def test_fixed_policy_family_is_preregistered_and_bounded() -> None:
    candidates = policies(PathManagementConfig())
    assert len(candidates) == 252
    assert len({item.policy_id for item in candidates}) == len(candidates)


def test_long_target_uses_executable_bid_and_charges_slippage() -> None:
    policy = Policy(0, 0, 60, 8, 4)
    result = simulate_path(
        1, _quotes([(100.00, 100.01), (100.06, 100.07)]), policy, 0.5, 0
    )
    assert result is not None
    assert result["exit_reason"] == "target"
    assert result["net_bps"] == result["gross_bps"] - 1.0


def test_pullback_can_abstain_when_never_filled() -> None:
    policy = Policy(5, 2, 60, 8, 8)
    result = simulate_path(
        1,
        _quotes([(100, 100.01), (100.005, 100.015), (100.01, 100.02)]),
        policy,
        0.5,
        0,
    )
    assert result is None


def test_short_stop_uses_executable_ask() -> None:
    policy = Policy(0, 0, 60, 4, 12)
    result = simulate_path(-1, _quotes([(100, 101), (100.5, 102)]), policy, 0.5, 0)
    assert result is not None
    assert result["exit_reason"] == "stop"


def test_decision_latency_prevents_same_bar_entry() -> None:
    policy = Policy(5, 0, 60, 8, 8)
    result = simulate_path(
        1, _quotes([(100, 100.01), (101, 101.01), (102, 102.01)]), policy, 0.5
    )
    assert result is not None
    assert result["entered_at"] == datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=1)


def test_arrow_utc_conversion_does_not_require_timezone_database() -> None:
    column = pa.array([0, 1_000_000], type=pa.timestamp("us", tz="UTC"))
    assert _utc_timestamps(column) == [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC),
    ]
