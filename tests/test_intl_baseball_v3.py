"""Tests for International Baseball v3 Models (KBO & NPB)."""

from model_prediction.models.international_baseball_v3 import (
    InternationalBaseballV3Model,
)


def test_npb_tie_awareness_and_probability_coherence():
    model = InternationalBaseballV3Model(league="NPB")
    fc = model.forecast_match(
        home_team="Hanshin Tigers",
        away_team="Yomiuri Giants",
        lambda_home=3.5,
        lambda_away=3.2,
    )

    assert fc.prob_draw > 0.01  # NPB has significant tie probability
    assert abs(fc.prob_home_win + fc.prob_draw + fc.prob_away_win - 1.0) < 1e-4
    assert fc.prob_home_win > fc.prob_away_win


def test_kbo_lower_tie_rate_than_npb():
    model_npb = InternationalBaseballV3Model(league="NPB")
    model_kbo = InternationalBaseballV3Model(league="KBO")

    fc_npb = model_npb.forecast_match("A", "B", 4.0, 4.0)
    fc_kbo = model_kbo.forecast_match("A", "B", 4.0, 4.0)

    assert fc_npb.prob_draw > fc_kbo.prob_draw
