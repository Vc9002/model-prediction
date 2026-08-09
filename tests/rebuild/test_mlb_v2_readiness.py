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
)
from model_prediction.rebuild.shadow_ledger import ShadowLedger

REPO_ROOT = Path(__file__).resolve().parents[2]
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
        "model_artifact_hash": "bundle-hash",
        "calibration_artifact_hash": "calibration-hash",
    }


def _contract(minimum: int = 1):
    return readiness.ReadinessContract(
        test_start="2026-08-08T02:20:00+00:00",
        test_end=None,
        consumed=False,
        minimum_predictions=minimum,
        candidate_version=MLB_V2_CANDIDATE_VERSION,
    )


def test_zero_committed_predictions_is_not_ready_even_if_games_exist(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    ledger.record_run("mlb", run_type="collection")
    ledger.close()
    assert readiness.count_committed_predictions(tmp_path / "shadow.db", _contract()) == 0


def test_only_exact_on_time_v2_candidate_predictions_count(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow.db")
    run_id = ledger.record_run("mlb", run_type="shadow")
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
            test_id=MLB_V2_TEST_ID,
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
        "minimum_sample_before_evaluation": {"n_prospective_predictions": 100},
        "metrics": {"log_loss": 0.1},
    })
    contract = readiness.load_readiness_contract({"active_tests": {MLB_V2_TEST_ID: raw}})
    assert contract.minimum_predictions == 100
    assert contract.consumed is False


def test_registry_keeps_sealed_test_unconsumed_and_stable():
    registry = json.loads((REPO_ROOT / "outputs/rebuild/test_consumption_registry.json").read_text())
    test = registry["active_tests"][MLB_V2_TEST_ID]
    assert test["test_start"] == "2026-08-08T02:20Z"
    assert test["test_end"] is None
    assert test["consumed"] is False
    assert test["candidate_version"] == MLB_V2_CANDIDATE_VERSION
    assert test["minimum_sample_before_evaluation"]["n_prospective_predictions"] == 100
