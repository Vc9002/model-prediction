"""Unit tests for Tennis Barnett & Clarke Point-Level Markov Chain Engine."""

from __future__ import annotations

import pytest

from model_prediction.models.tennis_markov import (
    TennisMarkovEngine,
    TennisPlayerStats,
    game_hold_probability,
    tiebreak_probability,
)


def test_game_hold_probability_monotonicity():
    # 60% point win -> ~73.6% hold
    p_hold_60 = game_hold_probability(0.60)
    assert 0.70 < p_hold_60 < 0.76

    # 65% point win (ATP server standard) -> ~83% hold
    p_hold_65 = game_hold_probability(0.65)
    assert 0.80 < p_hold_65 < 0.86
    assert p_hold_65 > p_hold_60

    # 50% point win -> 50% hold
    assert game_hold_probability(0.50) == pytest.approx(0.50, abs=1e-3)


def test_tiebreak_probability_symmetry():
    # Equal players -> 50% tiebreak win probability
    p_tb_equal = tiebreak_probability(0.65, 0.65)
    assert p_tb_equal == pytest.approx(0.50, abs=1e-3)

    # Player A has stronger serve -> higher tiebreak win probability
    p_tb_stronger = tiebreak_probability(0.70, 0.60)
    assert p_tb_stronger > 0.60


def test_barnett_clarke_opponent_adjustment():
    engine = TennisMarkovEngine()

    player_serve_bot = TennisPlayerStats(
        player_id="p_bot",
        name="Isner",
        serve_points_won_pct=0.72,
        return_points_won_pct=0.25,
    )
    player_elite_returner = TennisPlayerStats(
        player_id="p_ret",
        name="Djokovic",
        serve_points_won_pct=0.66,
        return_points_won_pct=0.42,
    )

    # Against elite returner, Isner serve % is adjusted down from 72%
    p_adj_isner, p_adj_djok = engine.adjust_serve_probabilities(
        player_serve_bot, player_elite_returner, surface="Hard"
    )
    assert p_adj_isner < player_serve_bot.serve_points_won_pct
    # Against poor returner, Djokovic serve % is adjusted up from 66%
    assert p_adj_djok > player_elite_returner.serve_points_won_pct


def test_full_hierarchical_match_forecast():
    engine = TennisMarkovEngine()

    p_sinner = TennisPlayerStats(
        player_id="p_sin",
        name="Sinner",
        serve_points_won_pct=0.68,
        return_points_won_pct=0.40,
    )
    p_opponent = TennisPlayerStats(
        player_id="p_opp",
        name="Qualifier",
        serve_points_won_pct=0.60,
        return_points_won_pct=0.32,
    )

    forecast_bo3 = engine.forecast_match(p_sinner, p_opponent, surface="Hard", match_format="Bo3")
    forecast_bo5 = engine.forecast_match(p_sinner, p_opponent, surface="Hard", match_format="Bo5")

    # Sinner should be heavy favorite
    assert forecast_bo3.p_match_a > 0.75
    # Grand slam format (Bo5) further suppresses upset variance
    assert forecast_bo5.p_match_a > forecast_bo3.p_match_a
    # Sum of win probs is 1.0
    assert pytest.approx(forecast_bo3.p_match_a + forecast_bo3.p_match_b) == 1.0
    assert pytest.approx(forecast_bo3.p_set_1_a + forecast_bo3.p_set_1_b) == 1.0
