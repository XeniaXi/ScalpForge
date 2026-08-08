from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdvisorMandate:
    """A falsifiable research mandate, not permission to trade."""

    advisor_id: str
    hypothesis: str
    allowed_regimes: tuple[str, ...]
    required_features: tuple[str, ...]
    primary_failure_mode: str


@dataclass(frozen=True)
class PromotionEvidence:
    net_expectancy_r: float
    probability_of_positive_expectancy: float
    maximum_drawdown_pct: float
    trade_count: int
    profitable_walk_forward_folds: int
    total_walk_forward_folds: int
    cost_stress_multiple: float
    holdout_touched: bool = False


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    reasons: tuple[str, ...]


class ResearchCouncil:
    """Registers diverse hypotheses and applies a shared, fail-closed promotion gate."""

    def __init__(self, mandates: tuple[AdvisorMandate, ...] | None = None) -> None:
        self.mandates = mandates or default_advisor_mandates()
        ids = [mandate.advisor_id for mandate in self.mandates]
        if len(ids) != len(set(ids)):
            raise ValueError("advisor IDs must be unique")

    def evaluate(self, evidence: PromotionEvidence) -> PromotionDecision:
        reasons: list[str] = []
        if evidence.holdout_touched:
            reasons.append("final holdout has already influenced development")
        if evidence.net_expectancy_r <= 0:
            reasons.append("net expectancy is not positive after costs")
        if evidence.probability_of_positive_expectancy < 0.95:
            reasons.append("positive-expectancy confidence is below 95%")
        if evidence.maximum_drawdown_pct > 5:
            reasons.append("maximum drawdown exceeds 5% research limit")
        if evidence.trade_count < 200:
            reasons.append("fewer than 200 out-of-sample trades")
        if evidence.total_walk_forward_folds < 4:
            reasons.append("fewer than four walk-forward folds")
        elif evidence.profitable_walk_forward_folds / evidence.total_walk_forward_folds < 0.75:
            reasons.append("fewer than 75% of walk-forward folds are profitable")
        if evidence.cost_stress_multiple < 2:
            reasons.append("not tested at twice the estimated trading cost")
        return PromotionDecision(promoted=not reasons, reasons=tuple(reasons))


def default_advisor_mandates() -> tuple[AdvisorMandate, ...]:
    return (
        AdvisorMandate(
            "trend_continuation",
            "Directional order flow continues after a controlled pullback.",
            ("directional",),
            ("multi_horizon_return", "trend_strength", "spread_bps"),
            "late entry after trend exhaustion",
        ),
        AdvisorMandate(
            "short_horizon_momentum",
            "Price acceleration with tick-pressure confirmation persists briefly.",
            ("directional", "high_volatility"),
            ("return_acceleration", "tick_imbalance", "spread_bps"),
            "paying peak spread into exhausted momentum",
        ),
        AdvisorMandate(
            "range_mean_reversion",
            "Non-catalyst deviations revert inside a stable range.",
            ("range",),
            ("robust_zscore", "range_stability", "catalyst_state"),
            "fading a genuine breakout",
        ),
        AdvisorMandate(
            "compression_breakout",
            "Compression followed by participation produces expansion.",
            ("range", "directional"),
            ("volatility_compression", "breakout_distance", "tick_rate_change"),
            "false breakout without participation",
        ),
        AdvisorMandate(
            "volatility_expansion",
            "A volatility transition creates a continuation window.",
            ("high_volatility",),
            ("realized_volatility", "volatility_ratio", "spread_bps"),
            "costs dominate the gross move",
        ),
        AdvisorMandate(
            "liquidity_reversal",
            "A spike with rapid quote recovery signals temporary liquidity stress.",
            ("high_volatility", "range"),
            ("price_jump", "spread_shock", "quote_recovery"),
            "reversing a news-driven repricing",
        ),
        AdvisorMandate(
            "microstructure_pressure",
            "Bid/ask arrival pressure predicts the next executable move.",
            ("directional", "range"),
            ("tick_imbalance", "quote_duration", "arrival_intensity"),
            "feed artefact that fails broker reconciliation",
        ),
        AdvisorMandate(
            "news_reaction",
            "A relevant surprise with confirming reaction continues after latency.",
            ("event", "high_volatility"),
            ("event_surprise", "attribution_confidence", "reaction_confirmation"),
            "timestamp or causal-attribution leakage",
        ),
        AdvisorMandate(
            "post_news_reversal",
            "An event move reverses when confirmation fails and liquidity normalizes.",
            ("event", "high_volatility"),
            ("reaction_overshoot", "confirmation_divergence", "spread_recovery"),
            "fading a durable macro repricing",
        ),
        AdvisorMandate(
            "validation_risk_veto",
            "No candidate passes without lineage, leakage, cost, stability, and drawdown evidence.",
            ("all",),
            ("walk_forward_metrics", "cost_stress", "data_lineage"),
            "false discovery from selection and repeated testing",
        ),
    )
