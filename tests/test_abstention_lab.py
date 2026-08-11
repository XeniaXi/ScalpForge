from scalpforge_strategy.abstention_lab import (
    NUMERIC_FEATURES,
    AbstentionLabConfig,
    _fit,
    _predict,
    _select_threshold,
)


def _row(index: int, net: float) -> dict:
    row = {name: float(index % 5) for name in NUMERIC_FEATURES}
    row.update(
        {
            "window": ("asia", "london_open", "new_york_open")[index % 3],
            "net_bps": net,
            "gross_bps": net + 2.5,
        }
    )
    return row


def test_ridge_prediction_is_deterministic() -> None:
    rows = [_row(index, float(index % 5) - 1.0) for index in range(60)]
    model = _fit(rows, penalty=10.0)
    assert _predict(model, rows[0]) == _predict(model, rows[0])


def test_validation_can_choose_abstention() -> None:
    rows = [_row(index, -2.0) for index in range(30)]
    model = _fit(rows, penalty=10.0)
    threshold, chosen, mean = _select_threshold(
        model,
        rows,
        AbstentionLabConfig(
            bootstrap_samples=40,
            bootstrap_block_trades=5,
            minimum_validation_trades=5,
        ),
    )
    assert threshold is None
    assert chosen == []
    assert mean == 0.0


def test_abstention_lab_cannot_enable_real_money() -> None:
    assert "real_money" not in AbstentionLabConfig.__dataclass_fields__
