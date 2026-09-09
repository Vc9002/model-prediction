import json

import pytest

from scripts import mlb_v10_daily_operational_audit as audit


@pytest.fixture
def paths(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        json.dumps({"hashes": {"v10_model_spec_hash": "spec", "v10_probability_model_hash": "prob"}})
    )
    monkeypatch.setattr(audit, "AUDIT_OUTPUT_PATH", tmp_path / "audit.json")
    return tmp_path / "ledger.jsonl", artifact


@pytest.mark.parametrize("content", [None, "", "\n"])
def test_absent_predictions_are_not_healthy(paths, content):
    ledger, artifact = paths
    if content is not None:
        ledger.write_text(content)
    report = audit.run_daily_operational_audit(ledger, artifact)
    assert report.operational_status == "NO_DATA"
    assert report.reasons


@pytest.mark.parametrize("content", ["{broken\n", "[]\n", "null\n", '{"record_type":"UNKNOWN"}\n'])
def test_invalid_records_fail_closed(paths, content):
    ledger, artifact = paths
    ledger.write_text(content)
    report = audit.run_daily_operational_audit(ledger, artifact)
    assert report.operational_status == "FAIL_INTEGRITY"


def test_orphaned_settlement_fails_integrity(paths):
    ledger, artifact = paths
    ledger.write_text(json.dumps({"record_type": "SETTLEMENT", "prediction_hash": "missing"}) + "\n")
    report = audit.run_daily_operational_audit(ledger, artifact)
    assert report.orphaned_settlements_count == 1
    assert report.operational_status == "FAIL_INTEGRITY"


def test_valid_prediction_remains_healthy(paths):
    ledger, artifact = paths
    ledger.write_text(
        json.dumps(
            {
                "record_type": "PREDICTION",
                "event_id": "game",
                "prediction_hash": "prediction",
                "model_spec_hash": "spec",
                "probability_model_hash": "prob",
                "feature_snapshot_hash": "features",
                "game_start_utc": "2026-09-08T20:00:00+00:00",
                "decision_utc": "2026-09-08T19:30:00+00:00",
                "created_at_utc": "2026-09-08T19:30:00+00:00",
            }
        )
        + "\n"
    )
    report = audit.run_daily_operational_audit(ledger, artifact)
    assert report.operational_status == "PASS"
