from __future__ import annotations

GROUPS = {
    "gold": ("gold", "xau", "bullion"),
    "federal_reserve": ("federal reserve", "fomc", "fed chair", "interest rate"),
    "inflation": ("inflation", "cpi", "pce", "consumer price"),
    "employment": ("nonfarm", "payroll", "unemployment", "jobs report"),
    "usd_yields": ("dollar", "dxy", "treasury", "bond yield"),
    "geopolitics": ("war", "missile", "sanction", "ceasefire", "invasion", "conflict"),
    "systemic_risk": ("bank failure", "banking crisis", "default", "debt ceiling"),
    "physical_demand": ("central bank gold", "gold reserve", "china gold", "india gold"),
}


def score_gold_relevance(text: str) -> tuple[float, list[str]]:
    normalized = text.casefold()
    reasons = [name for name, terms in GROUPS.items() if any(term in normalized for term in terms)]
    if not reasons:
        return 0.0, []
    score = min(1.0, 0.25 + 0.18 * len(reasons))
    if "gold" in reasons:
        score = min(1.0, score + 0.25)
    return round(score, 3), reasons
