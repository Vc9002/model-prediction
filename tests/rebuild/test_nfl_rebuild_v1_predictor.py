"""Tests for the `nfl-elo-trend-lr-rebuild-v1` challenger predictor
(`rebuild/nfl/rebuild_v1_predictor.py`) — proves the persisted artifact +
calibrator actually load and produce a real prediction for a synthetic
NFL WalkForwardRow, per the task's serving-integration requirement.
This module is deliberately NOT wired into `sport_adapter.py`'s
`rebuild-shadow` registry (see the model card's "Serving integration"
section) — these tests exercise the standalone loader/predictor directly.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from model_prediction.rebuild.nfl.elo import WalkForwardRow
from model_prediction.rebuild.nfl.rebuild_v1_predictor import (
    load_calibrator_artifact,
    load_model_artifact,
    predict_row,
    raw_probability,
)

MODEL_PATH = Path("config/models/challengers/nfl-elo-trend-lr-rebuild-v1.json")
CALIBRATOR_PATH = Path("config/models/challengers/nfl-elo-trend-lr-rebuild-v1-calibrator.json")


def _require_artifacts() -> None:
    if not MODEL_PATH.exists() or not CALIBRATOR_PATH.exists():
        pytest.skip("nfl-elo-trend-lr-rebuild-v1 artifacts not present in this environment")


def _synthetic_row(**overrides: object) -> WalkForwardRow:
    defaults: dict[str, object] = {
        "event_id": "synth-001",
        "season": 2024,
        "season_type": "REG",
        "week": 5,
        "event_start_utc": "2024-10-06T17:00:00+00:00",
        "home_team_id": "KC",
        "away_team_id": "LV",
        "home_score": 28,
        "away_score": 17,
        "home_win": 1,
        "elo_probability": 0.72,
        "trend_gap": 0.15,
        "home_elo": 1580.0,
        "away_elo": 1470.0,
    }
    defaults.update(overrides)
    return WalkForwardRow(**defaults)  # type: ignore[arg-type]


# ── artifact loading ─────────────────────────────────────────────────────────


class TestArtifactLoading:
    def test_artifacts_exist_on_disk(self) -> None:
        _require_artifacts()
        assert MODEL_PATH.exists()
        assert CALIBRATOR_PATH.exists()

    def test_model_artifact_loads_and_has_expected_top_level_keys(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        assert artifact["sport"] == "nfl"
        assert artifact["model_version"] == "nfl-elo-trend-lr-rebuild-v1"
        assert artifact["method"] == "logistic_regression"
        assert "market_models" in artifact
        assert "moneyline" in artifact["market_models"]
        assert "artifact_hash" in artifact

    def test_model_artifact_has_two_feature_names(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        features = artifact["market_models"]["moneyline"]["feature_names"]
        assert features == ["elo_probability", "trend_gap"]

    def test_model_artifact_has_two_coefficients(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        coeffs = artifact["market_models"]["moneyline"]["coefficients"]
        assert len(coeffs) == 2

    def test_calibrator_artifact_loads(self) -> None:
        _require_artifacts()
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        assert calibrator is not None
        assert calibrator.method in ("identity", "platt", "temperature", "isotonic")

    def test_calibrator_references_base_model_hash(self) -> None:
        _require_artifacts()
        import json

        model = load_model_artifact(MODEL_PATH)
        cal_payload = json.loads(CALIBRATOR_PATH.read_text())
        assert cal_payload["base_model_hash"] == model["artifact_hash"]


class TestCaveats:
    def test_production_not_allowed(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        assert artifact["provenance"]["production_allowed"] is False

    def test_not_qualified(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        assert artifact["qualification"]["qualified"] is False

    def test_capture_time_only_provenance(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        provenance = artifact["provenance"]
        assert "capture_time_only" in provenance["availability_basis"]

    def test_sibling_of_incumbent_declared(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        assert "nfl-elo-trend-lr-v4" in artifact["provenance"]["sibling_of_incumbent"]

    def test_calibrator_production_not_allowed(self) -> None:
        _require_artifacts()
        import json

        cal_payload = json.loads(CALIBRATOR_PATH.read_text())
        assert cal_payload["provenance"]["production_allowed"] is False

    def test_epa_cpoe_blocked_in_note(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        note = artifact["provenance"]["note"]
        assert "EPA" in note or "CPOE" in note


# ── prediction ───────────────────────────────────────────────────────────────


class TestRawProbability:
    def test_raw_probability_is_between_zero_and_one(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        row = _synthetic_row()
        p = raw_probability(artifact, row)
        assert 0.0 < p < 1.0

    def test_raw_probability_deterministic(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        row = _synthetic_row()
        assert raw_probability(artifact, row) == raw_probability(artifact, row)

    def test_raw_probability_matches_hand_computed_forward_pass(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        market = artifact["market_models"]["moneyline"]

        row = _synthetic_row(elo_probability=0.65, trend_gap=0.15)
        values = {"elo_probability": 0.65, "trend_gap": 0.15}
        expected_z = market["intercept"] + sum(
            coef * values[name]
            for coef, name in zip(market["coefficients"], market["feature_names"], strict=True)
        )
        expected_p = 1.0 / (1.0 + math.exp(-expected_z))

        assert raw_probability(artifact, row) == pytest.approx(expected_p)

    def test_raw_probability_increases_with_elo_probability(self) -> None:
        """Higher elo_probability feature → higher raw probability."""
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        row_lo = _synthetic_row(elo_probability=0.45, trend_gap=0.0)
        row_hi = _synthetic_row(elo_probability=0.75, trend_gap=0.0)
        assert raw_probability(artifact, row_lo) < raw_probability(artifact, row_hi)


class TestPredictRow:
    def test_predict_row_returns_expected_keys(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        row = _synthetic_row()
        result = predict_row(artifact, calibrator, row)
        assert set(result.keys()) == {
            "event_id",
            "event_start_utc",
            "home_team_id",
            "away_team_id",
            "home_win_probability_raw",
            "home_win_probability_calibrated",
            "model_version",
            "calibration_method",
        }

    def test_predict_row_is_deterministic(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        row = _synthetic_row()
        p1 = predict_row(artifact, calibrator, row)
        p2 = predict_row(artifact, calibrator, row)
        assert p1 == p2

    def test_predict_row_event_id_matches_input(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        row = _synthetic_row(event_id="custom-event-42")
        result = predict_row(artifact, calibrator, row)
        assert result["event_id"] == "custom-event-42"

    def test_predict_row_model_version_correct(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        row = _synthetic_row()
        result = predict_row(artifact, calibrator, row)
        assert result["model_version"] == "nfl-elo-trend-lr-rebuild-v1"

    def test_calibrated_probability_in_range(self) -> None:
        _require_artifacts()
        artifact = load_model_artifact(MODEL_PATH)
        calibrator = load_calibrator_artifact(CALIBRATOR_PATH)
        row = _synthetic_row()
        result = predict_row(artifact, calibrator, row)
        assert 0.0 < result["home_win_probability_calibrated"] < 1.0
