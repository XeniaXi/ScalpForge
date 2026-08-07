from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeAssessment:
    label: str
    confidence: float


class FeaturePipeline:
    """Pure transformations; online and replay paths must call the same implementation."""

    def transform(self, prices: list[float]) -> dict[str, float]:
        if len(prices) < 2:
            raise ValueError("at least two prices are required")
        returns = [(b / a) - 1 for a, b in zip(prices, prices[1:], strict=True)]
        return {
            "return": prices[-1] / prices[0] - 1,
            "realized_volatility": (sum(r * r for r in returns) / len(returns)) ** 0.5,
            "momentum": sum(returns),
        }


class RegimeDetector:
    def detect(self, features: dict[str, float]) -> RegimeAssessment:
        vol = features["realized_volatility"]
        momentum = abs(features["momentum"])
        if vol > 0.002:
            return RegimeAssessment("high_volatility", min(0.99, 0.6 + vol * 20))
        if momentum > 0.001:
            return RegimeAssessment("directional", min(0.95, 0.6 + momentum * 30))
        return RegimeAssessment("range", 0.7)
