"""Unit tests for CS2 Map Veto & Pistol Economy Model."""

from __future__ import annotations

import pytest

from model_prediction.models.esports_cs2 import (
    CS2SeriesForecast,
    CS2TeamProfile,
    CS2VetoEngine,
)


def test_veto_simulation_selection():
    engine = CS2VetoEngine()

    navi = CS2TeamProfile(
        team_id="navi",
        team_name="Natus Vincere",
        overall_rating=1650.0,
        map_ratings={"Mirage": 1720.0, "Nuke": 1700.0, "Dust2": 1600.0, "Vertigo": 1400.0},
        map_permabans=["Vertigo"],
        pistol_win_rate_ct=0.58,
        pistol_win_rate_t=0.52,
    )
    faze = CS2TeamProfile(
        team_id="faze",
        team_name="FaZe Clan",
        overall_rating=1620.0,
        map_ratings={"Inferno": 1710.0, "Ancient": 1680.0, "Anubis": 1420.0},
        map_permabans=["Anubis"],
        pistol_win_rate_ct=0.52,
        pistol_win_rate_t=0.50,
    )

    maps = engine.simulate_bo3_veto(navi, faze)
    assert len(maps) == 3
    # Permabans must be respected (Vertigo and Anubis not in maps)
    assert "Vertigo" not in maps
    assert "Anubis" not in maps
    # Map 1 should be NaVi pick (e.g. Mirage or Nuke)
    assert maps[0] in ["Mirage", "Nuke"]
    # Map 2 should be FaZe pick (e.g. Inferno or Ancient)
    assert maps[1] in ["Inferno", "Ancient"]


def test_series_forecast_exact_probabilities():
    engine = CS2VetoEngine()

    team_a = CS2TeamProfile(
        team_id="t1",
        team_name="Vitality",
        overall_rating=1680.0,
        map_ratings={"Mirage": 1700.0, "Inferno": 1650.0, "Nuke": 1680.0},
        pistol_win_rate_ct=0.60,
    )
    team_b = CS2TeamProfile(
        team_id="t2",
        team_name="MOUZ",
        overall_rating=1580.0,
        map_ratings={"Mirage": 1550.0, "Inferno": 1600.0, "Nuke": 1560.0},
        pistol_win_rate_ct=0.45,
    )

    forecast = engine.forecast_series(team_a, team_b, match_format="Bo3")

    assert isinstance(forecast, CS2SeriesForecast)
    assert forecast.p_series_a > 0.65
    assert pytest.approx(forecast.p_series_a + forecast.p_series_b) == 1.0
    # Sum of 2-0 and 2-1 covers all win paths
    assert pytest.approx(forecast.p_2_0_a + forecast.p_2_1_a) == forecast.p_series_a
    assert pytest.approx(forecast.p_2_0_b + forecast.p_2_1_b) == forecast.p_series_b
