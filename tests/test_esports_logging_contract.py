from datetime import UTC, datetime

import pytest

from model_prediction.cli.forecast import _log_esports_forecast
from model_prediction.ledger import PickLedger


def contract(market_type, line, selected, other):
    return {
        "event_id": "one",
        "event_start_utc": "2099-01-01T12:00:00Z",
        "market_slug": "fixture",
        "market_type": market_type,
        "line": line,
        "teams": ["Alpha", "Beta"],
        "model_version": f"lol-{market_type}-tiered-elo-v6",
        "artifact_hash": "fixture",
        "gated_research_eligible": False,
        "gated_research_ineligibility_reason": "NO_CALL_DERIVATIVE_UNVALIDATED",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "sides": [
            {"team": selected, "model_probability": 0.65, "executable_ask": 0.5},
            {"team": other, "model_probability": 0.35, "executable_ask": 0.5},
        ],
    }


def log(tmp_path, item):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    forecast = {"title": "lol", "model_version": "lol-tiered-elo-v6", "priced_contracts": [item]}
    config = {
        "models": {"LOL": {"status": "research", "min_edge": 0.02}},
        "project": {},
        "bankroll": {"base_unit_dollars": 10},
    }
    gated = PickLedger(tmp_path / "gated.xlsx", tmp_path / "gated-events.jsonl")
    count = _log_esports_forecast(forecast, config, ledger, gated_ledger=gated)
    assert len(gated.rows()) == count
    if count:
        assert gated.rows()[0]["decision"] == "CALL"
        assert gated.rows()[0]["line"] == ledger.rows()[0]["line"]
        assert gated.rows()[0]["selection"] == ledger.rows()[0]["selection"]
        assert "actionable_inputs_valid=False" in gated.rows()[0]["risks"]
        assert "NO_CALL_DERIVATIVE_UNVALIDATED" in gated.rows()[0]["risks"]
    return count, ledger.rows(), forecast


@pytest.mark.parametrize(
    ("market_type", "line", "selected", "other", "selection", "stored_line"),
    [
        ("total", 3.5, "Over", "Under", "over", 3.5),
        ("total", 4.5, "Under", "Over", "under", 4.5),
        ("spread", -2.5, "Beta +2.5", "Alpha -2.5", "away", 2.5),
        ("spread", 2.5, "Beta -2.5", "Alpha +2.5", "away", -2.5),
    ],
)
def test_logger_preserves_exact_selected_derivative_contract(
    tmp_path, market_type, line, selected, other, selection, stored_line
):
    count, rows, forecast = log(tmp_path, contract(market_type, line, selected, other))
    assert count == 1
    assert forecast["errors"] == []
    assert rows[0]["selection"] == selection
    assert float(rows[0]["line"]) == stored_line
    assert rows[0]["record_type"] == "RESEARCH_OBSERVATION"


@pytest.mark.parametrize(
    ("market_type", "line", "selected", "other"),
    [
        ("total", None, "Over", "Under"),
        ("spread", None, "Alpha -2.5", "Beta +2.5"),
        ("spread", -1.5, "Alpha -2.5", "Beta +2.5"),
        ("spread", -2.5, "Unknown -2.5", "Beta +2.5"),
    ],
)
def test_logger_rejects_missing_or_ambiguous_exact_contract(tmp_path, market_type, line, selected, other):
    count, rows, forecast = log(tmp_path, contract(market_type, line, selected, other))
    assert count == 0
    assert rows == []
    assert len(forecast["errors"]) == 1
