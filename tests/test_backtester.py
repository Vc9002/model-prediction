import json
import random

from model_prediction.backtester import _qualification_report, walk_forward_backtest
from model_prediction.features.base import FeatureStore


def seed_games(tmp_path, count: int = 240) -> FeatureStore:
    """Synthetic two-tier league: Strong teams beat Weak ones ~75% of the time."""
    rng = random.Random(3)
    teams = [f"Strong{i}" for i in range(4)] + [f"Weak{i}" for i in range(4)]
    store = FeatureStore(tmp_path)
    path = store.processed_path("test")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    day = 0
    for index in range(count):
        if index % 4 == 0:
            day += 1
        home, away = rng.sample(teams, 2)
        home_edge = ("Strong" in home) - ("Strong" in away)
        home_win_probability = 0.5 + 0.25 * home_edge + 0.05
        home_wins = rng.random() < home_win_probability
        home_score = rng.randint(80, 100) + (5 if home_wins else -5)
        away_score = home_score - rng.randint(1, 12) if home_wins else home_score + rng.randint(1, 12)
        month = 3 + day // 28
        rows.append(
            {
                "event_id": f"g{index}",
                "event_start_utc": f"2026-{month:02d}-{day % 28 + 1:02d}T23:00:00Z",
                "league": "TEST",
                "away_team": away,
                "home_team": home,
                "away_score": away_score,
                "home_score": home_score,
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return store


def test_walk_forward_produces_metrics_and_beats_coin_flip(tmp_path) -> None:
    store = seed_games(tmp_path)
    report = walk_forward_backtest(store, "test", "2026-04-01", "2026-06-30", minimum_history_games=40)
    assert report["status"] == "ok"
    assert report["observations"] > 30
    # Elo on a strongly tiered league must beat the 0.25 coin-flip Brier.
    assert report["brier"] < 0.25
    assert report["by_trend_strength"]
    assert isinstance(report["promotion_eligible"], bool)


def test_empty_cache_reports_status(tmp_path) -> None:
    report = walk_forward_backtest(FeatureStore(tmp_path), "test", "2026-04-01", "2026-06-30")
    assert report["status"] == "no_cached_games"


def test_qualification_ignores_brier_roi_and_price_costs() -> None:
    rows = [{"probability": 0.80, "outcome": 1} for _ in range(33)] + [
        {"probability": 0.80, "outcome": 0} for _ in range(17)
    ]
    decision = _qualification_report(
        rows,
        confidence_threshold=0.65,
        locked_holdout=True,
        brier_score=0.40,
        calibration={"expected_calibration_error": 0.30},
        roi=-0.75,
        price_diagnostics={"bid_ask_cost": 0.20},
    )

    assert decision["qualified"] is True
    assert decision["calls"] == 50
    assert decision["hit_rate"] == 0.66
    assert decision["diagnostic_metrics_affect_qualification"] is False


def test_mlb_backtest_prices_all_markets_and_trends_change_probabilities(tmp_path) -> None:
    rng = random.Random(11)
    store = FeatureStore(tmp_path)
    path = store.processed_path("mlb")
    path.parent.mkdir(parents=True, exist_ok=True)
    teams = ["A", "B", "C", "D", "E", "F"]
    rows = []
    for index in range(360):
        day = index // 6 + 1
        month = 3 + (day - 1) // 28
        away, home = rng.sample(teams, 2)
        home_strength = teams.index(away) - teams.index(home)
        away_score = max(0, int(rng.gauss(4.2 - 0.15 * home_strength, 1.8)))
        home_score = max(0, int(rng.gauss(4.5 + 0.15 * home_strength, 1.8)))
        if home_score == away_score:
            home_score += 1
        rows.append(
            {
                "event_id": f"mlb-{index}",
                "event_start_utc": f"2026-{month:02d}-{(day - 1) % 28 + 1:02d}T23:00:00Z",
                "league": "MLB",
                "away_team": away,
                "home_team": home,
                "away_score": away_score,
                "home_score": home_score,
                "season_type": "regular-season",
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    report = walk_forward_backtest(
        store,
        "mlb",
        "2026-04-01",
        "2026-05-28",
        minimum_history_games=60,
    )
    assert report["status"] == "ok"
    assert set(report["by_market"]) == {"moneyline", "spread", "total"}
    assert all(report["by_market"][market]["observations"] > 0 for market in report["by_market"])
    assert report["trend_causality"]["records_changed_vs_long_horizon_only"] > 0
    assert report["market_economics"]["roi"] is None
    assert report["promotion_eligible"] is False


def test_mlb_feature_store_excludes_preseason_from_history(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    processed = store.processed_path("mlb")
    processed.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "event_id": "pre",
            "event_start_utc": "2026-03-10T18:00:00Z",
            "league": "MLB",
            "away_team": "A",
            "home_team": "B",
            "away_score": 10,
            "home_score": 0,
        },
        {
            "event_id": "reg",
            "event_start_utc": "2026-04-01T18:00:00Z",
            "league": "MLB",
            "away_team": "A",
            "home_team": "B",
            "away_score": 2,
            "home_score": 3,
        },
    ]
    processed.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    raw = tmp_path / "raw" / "mlb" / "2026-03-10" / "scores_mlb.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(
        json.dumps(
            {
                "events": [
                    {"id": "pre", "season": {"slug": "preseason", "year": 2026}},
                    {"id": "reg", "season": {"slug": "regular-season", "year": 2026}},
                ]
            }
        )
    )
    assert [game.event_id for game in store.load_games("mlb")] == ["reg"]


def test_mlb_feature_store_enforces_april_through_october_when_season_type_missing(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    processed = store.processed_path("mlb")
    processed.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "event_id": event_id,
            "event_start_utc": timestamp,
            "league": "MLB",
            "away_team": "A",
            "home_team": "B",
            "away_score": 2,
            "home_score": 3,
        }
        for event_id, timestamp in (
            ("march", "2026-03-31T23:59:00Z"),
            ("utc_april_but_et_march", "2026-04-01T03:59:00Z"),
            ("april", "2026-04-01T04:00:00Z"),
            ("october", "2026-11-01T03:59:00Z"),
            ("november", "2026-11-01T05:00:00Z"),
        )
    ]
    processed.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    assert [game.event_id for game in store.load_games("mlb")] == ["april", "october"]
