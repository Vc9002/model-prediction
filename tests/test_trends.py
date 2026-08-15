from model_prediction.features.trends import TrendObservation, opponent_adjusted_ewm_trend


def test_trend_is_opponent_adjusted_recent_and_shrunk() -> None:
    trend = opponent_adjusted_ewm_trend(
        [TrendObservation(100), TrendObservation(105, 2), TrendObservation(110)],
        baseline=100,
        half_life_games=2,
        prior_strength_games=3,
    )
    assert 100 < trend.adjusted_recent_level < 110
    assert trend.change_from_baseline == trend.adjusted_recent_level - 100
    assert trend.effective_games == 3


def test_empty_trend_returns_baseline() -> None:
    trend = opponent_adjusted_ewm_trend([], 42, 3)
    assert trend.adjusted_recent_level == 42
    assert trend.change_from_baseline == 0
