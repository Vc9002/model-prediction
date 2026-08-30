"""Alias module exporting CollegeFootballModel and cfb_model."""

from .college_football import (
    MODEL_VERSION,
    CollegeFootballModel,
    UpcomingCFBGame,
    cfb_model,
)

__all__ = [
    "MODEL_VERSION",
    "CollegeFootballModel",
    "UpcomingCFBGame",
    "cfb_model",
]
