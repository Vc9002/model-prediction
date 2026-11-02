"""Targeted tests for NBA Structural Model v7 (Spread, Total, Moneyline)."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import (
    NBAMoneylineModelV7,
    NBASpreadModelV7,
    NBAStructuralV7Engine,
    NBATotalModelV7,
)
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


def test_nba_v7_artifacts_hash_validity():
    for name in ["nba-spread-margin-v7.json", "nba-total-margin-v7.json", "nba-moneyline-v7.json"]:
        path = Path("config/models") / name
        assert path.exists(), f"{path} must exist"
        with open(path, "r", encoding="utf-8") as f:
            artifact = json.load(f)
        expected_hash = compute_artifact_hash(artifact)
        assert artifact["artifact_hash"] == expected_hash, f"Hash mismatch in {name}"


def test_nba_v7_registry_champions():
    reg = ProductionModelRegistry.load(Path("."))
    ml_champ = reg.champion("NBA", "moneyline")
    assert ml_champ is not None
    assert ml_champ.model_id == "nba-moneyline-v7"
    assert ml_champ.evidence_status == "predictively_qualified"

    sp_champ = reg.champion("NBA", "spread")
    assert sp_champ is not None
    assert sp_champ.model_id == "nba-spread-margin-v7"
    assert sp_champ.evidence_status == "predictively_qualified"

    tot_champ = reg.champion("NBA", "total")
    assert tot_champ is not None
    assert tot_champ.model_id == "nba-total-margin-v7"
    assert tot_champ.evidence_status == "predictively_qualified"


def test_nba_v7_engine_mathematical_invariants():
    engine = NBAStructuralV7Engine()
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

    # Invariants
    p_h = ml_pred.probability("home")
    p_a = ml_pred.probability("away")
    assert 0.0 < p_h < 1.0
    assert 0.0 < p_a < 1.0
    assert abs((p_h + p_a) - 1.0) < 1e-4

    # Spread
    p_away_cover = sp_pred.probability("away")
    p_home_cover = sp_pred.probability("home")
    assert abs((p_away_cover + p_home_cover) - 1.0) < 1e-4
    assert -25.0 <= sp_pred.feature_basis["expected_margin"] <= 25.0

    # Total
    p_over = tot_pred.probability("over")
    p_under = tot_pred.probability("under")
    assert abs((p_over + p_under) - 1.0) < 1e-4
    assert 180.0 <= tot_pred.feature_basis["expected_total"] <= 260.0


def test_nba_v7_dedicated_model_wrappers():
    with open("config/models/nba-spread-margin-v7.json", "r", encoding="utf-8") as f:
        sp_artifact = json.load(f)
    with open("config/models/nba-total-margin-v7.json", "r", encoding="utf-8") as f:
        tot_artifact = json.load(f)
    with open("config/models/nba-moneyline-v7.json", "r", encoding="utf-8") as f:
        ml_artifact = json.load(f)

    sp_model = NBASpreadModelV7.from_artifact(sp_artifact)
    tot_model = NBATotalModelV7.from_artifact(tot_artifact)
    ml_model = NBAMoneylineModelV7.from_artifact(ml_artifact)

    history = [
        GameRecord(
            event_id="test_1",
            event_start_utc="2026-10-20T23:00:00Z",
            league="NBA",
            away_team="Golden State Warriors",
            home_team="Los Angeles Lakers",
            away_score=115,
            home_score=118,
            season_type="regular",
            season_year=2026,
        )
    ]
    upcoming = [
        UpcomingGame(
            event_id="test_2",
            event_start_utc="2026-10-22T23:00:00Z",
            away_team="Los Angeles Lakers",
            home_team="Golden State Warriors",
            spread_away_line=-2.5,
            total_line=228.0,
            league="NBA",
        )
    ]

    sp_preds = sp_model.predict_spreads(history, upcoming)
    assert len(sp_preds) == 1
    assert sp_preds[0].market_type == "spread"
    assert sp_preds[0].model_version == "nba-spread-margin-v7"

    tot_preds = tot_model.predict_totals(history, upcoming)
    assert len(tot_preds) == 1
    assert tot_preds[0].market_type == "total"
    assert tot_preds[0].model_version == "nba-total-margin-v7"

    ml_preds = ml_model.predict_moneylines(history, upcoming)
    assert len(ml_preds) == 1
    assert ml_preds[0].market_type == "moneyline"
    assert ml_preds[0].model_version == "nba-moneyline-v7"
