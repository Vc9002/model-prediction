"""Unified WNBA model (shadow-qualified; versioned in config/git, not filenames)."""

from __future__ import annotations

from .basketball import BasketballModel, UpcomingGame  # noqa: F401

WNBA_MODEL_VERSION = "wnba-elo-trend-v1"


def wnba_model() -> BasketballModel:
    return BasketballModel(
        sport="wnba",
        version=WNBA_MODEL_VERSION,
        margin_sd=10.5,
        total_sd=15.0,
        league="WNBA",
    )
