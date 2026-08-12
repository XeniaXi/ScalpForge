import pyarrow as pa
from scalpforge_strategy.shock_resiliency_lab import (
    ShockResiliencyConfig,
    _robust_threshold,
    _utc_timestamps,
    classify_shock,
)


def test_robust_threshold_uses_training_distribution() -> None:
    values = [0.1 + index / 10_000 for index in range(200)]
    assert _robust_threshold(values, 6) > 0.1


def test_shock_classifier_fades_recovery_and_continues_persistence() -> None:
    cfg = ShockResiliencyConfig()
    fade = {
        "return_5s": 0.001,
        "return_1s": -0.0002,
        "spread_shock_ratio": 1.0,
        "tick_intensity_ratio": 2.0,
    }
    continuation = {**fade, "return_1s": 0.0002}
    assert classify_shock(fade, cfg) == -1
    assert classify_shock(continuation, cfg) == 1


def test_classifier_abstains_when_spread_has_not_recovered() -> None:
    cfg = ShockResiliencyConfig()
    row = {
        "return_5s": 0.001,
        "return_1s": 0.0002,
        "spread_shock_ratio": 2.0,
        "tick_intensity_ratio": 2.0,
    }
    assert classify_shock(row, cfg) == 0


def test_arrow_timestamps_do_not_require_optional_timezone_database() -> None:
    column = pa.array([0, 1_000_000], type=pa.timestamp("us", tz="UTC"))
    assert [value.isoformat() for value in _utc_timestamps(column)] == [
        "1970-01-01T00:00:00+00:00",
        "1970-01-01T00:00:01+00:00",
    ]
