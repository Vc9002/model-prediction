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


def _two_team_games(n: int = 3) -> list[GameRecord]:
    return [
        GameRecord(
            event_id=f"g{i}",
            event_start_utc=f"2026-01-{10 + i:02d}T00:00:00Z",
            league="NCAAB",
            away_team="Fast",
            home_team="Slow",
            away_score=80,
            home_score=70,
        )
        for i in range(n)
    ]


def test_trend_engine_without_team_baselines_is_unchanged() -> None:
    games = _two_team_games()
    plain = TrendEngine(games, offseason_gap_days=150.0, offseason_retention=0.35)
    explicit_none = TrendEngine(
        games,
        offseason_gap_days=150.0,
        offseason_retention=0.35,
        offense_baselines=None,
        defense_baselines=None,
    )
    assert plain.team_trend("Fast") == explicit_none.team_trend("Fast")
    assert plain.team_trend("Unknown").offense["hl10"] == plain.league_baseline


def test_trend_engine_team_baselines_set_the_shrinkage_target() -> None:
    games = _two_team_games()
    engine = TrendEngine(
        games,
        offseason_gap_days=150.0,
        offseason_retention=0.35,
        offense_baselines={"Unknown": 90.0, "Fast": 95.0},
        defense_baselines={"Unknown": 60.0},
    )
    # A team with no games sits exactly on its own prior, not the league mean.
    unknown = engine.team_trend("Unknown")
    assert unknown.offense["hl10"] == 90.0
    assert unknown.defense["hl10"] == 60.0
    # A team with games is pulled toward its prior: a higher prior gives a higher level
    # than the league-mean shrinkage, with the same observed scores.
    plain = TrendEngine(games, offseason_gap_days=150.0, offseason_retention=0.35)
    assert engine.team_trend("Fast").offense["hl10"] > plain.team_trend("Fast").offense["hl10"]
    # Teams without an entry keep the league mean (defense for Fast was not given).
    assert engine.team_trend("Fast").defense["hl10"] == plain.team_trend("Fast").defense["hl10"]
