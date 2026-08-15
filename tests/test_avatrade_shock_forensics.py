from scalpforge_strategy.avatrade_shock_forensics import _gates, _groups


def test_groups_preserve_direction_without_selection() -> None:
    rows = [
        {"side": 1, "net_bps": 3.0, "boundary_valid": True},
        {"side": -1, "net_bps": -1.0, "boundary_valid": True},
    ]
    result = _groups(rows, lambda row: "long" if row["side"] == 1 else "short")
    assert result["long"]["total_bps"] == 3.0
    assert result["short"]["mean_after_extra_2bps"] == -3.0


def test_evidence_gate_refuses_tiny_concentrated_sample() -> None:
    metrics = {
        "trades": 15,
        "active_days": 5,
        "direction": {
            "long": {"trades": 8, "mean_after_extra_2bps": 5.0},
            "short": {"trades": 7, "mean_after_extra_2bps": 5.0},
        },
        "leave_best_day_total_bps": 49.0,
        "best_day_positive_profit_share": 0.52,
    }
    result = _gates(metrics)
    assert result["minimum_trades"] is False
    assert result["maximum_best_day_positive_profit_share"] is False
