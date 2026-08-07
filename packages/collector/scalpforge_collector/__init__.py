"""Durable, read-only MT4 market-data collection."""

from .collector import CollectionResult, collect_once

__all__ = ["CollectionResult", "collect_once"]
