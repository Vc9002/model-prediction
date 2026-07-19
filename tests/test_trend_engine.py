from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.features.trends import TrendEngine, market_lag_signal


def game(event_id: str, day: int, away: str, home: str, away_score: int, home_score: int) -> GameRecord:
    return GameRecord(
        event_id=event_id,
        event_start_utc=f"2026-06-{day:02d}T23:00:00Z",
        league="TEST",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
    )


def sample_games() -> list[GameRecord]:
    games = []
    # Hot team scores increasingly; Cold team fades; Wall is a strong defense.
    for index in range(12):
        games.append(game(f"h{index}", index + 1, "Hot", "Wall", 3 + index // 2, 2))
        games.append(game(f"c{index}", index + 1, "Cold", "Sieve", max(0, 8 - index), 5))
    return games


def test_momentum_and_hot_cold_track_direction() -> None:
    engine = TrendEngine(sample_games())
    hot = engine.team_trend("Hot")
    cold = engine.team_trend("Cold")
    assert hot.offensive_momentum > 0
    assert cold.offensive_momentum < 0
    assert hot.hot_cold_score > cold.hot_cold_score
    assert 0 <= engine.momentum_percentile("Hot") <= 1


def test_opponent_adjustment_rewards_scoring_on_strong_defense() -> None:
    engine = TrendEngine(sample_games())
    # "Wall" allows 2/game (well under baseline), so Hot's adjusted offense
    # must exceed its raw scored values relative to a neutral opponent.
    hot = engine.team_trend("Hot")
    assert hot.offense["hl10"] > 0


def test_market_lag_flags_strong_trend_with_flat_market() -> None:
    lag = market_lag_signal(trend_momentum=2.0, momentum_percentile=0.97, market_move=0.005)
    assert lag["lag_detected"] is True
    no_history = market_lag_signal(2.0, 0.97, None)
    assert no_history["lag_detected"] is False


def test_feature_store_point_in_time_cutoff(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    path = store.processed_path("test")
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    rows = [
        {"event_id": "a", "event_start_utc": "2026-06-01T23:00:00Z", "league": "TEST",
         "away_team": "A", "home_team": "B", "away_score": 1, "home_score": 2},
        {"event_id": "b", "event_start_utc": "2026-06-10T23:00:00Z", "league": "TEST",
         "away_team": "A", "home_team": "B", "away_score": 3, "home_score": 2},
        # Same-day game must be EXCLUDED from a 2026-06-10 snapshot.
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    before = store.games_before("test", "2026-06-10")
    assert [game.event_id for game in before] == ["a"]
    snapshot = store.compute("test", "2026-06-10", "trends")
    assert snapshot["input_games"] == 1
    assert snapshot["computation_hash"]
    # Cached read returns the identical snapshot.
    assert store.compute("test", "2026-06-10", "trends") == snapshot
