"""Unified NBA model (shadow-qualified; versioned in config/git, not filenames)."""

from __future__ import annotations

from .basketball import BasketballModel, UpcomingGame  # noqa: F401

NBA_MODEL_VERSION = "nba-elo-trend-v1"


def nba_model() -> BasketballModel:
    return BasketballModel(
        sport="nba",
        version=NBA_MODEL_VERSION,
        margin_sd=12.0,
        total_sd=18.5,
        league="NBA",
    )
