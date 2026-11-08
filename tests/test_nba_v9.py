"""Targeted tests for NBA Structural Model v9 (Spread, Total, Moneyline)."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v9 import (
    NBAMoneylineModelV9,
    NBASpreadModelV9,
    NBAStructuralV9Engine,
    NBATotalModelV9,
)
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


def test_nba_v9_artifacts_hash_validity():
    for name in ["nba-spread-margin-v9.json", "nba-total-margin-v9.json", "nba-moneyline-v9.json"]:
        path = Path("config/models") / name
        assert path.exists(), f"{path} must exist"
        with open(path, "r", encoding="utf-8") as f:
            artifact = json.load(f)
        expected_hash = compute_artifact_hash(artifact)
        assert artifact["artifact_hash"] == expected_hash, f"Hash mismatch in {name}"


def test_nba_v9_engine_mathematical_invariants():
    engine = NBAStructuralV9Engine()
    assert engine.dynamic_volatility is False, "v9 must disable dynamic volatility"
    assert engine.half_pace_additive is True, "v9 must use additive half-pace adjustment"
    assert engine.sample_scaled_overtime is True, "v9 must use sample-scaled overtime"

    history = [
        GameRecord(
            event_id="test_1",
            event_start_utc="2026-10-20T23:00:00Z",
            league="NBA",
            away_team="Boston Celtics",
            home_team="New York Knicks",
            away_score=108,
            home_score=114,
            season_type="regular",
            season_year=2026,
        ),
        GameRecord(
            event_id="test_2",
            event_start_utc="2026-10-22T23:00:00Z",
            league="NBA",
            away_team="Miami Heat",
            home_team="New York Knicks",
            away_score=99,
            home_score=105,
            season_type="regular",
            season_year=2026,
        ),
    ]

    upcoming = [
        UpcomingGame(
            event_id="test_3",
            event_start_utc="2026-10-24T23:00:00Z",
            away_team="Miami Heat",
            home_team="Boston Celtics",
            spread_away_line=4.5,
            total_line=222.5,
            availability_points_gap=1.5,
            availability_total_loss=3.0,
            league="NBA",
        )
    ]

    preds = engine.predict_games(history, upcoming)
    assert len(preds) == 3, "Engine must produce ML, Spread, and Total predictions"

    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    sp_pred = next(p for p in preds if p.market_type == "spread")
    tot_pred = next(p for p in preds if p.market_type == "total")

    p_h = ml_pred.probability("home")
    p_a = ml_pred.probability("away")
    assert 0.0 < p_h < 1.0
    assert 0.0 < p_a < 1.0
    assert abs((p_h + p_a) - 1.0) < 1e-4

    # Spread probabilities
    sp_a = sp_pred.probability("away")
    sp_h = sp_pred.probability("home")
    assert 0.0 < sp_a < 1.0
    assert abs((sp_a + sp_h) - 1.0) < 1e-4
    assert sp_pred.feature_basis["effective_margin_sd"] == 14.0, (
        "Effective margin SD must be fixed 14.0 without dynamic volatility"
    )

    # Total probabilities
    tot_over = tot_pred.probability("over")
    tot_under = tot_pred.probability("under")
    assert 0.0 < tot_over < 1.0
    assert abs((tot_over + tot_under) - 1.0) < 1e-4


def test_nba_v9_models_from_artifact():
    with open("config/models/nba-spread-margin-v9.json", "r", encoding="utf-8") as f:
        sp_art = json.load(f)
    sp_mod = NBASpreadModelV9.from_artifact(sp_art)
    assert sp_mod.version == "nba-spread-margin-v9"
    assert sp_mod.engine.dynamic_volatility is False

    with open("config/models/nba-total-margin-v9.json", "r", encoding="utf-8") as f:
        tot_art = json.load(f)
    tot_mod = NBATotalModelV9.from_artifact(tot_art)
    assert tot_mod.version == "nba-total-margin-v9"
    assert tot_mod.engine.half_pace_additive is True

    with open("config/models/nba-moneyline-v9.json", "r", encoding="utf-8") as f:
        ml_art = json.load(f)
    ml_mod = NBAMoneylineModelV9.from_artifact(ml_art)
    assert ml_mod.version == "nba-moneyline-v9"


def test_nba_v9_production_registry():
    reg = ProductionModelRegistry.load(Path("."))
    champ_sp = reg.champion("NBA", "spread")
    assert champ_sp is not None
    assert champ_sp.model_id == "nba-spread-margin-v9"
    assert champ_sp.serving_status == "production"

    champ_tot = reg.champion("NBA", "total")
    assert champ_tot is not None
    assert champ_tot.model_id == "nba-total-margin-v9"
    assert champ_tot.serving_status == "production"

    champ_ml = reg.champion("NBA", "moneyline")
    assert champ_ml is not None
    assert champ_ml.model_id == "nba-moneyline-v9"
    assert champ_ml.serving_status == "production"
