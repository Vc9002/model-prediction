import json
from dataclasses import replace

import pytest

from model_prediction.domain import League, MarketType
from model_prediction.international_baseball import HomeElo, _game_tie_probability, tie_aware_fair_values
from model_prediction.international_replay import (
    build_snapshot,
    capture_inputs,
    replay_inputs,
    validate_decision_snapshot,
)
from model_prediction.ledger import PickLedger
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_ledger import request


@pytest.mark.parametrize("league", ["KBO", "NPB"])
@pytest.mark.parametrize("method", ["flat", "elo_gap"])
def test_exact_tie_values_and_win_probabilities_remain_distinct(league, method, tmp_path):
    book = HomeElo(20, 20, {"a": 1500.123456, "h": 1620.123456})
    inputs = capture_inputs(book, "a", "h", method, 0.05)
    tie = _game_tie_probability("a", "h", book, method, 0.05)
    away, home = tie_aware_fair_values(book.decisive_home_probability("a", "h"), tie)
    values = replay_inputs(inputs)
    assert values["settlement_values"] == {"away": round(away, 6), "home": round(home, 6)}
    assert sum(values["outcome_probabilities"].values()) == pytest.approx(1)
    assert values["outcome_probabilities"]["home"] < values["settlement_values"]["home"]
    original = request()
    contract = {
        "league": league,
        "model_version": league.lower() + "-tie-aware-elo-v2",
        "artifact_hash": "artifact",
        "event_id": "event-1",
        "event_start_utc": original.event_start_utc,
        "home_team": "Home",
        "away_team": "Away",
        "inference_inputs": inputs,
        "sides": [
            {"team": "Home", "model_fair_settlement_value": round(home, 6)},
            {"team": "Away", "model_fair_settlement_value": round(away, 6)},
        ],
    }
    snapshot = build_snapshot(contract, "2026-09-09T12:00:00Z")
    req = replace(
        original,
        league=League(league),
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        event_id="event-1",
        home_team="Home",
        away_team="Away",
        model_probability=round(home, 6),
        model_version=contract["model_version"],
        model_artifact_hash="artifact",
        model_input_snapshot_json=json.dumps(snapshot),
    )
    assert validate_decision_snapshot(snapshot, req.as_dict()) == round(home, 6)
    with pytest.raises(ValueError, match="identity"):
        validate_decision_snapshot(snapshot, req.as_dict() | {"home_team": "Away"})
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(
        tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport=league.lower()
    )
    logged = ledger.append_call(req, 0.25, 70)
    ledger.settle(logged["pick_id"], away_score=1, home_score=1, binary_contract_settlement_value=0.5)
    [settled] = store.records(tier="flat", sport=league.lower())
    assert replay(settled, None)["status"] == "PASS"
    assert json.loads(settled["feature_payload_json"])["model_input_snapshot"] == snapshot
    store.close()
