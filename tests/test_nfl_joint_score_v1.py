import math

import pytest

from model_prediction.models.nfl_joint_score_v1 import joint_score_forecast


def test_joint_probabilities_obey_score_constraints() -> None:
    forecast = joint_score_forecast(
        home_win_probability=0.524444,
        margin_mean=-5.24,
        margin_sd=14.5,
        total_mean=51.27,
        total_sd=14.2,
    )
    assert math.isclose(sum(forecast.scores.values()), 1.0, abs_tol=1e-12)
    assert all(h >= 0 and a >= 0 for h, a in forecast.scores)
    assert forecast.home_win_probability() == pytest.approx(0.524444, abs=1e-10)
    assert forecast.away_cover_probability(-4.5) + forecast.home_win_probability() <= 1
    assert forecast.over_probability(43.5) + forecast.under_probability(43.5) == pytest.approx(1)


def test_joint_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        joint_score_forecast(float("nan"), -3, 14, 45, 14)
    with pytest.raises(ValueError):
        joint_score_forecast(0.5, -3, 0, 45, 14)
