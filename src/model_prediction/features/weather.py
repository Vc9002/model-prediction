"""Weather features for outdoor sports.

Primary source: Open-Meteo free API (no key required).
Fallback: ESPN gameInfo.weather from summaries.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

# Per-team cache of the raw Open-Meteo hourly forecast payload. One call
# covers 7 days, so this is safe to reuse across every game a team plays
# within that window and across daily/flat re-forecasts of the same team on
# the same day (a doubleheader). Populated either lazily by live_weather
# itself or eagerly by prefetch_live_weather, which lets callers warm every
# team's forecast concurrently before a sequential per-game loop instead of
# blocking on one live HTTP round-trip per game in turn.
_FORECAST_CACHE: dict[str, dict[str, Any] | None] = {}
_FORECAST_CACHE_LOCK = threading.Lock()


def _fetch_forecast_hourly(home_team: str) -> dict[str, Any] | None:
    """Raw Open-Meteo hourly payload for a team's ballpark, cached by team.

    Returns None on any fetch/parse failure so callers fall back to the
    existing neutral defaults. Not meant to be called concurrently for the
    SAME team from multiple threads without prefetch_live_weather -- a lock
    guards the cache dict itself (cheap), but two threads racing on a cache
    miss for the same team will both make the same HTTP call once; that's
    wasteful, not incorrect, and prefetch_live_weather avoids it entirely by
    warming every distinct team up front.
    """
    with _FORECAST_CACHE_LOCK:
        if home_team in _FORECAST_CACHE:
            return _FORECAST_CACHE[home_team]
    coords = BALLPARK_COORDS.get(home_team)
    if not coords:
        with _FORECAST_CACHE_LOCK:
            _FORECAST_CACHE[home_team] = None
        return None
    lat, lon = coords
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&hourly=temperature_2m,wind_speed_10m,relative_humidity_2m"
            f"&forecast_days=7&temperature_unit=fahrenheit&wind_speed_unit=mph"
            f"&timezone=UTC"
        )
        resp = httpx.get(url, timeout=10)
        hourly = resp.json().get("hourly", {}) if resp.status_code == 200 else None
    except (httpx.HTTPError, TypeError, ValueError):
        hourly = None
    with _FORECAST_CACHE_LOCK:
        _FORECAST_CACHE[home_team] = hourly
    return hourly


def prefetch_live_weather(home_teams: Any) -> None:
    """Warm the forecast cache for every distinct team concurrently.

    Call this once with all of a slate's home teams before forecasting each
    game one at a time -- turns N sequential ~1-2s HTTP round trips into one
    concurrent batch. Safe to call with teams already cached or with dome/
    unknown-park teams; those resolve instantly with no network call.
    """
    teams = sorted({team for team in home_teams if team not in DOME_TEAMS})
    pending = [team for team in teams if team not in _FORECAST_CACHE]
    if not pending:
        return
    with ThreadPoolExecutor(max_workers=min(16, len(pending))) as pool:
        list(pool.map(_fetch_forecast_hourly, pending))


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


def run_factor_from_conditions(temperature_f: float | None, wind_mph: float | None) -> float:
    """One shared run-factor formula for live fetches AND historical DB builds.

    ~+1% runs per 10F above 70F; +0.2% per mph of wind above 10. Clamped to
    [0.90, 1.10]. Any weather source used for training or serving must go
    through this function so the feature means the same thing everywhere.
    """
    factor = 1.0
    if temperature_f is not None:
        factor *= 1.0 + (float(temperature_f) - 70.0) / 1000.0
    if wind_mph is not None:
        factor *= 1.0 + max(0.0, float(wind_mph) - 10.0) / 500.0
    return round(max(0.90, min(1.10, factor)), 4)


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
    payload = espn_game_info or {}
    weather = payload.get("weather") or {}
    if not weather and any(key in payload for key in ("temperature", "windSpeed", "gust", "humidity")):
        weather = payload
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
    try:
        factor = run_factor_from_conditions(
            float(temperature) if temperature is not None else None,
            float(wind) if wind is not None else None,
        )
    except (TypeError, ValueError):
        factor = 1.0
    return {
        "dome_or_retractable": False,
        "temperature_f": temperature,
        "wind_mph": wind,
        "humidity_pct": weather.get("humidity"),
        "weather_run_factor": factor,
        "status": "available",
    }


# ── Open-Meteo live weather ───────────────────────────────────────────────

BALLPARK_COORDS: dict[str, tuple[float, float]] = {
    "Arizona Diamondbacks": (33.4455, -112.0667),
    "Atlanta Braves": (33.8908, -84.4678),
    "Baltimore Orioles": (39.2839, -76.6216),
    "Boston Red Sox": (42.3467, -71.0972),
    "Chicago Cubs": (41.9484, -87.6553),
    "Chicago White Sox": (41.8299, -87.6338),
    "Cincinnati Reds": (39.0979, -84.5066),
    "Cleveland Guardians": (41.4961, -81.6852),
    "Colorado Rockies": (39.7562, -104.9942),
    "Detroit Tigers": (42.3390, -83.0485),
    "Houston Astros": (29.7573, -95.3555),
    "Kansas City Royals": (39.0517, -94.4804),
    "Los Angeles Angels": (33.8003, -117.8827),
    "Los Angeles Dodgers": (34.0739, -118.2400),
    "Miami Marlins": (25.7782, -80.2197),
    "Milwaukee Brewers": (43.0283, -87.9712),
    "Minnesota Twins": (44.9817, -93.2776),
    "New York Mets": (40.7571, -73.8458),
    "New York Yankees": (40.8296, -73.9262),
    # Sutter Health Park, West Sacramento (current home park; renamed from
    # "Oakland Athletics" to match ESPN's live displayName -- see
    # park_factors.py's "Athletics" key and mlb_baseline_refresh.py's
    # _LEGACY_PARK_NAMES exclusion). Was silently falling to
    # status="unknown_park"/weather_run_factor=1.0 for every Athletics home
    # game since the rename.
    "Athletics": (38.5805, -121.5195),
    "Philadelphia Phillies": (39.9061, -75.1665),
    "Pittsburgh Pirates": (40.4469, -80.0057),
    "San Diego Padres": (32.7076, -117.1570),
    "San Francisco Giants": (37.7786, -122.3893),
    "Seattle Mariners": (47.5914, -122.3325),
    "St. Louis Cardinals": (38.6226, -90.1928),
    "Tampa Bay Rays": (27.7678, -82.6529),
    "Texas Rangers": (32.7473, -97.0845),
    "Toronto Blue Jays": (43.6414, -79.3894),
    "Washington Nationals": (38.8730, -77.0074),
}


def live_weather(home_team: str, game_start_utc: str | None = None) -> dict[str, Any]:
    """Fetch current weather from Open-Meteo for a ballpark.

    Uses free Open-Meteo API — no key required.
    Returns temperature (F), wind (mph), humidity, and run factor.
    Dome teams always return factor=1.0.
    """
    if home_team in DOME_TEAMS:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "dome",
            "source": "dome",
        }

    coords = BALLPARK_COORDS.get(home_team)
    if not coords:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "unknown_park",
            "source": "none",
        }

    try:
        hourly = _fetch_forecast_hourly(home_team)
        if hourly is None:
            return {
                "temperature_f": None,
                "wind_mph": None,
                "humidity_pct": None,
                "weather_run_factor": 1.0,
                "status": "api_error",
                "source": "open-meteo",
            }
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        humids = hourly.get("relative_humidity_2m", [])
        times = hourly.get("time", [])

        if not temps:
            return {
                "temperature_f": None,
                "wind_mph": None,
                "humidity_pct": None,
                "weather_run_factor": 1.0,
                "status": "no_data",
                "source": "open-meteo",
            }

        hour_index = 0
        if game_start_utc and times:
            game_start = datetime.fromisoformat(game_start_utc).astimezone(UTC)
            parsed_hours = [datetime.fromisoformat(str(value)).replace(tzinfo=UTC) for value in times]
            hour_index = min(
                range(min(len(parsed_hours), len(temps))),
                key=lambda index: abs((parsed_hours[index] - game_start).total_seconds()),
            )

        temp = float(temps[hour_index])
        wind = float(winds[hour_index]) if len(winds) > hour_index else None
        humid = float(humids[hour_index]) if len(humids) > hour_index else None

        return {
            "temperature_f": round(temp, 1),
            "wind_mph": round(wind, 1) if wind else None,
            "humidity_pct": round(humid, 1) if humid else None,
            "weather_run_factor": run_factor_from_conditions(temp, wind),
            "status": "available",
            "source": "open-meteo",
        }
    except (httpx.HTTPError, IndexError, KeyError, TypeError, ValueError):
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "api_error",
            "source": "open-meteo",
        }


def historical_weather(home_team: str, game_start_utc: str) -> dict[str, Any]:
    """Real recorded weather for a past game, from Open-Meteo's historical archive.

    Same shape and same run-factor formula as live_weather -- this is what
    makes it safe to fit calibration against: a walk-forward backtest and a
    live serve both see 'real weather at first pitch', not a fabricated
    stand-in for one of the two.
    """
    if home_team in DOME_TEAMS:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "dome",
            "source": "dome",
        }
    coords = BALLPARK_COORDS.get(home_team)
    if not coords:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "unknown_park",
            "source": "none",
        }
    lat, lon = coords
    try:
        game_start = datetime.fromisoformat(game_start_utc).astimezone(UTC)
        day = game_start.date().isoformat()
        url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}"
            f"&start_date={day}&end_date={day}"
            f"&hourly=temperature_2m,wind_speed_10m,relative_humidity_2m"
            f"&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=UTC"
        )
        resp = httpx.get(url, timeout=10)
        if resp.status_code != 200:
            return {
                "temperature_f": None,
                "wind_mph": None,
                "humidity_pct": None,
                "weather_run_factor": 1.0,
                "status": "api_error",
                "source": "open-meteo-archive",
            }
        data = resp.json()
        hourly = data.get("hourly", {})
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        humids = hourly.get("relative_humidity_2m", [])
        times = hourly.get("time", [])
        if not temps:
            return {
                "temperature_f": None,
                "wind_mph": None,
                "humidity_pct": None,
                "weather_run_factor": 1.0,
                "status": "no_data",
                "source": "open-meteo-archive",
            }
        parsed_hours = [datetime.fromisoformat(str(value)).replace(tzinfo=UTC) for value in times]
        hour_index = min(
            range(min(len(parsed_hours), len(temps))),
            key=lambda index: abs((parsed_hours[index] - game_start).total_seconds()),
        )
        temp = float(temps[hour_index])
        wind = float(winds[hour_index]) if len(winds) > hour_index else None
        humid = float(humids[hour_index]) if len(humids) > hour_index else None
        return {
            "temperature_f": round(temp, 1),
            "wind_mph": round(wind, 1) if wind else None,
            "humidity_pct": round(humid, 1) if humid else None,
            "weather_run_factor": run_factor_from_conditions(temp, wind),
            "status": "available",
            "source": "open-meteo-archive",
        }
    except (httpx.HTTPError, IndexError, KeyError, TypeError, ValueError):
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "api_error",
            "source": "open-meteo-archive",
        }


def resolve_weather(home_team: str, game_start_utc: str | None) -> dict[str, Any]:
    """Real weather for any game, live or historical, through one code path.

    ESPN's scoreboard/summary payloads never actually carry MLB weather data
    in practice (empirically confirmed: situation.weather and gameInfo.weather
    are empty for both pregame and completed games) -- Open-Meteo is the only
    real source here. Games within Open-Meteo's forecast window use the live
    7-day forecast endpoint; anything older uses the historical archive, so a
    walk-forward backtest sees the same real conditions a live run would have.
    """
    if not game_start_utc:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "unavailable_from_source",
            "source": "none",
        }
    try:
        start = datetime.fromisoformat(str(game_start_utc)).astimezone(UTC)
    except ValueError:
        return {
            "temperature_f": None,
            "wind_mph": None,
            "humidity_pct": None,
            "weather_run_factor": 1.0,
            "status": "unavailable_from_source",
            "source": "none",
        }
    if start >= datetime.now(UTC) - timedelta(hours=6):
        return live_weather(home_team, game_start_utc)
    return historical_weather(home_team, game_start_utc)
