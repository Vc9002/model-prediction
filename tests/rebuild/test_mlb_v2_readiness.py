"""Outcome-blind readiness and prospective timestamp regression tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest

from model_prediction.rebuild.mlb_v2_artifact import (
    MLB_V2_CANDIDATE_VERSION,
    MLB_V2_TEST_ID,
    FrozenMLBV2Anchor,
)
from model_prediction.rebuild.shadow_ledger import ShadowLedger

REPO_ROOT = Path(__file__).resolve().parents[2]
ANCHOR = FrozenMLBV2Anchor(
    status="sealed",
    bundle_manifest_sha256="1" * 64,
    bundle_hash="2" * 64,
    primary_content_sha256="3" * 64,
    primary_artifact_sha256="4" * 64,
    calibrator_artifact_sha256="5" * 64,
    calibrator_hash="6" * 64,
    source_tree_sha256="7" * 64,
)


def _anchor_dict() -> dict:
    return dict(ANCHOR.__dict__)
SPEC = importlib.util.spec_from_file_location(
    "check_mlb_v2_readiness",
    REPO_ROOT / "scripts" / "check_mlb_v2_readiness.py",
)
assert SPEC is not None and SPEC.loader is not None
readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness
SPEC.loader.exec_module(readiness)


def _forecast(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "predicted_winner": "home",
        "raw_probabilities": {"home": 0.55, "away": 0.45},
        "calibrated_probabilities": {"home": 0.54, "away": 0.46},
        "probability_lower": {"home": 0.50, "away": 0.42},
        "probability_upper": {"home": 0.58, "away": 0.50},
        "expected_home_score": 4.5,
        "expected_away_score": 4.0,
        "model_artifact_hash": ANCHOR.bundle_hash,
        "calibration_artifact_hash": ANCHOR.calibrator_hash,
    }


def _contract(minimum: int = 1):
    return readiness.ReadinessContract(
        test_start="2026-08-08T02:20:00+00:00",
        test_end=None,
        consumed=False,
        minimum_predictions=minimum,
        minimum_real_games=minimum,
        candidate_version=MLB_V2_CANDIDATE_VERSION,
        horizon="late",
        anchor=ANCHOR,
    )


def _record_anchor_lineage(ledger: ShadowLedger, run_id: str) -> None:
    ledger.record_model_artifact(
        run_id=run_id,
        sport="mlb",
        model_name=MLB_V2_TEST_ID,
        model_version=MLB_V2_CANDIDATE_VERSION,
        artifact_hash=ANCHOR.bundle_hash,
        dataset_hash="8" * 64,
        code_revision="9" * 40,
        dependency_lock_hash="a" * 64,
        artifact_path="config/models/challengers/mlb_moneyline_v2_frozen_v1",
        manifest_sha256=ANCHOR.bundle_manifest_sha256,
        primary_content_sha256=ANCHOR.primary_content_sha256,
        primary_artifact_sha256=ANCHOR.primary_artifact_sha256,
        calibrator_artifact_sha256=ANCHOR.calibrator_artifact_sha256,
        source_tree_sha256=ANCHOR.source_tree_sha256,
    )
    ledger.record_calibration_artifact(
        run_id=run_id,
        sport="mlb",
        model_artifact_hash=ANCHOR.bundle_hash,
        calibration_hash=ANCHOR.calibrator_hash,
        method="temperature",
        fitted_on_hash="8" * 64,
    )


def test_zero_committed_predictions_is_not_ready_even_if_games_exist(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    ledger.record_run("mlb", run_type="collection")
    ledger.close()
    assert readiness.count_committed_predictions(tmp_path / "shadow.db", _contract()) == 0


def test_only_exact_on_time_v2_candidate_predictions_count(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    run_id = ledger.record_run("mlb", run_type="shadow")
    _record_anchor_lineage(ledger, run_id)
    with patch("model_prediction.rebuild.shadow_ledger.utc_now", return_value="2026-08-09T21:01:00+00:00"):
        ledger.record_prediction(
            run_id=run_id,
            sport="mlb",
            event_id="401",
            horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc="2026-08-09T21:00:00+00:00",
            test_id=MLB_V2_TEST_ID,
            candidate_version=MLB_V2_CANDIDATE_VERSION,
            forecast=_forecast("401"),
        )
        ledger.record_prediction(
            run_id=run_id,
            sport="mlb",
            event_id="402",
            horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc="2026-08-09T21:00:00+00:00",
            test_id="other_test",
            candidate_version="different-candidate",
            forecast={**_forecast("402"), "model_artifact_hash": "other"},
        )
    ledger.close()
    assert readiness.count_committed_predictions(tmp_path / "shadow.db", _contract()) == 1


@pytest.mark.parametrize(
    ("observed", "created", "message"),
    [
        ("2026-08-09T21:11:00+00:00", "2026-08-09T21:01:00+00:00", "observation"),
        ("2026-08-09T21:00:00+00:00", "2026-08-09T21:11:00+00:00", "creation"),
    ],
)
def test_late_manual_prospective_insert_fails_closed(tmp_path, observed, created, message):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    run_id = ledger.record_run("mlb", run_type="manual")
    with patch("model_prediction.rebuild.shadow_ledger.utc_now", return_value=created), pytest.raises(
        ValueError, match=message,
    ):
        ledger.record_prediction(
            run_id=run_id,
            sport="mlb",
            event_id="401",
            horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc=observed,
            test_id=MLB_V2_TEST_ID,
            candidate_version=MLB_V2_CANDIDATE_VERSION,
            forecast=_forecast("401"),
        )
    ledger.close()


class _SealedTest(dict):
    """Explodes if readiness tries to inspect a sealed performance field."""

    _forbidden: ClassVar[set[str]] = {
        "accuracy", "log_loss", "brier", "roi", "clv", "metrics", "outcomes",
    }

    def get(self, key, default=None):
        if key in self._forbidden:
            raise AssertionError(f"sealed metric read: {key}")
        return super().get(key, default)


def test_readiness_contract_never_reads_sealed_metrics():
    raw = _SealedTest({
        "test_start": "2026-08-08T02:20:00+00:00",
        "test_end": None,
        "consumed": False,
        "candidate_version": MLB_V2_CANDIDATE_VERSION,
        "minimum_sample_before_evaluation": {
            "n_prospective_predictions": 100,
            "n_real_games": 100,
        },
        "frozen_artifact_anchor": _anchor_dict(),
        "metrics": {"log_loss": 0.1},
    })
    contract = readiness.load_readiness_contract({"active_tests": {MLB_V2_TEST_ID: raw}})
    assert contract.minimum_predictions == 100
    assert contract.consumed is False


def test_registry_keeps_test_unconsumed_but_fails_closed_until_bundle_is_sealed():
    registry = json.loads((REPO_ROOT / "outputs/rebuild/test_consumption_registry.json").read_text())
    test = registry["active_tests"][MLB_V2_TEST_ID]
    assert test["test_start"] == "2026-08-08T02:20Z"
    assert test["test_end"] is None
    assert test["consumed"] is False
    assert test["candidate_version"] == MLB_V2_CANDIDATE_VERSION
    assert test["minimum_sample_before_evaluation"]["n_prospective_predictions"] == 100
    assert test["frozen_artifact_anchor"]["status"] == "sealing_required"
    with pytest.raises(ValueError, match="not sealed"):
        readiness.load_readiness_contract(registry)


def test_arbitrary_prediction_hashes_do_not_enter_readiness_cohort(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    run_id = ledger.record_run("mlb", run_type="synthetic")
    forged = {
        **_forecast("forged"),
        "model_artifact_hash": "b" * 64,
        "calibration_artifact_hash": "c" * 64,
    }
    with patch("model_prediction.rebuild.shadow_ledger.utc_now", return_value="2026-08-09T21:01:00+00:00"):
        ledger.record_prediction(
            run_id=run_id,
            sport="mlb",
            event_id="forged",
            horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc="2026-08-09T21:00:00+00:00",
            test_id=MLB_V2_TEST_ID,
            candidate_version=MLB_V2_CANDIDATE_VERSION,
            forecast=forged,
        )
    ledger.close()
    assert readiness.count_committed_predictions(tmp_path / "shadow.db", _contract()) == 0


def test_superseded_original_is_not_counted_when_latest_correction_leaves_anchor(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    run_id = ledger.record_run("mlb", run_type="synthetic")
    _record_anchor_lineage(ledger, run_id)
    with patch("model_prediction.rebuild.shadow_ledger.utc_now", return_value="2026-08-09T21:01:00+00:00"):
        original_id, _ = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id="401", horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc="2026-08-09T21:00:00+00:00",
            test_id=MLB_V2_TEST_ID, candidate_version=MLB_V2_CANDIDATE_VERSION,
            forecast=_forecast("401"),
        )
        ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id="401", horizon="late",
            decision_time_utc="2026-08-09T21:10:00+00:00",
            prediction_observed_at_utc="2026-08-09T21:00:00+00:00",
            test_id=MLB_V2_TEST_ID, candidate_version=MLB_V2_CANDIDATE_VERSION,
            forecast={**_forecast("401"), "model_artifact_hash": "f" * 64},
            supersedes_id=original_id,
        )
    ledger.close()
    assert readiness.count_committed_predictions(tmp_path / "shadow.db", _contract()) == 0
