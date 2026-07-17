"""Unified NFL score-market model — shadow-qualified moneyline."""

from __future__ import annotations

from .basketball import BasketballModel, UpcomingGame  # noqa: F401

NFL_MODEL_VERSION = "nfl-elo-trend-v1"


def nfl_model() -> BasketballModel:
    return BasketballModel(
        sport="nfl",
        version=NFL_MODEL_VERSION,
        margin_sd=13.5,
        total_sd=14.5,
        league="NFL",
    )
