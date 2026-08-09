"""Regression tests for the immutable MLB v2 prospective candidate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from model_prediction.rebuild import mlb_shadow_pipeline as pipeline
from model_prediction.rebuild.mlb_v2_artifact import (
    MLB_V2_CANDIDATE_VERSION,
    load_frozen_mlb_v2_bundle,
)
from tests.rebuild.mlb_v2_helpers import TEST_CODE_REVISION, build_test_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sealed_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp("mlb-v2-bundle")
    target, row = build_test_bundle(root / "challengers", REPO_ROOT)
    return root, target, row


def _load(root: Path):
    return load_frozen_mlb_v2_bundle(
        root / "challengers",
        repo_root=REPO_ROOT,
        expected_code_revision=TEST_CODE_REVISION,
    )


def _forecast(bundle, row):
    calibration = bundle.calibrator
    return pipeline.build_forecast(
        bundle.primary,
        row,
        bootstrap=bundle.bootstrap,
        calibrator=calibration.calibrator,
        calibrator_hash=calibration.calibrator_hash,
        sklearn_baseline=bundle.sklearn_baseline,
        xgb_direct=bundle.xgb_direct,
        calibration_oof_probs=calibration.oof_probs,
        calibration_oof_labels=calibration.oof_labels,
        model_artifact_hash=bundle.bundle_hash,
    )


def _forecast_projection(forecast) -> dict:
    return {
        "raw": forecast.raw_probabilities,
        "calibrated": forecast.calibrated_probabilities,
        "winner": forecast.predicted_winner,
        "lower": forecast.probability_lower,
        "disagreement": forecast.model_disagreement,
        "conservative": forecast.conservative_probabilities,
        "artifact_hash": forecast.model_artifact_hash,
    }


def test_fresh_process_load_is_prediction_equivalent(sealed_bundle):
    root, _, row = sealed_bundle
    expected = _forecast_projection(_forecast(_load(root), row))
    code = """
import json
from pathlib import Path
from model_prediction.rebuild.mlb_v2_artifact import load_frozen_mlb_v2_bundle
from model_prediction.rebuild import mlb_shadow_pipeline as pipeline

root = Path(__import__('sys').argv[1])
repo = Path(__import__('sys').argv[2])
revision = __import__('sys').argv[3]
row = json.loads(__import__('sys').argv[4])
bundle = load_frozen_mlb_v2_bundle(root / 'challengers', repo_root=repo, expected_code_revision=revision)
c = bundle.calibrator
f = pipeline.build_forecast(
    bundle.primary, row, bootstrap=bundle.bootstrap,
    calibrator=c.calibrator, calibrator_hash=c.calibrator_hash,
    sklearn_baseline=bundle.sklearn_baseline, xgb_direct=bundle.xgb_direct,
    calibration_oof_probs=c.oof_probs, calibration_oof_labels=c.oof_labels,
    model_artifact_hash=bundle.bundle_hash,
)
print(json.dumps({
    'raw': f.raw_probabilities, 'calibrated': f.calibrated_probabilities,
    'winner': f.predicted_winner, 'lower': f.probability_lower,
    'disagreement': f.model_disagreement,
    'conservative': f.conservative_probabilities,
    'artifact_hash': f.model_artifact_hash,
}, sort_keys=True))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(REPO_ROOT), TEST_CODE_REVISION, json.dumps(row)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = json.loads(result.stdout.strip().splitlines()[-1])
    assert actual == expected


def test_component_byte_tamper_fails_before_deserialization(sealed_bundle):
    root, target, _ = sealed_bundle
    model_path = target / "primary" / "model.joblib"
    original = model_path.read_bytes()
    try:
        model_path.write_bytes(original + b"tamper")
        with pytest.raises(ValueError, match="component hash mismatch"):
            _load(root)
    finally:
        model_path.write_bytes(original)


