"""Tests for MLB Structural Runline v4."""

from model_prediction.models.mlb_runline_v4 import (
    MLBStructuralRunlineV4Model,
)


def test_mlb_runline_v4_complementarity():
    model = MLBStructuralRunlineV4Model()
    fc = model.forecast_runline(
        home_team="Dodgers",
        away_team="Rockies",
        lambda_home=5.5,
        lambda_away=3.2,
    )

    assert fc.lambda_home == 5.5
    assert fc.lambda_away == 3.2
    # Home -1.5 and Away +1.5 must sum to 1.0
    assert abs(fc.prob_home_minus_1_5 + fc.prob_away_plus_1_5 - 1.0) < 1e-4
    # Away -1.5 and Home +1.5 must sum to 1.0
    assert abs(fc.prob_away_minus_1_5 + fc.prob_home_plus_1_5 - 1.0) < 1e-4
    # Favorite (Dodgers) has higher -1.5 probability than Underdog (Rockies)
    assert fc.prob_home_minus_1_5 > fc.prob_away_minus_1_5
