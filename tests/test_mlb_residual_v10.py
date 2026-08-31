"""Tests for MLB Market-Residual v10 Architecture."""

from model_prediction.models.mlb_market_residual_v10 import (
    MLBMarketResidualV10Model,
    MLBResidualFeatures,
)


def test_market_residual_v10_identity_when_zero_deltas():
    model = MLBMarketResidualV10Model()
    feat = MLBResidualFeatures(
        market_fair_prob_home=0.55,
        market_open_prob_home=0.55,
    )
    fc = model.forecast_matchup(feat)

    # When information deltas are 0, model equals market prior exactly
    assert fc.p_home_win == 0.55
    assert fc.edge_vs_market == 0.0
    assert abs(fc.residual_adjustment_logit) < 1e-4


def test_market_residual_v10_reacts_to_lineup_and_starter_deltas():
    model = MLBMarketResidualV10Model()
    feat = MLBResidualFeatures(
        market_fair_prob_home=0.50,
        market_open_prob_home=0.50,
        lineup_woba_delta_home=0.040,  # Confirmed lineup significantly better
        starter_csw_delta=0.030,  # Ace starting pitcher
        bullpen_freshness_gap=1.0,
    )
    fc = model.forecast_matchup(feat)

    assert fc.residual_adjustment_logit > 0
    assert fc.p_home_win > 0.50
    assert fc.edge_vs_market > 0.0
