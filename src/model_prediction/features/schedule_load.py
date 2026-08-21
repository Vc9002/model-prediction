"""Point-in-time schedule-load features shared by validation and forward paths.

Only completed games strictly before the event start are eligible. Rest is
capped at seven days so offseason and long-break gaps do not dominate the
coefficient. Travel is deliberately absent because the repository does not yet
carry a versioned venue-coordinate history.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .base import GameRecord


@dataclass(frozen=True)
class TeamScheduleLoad:
    rest_days_capped: int
    back_to_back: bool
    games_last_7_days: int
    available: bool


def team_schedule_load(
    games: Iterable[GameRecord],
    team: str,
    event_start: datetime,
) -> TeamScheduleLoad:
    """Return schedule load known immediately before ``event_start``."""
    prior = sorted(
        (game for game in games if team in (game.home_team, game.away_team) and game.start < event_start),
        key=lambda game: game.start,
    )
    if not prior:
        return TeamScheduleLoad(0, False, 0, False)
    rest_days = max(0, min(7, (event_start - prior[-1].start).days))
    window_start = event_start - timedelta(days=7)
    games_last_7_days = sum(window_start <= game.start < event_start for game in prior)
    return TeamScheduleLoad(
        rest_days_capped=rest_days,
        back_to_back=rest_days <= 1,
        games_last_7_days=games_last_7_days,
        available=True,
    )


def matchup_schedule_load(
    games: Iterable[GameRecord],
    home_team: str,
    away_team: str,
    event_start: datetime,
) -> dict[str, float]:
    """Return selection-neutral home-minus-away schedule features."""
    history = tuple(games)
    home = team_schedule_load(history, home_team, event_start)
    away = team_schedule_load(history, away_team, event_start)
    available = home.available and away.available
    if not available:
        return {
            "rest_disparity": 0.0,
            "back_to_back_gap": 0.0,
            "games_last_7_gap": 0.0,
            "schedule_available": 0.0,
        }
    return {
        "rest_disparity": float(home.rest_days_capped - away.rest_days_capped),
        "back_to_back_gap": float(int(home.back_to_back) - int(away.back_to_back)),
        "games_last_7_gap": float(home.games_last_7_days - away.games_last_7_days),
        "schedule_available": 1.0,
    }
