from __future__ import annotations

from pathlib import Path

import pytest

from scripts.correct_v9_decision_prices import (
    _correct_model_row,
    _correct_primary_row,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v9_forecaster_pins_all_recorded_picks_to_stale_tolerant_calls() -> None:
    source = (ROOT / "scripts" / "forecast_mlb_v9_benchmark.py").read_text(encoding="utf-8")

    assert "maximum_age=None" in source
    assert 'decision="CALL"' in source
    assert 'reason_code="FLAT_BENCHMARK_TRACK"' in source
    assert "units=1.0" in source


def test_authenticated_known_price_restores_v9_pick_as_one_unit_call() -> None:
    row = {
        "event_id": "event-1",
        "selection": "home",
        "model_probability": "0.61",
        "status": "settled",
        "result": "win",
        "decision": "NO_CALL",
        "record_type": "RESEARCH_OBSERVATION",
        "reason_code": "NO_CALL_MARKET_PRICE_UNAVAILABLE",
        "sportsbook": "market_unavailable",
        "units": "0.00",
        "pnl_units": "",
    }
    evidence = {
        "observed_at_utc": "2026-08-23T19:19:47Z",
        "quote": {"decision_probability": 0.57, "american_odds": -133},
        "snapshot": {
            "snapshot_hash": "hash",
            "snapshot_archive_path": "/archive.jsonl",
            "snapshot_record_id": "hash",
            "raw_response": {},
        },
    }

    corrected = _correct_primary_row(row, evidence)

    assert corrected["status"] == "settled"
    assert corrected["result"] == "win"
    assert corrected["decision"] == "CALL"
    assert corrected["record_type"] == "QUALIFIED_SHADOW_CALL"
    assert corrected["reason_code"] == "FLAT_BENCHMARK_TRACK"
    assert corrected["sportsbook"] == "polymarket_us"
    assert corrected["units"] == "1.00"
    assert float(corrected["market_probability_at_decision"]) == pytest.approx(0.57)
    assert float(corrected["pnl_units"]) == pytest.approx(1 / 0.57 - 1, abs=1e-4)


def test_authenticated_v9_price_recomputes_one_unit_model_pnl() -> None:
    row = {
        "status": "settled",
        "result": "win",
        "model_probability": "0.61",
        "decision_price": "0.5238095238095238",
        "model_market_difference": "0.0862",
        "pnl_units": "0.0000",
        "input_availability": "market_price_unavailable_at_decision",
        "missing_inputs": "decision_price,other_feature",
    }
    evidence = {"quote": {"decision_probability": 0.60}}

    corrected = _correct_model_row(row, evidence)

    assert float(corrected["decision_price"]) == pytest.approx(0.60)
    assert float(corrected["model_market_difference"]) == pytest.approx(0.01)
    assert float(corrected["pnl_units"]) == pytest.approx(2 / 3, abs=1e-4)
    assert corrected["input_availability"] == "partial"
    assert corrected["missing_inputs"] == "other_feature"
