"""Tests for the real MLB weather/travel context lookups.

All fixtures are synthetic snapshots + synthetic weather-cache rows; no
live data or network access. The collapse formula itself
(run_factor_from_conditions) is pinned by features/weather.py's own tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from model_prediction.features.mlb_game_context import (
    _haversine_miles,
    mlb_away_travel_miles,
    mlb_weather_run_factor,
    venue_for_game,
)
from model_prediction.features.weather import run_factor_from_conditions


def _write_snapshots(tmp_path, rows: list[dict]) -> str:
    path = tmp_path / "snapshots.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return str(path)


def _snapshot(home_team: str, venue: str, day: str) -> dict:
    return {
        "game_start_utc": f"{day}T18:05:00Z",
        "venue_name": venue,
        "home": {"team_name": home_team},
    }


def _weather_row(venue: str, day: str, temp_f: float, wind_mph: float | None) -> dict:
    hourly = {
        "time": [f"{day}T00:00", f"{day}T01:00"],
        "temperature_2m": [temp_f, temp_f],
        "wind_speed_10m": [wind_mph if wind_mph is not None else 0.0] * 2,
    }
    return {"venue_name": venue, "date": day, "hourly": hourly, "status": "ok"}


def test_venue_for_game_maps_team_and_date(tmp_path) -> None:
    snapshots = _write_snapshots(
        tmp_path,
        [
            _snapshot("New York Yankees", "Yankee Stadium", "2025-07-01"),
            _snapshot("Boston Red Sox", "Fenway Park", "2025-07-01"),
        ],
    )
    day = datetime(2025, 7, 1, 18, 5, tzinfo=UTC)
    assert venue_for_game("New York Yankees", day, snapshot_path=snapshots) == "Yankee Stadium"
    assert venue_for_game("Boston Red Sox", day, snapshot_path=snapshots) == "Fenway Park"
    # Unknown team/date is None, never a guess.
    assert venue_for_game("Nowhere Team", day, snapshot_path=snapshots) is None


def test_weather_run_factor_from_cache(tmp_path) -> None:
    snapshots = _write_snapshots(tmp_path, [_snapshot("New York Yankees", "Yankee Stadium", "2025-07-01")])
    cache = tmp_path / "weather.jsonl"
    with cache.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(_weather_row("Yankee Stadium", "2025-07-01", temp_f=80.0, wind_mph=12.0)) + "\n"
        )
    day = datetime(2025, 7, 1, 18, 5, tzinfo=UTC)

    factor = mlb_weather_run_factor("New York Yankees", day, snapshot_path=snapshots, cache_path=cache)
    assert factor == run_factor_from_conditions(80.0, 12.0)
    assert factor is not None and factor > 1.0


def test_weather_run_factor_none_when_missing(tmp_path) -> None:
    snapshots = _write_snapshots(tmp_path, [_snapshot("New York Yankees", "Yankee Stadium", "2025-07-01")])
    cache = tmp_path / "empty.jsonl"
    cache.write_text("", encoding="utf-8")
    day = datetime(2025, 7, 1, 18, 5, tzinfo=UTC)
    # Venue resolves but cache row is absent -> None (caller falls back to 1.0).
    assert mlb_weather_run_factor("New York Yankees", day, snapshot_path=snapshots, cache_path=cache) is None
    # Unknown venue -> None.
    assert mlb_weather_run_factor("Nowhere Team", day, snapshot_path=snapshots, cache_path=cache) is None


def test_away_travel_miles_between_real_venues(tmp_path) -> None:
    snapshots = _write_snapshots(
        tmp_path,
        [
            _snapshot("New York Yankees", "Yankee Stadium", "2025-07-01"),
            _snapshot("Boston Red Sox", "Fenway Park", "2025-07-02"),
        ],
    )
    day2 = datetime(2025, 7, 2, 18, 5, tzinfo=UTC)
    miles = mlb_away_travel_miles(
        "New York Yankees",  # played at Yankee Stadium on 07-01
        "Boston Red Sox",  # now hosts at Fenway
        day2,
        {"New York Yankees": "Yankee Stadium"},
        snapshot_path=snapshots,
    )
    # Bronx -> Fenway is ~190 miles; assert within a sane band (not a guess).
    assert miles is not None and 150 < miles < 230

    # Same venue (same-series back-to-back) is genuinely zero.
    zero = mlb_away_travel_miles(
        "New York Yankees",
        "Boston Red Sox",
        day2,
        {"New York Yankees": "Fenway Park"},
        snapshot_path=snapshots,
    )
    assert zero == 0.0

    # No prior venue recorded -> None (caller falls back to 0.0).
    assert (
        mlb_away_travel_miles("New York Yankees", "Boston Red Sox", day2, {}, snapshot_path=snapshots) is None
    )


def test_haversine_reference_distance() -> None:
    # Known reference pair (NYC -> London, ~3,459 miles); pins the formula.
    miles = _haversine_miles(40.7128, -74.0060, 51.5074, -0.1278)
    assert 3400 < miles < 3520
