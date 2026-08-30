"""Unit tests for MLB Structural v10 Feature Engineering and Model Architecture."""

from __future__ import annotations

import pytest

from model_prediction.features.mlb_v10_features import (
    LEAGUE_XWOBA,
    MLBv10FeatureVector,
)
from model_prediction.models.mlb_structural_v10 import MLBStructuralV10Model, MLBv10Prediction


def _mock_feature_vector(event_id: str = "test-game-1") -> MLBv10FeatureVector:
    return MLBv10FeatureVector(
        event_id=event_id,
        home_team="Los Angeles Dodgers",
        away_team="San Francisco Giants",
        game_start_utc="2026-06-15T23:10:00Z",
        as_of_utc="2026-06-15T22:40:00Z",
        home_sp_name="Tyler Glasnow",
        home_sp_throws="R",
        home_sp_expected_ip=6.0,
        home_sp_k_pct=0.310,
        home_sp_bb_pct=0.075,
        home_sp_k_minus_bb=0.235,
        home_sp_tto_penalty=1.05,
        home_sp_rest_days=5.0,
        away_sp_name="Logan Webb",
        away_sp_throws="R",
        away_sp_expected_ip=6.5,
        away_sp_k_pct=0.240,
        away_sp_bb_pct=0.045,
        away_sp_k_minus_bb=0.195,
        away_sp_tto_penalty=1.075,
        away_sp_rest_days=5.0,
        away_lineup_xwoba_vs_sp=0.312,
        away_lineup_k_pct=0.220,
        away_lineup_bb_pct=0.080,
        away_lineup_iso=0.145,
        away_lineup_barrel_pct=0.070,
        away_lineup_hard_hit_pct=0.370,
        home_lineup_xwoba_vs_sp=0.345,
        home_lineup_k_pct=0.210,
        home_lineup_bb_pct=0.095,
        home_lineup_iso=0.185,
        home_lineup_barrel_pct=0.095,
        home_lineup_hard_hit_pct=0.420,
        away_matchup_k_interaction=0.310 * 0.220,
        home_matchup_k_interaction=0.240 * 0.210,
        away_matchup_bb_interaction=0.075 * 0.080,
        home_matchup_bb_interaction=0.045 * 0.095,
        away_platoon_edge=0.312 - LEAGUE_XWOBA,
        home_platoon_edge=0.345 - LEAGUE_XWOBA,
        home_bp_expected_ip=3.0,
        home_bp_effective_fip=3.60,
        home_bp_freshness=0.85,
        home_bp_hl_available=1.0,
        home_bp_pitches_3d=65,
        away_bp_expected_ip=2.0,
        away_bp_effective_fip=4.20,
        away_bp_freshness=0.70,
        away_bp_hl_available=0.5,
        away_bp_pitches_3d=110,
        park_factor=1.02,
        is_dome=0.0,
        temp_f=72.0,
        air_density_ratio=0.995,
        fly_ball_distance_factor=1.002,
        wind_out_x_barrel=0.03,
        temp_x_iso=0.01,
    )


def test_v10_feature_vector_serialization() -> None:
    feat = _mock_feature_vector()
    d = feat.to_dict()
    assert d["home_sp_expected_ip"] == 6.0
    assert d["away_sp_expected_ip"] == 6.5
    assert d["home_lineup_xwoba_vs_sp"] == 0.345
    assert d["home_bp_effective_fip"] == 3.60


def test_v10_model_fit_and_predict() -> None:
    model = MLBStructuralV10Model(ridge_alpha=5.0)
    feats = [_mock_feature_vector(f"game-{i}") for i in range(20)]
    actual_away = [3.0 + (i % 4) for i in range(20)]
    actual_home = [4.5 + (i % 5) for i in range(20)]

    model.fit(feats, actual_away, actual_home)
    assert model.fitted is True

    pred = model.predict(feats[0])
    assert isinstance(pred, MLBv10Prediction)
    assert pred.projected_total_runs == pytest.approx(
        pred.projected_away_runs + pred.projected_home_runs, abs=0.01
    )
    assert pred.projected_home_margin == pytest.approx(
        pred.projected_home_runs - pred.projected_away_runs, abs=0.01
    )
    assert 1.5 <= pred.projected_away_runs <= 11.0
    assert 1.5 <= pred.projected_home_runs <= 11.0


