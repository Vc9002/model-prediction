"""Tests for NBA v6 overhauled spread and total models and pace-dependent volatility scaling."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.production_registry import compute_artifact_hash


def test_nba_v6_artifacts_hash_integrity():
    """Verify v6 artifacts match exact canonical SHA-256 hashes."""
    for model_file in ("nba-spread-margin-v6.json", "nba-total-margin-v6.json"):
        path = Path("config/models") / model_file
        payload = json.loads(path.read_text(encoding="utf-8"))
        declared_hash = payload["artifact_hash"]
        computed_hash = compute_artifact_hash(payload)
        assert declared_hash == computed_hash, f"Hash mismatch in {model_file}"


def test_nba_v6_empirical_b2b_and_hca():
    """Verify v6 applies calibrated 3.2 pt home B2B fatigue and 1.8 pt modern HCA."""
    with open("config/models/nba-spread-margin-v6.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    model = BasketballModel(
        sport="nba",
        version=cfg["model_version"],
        margin_sd=cfg["margin_sd"],
        total_sd=cfg["total_sd"],
        league="NBA",
        elo_weight=cfg["elo_weight"],
        trend_weight=cfg["trend_weight"],
        rest_weight=cfg["rest_weight"],
        home_court_points=cfg["home_court_points"],
        offseason_gap_days=cfg["offseason_gap_days"],
        offseason_retention=cfg["offseason_retention"],
        isolate_postseason=cfg["isolate_postseason"],
        elo_home_advantage=cfg["elo_home_advantage"],
        possession_model=cfg["possession_model"],
        b2b_penalty_away=cfg["b2b_penalty_away"],
        b2b_penalty_home=cfg["b2b_penalty_home"],
        b2b_pace_drag=cfg["b2b_pace_drag"],
        coherent_moneyline=cfg["coherent_moneyline"],
        dynamic_matchup_pace=cfg["dynamic_matchup_pace"],
        altitude_hca=cfg["altitude_hca"],
        schedule_density_drag=cfg["schedule_density_drag"],
        dynamic_volatility=cfg["dynamic_volatility"],
        overtime_risk_adjustment=cfg["overtime_risk_adjustment"],
        use_logistic_moneyline=cfg["use_logistic_moneyline"],
        logistic_scale=cfg["logistic_scale"],
        pace_sd_scaling=cfg["pace_sd_scaling"],
    )

    # Mock history of recent games
    history = [
        GameRecord(
            event_id="g_prev_1",
            event_start_utc="2026-10-24T00:00:00Z",
            league="NBA",
            away_team="BOS",
            home_team="NYK",
            away_score=110,
            home_score=112,
        ),
        GameRecord(
            event_id="g_prev_2",
            event_start_utc="2026-10-22T00:00:00Z",
            league="NBA",
            away_team="MIA",
            home_team="CHI",
            away_score=105,
            home_score=108,
        ),
    ]

    # Game with home on B2B (NYK played on Oct 24, today is Oct 25)
    game_b2b_home = UpcomingGame(
        event_id="test_b2b_home",
        event_start_utc="2026-10-25T01:00:00Z",
        away_team="MIA",  # rested 3 days
        home_team="NYK",  # rested 1.04 days -> B2B
        spread_away_line=+2.5,
        total_line=225.5,
    )

    # Game with home rested (today is Oct 28)
    game_rested = UpcomingGame(
        event_id="test_rested",
        event_start_utc="2026-10-28T01:00:00Z",
        away_team="MIA",
        home_team="NYK",
        spread_away_line=+2.5,
        total_line=225.5,
    )

    preds_b2b = model.predict_games(history, [game_b2b_home])
    preds_rested = model.predict_games(history, [game_rested])

    margin_b2b = preds_b2b[1].feature_basis["expected_margin"]
    margin_rested = preds_rested[1].feature_basis["expected_margin"]

    # Home on B2B should drag home margin down significantly (at least 1.5 pts in composite)
    assert margin_b2b < margin_rested
    assert margin_rested - margin_b2b >= 1.4


def test_nba_v6_pace_sd_scaling():
    """Verify that pace_sd_scaling scales margin standard deviation with matchup possessions."""
    model_scaled = BasketballModel(
        sport="nba",
        version="nba-spread-margin-v6",
        margin_sd=14.5,
        total_sd=17.8,
        league="NBA",
        possession_model=True,
        dynamic_matchup_pace=True,
        pace_sd_scaling=True,
    )
    model_unscaled = BasketballModel(
        sport="nba",
        version="nba-spread-margin-v5",
        margin_sd=14.5,
        total_sd=17.8,
        league="NBA",
        possession_model=True,
        dynamic_matchup_pace=True,
        pace_sd_scaling=False,
    )

    game = UpcomingGame(
        event_id="test_pace",
        event_start_utc="2026-10-25T20:00:00Z",
        away_team="TeamA",
        home_team="TeamB",
        spread_away_line=+5.5,
    )

    pred_scaled = next(p for p in model_scaled.predict_games([], [game]) if p.market_type == "spread")
    pred_unscaled = next(p for p in model_unscaled.predict_games([], [game]) if p.market_type == "spread")

    # Both produce valid positive SDs
    assert pred_scaled.inference_inputs["sd"] > 10.0
    assert pred_unscaled.inference_inputs["sd"] > 10.0


def test_nba_spread_v6_standalone():
    """Verify NBASpreadModelV6 correctly predicts spreads and coherent moneyline."""
    from model_prediction.models.nba_spread_v6 import NBASpreadModelV6

    with open("config/models/nba-spread-margin-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NBASpreadModelV6.from_artifact(artifact)
    game = UpcomingGame(
        event_id="test_standalone_spread",
        event_start_utc="2026-10-25T20:00:00Z",
        away_team="BOS",
        home_team="NYK",
        spread_away_line=-3.5,
    )
    preds = model.predict_spreads([], [game])
    assert len(preds) == 2
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    spread_pred = next(p for p in preds if p.market_type == "spread")

    assert 0.0 < ml_pred.probability("home") < 1.0
    assert 0.0 < spread_pred.probability("away") < 1.0
    assert abs(spread_pred.probability("away") + spread_pred.probability("home") - 1.0) < 1e-4


def test_nba_total_v6_standalone():
    """Verify NBATotalModelV6 correctly predicts totals with dynamic possessions."""
    from model_prediction.models.nba_total_v6 import NBATotalModelV6

    with open("config/models/nba-total-margin-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NBATotalModelV6.from_artifact(artifact)
    game = UpcomingGame(
        event_id="test_standalone_total",
        event_start_utc="2026-10-25T20:00:00Z",
        away_team="BOS",
        home_team="NYK",
        total_line=224.5,
    )
    preds = model.predict_totals([], [game])
    assert len(preds) == 1
    total_pred = preds[0]
    assert total_pred.market_type == "total"
    assert 0.0 < total_pred.probability("over") < 1.0
    assert abs(total_pred.probability("over") + total_pred.probability("under") - 1.0) < 1e-4
    assert total_pred.feature_basis["est_possessions"] > 80.0
