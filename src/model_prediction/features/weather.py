"""Weather features for outdoor sports.

No weather feed is cached yet, so this module normalizes whatever payload the
caller has (ESPN summaries sometimes include gameInfo.weather) and otherwise
reports ``unavailable_from_source``. It never invents conditions.
"""

from __future__ import annotations

from typing import Any


DOME_TEAMS = {
    "Tampa Bay Rays",
    "Miami Marlins",
    "Houston Astros",
    "Texas Rangers",
    "Arizona Diamondbacks",
    "Milwaukee Brewers",
    "Toronto Blue Jays",
    "Seattle Mariners",  # retractable
}


def weather_profile(home_team: str, espn_game_info: dict[str, Any] | None = None) -> dict[str, Any]:
    if home_team in DOME_TEAMS:
        return {
            "dome_or_retractable": True,
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "dome",
        }
    weather = (espn_game_info or {}).get("weather") or {}
    temperature = weather.get("temperature")
    wind = weather.get("windSpeed") or weather.get("gust")
    if temperature is None and wind is None:
        return {
            "dome_or_retractable": False,
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "unavailable_from_source",
        }
    factor = 1.0
    try:
        if temperature is not None:
            # Roughly +1% runs per 10F above 70F, symmetric below.
            factor *= 1.0 + (float(temperature) - 70.0) / 1000.0
    except (TypeError, ValueError):
        pass
    return {
        "dome_or_retractable": False,
        "temperature_f": temperature,
        "wind_mph": wind,
        "humidity_pct": weather.get("humidity"),
        "weather_run_factor": round(max(0.9, min(1.1, factor)), 6),
        "status": "available",
    }
