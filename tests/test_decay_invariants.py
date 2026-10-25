"""Automated regression gate enforcing offseason and inactivity decay across all models.

Prevents silent parameter forwarding disconnects, missing timestamps, and omitted
gap thresholds in Basketball, Trends, Soccer, Hockey, and Tennis.
"""

from __future__ import annotations

from model_prediction.features.base import GameRecord
from model_prediction.features.trends import TrendEngine
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.models.hockey import HockeyModel
from model_prediction.models.soccer_v3 import soccer_model_v3
from model_prediction.models.tennis import TennisModel


def _game(
    event_id: str, date_str: str, sport: str, away: str, home: str, a_score: int, h_score: int
) -> GameRecord:
    return GameRecord(
        event_id=event_id,
        event_start_utc=date_str,
        league=sport.upper(),
        away_team=away,
        home_team=home,
        away_score=a_score,
        home_score=h_score,
    )


def test_basketball_offseason_elo_and_trend_decay() -> None:
    """Verify BasketballModel regresses Elo and trend levels after an offseason gap."""
    history = [
        _game("b1", "2026-04-10T00:00:00Z", "nba", "BOS", "NYK", 130, 90),
        _game("b2", "2026-04-12T00:00:00Z", "nba", "BOS", "NYK", 135, 95),
        # Offseason break: ~190 days
        _game("b3", "2026-10-20T00:00:00Z", "nba", "BOS", "NYK", 100, 100),
    ]

    model_with_decay = BasketballModel(
        sport="nba",
        version="nba-test-v5",
        margin_sd=14.5,
        total_sd=18.0,
        league="NBA",
        offseason_gap_days=60.0,
        offseason_retention=0.40,
    )

    preds = model_with_decay.predict_games(
        history,
        [UpcomingGame("u1", "2026-10-22T00:00:00Z", "NYK", "BOS", spread_away_line=-5.5)],
    )
    assert len(preds) > 0
    margin_pred = next(p for p in preds if p.market_type == "spread")
    assert margin_pred.feature_basis["elo_margin"] < 12.0


def test_trend_engine_opponent_offseason_decay() -> None:
    """Verify TrendEngine discounts pre-offseason opponent scoring difficulty."""
    games = [
        _game("g1", "2026-04-10T00:00:00Z", "NBA", "TeamA", "Sieve", 150, 90),
        _game("g2", "2026-04-12T00:00:00Z", "NBA", "TeamB", "Sieve", 150, 90),
        # 190-day offseason
        _game("g3", "2026-10-20T00:00:00Z", "NBA", "TeamC", "Sieve", 80, 90),
    ]

    engine_decayed = TrendEngine(games, offseason_gap_days=60.0, offseason_retention=0.20)
    engine_flat = TrendEngine(games, offseason_gap_days=None)

    def_decayed = engine_decayed._defense_simple("Sieve")
    def_flat = engine_flat._defense_simple("Sieve")

    assert def_decayed < def_flat


def test_soccer_v3_offseason_decay() -> None:
    """Verify SoccerModelV3 discounts scoring strengths across the summer break."""
    history_with_summer_break = [
        _game("s1", "2026-05-10T15:00:00Z", "EPL", "Away", "Arsenal", 0, 5),
        _game("s2", "2026-05-17T15:00:00Z", "EPL", "Away", "Arsenal", 0, 5),
        # 85-day summer break (May to August)
        _game("s3", "2026-08-15T15:00:00Z", "EPL", "Away", "Arsenal", 0, 1),
    ]

    history_compact = [
        _game("s1", "2026-08-01T15:00:00Z", "EPL", "Away", "Arsenal", 0, 5),
        _game("s2", "2026-08-08T15:00:00Z", "EPL", "Away", "Arsenal", 0, 5),
        # Only 7 days gap
        _game("s3", "2026-08-15T15:00:00Z", "EPL", "Away", "Arsenal", 0, 1),
    ]

    model = soccer_model_v3()
    strengths_decayed, _ = model._strengths(history_with_summer_break)
    strengths_compact, _ = model._strengths(history_compact)

    assert strengths_decayed["Arsenal"]["attack"] < strengths_compact["Arsenal"]["attack"]


def test_hockey_offseason_decay() -> None:
    """Verify HockeyModel discounts scoring strengths across the summer offseason."""
    history_summer_break = [
        _game("h1", "2026-04-10T19:00:00Z", "NHL", "Away", "Oilers", 1, 6),
        _game("h2", "2026-04-15T19:00:00Z", "NHL", "Away", "Oilers", 1, 6),
        # 180-day summer break
        _game("h3", "2026-10-15T19:00:00Z", "NHL", "Away", "Oilers", 1, 2),
    ]

    history_compact = [
        _game("h1", "2026-10-01T19:00:00Z", "NHL", "Away", "Oilers", 1, 6),
        _game("h2", "2026-10-08T19:00:00Z", "NHL", "Away", "Oilers", 1, 6),
        # 7-day gap
        _game("h3", "2026-10-15T19:00:00Z", "NHL", "Away", "Oilers", 1, 2),
    ]

    model = HockeyModel(version="hockey-poisson-dc-v2")
    strengths_decayed, _ = model._strengths(history_summer_break, league="NHL")
    strengths_compact, _ = model._strengths(history_compact, league="NHL")

    assert strengths_decayed["Oilers"]["attack"] < strengths_compact["Oilers"]["attack"]


def test_tennis_inactivity_decay() -> None:
    """Verify TennisModel decays ratings for players inactive > 35 days."""
    model = TennisModel(version="tennis-v1")
    overall = {"Alcaraz": 1800.0, "Sinner": 1600.0}
    by_surface = {("Alcaraz", "Hard"): 1800.0, ("Sinner", "Hard"): 1600.0}
    counts = {"Alcaraz": 30, "Sinner": 30}
    surface_counts = {("Alcaraz", "Hard"): 20, ("Sinner", "Hard"): 20}

    p_fresh = model.match_probability_v5(
        overall,
        by_surface,
        surface_counts,
        counts,
        "Alcaraz",
        "Sinner",
        "Hard",
        rest_days={"Alcaraz": 3.0, "Sinner": 3.0},
    )
    p_rusty = model.match_probability_v5(
        overall,
        by_surface,
        surface_counts,
        counts,
        "Alcaraz",
        "Sinner",
        "Hard",
        rest_days={"Alcaraz": 120.0, "Sinner": 3.0},
    )
    assert p_rusty < p_fresh