def test_v10_innings_decomposition_conservation() -> None:
    feat = _mock_feature_vector()
    model = MLBStructuralV10Model()
    pred = model.predict(feat)

    # Away runs vs SP + vs BP = total away runs
    assert pred.away_vs_sp_runs + pred.away_vs_bp_runs == pytest.approx(pred.projected_away_runs, abs=0.02)
    # Home runs vs SP + vs BP = total home runs
    assert pred.home_vs_sp_runs + pred.home_vs_bp_runs == pytest.approx(pred.projected_home_runs, abs=0.02)


def test_v10_frozen_artifact_hashes_and_loading() -> None:
    import json
    from pathlib import Path

    artifact_path = (
        Path(__file__).resolve().parent.parent / "config/models/research/mlb_structural_v10_frozen.json"
    )
    assert artifact_path.exists()

    d = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert d["model_name"] == "MLB Structural v10"
    assert d["model_version"] == "mlb-structural-v10-frozen"
    assert "v10_model_spec_hash" in d["hashes"]
    assert "v10_feature_schema_hash" in d["hashes"]
    assert "v10_confirmation_protocol_hash" in d["hashes"]
    assert "v10_probability_model_hash" in d["hashes"]
    assert len(d["hashes"]["v10_model_spec_hash"]) == 16
    assert len(d["hashes"]["v10_feature_schema_hash"]) == 16
    assert len(d["hashes"]["v10_confirmation_protocol_hash"]) == 16
    assert len(d["hashes"]["v10_probability_model_hash"]) == 16
    assert d["training_sample_size"] == 5427


def test_v10_prospective_shadow_record() -> None:
    from scripts.mlb_v10_prospective_shadow import ProspectivePredictionRecord

    rec = ProspectivePredictionRecord(
        record_type="PREDICTION",
        event_id="test-slug-2026",
        home_team="Los Angeles Dodgers",
        away_team="San Francisco Giants",
        game_start_utc="2026-09-01T23:10:00Z",
        decision_utc="2026-09-01T22:40:00Z",
        created_at_utc="2026-09-01T22:40:05Z",
        market_line=8.5,
        market_prob=0.51,
        market_state_hash="a1b2c3d4e5f6",
        v10_pred_away=4.2,
        v10_pred_home=4.8,
        v10_pred_total=9.0,
        v10_pred_margin=0.6,
        v10_delta_vs_market=0.5,
        m0b_prediction=8.97,
        m4_1_v10_prediction=9.12,
        p_over=0.54,
        p_under=0.46,
        p_push=0.0,
        model_spec_hash="6b677efdf92de0cd",
        feature_snapshot_hash="107a42b6586e7be2",
        probability_model_hash="6c7e1141e365b55b",
        prediction_hash="",
    )
    rec.prediction_hash = rec.compute_prediction_hash()
    assert len(rec.prediction_hash) == 16
    d = rec.to_dict()
    assert d["event_id"] == "test-slug-2026"
    assert d["record_type"] == "PREDICTION"
    assert d["p_over"] == 0.54
    assert d["prediction_hash"] == rec.prediction_hash


def test_v10_daily_operational_audit() -> None:
    from scripts.mlb_v10_daily_operational_audit import run_daily_operational_audit

    rep = run_daily_operational_audit()
    assert rep.operational_status == "PASS"
    assert rep.pit_violations_count == 0
    assert rep.duplicate_predictions_count == 0
    assert rep.late_predictions_count == 0
    assert rep.stage == "F1C_V10_PROSPECTIVE_CONFIRMATION"
