from __future__ import annotations

import pytest

from scripts.correct_v9_decision_prices import (
    _correct_model_row,
    _invalidate_unpriced_model_row,
)


def test_missing_v9_price_invalidates_model_economics_without_erasing_outcome() -> None:
    row = {
        "prediction_id": "missing-price",
        "status": "settled",
        "result": "win",
        "decision_price": "0.5238095238095238",
        "market_no_vig_probability": "0.5238095238095238",
        "model_market_difference": "0.1171",
        "pnl_units": "0.0000",
        "input_availability": "available",
        "missing_inputs": "",
    }

    corrected = _invalidate_unpriced_model_row(row)

    assert corrected["status"] == "settled"
    assert corrected["result"] == "win"
    assert corrected["decision_price"] == ""
    assert corrected["market_no_vig_probability"] == ""
    assert corrected["model_market_difference"] == ""
    assert corrected["pnl_units"] == ""
    assert corrected["input_availability"] == "market_price_unavailable_at_decision"
    assert corrected["missing_inputs"] == "decision_price"


def test_authenticated_v9_price_recomputes_one_unit_model_pnl() -> None:
    row = {
        "status": "settled",
        "result": "win",
        "model_probability": "0.61",
        "decision_price": "0.5238095238095238",
        "model_market_difference": "0.0862",
        "pnl_units": "0.0000",
    }
    evidence = {"quote": {"decision_probability": 0.60}}

    corrected = _correct_model_row(row, evidence)

    assert float(corrected["decision_price"]) == pytest.approx(0.60)
    assert float(corrected["model_market_difference"]) == pytest.approx(0.01)
    assert float(corrected["pnl_units"]) == pytest.approx(2 / 3, abs=1e-4)
