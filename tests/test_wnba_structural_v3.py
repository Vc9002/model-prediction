"""Tests for WNBA Unified Structural v3 Engine."""

from model_prediction.models.wnba_structural_v3 import (
    WNBAStructuralV3Engine,
)


def test_wnba_structural_v3_forecast():
    engine = WNBAStructuralV3Engine()
    fc = engine.forecast_game(
        home_team="Liberty",
        away_team="Aces",
        home_pace=82.0,
        away_pace=81.0,
        home_ortg=1.08,
        home_drtg=0.98,
        away_ortg=1.05,
        away_drtg=1.00,
        spread_home_line=-4.5,
        total_line=168.5,
    )

    assert fc.projected_possessions > 75.0
    assert fc.projected_home_points > fc.projected_away_points
    assert fc.prob_home_win > 0.50
    assert abs(fc.prob_home_win + fc.prob_away_win - 1.0) < 1e-4
    assert abs(fc.prob_home_cover + fc.prob_away_cover - 1.0) < 1e-4
    assert abs(fc.prob_over + fc.prob_under - 1.0) < 1e-4
