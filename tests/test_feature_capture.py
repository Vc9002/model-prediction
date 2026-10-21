"""feature-capture-v1: recorded inputs for markets with no exact replay adapter."""

from __future__ import annotations

import dataclasses
import json

import pytest

from model_prediction.feature_capture import (
    build_snapshot,
    contract_snapshot_json,
    flatten_features,
    validate_decision_snapshot,
)
from model_prediction.ledger import PickLedger
from tests.test_ledger import request as make_request


def _capture(req, **overrides):
    kwargs = {
        "sport": req.league.value,
        "market_type": req.market_type.value,
        "model_version": req.model_version,
        "event_id": req.event_id,
        "event_start_utc": req.event_start_utc,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "selection": req.selection,
        "line": req.line,
        "probability": req.model_probability,
        "observed_at_utc": "2026-01-01T00:00:00+00:00",
        "features": {"elo_gap": 41.0, "rest_days": [3, 5]},
    }
    return build_snapshot(**(kwargs | overrides))


def test_flatten_features_flattens_one_level_and_drops_none() -> None:
    flat = flatten_features({"a": 1, "b": {"c": 2, "d": None}, "e": None}, {"f": [1, 2]})
    assert flat == {"a": 1, "b.c": 2, "f": [1, 2]}


def test_valid_capture_passes_and_returns_probability() -> None:
    req = make_request()
    snapshot = _capture(req)
    assert snapshot["replay_verified"] is False
    assert validate_decision_snapshot(snapshot, req.as_dict()) == pytest.approx(0.59)


def test_tampered_capture_is_rejected() -> None:
    req = make_request()
    snapshot = _capture(req)
    snapshot["features"]["elo_gap"] = 99.0
    with pytest.raises(ValueError, match="feature_capture_hash_mismatch"):
        validate_decision_snapshot(snapshot, req.as_dict())


def test_identity_or_probability_mismatch_is_rejected() -> None:
    req = make_request()
    other = dataclasses.replace(req, event_id="event-2")
    with pytest.raises(ValueError, match="feature_capture_identity_mismatch"):
        validate_decision_snapshot(_capture(req), other.as_dict())
    with pytest.raises(ValueError, match="feature_capture_probability_mismatch"):
        validate_decision_snapshot(_capture(req, probability=0.5), req.as_dict())
    # A snapshot flagged as not matching the served probability skips that check only.
    inexact = _capture(req, probability=0.5, serving_probability_exact=False)
    assert validate_decision_snapshot(inexact, req.as_dict()) == pytest.approx(0.5)


def test_capture_without_features_is_refused() -> None:
    with pytest.raises(ValueError, match="feature_capture_no_features"):
        _capture(make_request(), features={})


def test_contract_snapshot_json_is_best_effort() -> None:
    contract = {
        "market_type": "total",
        "model_version": "mlb-test-v1",
        "event_id": "event-1",
        "event_start_utc": "2030-01-01T00:00:00Z",
        "home_team": "BOS",
        "away_team": "NYY",
        "feature_basis": {"elo_gap": 3.0},
    }
    kwargs = {
        "sport": "MLB",
        "selection": "over",
        "line": 8.5,
        "probability": 0.59,
        "observed_at_utc": "2026-01-01T00:00:00+00:00",
    }
    assert json.loads(contract_snapshot_json(contract, **kwargs))["features"] == {"elo_gap": 3.0}
    assert contract_snapshot_json({**contract, "feature_basis": {}}, **kwargs) is None
    assert contract_snapshot_json({k: v for k, v in contract.items() if k != "event_id"}, **kwargs) is None


def test_ledger_records_captured_features(tmp_path) -> None:
    req = make_request()
    req = dataclasses.replace(
        req, model_input_snapshot_json=json.dumps(_capture(req), sort_keys=True, allow_nan=False)
    )
    ledger = PickLedger(tmp_path / "picks.xlsx")
    logged = ledger.append_call(req, 0.25, 70)
    payload = PickLedger._feature_payload(
        next(row for row in ledger.rows() if row["pick_id"] == logged["pick_id"])
    )
    assert payload["availability_status"] == "available"
    assert payload["features"]["elo_gap"] == "41.0"
