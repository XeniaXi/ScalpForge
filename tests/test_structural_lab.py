from scalpforge_strategy.structural_lab import stationary_block_interval


def test_block_bootstrap_is_deterministic_and_preserves_constant_mean() -> None:
    first = stationary_block_interval([2.0] * 100, 100, 10, 7)
    second = stationary_block_interval([2.0] * 100, 100, 10, 7)
    assert first == second == (2.0, 2.0)


def test_block_bootstrap_handles_no_events() -> None:
    assert stationary_block_interval([], 100, 10, 7) == (0.0, 0.0)
