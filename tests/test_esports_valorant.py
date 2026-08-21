"""Unit tests for Valorant Map Veto & Tactical Economy Model."""

from __future__ import annotations

import pytest

from model_prediction.models.esports_valorant import (
    ValorantSeriesForecast,
    ValorantTeamProfile,
    ValorantVetoEngine,
)


def test_valorant_veto_simulation():
    engine = ValorantVetoEngine()

    sen = ValorantTeamProfile(
        team_id="sen",
        team_name="Sentinels",
        overall_rating=1650.0,
        map_ratings={"Sunset": 1720.0, "Lotus": 1700.0, "Split": 1600.0, "Abyss": 1400.0},
        map_permabans=["Abyss"],
        pistol_win_rate=0.56,
    )
    fnc = ValorantTeamProfile(
        team_id="fnc",
        team_name="Fnatic",
        overall_rating=1640.0,
        map_ratings={"Bind": 1710.0, "Haven": 1680.0, "Ascent": 1450.0},
        map_permabans=["Ascent"],
        pistol_win_rate=0.54,
    )

    maps = engine.simulate_bo3_veto(sen, fnc)
    assert len(maps) == 3
    # Permabans must not be played
    assert "Abyss" not in maps
    assert "Ascent" not in maps


def test_valorant_series_forecast():
    engine = ValorantVetoEngine()

    team_a = ValorantTeamProfile(
        team_id="t1",
        team_name="PRX",
        overall_rating=1660.0,
        map_ratings={"Sunset": 1700.0, "Bind": 1650.0, "Lotus": 1680.0},
        pistol_win_rate=0.58,
    )
    team_b = ValorantTeamProfile(
        team_id="t2",
        team_name="DRX",
        overall_rating=1580.0,
        map_ratings={"Sunset": 1550.0, "Bind": 1600.0, "Lotus": 1560.0},
        pistol_win_rate=0.48,
    )

    forecast = engine.forecast_series(team_a, team_b, match_format="Bo3")

    assert isinstance(forecast, ValorantSeriesForecast)
    assert forecast.p_series_a > 0.60
    assert pytest.approx(forecast.p_series_a + forecast.p_series_b) == 1.0
    assert pytest.approx(forecast.p_2_0_a + forecast.p_2_1_a) == forecast.p_series_a
    assert pytest.approx(forecast.p_2_0_b + forecast.p_2_1_b) == forecast.p_series_b
