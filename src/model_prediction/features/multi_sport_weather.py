"""Multi-sport venue weather and atmospheric physics engine.

Provides point-in-time environmental features across NFL, CFB, and Soccer venues:
- Venue coordinates (lat/lon), altitude/elevation (m), orientation (degrees), and roof type
- Open-Meteo hourly weather fetching (temperature, humidity, surface pressure, wind, precipitation)
- Bahill air density calculation (rho) and aerodynamic drag scaling
- Stadium-relative wind vectors (headwind/tailwind and crosswind)
- Weather impact multipliers on passing EPA, kicking success, and game totals
"""

from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .air_density import air_density

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True, slots=True)
class VenueProfile:
    venue_name: str
    sport: str
    city: str
    state_or_country: str
    latitude: float
    longitude: float
    elevation_m: float
    field_orientation_deg: float  # North = 0, East = 90, South = 180, West = 270
    is_indoor: bool


# Canonical Venue Catalog for NFL, CFB, and Top European Venues
VENUE_CATALOG: dict[str, VenueProfile] = {
    # NFL Venues
    "Lambeau Field": VenueProfile(
        "Lambeau Field", "nfl", "Green Bay", "WI", 44.5013, -88.0622, 195.0, 0.0, False
    ),
    "Highmark Stadium": VenueProfile(
        "Highmark Stadium", "nfl", "Orchard Park", "NY", 42.7738, -78.7870, 183.0, 45.0, False
    ),
    "Soldier Field": VenueProfile(
        "Soldier Field", "nfl", "Chicago", "IL", 41.8623, -87.6167, 181.0, 0.0, False
    ),
    "Gillette Stadium": VenueProfile(
        "Gillette Stadium", "nfl", "Foxborough", "MA", 42.0909, -71.2643, 85.0, 345.0, False
    ),
    "MetLife Stadium": VenueProfile(
        "MetLife Stadium", "nfl", "East Rutherford", "NJ", 40.8128, -74.0742, 2.0, 315.0, False
    ),
    "Lincoln Financial Field": VenueProfile(
        "Lincoln Financial Field", "nfl", "Philadelphia", "PA", 39.9008, -75.1675, 12.0, 0.0, False
    ),
    "Empower Field at Mile High": VenueProfile(
        "Empower Field at Mile High", "nfl", "Denver", "CO", 39.7439, -105.0201, 1610.0, 0.0, False
    ),
    "Arrowhead Stadium": VenueProfile(
        "Arrowhead Stadium", "nfl", "Kansas City", "MO", 39.0489, -94.4839, 275.0, 0.0, False
    ),
    "Acrisure Stadium": VenueProfile(
        "Acrisure Stadium", "nfl", "Pittsburgh", "PA", 40.4468, -80.0158, 222.0, 345.0, False
    ),
    "Cleveland Browns Stadium": VenueProfile(
        "Cleveland Browns Stadium", "nfl", "Cleveland", "OH", 41.5061, -81.6995, 178.0, 70.0, False
    ),
    "Mercedes-Benz Superdome": VenueProfile(
        "Mercedes-Benz Superdome", "nfl", "New Orleans", "LA", 29.9511, -90.0812, 1.0, 0.0, True
    ),
    "Ford Field": VenueProfile("Ford Field", "nfl", "Detroit", "MI", 42.3400, -83.0456, 183.0, 0.0, True),
    "AT&T Stadium": VenueProfile(
        "AT&T Stadium", "nfl", "Arlington", "TX", 32.7473, -97.0945, 165.0, 0.0, True
    ),
    "SoFi Stadium": VenueProfile(
        "SoFi Stadium", "nfl", "Inglewood", "CA", 33.9535, -118.3390, 40.0, 0.0, True
    ),
    "U.S. Bank Stadium": VenueProfile(
        "U.S. Bank Stadium", "nfl", "Minneapolis", "MN", 44.9735, -93.2575, 255.0, 0.0, True
    ),
    # CFB Venues
    "Michigan Stadium": VenueProfile(
        "Michigan Stadium", "ncaaf", "Ann Arbor", "MI", 42.2658, -83.7487, 256.0, 0.0, False
    ),
    "Ohio Stadium": VenueProfile(
        "Ohio Stadium", "ncaaf", "Columbus", "OH", 40.0016, -83.0197, 225.0, 0.0, False
    ),
    "Bryant-Denny Stadium": VenueProfile(
        "Bryant-Denny Stadium", "ncaaf", "Tuscaloosa", "AL", 33.2075, -87.5504, 69.0, 345.0, False
    ),
    "Tiger Stadium": VenueProfile(
        "Tiger Stadium", "ncaaf", "Baton Rouge", "LA", 30.4120, -91.1838, 7.0, 0.0, False
    ),
    "Darrell K Royal Stadium": VenueProfile(
        "Darrell K Royal Stadium", "ncaaf", "Austin", "TX", 30.2837, -97.7323, 160.0, 0.0, False
    ),
    "Beaver Stadium": VenueProfile(
        "Beaver Stadium", "ncaaf", "University Park", "PA", 40.8122, -77.8561, 355.0, 355.0, False
    ),
    "Sanford Stadium": VenueProfile(
        "Sanford Stadium", "ncaaf", "Athens", "GA", 33.9498, -83.3734, 185.0, 80.0, False
    ),
    "Folsom Field": VenueProfile(
        "Folsom Field", "ncaaf", "Boulder", "CO", 40.0095, -105.2669, 1630.0, 0.0, False
    ),
    "Rice-Eccles Stadium": VenueProfile(
        "Rice-Eccles Stadium", "ncaaf", "Salt Lake City", "UT", 40.7598, -111.8488, 1420.0, 0.0, False
    ),
    "Carrier Dome": VenueProfile(
        "Carrier Dome", "ncaaf", "Syracuse", "NY", 43.0362, -76.1363, 160.0, 0.0, True
    ),
    # Soccer Venues
    "Emirates Stadium": VenueProfile(
        "Emirates Stadium", "soccer", "London", "UK", 51.5549, -0.1084, 43.0, 0.0, False
    ),
    "Old Trafford": VenueProfile(
        "Old Trafford", "soccer", "Manchester", "UK", 53.4631, -2.2913, 44.0, 0.0, False
    ),
    "Anfield": VenueProfile("Anfield", "soccer", "Liverpool", "UK", 53.4308, -2.9608, 50.0, 0.0, False),
    "Santiago Bernabeu": VenueProfile(
        "Santiago Bernabeu", "soccer", "Madrid", "Spain", 40.4530, -3.6883, 700.0, 0.0, False
    ),
    "Camp Nou": VenueProfile("Camp Nou", "soccer", "Barcelona", "Spain", 41.3809, 2.1228, 42.0, 0.0, False),
    "Allianz Arena": VenueProfile(
        "Allianz Arena", "soccer", "Munich", "Germany", 48.2188, 11.6247, 500.0, 0.0, False
    ),
    "San Siro": VenueProfile("San Siro", "soccer", "Milan", "Italy", 45.4781, 9.1240, 120.0, 0.0, False),
    "Parc des Princes": VenueProfile(
        "Parc des Princes", "soccer", "Paris", "France", 48.8414, 2.2530, 35.0, 0.0, False
    ),
}


