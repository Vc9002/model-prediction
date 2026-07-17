"""Rest days, back-to-back flags, and schedule-density features.

Travel distance requires venue coordinates we do not cache yet; that field is
reported as ``unavailable_from_source`` rather than fabricated.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain import parse_utc
from .base import FeatureContext, GameRecord, register_feature


def rest_profile(games: Iterable[GameRecord], team: str, as_of_utc: str) -> dict[str, Any]:
    """Rest/back-to-back profile for one team immediately before ``as_of_utc``."""
    cutoff = parse_utc(as_of_utc)
    played = sorted(
        (game for game in games if team in (game.away_team, game.home_team) and game.start < cutoff),
        key=lambda game: game.start,
    )
    if not played:
        return {
            "team": team,
            "rest_days": None,
            "back_to_back": False,
            "games_last_7_days": 0,
            "travel_distance_km": None,
            "travel_status": "unavailable_from_source",
        }
    last = played[-1]
    rest_days = (cutoff - last.start).days
    games_last_week = sum(1 for game in played if (cutoff - game.start).days <= 7)
    return {
        "team": team,
        "rest_days": rest_days,
        "back_to_back": rest_days <= 1,
        "games_last_7_days": games_last_week,
        "travel_distance_km": None,
        "travel_status": "unavailable_from_source",
    }


@register_feature("rest_travel")
def rest_travel_snapshot(context: FeatureContext) -> dict[str, Any]:
    cutoff = f"{context.as_of_date}T00:00:00Z"
    teams = sorted({game.away_team for game in context.games} | {game.home_team for game in context.games})
    return {"teams": {team: rest_profile(context.games, team, cutoff) for team in teams}}
