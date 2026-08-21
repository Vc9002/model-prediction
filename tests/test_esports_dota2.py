"""Unit tests for Dota 2 Objective Priority & Map Geography Model."""

from __future__ import annotations

import pytest

from model_prediction.models.esports_dota2 import (
    Dota2Engine,
    Dota2MatchForecast,
    Dota2TeamProfile,
)


def test_radiant_boost_and_nw15():
    engine = Dota2Engine(radiant_boost=0.025)

    spirit = Dota2TeamProfile(
        team_id="ts",
        team_name="Team Spirit",
        overall_rating=1650.0,
        avg_nw15_diff=1200.0,
        first_roshan_rate=0.65,
    )
    falcons = Dota2TeamProfile(
        team_id="flc",
        team_name="Team Falcons",
        overall_rating=1650.0,
        avg_nw15_diff=0.0,
        first_roshan_rate=0.50,
    )

    p_game_1 = engine.evaluate_game_probability(spirit, falcons)
    # Equal Elo -> 50% base + 2.5% Radiant boost + ~3.6% NW boost = ~56.1%
    assert p_game_1 > 0.55


def test_bo2_group_stage_polymarket_tie_settlement():
    engine = Dota2Engine()

    team_a = Dota2TeamProfile(team_id="t1", team_name="Liquid", overall_rating=1600.0)
    team_b = Dota2TeamProfile(team_id="t2", team_name="Gaimin", overall_rating=1600.0)

    forecast_bo2 = engine.forecast_series(team_a, team_b, match_format="Bo2")

    assert isinstance(forecast_bo2, Dota2MatchForecast)
    assert forecast_bo2.p_tie_bo2 > 0.40  # Bo2 ties are frequent between equal teams (~45-50%)
    # Sum of 2-0, 0-2, and 1-1 is 1.0
    assert (
        pytest.approx(forecast_bo2.p_series_radiant + forecast_bo2.p_series_dire + forecast_bo2.p_tie_bo2)
        == 1.0
    )
    # Expected payout: P(2-0) + 0.5*P(Tie) sums to 1.0 across both teams
    assert pytest.approx(forecast_bo2.expected_payout_radiant + forecast_bo2.expected_payout_dire) == 1.0
