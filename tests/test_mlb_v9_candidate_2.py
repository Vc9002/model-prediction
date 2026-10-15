"""Unit tests for MLB v9 Candidate 2 Rebuilt Artifact Integrity."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from model_prediction.champion_challenger import _compute_artifact_hash

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "config/models/research/mlb-v9-candidate-2.json"


def test_v9_candidate_2_artifact_exists_and_hashes():
    assert ARTIFACT_PATH.is_file(), f"Artifact not found at {ARTIFACT_PATH}"
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))

    assert data["model_id"] == "mlb-v9-candidate-2"
    assert data["league"] == "MLB"
    assert data["status"] == "RESEARCH_CANDIDATE"

    # Self-hash verification
    stored_hash = data.get("artifact_hash")
    assert stored_hash is not None and len(stored_hash) == 64
    computed_hash = _compute_artifact_hash(data)
    assert stored_hash == computed_hash, "Artifact failed self-hash verification"


def test_v9_candidate_2_feature_and_parameter_dimensions():
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    features = data["feature_names"]
    n_features = len(features)
    assert n_features == 14

    assert len(data["scaler_mean"]) == n_features
    assert len(data["scaler_std"]) == n_features
    assert len(data["coefficients"]) == n_features
    assert isinstance(data["intercept"], float)

    # Calibration parameters present
    cal = data["calibration"]
    assert cal["method"] == "platt"
    assert isinstance(cal["calibration_slope"], float)
    assert isinstance(cal["calibration_intercept"], float)
    assert cal["expected_calibration_error"] < 0.05


def test_v9_candidate_2_deterministic_prediction():
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    means = np.array(data["scaler_mean"])
    stds = np.array(data["scaler_std"])
    coefs = np.array(data["coefficients"])
    intercept = float(data["intercept"])
    cal_slope = float(data["calibration"]["calibration_slope"])
    cal_intercept = float(data["calibration"]["calibration_intercept"])

    # Neutral feature vector (equal to mean -> standardized to 0.0)
    x_neutral = means
    x_scaled = (x_neutral - means) / stds
    assert np.allclose(x_scaled, 0.0)

    raw_logit = float(np.dot(x_scaled, coefs) + intercept)
    raw_prob = 1.0 / (1.0 + np.exp(-raw_logit))

    # Home field advantage reflected
    assert raw_prob > 0.50

    # Platt calibration applied
    cal_logit = cal_slope * np.log(raw_prob / (1.0 - raw_prob)) + cal_intercept
    cal_prob = 1.0 / (1.0 + np.exp(-cal_logit))
    assert 0.50 < cal_prob < 0.60
