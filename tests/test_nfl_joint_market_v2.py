import pytest

from model_prediction.models.nfl_joint_market_v2 import forecast_with_market_anchor


def test_all_markets_come_from_one_market_anchored_score_lattice() -> None:
    forecast = forecast_with_market_anchor(
        incumbent_home_probability=0.524444,
        market_home_probability=0.45,
        market_weight=1.0,
        margin_mean=-5.24,
        margin_sd=14.5,
        total_mean=51.27,
        total_sd=14.2,
        league_avg_total=45.5,
    )
    assert forecast.home_win_probability() == pytest.approx(0.45)
    assert forecast.away_cover_probability(-4.5) + forecast.home_win_probability() <= 1
    assert forecast.over_probability(43.5) + forecast.under_probability(43.5) == pytest.approx(1)


def test_missing_market_probability_fails_closed() -> None:
    with pytest.raises(ValueError):
        forecast_with_market_anchor(0.52, float("nan"), 1.0, -5.2, 14.5, 51.3, 14.2, 45.5)
