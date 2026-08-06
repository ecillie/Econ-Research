"""Airline business-model spectrum data and scoring pipeline."""

from .config import INDEX_NAMES, METRIC_SPECS, MetricSpec
from .scoring import ScoreResults, score_business_models

__all__ = [
    "INDEX_NAMES",
    "METRIC_SPECS",
    "MetricSpec",
    "ScoreResults",
    "score_business_models",
]

