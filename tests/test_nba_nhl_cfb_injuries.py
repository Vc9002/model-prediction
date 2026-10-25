"""Unit tests for NBA, NHL, CFB injury ingestion and multi-sport environmental physics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.data_sources.espn_cfb_injuries import normalize_espn_cfb_injuries
from model_prediction.data_sources.espn_nba_injuries import normalize_espn_nba_injuries
from model_prediction.data_sources.espn_nhl_injuries import normalize_espn_nhl_injuries
from model_prediction.features.multi_sport_weather import (
    VENUE_CATALOG,
    compute_environmental_physics,
    fetch_venue_hourly_weather,
    resolve_cfb_game_weather,
)
from model_prediction.features.nba_player_availability import compute_nba_matchup_availability


def test_normalize_espn_nba_injuries() -> None:
    raw_payload = {
        "timestamp": "2026-10-24T20:00:00Z",
        "injuries": [
            {
                "displayName": "Boston Celtics",
                "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"},
                "injuries": [
                    {
                        "status": "Out",
                        "type": {"description": "Out", "abbreviation": "O"},
                        "athlete": {
                            "id": "12345",
                            "displayName": "Kristaps Porzingis",
                            "position": {"abbreviation": "C"},
                            "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"},
                        },
                        "details": {"type": "Foot", "detail": "Surgery rehab", "returnDate": "2026-12-01"},
                    },
                    {
                        "status": "Day-To-Day",
                        "type": {"description": "Questionable", "abbreviation": "Q"},
                        "athlete": {
                            "id": "67890",
                            "displayName": "Jayson Tatum",
                            "position": {"abbreviation": "SF"},
                            "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"},
                        },
                        "details": {"type": "Ankle", "detail": "Sprain"},
                    },
                ],
            }
        ],
    }

    observed_at = datetime(2026, 10, 24, 21, 0, 0, tzinfo=UTC)
    result = normalize_espn_nba_injuries(raw_payload, observed_at=observed_at)

    assert result["sport"] == "nba"
    assert result["source"] == "espn_nba_injuries"
    assert result["total_injuries"] == 2
    assert len(result["entries"]) == 2

    e0 = result["entries"][0]
    assert e0["player_name"] == "Kristaps Porzingis"
    assert e0["status"] == "Out"
    assert e0["team"] == "Boston Celtics"
    assert e0["return_date"] == "2026-12-01"

    e1 = result["entries"][1]
    assert e1["player_name"] == "Jayson Tatum"
    assert e1["status"] == "Questionable"


def test_normalize_espn_nhl_injuries() -> None:
    raw_payload = {
        "timestamp": "2026-10-24T20:00:00Z",
        "injuries": [
            {
                "displayName": "Edmonton Oilers",
                "team": {"displayName": "Edmonton Oilers", "abbreviation": "EDM"},
                "injuries": [
                    {
                        "status": "Injured Reserve",
                        "type": {"description": "Injured Reserve", "abbreviation": "IR"},
                        "athlete": {
                            "id": "99999",
                            "displayName": "Evander Kane",
                            "position": {"abbreviation": "LW"},
                            "team": {"displayName": "Edmonton Oilers", "abbreviation": "EDM"},
                        },
                        "details": {"type": "Abdomen", "detail": "Surgery"},
                    }
                ],
            }
        ],
    }

    observed_at = datetime(2026, 10, 24, 21, 0, 0, tzinfo=UTC)
    result = normalize_espn_nhl_injuries(raw_payload, observed_at=observed_at)

    assert result["sport"] == "hockey"
    assert result["total_injuries"] == 1
    assert result["entries"][0]["status"] == "IR"
    assert result["entries"][0]["injury_type"] == "Abdomen"


def test_normalize_espn_cfb_injuries() -> None:
    raw_payload = {
        "timestamp": "2026-10-24T20:00:00Z",
        "injuries": [
            {
                "displayName": "Texas Longhorns",
                "team": {"displayName": "Texas Longhorns", "abbreviation": "TEX"},
                "injuries": [
                    {
                        "status": "Doubtful",
                        "type": {"description": "Doubtful", "abbreviation": "D"},
                        "athlete": {
                            "id": "55555",
                            "displayName": "Star Receiver",
                            "position": {"abbreviation": "WR"},
                            "team": {"displayName": "Texas Longhorns", "abbreviation": "TEX"},
                        },
                        "details": {"type": "Hamstring"},
                    }
                ],
            }
        ],
    }

    observed_at = datetime(2026, 10, 24, 21, 0, 0, tzinfo=UTC)
    result = normalize_espn_cfb_injuries(raw_payload, observed_at=observed_at)

    assert result["sport"] == "ncaaf"
    assert result["total_injuries"] == 1
    assert result["entries"][0]["status"] == "Doubtful"


def test_environmental_physics_indoor_vs_outdoor() -> None:
    # 1. Indoor Dome: Climate controlled, zero wind, zero weather penalty
    dome = VENUE_CATALOG["Mercedes-Benz Superdome"]
    indoor_res = compute_environmental_physics(
        dome,
        temperature_c=5.0,
        surface_pressure_hpa=1020.0,
        relative_humidity_pct=85.0,
        wind_speed_mps=12.0,
        wind_direction_deg=180.0,
    )
    assert indoor_res.is_indoor is True
    assert indoor_res.wind_speed_mps == 0.0
    assert indoor_res.passing_drag_penalty == 1.0
    assert indoor_res.total_points_adjustment == 0.0

    # 2. Outdoor Highmark Stadium in heavy wind & rain
    bills_stadium = VENUE_CATALOG["Highmark Stadium"]
    outdoor_res = compute_environmental_physics(
        bills_stadium,
        temperature_c=2.0,
        surface_pressure_hpa=1005.0,
        relative_humidity_pct=90.0,
        wind_speed_mps=10.0,  # ~22 mph wind
        wind_direction_deg=45.0,
        precipitation_mm=5.0,
    )
    assert outdoor_res.is_indoor is False
    assert outdoor_res.passing_drag_penalty < 1.0
    assert outdoor_res.total_points_adjustment < 0.0  # Wind and rain suppress scoring
    assert outdoor_res.air_density_kg_m3 > 1.25  # Dense cold air

    # 3. Indoor venue hourly weather synthesis schema
    indoor_weather = fetch_venue_hourly_weather(dome, "2026-10-24")
    assert indoor_weather is not None
    assert indoor_weather["is_indoor"] is True
    assert indoor_weather["hourly"]["wind_speed_10m"][0] == 0.0
    assert indoor_weather["hourly"]["precipitation"][0] == 0.0


def test_compute_nba_matchup_availability() -> None:
    snapshot = {
        "timestamp": "2026-10-24T20:00:00Z",
        "entries": [
            {
                "player_name": "Jayson Tatum",
                "team": "Boston Celtics",
                "team_abbreviation": "BOS",
                "status": "Questionable",
                "position": "SF",
            },
            {
                "player_name": "Kristaps Porzingis",
                "team": "Boston Celtics",
                "team_abbreviation": "BOS",
                "status": "Out",
                "position": "C",
            },
            {
                "player_name": "Jimmy Butler",
                "team": "Miami Heat",
                "team_abbreviation": "MIA",
                "status": "Out",
                "position": "SF",
            },
        ],
    }

    result = compute_nba_matchup_availability(
        data_root="dummy",
        home_team="Boston Celtics",
        away_team="Miami Heat",
        snapshot=snapshot,
    )

    # Tatum: 4.8 * (1 - 0.5) = 2.4; Porzingis: 2.5 * (1 - 0.0) = 2.5 -> home_loss = 4.9
    # Butler: 4.8 * (1 - 0.0) = 4.8 -> away_loss = 4.8
    # Gap: away_loss - home_loss = 4.8 - 4.9 = -0.1
    # Total loss: 4.9 + 4.8 = 9.7
    assert result["home_points_lost"] == 4.9
    assert result["away_points_lost"] == 4.8
    assert result["availability_points_gap"] == -0.1
    assert result["availability_total_loss"] == 9.7
    assert len(result["home_injuries"]) == 2
    assert len(result["away_injuries"]) == 1

    empty_result = compute_nba_matchup_availability(
        data_root="dummy",
        home_team="Boston Celtics",
        away_team="Miami Heat",
        snapshot={"entries": []},
    )
    assert empty_result["availability_points_gap"] == 0.0
    assert empty_result["availability_total_loss"] == 0.0


def test_resolve_cfb_game_weather(tmp_path: Path) -> None:
    # 1. Dome team (Syracuse)
    temp, wind, precip, is_dome = resolve_cfb_game_weather(
        data_root=tmp_path,
        home_team="Syracuse",
        game_date="2026-10-24",
    )
    assert is_dome is True
    assert temp == 70.0
    assert wind == 0.0
    assert precip == 0.0

    # 2. Outdoor team with no weather file
    temp, wind, precip, is_dome = resolve_cfb_game_weather(
        data_root=tmp_path,
        home_team="Michigan",
        game_date="2026-10-24",
    )
    assert is_dome is False
    assert temp is None
    assert wind is None
    assert precip is None

    # 3. Outdoor team with historical weather data
    weather_dir = tmp_path / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)
    ncaaf_file = weather_dir / "ncaaf_historical.jsonl"

    record = {
        "venue_name": "Michigan Stadium",
        "date": "2026-10-24",
        "hourly": {
            "temperature_2m": [10.0] * 24,
            "wind_speed_10m": [20.0] * 24,
            "precipitation": [2.54] * 24,
        },
    }
    with open(ncaaf_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    temp, wind, precip, is_dome = resolve_cfb_game_weather(
        data_root=tmp_path,
        home_team="Michigan",
        game_date="2026-10-24",
        event_start_utc="2026-10-24T19:00:00Z",
    )
    assert is_dome is False
    assert temp == 50.0
    assert wind == 12.4
    assert precip == 0.10
