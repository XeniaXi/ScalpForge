from scalpforge_strategy import PromotionEvidence, ResearchCouncil


def evidence(**overrides: object) -> PromotionEvidence:
    values: dict[str, object] = {
        "net_expectancy_r": 0.08,
        "probability_of_positive_expectancy": 0.97,
        "maximum_drawdown_pct": 3.0,
        "trade_count": 350,
        "profitable_walk_forward_folds": 4,
        "total_walk_forward_folds": 5,
        "cost_stress_multiple": 2.0,
    }
    values.update(overrides)
    return PromotionEvidence(**values)  # type: ignore[arg-type]


def test_default_council_has_ten_distinct_mandates() -> None:
    mandates = ResearchCouncil().mandates
    assert len(mandates) == 10
    assert len({item.advisor_id for item in mandates}) == 10
    assert mandates[-1].advisor_id == "validation_risk_veto"


def test_candidate_must_pass_every_promotion_gate() -> None:
    decision = ResearchCouncil().evaluate(evidence())
    assert decision.promoted
    assert not decision.reasons


def test_gate_fails_closed_on_weak_or_contaminated_evidence() -> None:
    decision = ResearchCouncil().evaluate(
        evidence(net_expectancy_r=-0.01, trade_count=50, holdout_touched=True)
    )
    assert not decision.promoted
    assert "net expectancy is not positive after costs" in decision.reasons
    assert "fewer than 200 out-of-sample trades" in decision.reasons
    assert "final holdout has already influenced development" in decision.reasons
