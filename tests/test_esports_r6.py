"""Unit tests for Rainbow Six Siege (R6) Tactical Veto & Site Defense Model."""

from __future__ import annotations

import pytest

from model_prediction.models.esports_r6 import (
    R6SeriesForecast,
    R6TeamProfile,
    R6VetoEngine,
)


def test_r6_9map_veto_simulation():
    engine = R6VetoEngine()

    w7m = R6TeamProfile(
        team_id="w7m",
        team_name="w7m esports",
        overall_rating=1680.0,
        map_ratings={"Clubhouse": 1720.0, "Oregon": 1700.0, "Bank": 1400.0},
        map_permabans=["Bank"],
    )
    bds = R6TeamProfile(
        team_id="bds",
        team_name="Team BDS",
        overall_rating=1650.0,
        map_ratings={"Kafe": 1710.0, "Chalet": 1680.0, "Nighthaven": 1420.0},
        map_permabans=["Nighthaven"],
    )

    maps = engine.simulate_bo3_veto(w7m, bds)
    assert len(maps) == 3
    # Permabans must not be played
    assert "Bank" not in maps
    assert "Nighthaven" not in maps


def test_r6_series_forecast():
    engine = R6VetoEngine()

    team_a = R6TeamProfile(team_id="t1", team_name="w7m", overall_rating=1660.0)
    team_b = R6TeamProfile(team_id="t2", team_name="FaZe", overall_rating=1580.0)

    forecast = engine.forecast_series(team_a, team_b, match_format="Bo3")

    assert isinstance(forecast, R6SeriesForecast)
    assert forecast.p_series_a > 0.60
    assert pytest.approx(forecast.p_series_a + forecast.p_series_b) == 1.0
    assert pytest.approx(forecast.p_2_0_a + forecast.p_2_1_a) == forecast.p_series_a
    assert pytest.approx(forecast.p_2_0_b + forecast.p_2_1_b) == forecast.p_series_b
