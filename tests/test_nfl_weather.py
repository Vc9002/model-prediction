"""NFL pregame weather: PIT-verified Open-Meteo capture replaces the 5 mph / 65F placeholder."""

from __future__ import annotations

from pathlib import Path

from model_prediction.features.multi_sport_weather import (
    NFL_TEAM_VENUES,
    capture_nfl_pregame_weather,
    resolve_nfl_game_weather,
    store_weather_observations,
)
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7

_NFL_TEAMS = {
    "Arizona Cardinals",
    "Atlanta Falcons",
    "Baltimore Ravens",
    "Buffalo Bills",
    "Carolina Panthers",
    "Chicago Bears",
    "Cincinnati Bengals",
    "Cleveland Browns",
    "Dallas Cowboys",
    "Denver Broncos",
    "Detroit Lions",
    "Green Bay Packers",
    "Houston Texans",
    "Indianapolis Colts",
    "Jacksonville Jaguars",
    "Kansas City Chiefs",
    "Las Vegas Raiders",
    "Los Angeles Chargers",
    "Los Angeles Rams",
    "Miami Dolphins",
    "Minnesota Vikings",
    "New England Patriots",
    "New Orleans Saints",
    "New York Giants",
    "New York Jets",
    "Philadelphia Eagles",
    "Pittsburgh Steelers",
    "San Francisco 49ers",
    "Seattle Seahawks",
    "Tampa Bay Buccaneers",
    "Tennessee Titans",
    "Washington Commanders",
}


def _forecast_row(observed_at: str, *, wind_kmh: float, temp_c: float) -> dict:
    hours = [f"2026-11-15T{h:02d}:00" for h in range(24)]
    return {
        "venue_name": "Lambeau Field",
        "sport": "nfl",
        "date": "2026-11-15",
        "is_indoor": False,
        "hourly": {
            "time": hours,
            "temperature_2m": [temp_c] * 24,
            "wind_speed_10m": [wind_kmh] * 24,
            "precipitation": [0.0] * 24,
        },
        "source": "open_meteo:forecast",
        "observed_at_utc": observed_at,
        "home_team": "Green Bay Packers",
    }


def test_venue_catalog_covers_all_32_teams() -> None:
    assert set(NFL_TEAM_VENUES) == _NFL_TEAMS


def test_resolve_uses_only_a_forecast_captured_before_the_decision(tmp_path: Path) -> None:
    store_weather_observations(
        tmp_path, "nfl", [_forecast_row("2026-11-15T12:00:00Z", wind_kmh=32.2, temp_c=-1.0)]
    )
    kickoff = "2026-11-15T18:00:00Z"
    evidence: dict = {}
    temp_f, wind_mph, precip_in, is_dome = resolve_nfl_game_weather(
        tmp_path,
        "Green Bay Packers",
        "2026-11-15",
        event_start_utc=kickoff,
        observed_at_utc="2026-11-15T13:00:00Z",
        provenance_out=evidence,
    )
    assert (round(temp_f, 1), round(wind_mph, 1), precip_in, is_dome) == (30.2, 20.0, 0.0, False)
    assert evidence["source"] == "open_meteo:forecast"
    # A decision made BEFORE the capture must not see it (point-in-time).
    assert resolve_nfl_game_weather(
        tmp_path,
        "Green Bay Packers",
        "2026-11-15",
        event_start_utc=kickoff,
        observed_at_utc="2026-11-15T11:00:00Z",
    ) == (None, None, None, False)


def test_resolve_dome_neutral_and_unknown(tmp_path: Path) -> None:
    assert resolve_nfl_game_weather(tmp_path, "Detroit Lions", "2026-11-15") == (70.0, 0.0, 0.0, True)
    assert resolve_nfl_game_weather(tmp_path, "Green Bay Packers", "2026-11-15", is_neutral_site=True) == (
        None,
        None,
        None,
        False,
    )
    assert resolve_nfl_game_weather(tmp_path, "London Monarchs", "2026-11-15") == (None, None, None, False)


def test_capture_skips_indoor_and_neutral_without_fetching(tmp_path: Path) -> None:
    scoreboard = {
        "events": [
            {
                "date": "2099-01-01T18:00:00Z",
                "competitions": [
                    {
                        "neutralSite": False,
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Detroit Lions"}},
                            {"homeAway": "away", "team": {"displayName": "Chicago Bears"}},
                        ],
                    }
                ],
            },
            {
                "date": "2099-01-01T14:30:00Z",
                "competitions": [
                    {
                        "neutralSite": True,
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Jacksonville Jaguars"}},
                            {"homeAway": "away", "team": {"displayName": "Los Angeles Rams"}},
                        ],
                    }
                ],
            },
        ]
    }
    report = capture_nfl_pregame_weather(tmp_path, "2099-01-01", scoreboard=scoreboard)
    assert report["scheduled_outdoor"] == 0 and report["captured"] == 0
    assert report["neutral_skipped"] == 1 and report["skipped"] == 1


def test_total_engine_applies_no_weather_term_when_unavailable() -> None:
    model = NFLTotalModelV7()
    unavailable = model.forecast_total(home_team="A", away_team="B", total_line=47.0)
    benign = model.forecast_total(
        home_team="A", away_team="B", total_line=47.0, wind_mph=5.0, temperature_f=65.0
    )
    windy = model.forecast_total(
        home_team="A", away_team="B", total_line=47.0, wind_mph=20.0, temperature_f=20.0
    )
    assert unavailable.weather_inputs_available is False and benign.weather_inputs_available is True
    assert unavailable.weather_total_penalty == 0.0
    assert unavailable.projected_total == benign.projected_total
    assert windy.weather_total_penalty == 2.5 + 1.2