def test_calibrator_must_be_semantically_bound_to_primary_model(sealed_bundle):
    root, target, _ = sealed_bundle
    calibrator_path = target / "calibrator.json"
    manifest_path = target / "manifest.json"
    original_calibrator = calibrator_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    try:
        calibrator = json.loads(original_calibrator)
        calibrator["base_model_hash"] = "wrong-primary-schema-hash"
        identity = {
            key: value for key, value in calibrator.items()
            if key not in {"calibrator_hash", "oof_probs", "oof_labels"}
        }
        calibrator["calibrator_hash"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=str).encode()
        ).hexdigest()
        calibrator_path.write_text(json.dumps(calibrator))

        manifest = json.loads(original_manifest)
        receipt = manifest["components"]["calibrator"]
        receipt["artifact_sha256"] = hashlib.sha256(calibrator_path.read_bytes()).hexdigest()
        receipt["calibrator_hash"] = calibrator["calibrator_hash"]
        receipt["base_model_hash"] = calibrator["base_model_hash"]
        manifest_identity = {key: value for key, value in manifest.items() if key != "bundle_hash"}
        manifest["bundle_hash"] = hashlib.sha256(
            json.dumps(manifest_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="not bound to the primary model"):
            _load(root)
    finally:
        calibrator_path.write_bytes(original_calibrator)
        manifest_path.write_bytes(original_manifest)


def test_missing_bundle_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_frozen_mlb_v2_bundle(
            tmp_path,
            repo_root=REPO_ROOT,
            expected_code_revision=TEST_CODE_REVISION,
        )


def test_runtime_predict_never_retrains_and_rejects_late_rows(sealed_bundle, tmp_path):
    root, _, _ = sealed_bundle
    frozen = _load(root)
    tonight = pl.DataFrame({
        "event_id": ["401"],
        "event_start_utc": ["2099-01-02T22:10:00+00:00"],
        "home_team": ["Seattle Mariners"],
        "away_team": ["Detroit Tigers"],
    })
    state = pipeline.MLBRunState(
        target_date="2099-01-02",
        tonight=tonight,
        decision_times={"401": datetime(2099, 1, 2, 21, 10, tzinfo=UTC)},
    )
    with patch.object(pipeline, "load_frozen_mlb_v2_bundle", return_value=frozen), patch.object(
        pipeline, "train_through", side_effect=AssertionError("runtime retraining is forbidden")
    ), patch.object(
        pipeline, "point_in_time_probable_starters",
        return_value={"401": {"home_starter": "A", "away_starter": "B"}},
    ), patch.object(
        pipeline, "build_live_game_feature_row", return_value={"event_id": "401"}
    ), patch.object(
        pipeline, "_utc_now_dt", return_value=datetime(2099, 1, 2, 21, 0, tzinfo=UTC)
    ):
        on_time = pipeline.predict_stage(state, str(tmp_path))
    assert on_time["games_predicted"] == 1
    assert state.frozen_bundle_hash == frozen.bundle_hash

    late_state = pipeline.MLBRunState(
        target_date="2099-01-02",
        tonight=tonight,
        decision_times={"401": datetime(2099, 1, 2, 21, 10, tzinfo=UTC)},
    )
    with patch.object(pipeline, "load_frozen_mlb_v2_bundle", return_value=frozen), patch.object(
        pipeline, "train_through", side_effect=AssertionError("runtime retraining is forbidden")
    ), patch.object(
        pipeline, "_utc_now_dt", return_value=datetime(2099, 1, 2, 21, 11, tzinfo=UTC)
    ):
        late = pipeline.predict_stage(late_state, str(tmp_path))
    assert late["games_predicted"] == 0
    assert late["skipped"] == {"401": "prediction_cutoff_passed"}


def test_resume_reloads_identical_full_uncertainty_bundle(sealed_bundle, tmp_path):
    root, _, row = sealed_bundle
    initial_bundle = _load(root)
    state = pipeline.MLBRunState(
        target_date="2099-01-02",
        tonight=pl.DataFrame({"event_id": ["401"]}),
        decision_times={"401": datetime(2099, 1, 2, 21, 10, tzinfo=UTC)},
        rows_by_event={"401": row},
        prediction_observed_at_by_event={"401": "2099-01-02T21:00:00+00:00"},
    )
    pipeline._apply_frozen_bundle(state, initial_bundle)
    pipeline.save_resume_state(state, str(tmp_path), "run-1")

    reconstructed = pipeline.MLBRunState(
        target_date=state.target_date,
        tonight=state.tonight,
        decision_times=state.decision_times,
    )
    with patch.object(pipeline, "load_state", return_value=reconstructed):
        resumed = pipeline.load_resume_state(
            str(tmp_path),
            "run-1",
            state.target_date,
            challenger_root=root / "challengers",
            repo_root=REPO_ROOT,
            expected_code_revision=TEST_CODE_REVISION,
        )
    assert resumed is not None
    assert resumed.bootstrap is not None
    assert resumed.sklearn_baseline is not None
    assert resumed.xgb_direct is not None
    assert resumed.calibrator_bundle is not None
    assert resumed.frozen_bundle_hash == initial_bundle.bundle_hash

    fresh = _forecast(_load(root), row)
    resumed_forecast = pipeline.build_forecast(
        resumed.model,
        resumed.rows_by_event["401"],
        bootstrap=resumed.bootstrap,
        calibrator=resumed.calibrator_bundle.calibrator,
        calibrator_hash=resumed.calibrator_bundle.calibrator_hash,
        sklearn_baseline=resumed.sklearn_baseline,
        xgb_direct=resumed.xgb_direct,
        calibration_oof_probs=resumed.calibrator_bundle.oof_probs,
        calibration_oof_labels=resumed.calibrator_bundle.oof_labels,
        model_artifact_hash=resumed.frozen_bundle_hash,
    )
    assert _forecast_projection(resumed_forecast) == _forecast_projection(fresh)
    assert resumed.candidate_version == MLB_V2_CANDIDATE_VERSION
