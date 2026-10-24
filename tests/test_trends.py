from datetime import UTC, datetime

from model_prediction.features.base import GameRecord
from model_prediction.features.trends import (
    TrendEngine,
    TrendObservation,
    ewm_level,
    opponent_adjusted_ewm_trend,
)


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


def test_ewm_level_offseason_decay() -> None:
    values = [100.0, 100.0, 150.0]
    dates = [
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 3, tzinfo=UTC),
        datetime(2026, 10, 20, tzinfo=UTC),  # ~200 days gap
    ]
    # Without offseason decay:
    standard = ewm_level(values, half_life=3.0, baseline=100.0, prior=3.0)
    # With offseason decay (retention 0.20):
    decayed = ewm_level(
        values,
        half_life=3.0,
        baseline=100.0,
        prior=3.0,
        dates=dates,
        offseason_gap_days=60.0,
        offseason_retention=0.20,
    )
    # The recent high score (150) should have much higher relative weight when older 100s are decayed
    assert decayed > standard


def test_trend_engine_isolate_postseason() -> None:
    games = [
        GameRecord(
            event_id="g1",
            event_start_utc="2026-04-10T00:00:00Z",
            league="NBA",
            away_team="BOS",
            home_team="MIA",
            away_score=120,
            home_score=110,
            season_type="regular-season",
        ),
        GameRecord(
            event_id="g2",
            event_start_utc="2026-05-10T00:00:00Z",
            league="NBA",
            away_team="BOS",
            home_team="MIA",
            away_score=90,
            home_score=85,
            season_type="post-season",
        ),
    ]
    engine_with_postseason = TrendEngine(games, isolate_postseason=False)
    engine_isolated = TrendEngine(games, isolate_postseason=True)

    # Isolated engine must have ignored the post-season game
    bos_post = engine_with_postseason.team_trend("BOS")
    bos_iso = engine_isolated.team_trend("BOS")

    assert bos_iso.games_played == 1
    assert bos_post.games_played == 2
    assert bos_iso.offense["hl10"] > bos_post.offense["hl10"]
