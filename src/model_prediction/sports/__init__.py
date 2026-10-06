"""Structured sports package organized by sport and bet type.

Provides modular organization:
    src/model_prediction/sports/<sport>/<bet_type>/
"""

from __future__ import annotations

import importlib
from types import ModuleType

SUPPORTED_SPORTS: tuple[str, ...] = (
    "mlb",
    "nfl",
    "nba",
    "wnba",
    "ncaaf",
    "soccer",
    "tennis",
    "cs2",
    "dota2",
    "lol",
    "valorant",
    "rainbow_six",
    "kbo",
    "npb",
)


def get_sport_module(sport: str) -> ModuleType:
    normalized = str(sport).strip().casefold()
    if normalized not in SUPPORTED_SPORTS:
        raise ValueError(f"unsupported sport: {sport}")
    return importlib.import_module(f"model_prediction.sports.{normalized}")


def get_bet_type_module(sport: str, bet_type: str) -> ModuleType:
    normalized_sport = str(sport).strip().casefold()
    normalized_bet = str(bet_type).strip().casefold()
    return importlib.import_module(f"model_prediction.sports.{normalized_sport}.{normalized_bet}")


__all__ = [
    "SUPPORTED_SPORTS",
    "get_bet_type_module",
    "get_sport_module",
]
