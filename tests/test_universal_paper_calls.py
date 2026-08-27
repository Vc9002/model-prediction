from __future__ import annotations

import pytest

from scripts.enforce_universal_paper_calls import normalize_model_row, normalize_pick_row


def test_pick_normalizer_hard_codes_call_units_and_settled_pnl() -> None:
    row = {
        "pick_id": "pick-1",
        "status": "settled",
        "result": "win",
        "decision": "NO_CALL",
        "reason_code": "NO_CALL_MODEL_UNVALIDATED",
        "record_type": "RESEARCH_OBSERVATION",
        "call_type": "research_observation",
        "units": "0.00",
        "decision_decimal_odds": "2.500000",
        "pnl_units": "",
    }

    corrected, unresolved = normalize_pick_row(row)

    assert unresolved == []
    assert corrected["decision"] == "CALL"
    assert corrected["reason_code"] == "PAPER_CALL_MODEL_UNVALIDATED"
    assert corrected["record_type"] == "RESEARCH_OBSERVATION"
    assert corrected["call_type"] == "paper_call"
    assert float(corrected["units"]) == 1.0
    assert float(corrected["pnl_units"]) == pytest.approx(1.5)


def test_model_normalizer_records_operator_call_and_reconstructs_missing_pnl() -> None:
    row = {
        "prediction_id": "prediction-1",
        "status": "settled",
        "result": "loss",
        "decision_price": "0.40",
        "pnl_units": "",
        "operator_decision": "",
        "operator_units": "",
        "operator_timestamp": "",
        "operator_note": "",
    }

    corrected, unresolved = normalize_model_row(row, "2026-08-25T00:00:00Z")

    assert unresolved == []
    assert corrected["operator_decision"] == "CALL"
    assert float(corrected["operator_units"]) == 1.0
    assert float(corrected["pnl_units"]) == -1.0


def test_open_pick_has_call_and_units_but_pnl_remains_pending() -> None:
    row = {
        "pick_id": "pick-open",
        "status": "open",
        "result": "",
        "decision": "NO_CALL",
        "reason_code": "NO_CALL_LOW_EDGE",
        "record_type": "RESEARCH_OBSERVATION",
        "units": "0",
        "pnl_units": "0",
    }

    corrected, unresolved = normalize_pick_row(row)

    assert unresolved == []
    assert corrected["decision"] == "CALL"
    assert float(corrected["units"]) == 1.0
    assert corrected["pnl_units"] == ""


def test_open_model_prediction_has_call_and_units_but_pnl_remains_pending() -> None:
    row = {
        "prediction_id": "prediction-open",
        "status": "open",
        "result": "",
        "operator_decision": "",
        "operator_units": "",
        "operator_timestamp": "",
        "operator_note": "",
        "pnl_units": "0",
    }

    corrected, unresolved = normalize_model_row(row, "2026-08-25T00:00:00Z")

    assert unresolved == []
    assert corrected["operator_decision"] == "CALL"
    assert float(corrected["operator_units"]) == 1.0
    assert corrected["pnl_units"] == ""
