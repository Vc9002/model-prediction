"""Unit tests for WNBA v5 Possession x PPP model."""

from __future__ import annotations

import pytest

from model_prediction.models.wnba_possession import (
    WNBA_LEAGUE_ORTG,
    WNBA_LEAGUE_PACE,
    WNBAGameBoxscore,
    WNBAPossessionEngine,
)


def test_wnba_possession_boxscore_calculation():
    # FGA=65, FTA=20, OREB=10, TOV=12 -> Poss = 65 + 8.8 - 10 + 12 = 75.8
    box = WNBAGameBoxscore(
        game_id="g1",
        game_date="2026-06-01",
        team_id="LVA",
        opponent_id="NYL",
        is_home=True,
        points_scored=88,
        points_allowed=75,
        fga=65,
        fta=20,
        oreb=10,
        tov=12,
    )
    assert box.possessions == pytest.approx(75.8, abs=0.1)


def test_wnba_team_state_shrinkage():
    engine = WNBAPossessionEngine()
    # High-pace high-efficiency game
    engine.record_boxscore(
        WNBAGameBoxscore(
            game_id="g1",
            game_date="2026-06-01",
            team_id="LVA",
            opponent_id="NYL",
            is_home=True,
            points_scored=100,
            points_allowed=80,
            fga=75,
            fta=25,
            oreb=8,
            tov=10,  # ~88 possessions
        )
    )

    state = engine.evaluate_team_state("LVA", as_of_date="2026-06-05")
    assert state.games_played == 1
    # Shrunk pace should be between league prior (79.5) and raw pace (~88)
    assert WNBA_LEAGUE_PACE < state.pace_per_40 < state.raw_pace
    # Shrunk ORtg should be between league prior (102.5) and raw ORtg (~113)
    assert WNBA_LEAGUE_ORTG < state.ortg < state.raw_ortg
    assert state.net_rating > 0


def test_wnba_game_forecast_and_derivative_markets():
    engine = WNBAPossessionEngine()
    # Record 5 strong games for LVA
    for i in range(1, 6):
        engine.record_boxscore(
            WNBAGameBoxscore(
                game_id=f"lva_{i}",
                game_date=f"2026-06-0{i}",
                team_id="LVA",
                opponent_id="OPP",
                is_home=True,
                points_scored=92,
                points_allowed=76,
                fga=70,
                fta=20,
                oreb=8,
                tov=10,
            )
        )
    # Record 5 average games for CHI
    for i in range(1, 6):
        engine.record_boxscore(
            WNBAGameBoxscore(
                game_id=f"chi_{i}",
                game_date=f"2026-06-0{i}",
                team_id="CHI",
                opponent_id="OPP",
                is_home=False,
                points_scored=78,
                points_allowed=84,
                fga=68,
                fta=16,
                oreb=9,
                tov=14,
            )
        )

    forecast = engine.forecast_game("LVA", "CHI", as_of_date="2026-06-10")
    assert forecast.expected_margin > 5.0
    assert forecast.p_home_win > 0.65
    assert forecast.p_away_win == pytest.approx(1.0 - forecast.p_home_win, abs=1e-3)
    assert forecast.expected_total > 150.0

    # Test spread and totals derivative pricing
    p_cover_minus4_5 = forecast.p_cover_spread(spread_away=4.5)  # Home -4.5
    assert p_cover_minus4_5 > 0.50

    p_over = forecast.p_over_total(total_line=160.0)
    p_under = forecast.p_under_total(total_line=160.0)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-3)
