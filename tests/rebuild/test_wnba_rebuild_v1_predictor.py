"""Tests for the `wnba-elo-trend-lr-rebuild-v1` challenger predictor
(`rebuild/wnba/rebuild_v1_predictor.py`) -- proves the persisted artifact +
calibrator actually load and produce a real prediction for a real
historical WNBA game, per the task's serving-integration requirement.
This module is deliberately NOT wired into `sport_adapter.py`'s
`rebuild-shadow` registry (see the model card's "Serving integration"
section) -- these tests exercise the standalone loader/predictor directly.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from model_prediction.rebuild.wnba.elo_trend import build_dataset
from model_prediction.rebuild.wnba.rebuild_v1_predictor import (
    load_calibrator_artifact,
    load_model_artifact,
    predict_row,
    raw_probability,
)

MODEL_PATH = Path("config/models/challengers/wnba-elo-trend-lr-rebuild-v1.json")
CALIBRATOR_PATH = Path("config/models/challengers/wnba-elo-trend-lr-rebuild-v1-calibrator.json")


def _require_artifacts() -> None:
    if not MODEL_PATH.exists() or not CALIBRATOR_PATH.exists():
        pytest.skip("wnba-elo-trend-lr-rebuild-v1 artifacts not present in this environment")


def test_artifacts_are_challenger_scoped_and_disclose_caveats() -> None:
    _require_artifacts()
    artifact = load_model_artifact(MODEL_PATH)
    assert artifact["sport"] == "wnba"
    assert artifact["method"] == "logistic_regression"
    provenance = artifact["provenance"]
    assert provenance["availability_basis"] == "capture_time_only"
    assert provenance["commercial_use_status"] == "unresolved"
    assert provenance["production_allowed"] is False
    assert artifact["qualification"]["qualified"] is False
    assert "wnba-elo-trend-lr-v4" in provenance["sibling_of_incumbent"]

    calibrator_artifact = load_model_artifact(CALIBRATOR_PATH)
    assert calibrator_artifact["provenance"]["production_allowed"] is False
    assert calibrator_artifact["base_model_hash"] == artifact["artifact_hash"]


def test_predicts_a_real_historical_game_deterministically() -> None:
    _require_artifacts()
    result = build_dataset("data/rebuild", [2022, 2023, 2024, 2025])
    if not result.rows:
        pytest.skip("real WNBA rebuild data not backfilled in this environment")

    artifact = load_model_artifact(MODEL_PATH)
    calibrator = load_calibrator_artifact(CALIBRATOR_PATH)

    # A real, completed, historical game -- the most recent one the walk-
    # forward loop could score.
    target_row = result.rows[-1]

    prediction_1 = predict_row(artifact, calibrator, target_row)
    prediction_2 = predict_row(artifact, calibrator, target_row)

    assert prediction_1 == prediction_2  # deterministic, no hidden randomness
    assert prediction_1["event_id"] == target_row.event_id
    assert 0.0 < prediction_1["home_win_probability_raw"] < 1.0
    assert 0.0 < prediction_1["home_win_probability_calibrated"] < 1.0
    assert prediction_1["model_version"] == "wnba-elo-trend-lr-rebuild-v1"


def test_raw_probability_matches_a_hand_computed_logistic_forward_pass() -> None:
    _require_artifacts()
    artifact = load_model_artifact(MODEL_PATH)
    market = artifact["market_models"]["moneyline"]

    from model_prediction.rebuild.wnba.elo_trend import WalkForwardRow

    row = WalkForwardRow(
        event_id="synthetic",
        event_start_utc="2024-05-01T00:00:00+00:00",
        sports_event_date="2024-05-01",
        season=2024,
        home_team_id="A",
        away_team_id="B",
        home_win=1,
        elo_probability=0.65,
        trend_gap=1.5,
        defensive_trend_gap=-0.5,
        home_elo_rating=1550.0,
        away_elo_rating=1480.0,
        home_games_played=20,
        away_games_played=20,
        last_home_update_utc=None,
        last_away_update_utc=None,
    )
    values = {"elo_probability": 0.65, "trend_gap": 1.5, "defensive_trend_gap": -0.5}
    expected_z = market["intercept"] + sum(
        coef * values[name]
        for coef, name in zip(market["coefficients"], market["feature_names"], strict=True)
    )
    expected_p = 1.0 / (1.0 + math.exp(-expected_z))

    assert raw_probability(artifact, row) == pytest.approx(expected_p)
