"""News ingestion and evidence-only causal attribution."""

from .attribution import measure_reactions
from .models import NormalizedEvent, ReactionMeasurement
from .relevance import score_gold_relevance

__all__ = ["NormalizedEvent", "ReactionMeasurement", "measure_reactions", "score_gold_relevance"]
