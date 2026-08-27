"""Point-in-time schedule-load features shared by validation and forward paths.

Only completed games strictly before the event start are eligible. Rest is
capped at seven days so offseason and long-break gaps do not dominate the
coefficient. Travel *distance* is deliberately absent because the repository
does not yet carry a versioned venue-coordinate history; timezone
displacement between the two cities is the available jetlag proxy.
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
            "travel_tz_displacement": 0.0,
            "schedule_available": 0.0,
        }

    travel_tz = travel_timezone_displacement(away_team, home_team)
    return {
        "rest_disparity": float(home.rest_days_capped - away.rest_days_capped),
        "back_to_back_gap": float(int(home.back_to_back) - int(away.back_to_back)),
        "games_last_7_gap": float(home.games_last_7_days - away.games_last_7_days),
        "travel_tz_displacement": float(travel_tz),
        "schedule_available": 1.0,
    }


# Standard North American team timezone offsets for circadian jetlag modeling
TEAM_TIMEZONE_OFFSETS: dict[str, int] = {
    # Eastern
    "BOS": -5,
    "NYY": -5,
    "NYM": -5,
    "PHI": -5,
    "BAL": -5,
    "WSH": -5,
    "TB": -5,
    "MIA": -5,
    "ATL": -5,
    "PIT": -5,
    "CLE": -5,
    "DET": -5,
    "CIN": -5,
    "TOR": -5,
    "IND": -5,
    "CON": -5,
    "ATL_W": -5,
    "NY_W": -5,
    # Central
    "CWS": -6,
    "CHC": -6,
    "MIL": -6,
    "MIN": -6,
    "STL": -6,
    "KC": -6,
    "HOU": -6,
    "TEX": -6,
    "CHI_W": -6,
    "MIN_W": -6,
    "DAL_W": -6,
    # Mountain
    "COL": -7,
    "AZ": -7,
    "PHX_W": -7,
    # Pacific
    "LAD": -8,
    "LAA": -8,
    "SF": -8,
    "OAK": -8,
    "SD": -8,
    "SEA": -8,
    "LA_W": -8,
    "LV_W": -8,
    "SEA_W": -8,
    # WNBA canonical team names (games store uses full display names, so the
    # abbreviation entries above never matched them). Uppercase keys: the
    # lookup in travel_timezone_displacement uppercases its input. Additive --
    # existing sport keys unchanged. Washington/Indiana share MLB Eastern
    # cities; GSV and Portland are Pacific (2026 clubs).
    "ATLANTA DREAM": -5,
    "CHICAGO SKY": -6,
    "CONNECTICUT SUN": -5,
    "DALLAS WINGS": -6,
    "GOLDEN STATE VALKYRIES": -8,
    "INDIANA FEVER": -5,
    "LAS VEGAS ACES": -8,
    "LOS ANGELES SPARKS": -8,
    "MINNESOTA LYNX": -6,
    "NEW YORK LIBERTY": -5,
    "PHOENIX MERCURY": -7,
    "PORTLAND FIRE": -8,
    "SEATTLE STORM": -8,
    "TORONTO TEMPO": -5,
    "WASHINGTON MYSTICS": -5,
}


def travel_timezone_displacement(origin_team: str, destination_team: str) -> int:
    """Calculate absolute timezone difference in hours between origin and destination."""
    tz_orig = TEAM_TIMEZONE_OFFSETS.get(origin_team.upper())
    tz_dest = TEAM_TIMEZONE_OFFSETS.get(destination_team.upper())
    if tz_orig is None or tz_dest is None:
        return 0
    return abs(tz_orig - tz_dest)
