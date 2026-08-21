"""Unit tests for League of Legends (LoL) Objective Priority & GD@15 Model."""

from __future__ import annotations

import pytest

from model_prediction.models.esports_lol import (
    LoLEngine,
    LoLMatchForecast,
    LoLTeamProfile,
)


def test_blue_side_boost_and_gd15():
    engine = LoLEngine(blue_side_boost=0.035)

    team_equal_blue = LoLTeamProfile(
        team_id="t1",
        team_name="T1",
        overall_rating=1600.0,
        avg_gd15=500.0,
    )
    team_equal_red = LoLTeamProfile(
        team_id="gen",
        team_name="Gen.G",
        overall_rating=1600.0,
        avg_gd15=0.0,
    )

    p_game_1 = engine.evaluate_game_probability(team_equal_blue, team_equal_red)
    # Equal ratings -> 50% base + 3.5% blue boost + 2.5% GD15 boost = ~56.0%
    assert p_game_1 > 0.55


def test_series_forecast_bo3_vs_bo5():
    engine = LoLEngine()

    t1 = LoLTeamProfile(
        team_id="t1",
        team_name="T1",
        overall_rating=1700.0,
        avg_gd15=800.0,
        first_dragon_rate=0.65,
        first_baron_rate=0.70,
    )
    wbg = LoLTeamProfile(
        team_id="wbg",
        team_name="Weibo Gaming",
        overall_rating=1550.0,
        avg_gd15=-300.0,
        first_dragon_rate=0.40,
        first_baron_rate=0.35,
    )

    forecast_bo3 = engine.forecast_series(t1, wbg, match_format="Bo3")
    forecast_bo5 = engine.forecast_series(t1, wbg, match_format="Bo5")

    assert isinstance(forecast_bo3, LoLMatchForecast)
    assert forecast_bo3.p_series_blue > 0.70
    assert (
        forecast_bo5.p_series_blue > forecast_bo3.p_series_blue
    )  # Bo5 suppresses variance for dominant team
    assert pytest.approx(forecast_bo3.p_series_blue + forecast_bo3.p_series_red) == 1.0
    assert forecast_bo3.p_first_dragon_blue > 0.55
    assert forecast_bo3.p_first_baron_blue > 0.60