@dataclass(frozen=True, slots=True)
class EnvironmentalPhysicsResult:
    venue_name: str
    is_indoor: bool
    temperature_c: float
    surface_pressure_hpa: float
    relative_humidity_pct: float
    wind_speed_mps: float
    wind_direction_deg: float
    precipitation_mm: float
    air_density_kg_m3: float
    density_ratio_to_sea_level: float
    headwind_tailwind_mps: float  # Positive = tailwind, Negative = headwind
    crosswind_mps: float
    passing_drag_penalty: float  # Multiplier on deep pass completion probability
    kicking_range_adjustment_yds: float  # Yards gained (+) or lost (-) on FG range
    total_points_adjustment: float  # Expected point adjustment on game total


def compute_environmental_physics(
    venue: VenueProfile,
    *,
    temperature_c: float,
    surface_pressure_hpa: float,
    relative_humidity_pct: float,
    wind_speed_mps: float,
    wind_direction_deg: float,
    precipitation_mm: float = 0.0,
) -> EnvironmentalPhysicsResult:
    """Computes rigorous aerodynamic and atmospheric physics factors for outdoor sports."""
    if venue.is_indoor:
        return EnvironmentalPhysicsResult(
            venue_name=venue.venue_name,
            is_indoor=True,
            temperature_c=21.1,  # 70 F climate-controlled
            surface_pressure_hpa=1013.25,
            relative_humidity_pct=45.0,
            wind_speed_mps=0.0,
            wind_direction_deg=0.0,
            precipitation_mm=0.0,
            air_density_kg_m3=1.200,
            density_ratio_to_sea_level=0.980,
            headwind_tailwind_mps=0.0,
            crosswind_mps=0.0,
            passing_drag_penalty=1.0,
            kicking_range_adjustment_yds=0.0,
            total_points_adjustment=0.0,
        )

    # 1. Air Density Calculation via Bahill / Virtual Temperature
    pressure_pa = surface_pressure_hpa * 100.0
    density_res = air_density(temperature_c, pressure_pa, relative_humidity_pct)
    rho = density_res.density_kg_m3
    rho_ratio = density_res.density_ratio

    # 2. Wind Vector Decomposition relative to Field Orientation
    # angle_diff = wind_direction - field_orientation
    theta_rad = math.radians(wind_direction_deg - venue.field_orientation_deg)
    # Along field axis: cos(theta) (tailwind along endzone to endzone)
    headwind_tailwind = wind_speed_mps * math.cos(theta_rad)
    # Across field: sin(theta)
    crosswind = wind_speed_mps * math.sin(theta_rad)

    # 3. Aerodynamic Passing Drag Penalty
    # Aerodynamic drag F_d = 0.5 * rho * v^2 * C_d * A
    # High wind speed and dense cold air increase drag on footballs / soccer balls
    drag_factor = (rho / 1.225) * (1.0 + (wind_speed_mps / 15.0) ** 1.5)
    passing_drag_penalty = max(0.70, round(1.0 - 0.04 * max(0.0, drag_factor - 1.0), 3))

    # 4. Field Goal / Kicking Range Adjustment
    # Altitude (+1 yd per 1000 ft elevation) minus wind drag
    elevation_gain_yds = (venue.elevation_m / 304.8) * 0.8
    wind_gain_yds = headwind_tailwind * 0.4
    kicking_range_adj = round(elevation_gain_yds + wind_gain_yds, 1)

    # 5. Total Scoring Adjustment
    # High wind (>15 mph / 6.7 m/s) and rain suppress scoring in NFL/CFB totals
    total_adj = 0.0
    if wind_speed_mps > 6.7:
        wind_excess = wind_speed_mps - 6.7
        total_adj -= min(7.0, wind_excess * 0.75)
    if precipitation_mm > 1.0:
        total_adj -= min(4.0, precipitation_mm * 0.5)
    if venue.elevation_m > 1200.0:
        total_adj += 1.5  # Thinner air at Mile High / Utah increases kicking & kickoff touchbacks

    return EnvironmentalPhysicsResult(
        venue_name=venue.venue_name,
        is_indoor=False,
        temperature_c=round(temperature_c, 1),
        surface_pressure_hpa=round(surface_pressure_hpa, 1),
        relative_humidity_pct=round(relative_humidity_pct, 1),
        wind_speed_mps=round(wind_speed_mps, 1),
        wind_direction_deg=round(wind_direction_deg, 1),
        precipitation_mm=round(precipitation_mm, 2),
        air_density_kg_m3=round(rho, 4),
        density_ratio_to_sea_level=round(rho_ratio, 4),
        headwind_tailwind_mps=round(headwind_tailwind, 2),
        crosswind_mps=round(crosswind, 2),
        passing_drag_penalty=passing_drag_penalty,
        kicking_range_adjustment_yds=kicking_range_adj,
        total_points_adjustment=round(total_adj, 2),
    )


