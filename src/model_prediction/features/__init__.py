"""Shared feature pipeline. Import modules for their @register_feature side effects."""

from . import (  # noqa: F401
    elo_ratings,
    head_to_head,
    lineup_strength,
    tennis_surface,
    trends,
)
from .base import FeatureContext, FeatureStore, GameRecord, register_feature, registered_features

__all__ = [
    "FeatureContext",
    "FeatureStore",
    "GameRecord",
    "register_feature",
    "registered_features",
]
