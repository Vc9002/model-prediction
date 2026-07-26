"""Weather features for outdoor sports.

Primary source: Open-Meteo free API (no key required).
Fallback: ESPN gameInfo.weather from summaries.
"""

from __future__ import annotations

from typing import Any

import httpx

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
    "Oakland Athletics": (37.7516, -122.2005),
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
        return {"temperature_f": None, "wind_mph": None, "humidity_pct": None,
                "weather_run_factor": 1.0, "status": "dome", "source": "dome"}
    
    coords = BALLPARK_COORDS.get(home_team)
    if not coords:
        return {"temperature_f": None, "wind_mph": None, "humidity_pct": None,
                "weather_run_factor": 1.0, "status": "unknown_park", "source": "none"}
    
    lat, lon = coords
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?"
               f"latitude={lat}&longitude={lon}"
               f"&hourly=temperature_2m,wind_speed_10m,relative_humidity_2m"
               f"&forecast_hours=6&temperature_unit=fahrenheit&wind_speed_unit=mph"
               f"&timezone=auto")
        resp = httpx.get(url, timeout=10)
        if resp.status_code != 200:
            return {"temperature_f": None, "wind_mph": None, "humidity_pct": None,
                    "weather_run_factor": 1.0, "status": "api_error", "source": "open-meteo"}
        data = resp.json()
        hourly = data.get("hourly", {})
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        humids = hourly.get("relative_humidity_2m", [])
        
        if not temps:
            return {"temperature_f": None, "wind_mph": None, "humidity_pct": None,
                    "weather_run_factor": 1.0, "status": "no_data", "source": "open-meteo"}
        
        temp = float(temps[0])
        wind = float(winds[0]) if winds else None
        humid = float(humids[0]) if humids else None
        
        return {
            "temperature_f": round(temp, 1),
            "wind_mph": round(wind, 1) if wind else None,
            "humidity_pct": round(humid, 1) if humid else None,
            "weather_run_factor": run_factor_from_conditions(temp, wind),
            "status": "available",
            "source": "open-meteo",
        }
    except Exception:
        return {"temperature_f": None, "wind_mph": None, "humidity_pct": None,
                "weather_run_factor": 1.0, "status": "api_error", "source": "open-meteo"}