def fetch_venue_hourly_weather(
    venue: VenueProfile,
    game_date: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Fetch hourly weather from Open-Meteo for a specific venue and date."""
    if venue.is_indoor:
        hourly_indoor = {
            "temperature_2m": [21.1] * 24,
            "relative_humidity_2m": [45.0] * 24,
            "surface_pressure": [1013.25] * 24,
            "wind_speed_10m": [0.0] * 24,
            "wind_direction_10m": [0.0] * 24,
            "precipitation": [0.0] * 24,
        }
        return {
            "venue_name": venue.venue_name,
            "sport": venue.sport,
            "date": game_date,
            "is_indoor": True,
            "hourly": hourly_indoor,
            "source": "indoor_constant",
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }

    http_client = client or httpx.Client(timeout=20)
    today = datetime.now(UTC).date().isoformat()
    endpoint = OPEN_METEO_ARCHIVE_URL if game_date < today else OPEN_METEO_FORECAST_URL

    params = {
        "latitude": venue.latitude,
        "longitude": venue.longitude,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,precipitation",
        "timezone": "UTC",
    }
    if endpoint == OPEN_METEO_ARCHIVE_URL:
        params["start_date"] = game_date
        params["end_date"] = game_date
    else:
        # An evening game on the requested Eastern date can start after UTC
        # midnight, so include the following UTC date in the forecast window.
        params["forecast_days"] = max(2, (date.fromisoformat(game_date) - datetime.now(UTC).date()).days + 2)

    try:
        resp = http_client.get(endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})
        return {
            "venue_name": venue.venue_name,
            "sport": venue.sport,
            "date": game_date,
            "is_indoor": False,
            "latitude": venue.latitude,
            "longitude": venue.longitude,
            "elevation_m": venue.elevation_m,
            "hourly": hourly,
            "source": f"open_meteo:{endpoint.split('/')[-1]}",
            "observed_at_utc": datetime.now(UTC).isoformat(),
        }
    except (httpx.HTTPError, KeyError, ValueError, TypeError, OSError):
        return None


def store_weather_observations(
    data_root: str | Path,
    sport: str,
    observations: list[dict[str, Any]],
) -> int:
    """Appends weather observations to data/weather/<sport>_historical.jsonl."""
    out_path = Path(data_root) / "weather" / f"{sport.lower()}_historical.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_keys: set[tuple[str, str, str, str, str]] = set()
    if out_path.exists():
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    existing_keys.add(_weather_storage_key(rec))
                except json.JSONDecodeError:
                    continue

    appended = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for raw_obs in observations:
            obs = dict(raw_obs)
            obs["capture_sha256"] = _weather_capture_hash(obs)
            key = _weather_storage_key(obs)
            if key not in existing_keys:
                f.write(json.dumps(obs, sort_keys=True) + "\n")
                existing_keys.add(key)
                appended += 1

    return appended


def _weather_storage_key(observation: dict[str, Any]) -> tuple[str, str, str, str, str]:
    source = str(observation.get("source"))
    return (
        str(observation.get("venue_name")),
        str(observation.get("date")),
        str(observation.get("home_team")),
        source,
        str(observation.get("observed_at_utc")) if source == "open_meteo:forecast" else "",
    )


def _weather_capture_hash(observation: dict[str, Any]) -> str:
    payload = {key: value for key, value in observation.items() if key != "capture_sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def capture_cfb_pregame_weather(
    data_root: str | Path,
    game_date: str,
    *,
    scoreboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture timestamped forecast weather for upcoming outdoor CFB games."""
    from ..data_sources.cfb_data import resolve_team
    from ..data_sources.espn import ESPNClient
    from ..domain import parse_utc

    events = (scoreboard if scoreboard is not None else ESPNClient().scoreboard("NCAAF", game_date)).get(
        "events", []
    )
    now = datetime.now(UTC)
    targets = []
    skipped = 0
    seen = set()
    for event in events:
        try:
            start = parse_utc(str(event["date"]))
            home_name = next(
                item["team"]["displayName"]
                for item in event["competitions"][0]["competitors"]
                if item["homeAway"] == "home"
            )
        except (KeyError, IndexError, StopIteration, TypeError, ValueError):
            skipped += 1
            continue
        team = resolve_team(home_name)
        if start <= now or team is None or team.is_dome or team.latitude is None or team.longitude is None:
            skipped += 1
            continue
        identity = (team.canonical_name, start.isoformat())
        if identity in seen:
            continue
        seen.add(identity)
        profile = VenueProfile(
            team.stadium_name,
            "ncaaf",
            team.city,
            team.state,
            team.latitude,
            team.longitude,
            team.elevation_ft / 3.28084,
            0.0,
            False,
        )
        targets.append((team.canonical_name, start, profile))

    def fetch(target):
        home_name, start, profile = target
        with httpx.Client(timeout=4.0) as client:
            row = fetch_venue_hourly_weather(profile, game_date, client=client)
        if row is None:
            return None
        try:
            captured = parse_utc(str(row["observed_at_utc"]))
        except (KeyError, TypeError, ValueError):
            return None
        target_hour = start.strftime("%Y-%m-%dT%H:00")
        if (
            captured >= start
            or row.get("source") != "open_meteo:forecast"
            or target_hour not in (row.get("hourly", {}).get("time") or [])
        ):
            return None
        row["home_team"] = home_name
        return row

    with ThreadPoolExecutor(max_workers=6) as pool:
        observations = [row for row in pool.map(fetch, targets) if row is not None]
    appended = store_weather_observations(data_root, "ncaaf", observations)
    return {
        "status": "ok" if len(observations) == len(targets) else "partial",
        "scheduled_outdoor": len(targets),
        "captured": len(observations),
        "appended": appended,
        "skipped": skipped,
    }


def resolve_cfb_game_weather(
    data_root: str | Path,
    home_team: str,
    game_date: str,
    event_start_utc: str | None = None,
    observed_at_utc: str | None = None,
    provenance_out: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, float | None, bool]:
    """Resolves (temperature_f, wind_mph, precipitation_in, is_dome) for a CFB matchup."""
    from ..data_sources.cfb_data import resolve_team
    from ..domain import parse_utc

    team_info = resolve_team(home_team)
    if team_info is not None and team_info.is_dome:
        if provenance_out is not None:
            provenance_out.update({"source": "cfb_team_catalog:indoor", "home_team": team_info.canonical_name})
        return (70.0, 0.0, 0.0, True)

    weather_file = Path(data_root) / "weather" / "ncaaf_historical.jsonl"
    if not weather_file.exists():
        return (None, None, None, False)

    decision_time = None
    event_time = None
    if observed_at_utc is not None:
        if event_start_utc is None:
            return (None, None, None, False)
        try:
            decision_time = parse_utc(observed_at_utc)
            event_time = parse_utc(event_start_utc)
        except (TypeError, ValueError):
            return (None, None, None, False)
        if decision_time >= event_time:
            return (None, None, None, False)

    stadium_name = team_info.stadium_name if team_info else home_team
    stadium_norm = stadium_name.casefold().strip()

    target_hour = 19  # Default ~3 PM EDT / 19:00 UTC
    if event_start_utc:
        try:
            dt = parse_utc(event_start_utc)
            target_hour = min(23, max(0, dt.hour))
        except (ValueError, TypeError):
            pass

    latest = None
    try:
        with open(weather_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                v_name = str(rec.get("venue_name", "")).casefold().strip()
                v_date = str(rec.get("date", ""))
                expected_home = team_info.canonical_name if team_info else home_team
                team_matches = (
                    not rec.get("home_team")
                    or str(rec["home_team"]).casefold() == expected_home.casefold()
                )
                if v_date == game_date and team_matches and (v_name in stadium_norm or stadium_norm in v_name):
                    hourly = rec.get("hourly", {})
                    temps = hourly.get("temperature_2m", [])
                    winds = hourly.get("wind_speed_10m", [])
                    precips = hourly.get("precipitation", [])

                    if decision_time is not None:
                        if rec.get("source") != "open_meteo:forecast":
                            continue
                        if rec.get("capture_sha256") != _weather_capture_hash(rec):
                            continue
                        try:
                            captured = parse_utc(str(rec["observed_at_utc"]))
                        except (KeyError, TypeError, ValueError):
                            continue
                        if captured > decision_time or captured >= event_time:
                            continue
                        target = event_time.strftime("%Y-%m-%dT%H:00")
                        times = hourly.get("time") or []
                        try:
                            target_hour = times.index(target)
                        except ValueError:
                            continue
                        if latest is not None and captured <= latest[0]:
                            continue

                    temp_c = temps[target_hour] if len(temps) > target_hour else (temps[0] if temps else None)
                    wind_kmh = (
                        winds[target_hour] if len(winds) > target_hour else (winds[0] if winds else None)
                    )
                    precip_mm = (
                        precips[target_hour]
                        if len(precips) > target_hour
                        else (precips[0] if precips else None)
                    )

                    temp_f = round(temp_c * 1.8 + 32.0, 1) if temp_c is not None else None
                    wind_mph = round(wind_kmh * 0.621371, 1) if wind_kmh is not None else None
                    precip_in = round(precip_mm / 25.4, 2) if precip_mm is not None else None
                    if decision_time is None:
                        return (temp_f, wind_mph, precip_in, False)
                    if None not in (temp_f, wind_mph, precip_in):
                        latest = (captured, temp_f, wind_mph, precip_in, rec)
    except (OSError, json.JSONDecodeError):
        pass

    if latest and provenance_out is not None:
        provenance_out.update(
            {
                "source": latest[4]["source"],
                "observed_at_utc": latest[4]["observed_at_utc"],
                "capture_sha256": latest[4]["capture_sha256"],
                "venue_name": latest[4]["venue_name"],
            }
        )
    return (latest[1], latest[2], latest[3], False) if latest else (None, None, None, False)
