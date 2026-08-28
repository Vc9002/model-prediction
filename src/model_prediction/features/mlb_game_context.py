"""Real MLB game context for research features: historical weather and travel.

Two lookups, both strictly point-in-time and both read-only:

- Weather: ``data/weather/mlb_historical.jsonl`` (built by
  ``scripts/backfill_mlb_weather.py``) holds RAW hourly archive
  observations per (venue, date). ``mlb_weather_run_factor`` collapses the
  hour closest to first pitch through the shared
  ``weather.run_factor_from_conditions`` formula -- the cache stores the
  raw values precisely so other collapses (wind vectors, pressure) can be
  derived later without refetching.

- Travel: each team's venue for every past game comes from
  ``data/mlb_statsapi/game_snapshots.jsonl`` (venue_name is recorded per
  game there). ``mlb_away_travel_miles`` is the away team's great-circle
  distance from the venue of its previous game (strictly before this
  game's start) to this game's venue -- the away team is the traveler by
  construction, and the single distance column in the totals feature
  vector can only carry one number per game.

Both helpers return neutral defaults (1.0 / None) on ANY missing data --
missing means "feature unavailable", never a guess. Note the realized-
weather caveat that already applies to ``features.weather.historical_weather``:
the archive endpoint returns actual recorded conditions, which a model at
first pitch would have had only as a forecast; the approximation is that
forecast≈actual for these variables (high correlation), not that realized
weather was literally knowable. This is a training-side feature only.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from .mlb_venue_geocoding import venue_location
from .weather import run_factor_from_conditions

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
DEFAULT_WEATHER_CACHE_PATH = PROJECT_ROOT / "data/weather/mlb_historical.jsonl"

# (home_team, date) -> venue_name, built lazily from the snapshot file.
_TEAM_DATE_VENUE_CACHE: dict[Path, dict[tuple[str, str], str]] = {}
# (venue_name, date) -> raw backfill row.
_WEATHER_CACHE: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}


def _team_date_venue(snapshot_path: Path) -> dict[tuple[str, str], str]:
    if snapshot_path in _TEAM_DATE_VENUE_CACHE:
        return _TEAM_DATE_VENUE_CACHE[snapshot_path]
    mapping: dict[tuple[str, str], str] = {}
    if snapshot_path.exists():
        with snapshot_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    day = datetime.fromisoformat(str(snap["game_start_utc"])).date().isoformat()
                except (KeyError, ValueError):
                    continue
                venue = snap.get("venue_name") or ""
                home_team = (snap.get("home") or {}).get("team_name") or ""
                if venue and home_team:
                    mapping[(home_team, day)] = venue
    _TEAM_DATE_VENUE_CACHE[snapshot_path] = mapping
    return mapping


def _weather_rows(cache_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if cache_path in _WEATHER_CACHE:
        return _WEATHER_CACHE[cache_path]
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") == "ok" and row.get("venue_name") and row.get("date"):
                    rows[(row["venue_name"], row["date"])] = row
    _WEATHER_CACHE[cache_path] = rows
    return rows


def venue_for_game(
    home_team: str,
    game_start: datetime,
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> str | None:
    """Venue name for an MLB home team on a game date, from the snapshot
    file. None when no snapshot record exists for that (team, date)."""
    mapping = _team_date_venue(Path(snapshot_path))
    return mapping.get((home_team, game_start.date().isoformat()))


def mlb_weather_run_factor(
    home_team: str,
    game_start: datetime,
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
    cache_path: Path = DEFAULT_WEATHER_CACHE_PATH,
) -> float | None:
    """Collapsed weather run factor at the hour closest to first pitch.

    None when the venue or the cached archive row is missing; 1.0-equivalent
    conditions (dome, neutral weather) come back as a real 1.0, not None.
    """
    venue = venue_for_game(home_team, game_start, snapshot_path=snapshot_path)
    if venue is None:
        return None
    row = _weather_rows(Path(cache_path)).get((venue, game_start.date().isoformat()))
    if row is None:
        return None
    hourly = row.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    winds = hourly.get("wind_speed_10m") or []
    if not times or not temps:
        return None
    target = game_start.astimezone(UTC)
    hour_index = min(
        range(min(len(times), len(temps))),
        key=lambda index: abs(datetime.fromisoformat(str(times[index])).astimezone(UTC) - target),
    )
    temp = float(temps[hour_index])
    wind = float(winds[hour_index]) if hour_index < len(winds) else None
    return run_factor_from_conditions(temp, wind)


def mlb_away_travel_miles(
    away_team: str,
    home_team: str,
    game_start: datetime,
    last_venue_by_team: dict[str, str],
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> float | None:
    """Great-circle miles the away team travels to this game, measured from
    the venue of that team's most recent strictly-prior game.

    ``last_venue_by_team`` is caller-maintained state (updated only after
    each game's features are emitted -- the same strictly-prior discipline
    the totals builder already uses for its EWMA accumulators). None when
    either venue is unknown.
    """
    this_venue = venue_for_game(home_team, game_start, snapshot_path=snapshot_path)
    prev_venue = last_venue_by_team.get(away_team)
    if not this_venue or not prev_venue:
        return None
    here = venue_location(this_venue)
    there = venue_location(prev_venue)
    if here is None or there is None:
        return None
    return round(_haversine_miles(here.latitude, here.longitude, there.latitude, there.longitude), 2)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_miles = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r_miles * math.asin(math.sqrt(a))
