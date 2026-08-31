"""Tests for Esports Contextual v7 Models."""

from model_prediction.models.esports_contextual_v7 import (
    EsportsContextFeatures,
    EsportsContextualV7Model,
)


def test_esports_v7_blue_side_advantage_in_lol():
    model = EsportsContextualV7Model()
    feat_blue = EsportsContextFeatures(
        game_title="lol",
        team_a="T1",
        team_b="GenG",
        elo_prob_a=0.50,
        side_a="blue",
    )
    fc = model.forecast_match(feat_blue)

    assert fc.prob_a_wins > 0.50
    assert fc.edge_vs_elo_prior > 0.0


def test_esports_v7_bo5_format_magnification():
    model = EsportsContextualV7Model()
    feat_bo1 = EsportsContextFeatures(
        game_title="cs2",
        team_a="Vitality",
        team_b="FaZe",
        elo_prob_a=0.60,
        series_format="Bo1",
    )
    feat_bo5 = EsportsContextFeatures(
        game_title="cs2",
        team_a="Vitality",
        team_b="FaZe",
        elo_prob_a=0.60,
        series_format="Bo5",
    )
    fc1 = model.forecast_match(feat_bo1)
    fc5 = model.forecast_match(feat_bo5)

    assert fc1.prob_a_wins > 0.50
    assert fc5.prob_a_wins > 0.50
