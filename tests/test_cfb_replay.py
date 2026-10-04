from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from model_prediction.cfb_replay import build_snapshot, replay_inputs, validate_decision_snapshot
from model_prediction.domain import League, MarketType
from model_prediction.learned_replay import snapshot_hash
from model_prediction.ledger import PickLedger
from model_prediction.models.college_football import CollegeFootballModel, UpcomingCFBGame
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_ledger import request


def predictions():
    model = CollegeFootballModel()
    game = UpcomingCFBGame(
        event_id="event-1",
        event_start_utc=request().event_start_utc,
        away_team="North Carolina Tar Heels",
        home_team="Duke Blue Devils",
        spread_home_line=-3.0,
        total_line=51.0,
        season_year=2026,
    )
    first = model.predict_matchup([], game)
    second = model.predict_matchup([], game)
    return first, second


def contract(prediction, selection):
    return {
        "event_id": prediction.event_id,
        "event_start_utc": prediction.event_start_utc,
        "model_version": prediction.model_version,
        "market_type": prediction.market_type,
        "inference_inputs": prediction.inference_inputs,
        "feature_basis": prediction.feature_basis,
        "selection": selection,
        "model_probability": prediction.probabilities[selection],
        "line": -prediction.line
        if prediction.market_type == "spread" and selection == "home"
        else prediction.line,
    }


def test_advancing_rng_reproduces_second_game_and_all_three_markets():
    first, second = predictions()
    assert first[0].inference_inputs["rng_state_before"] != second[0].inference_inputs["rng_state_before"]
    for group in (first, second):
        for prediction in group:
            assert (
                replay_inputs(prediction.inference_inputs, prediction.market_type) == prediction.probabilities
            )
    # A seed-only restart produces a different simulation for the second game.
    assert first[0].probabilities != second[0].probabilities


@pytest.mark.parametrize(
    "index,selection", [(0, "home"), (0, "away"), (1, "home"), (1, "away"), (2, "over"), (2, "under")]
)
def test_decision_identity_probability_and_exact_line(index, selection):
    prediction = predictions()[1][index]
    row = contract(prediction, selection)
    p = build_snapshot(row, model_hash="legacy-code-id", observed_at_utc="2026-09-09T12:00:00Z")
    row.update(league="NCAAF", model_artifact_hash="legacy-code-id")
    assert validate_decision_snapshot(p, row) == prediction.probabilities[selection]
    with pytest.raises(ValueError, match="identity"):
        validate_decision_snapshot(p, {**row, "event_id": "another-event"})
    if index:
        with pytest.raises(ValueError, match="line_mismatch"):
            validate_decision_snapshot(p, {**row, "line": row["line"] + 1})
    damaged = copy.deepcopy(p)
    damaged["inference_inputs"]["mu_home"] += 10
    damaged["snapshot_hash"] = snapshot_hash(damaged)
    with pytest.raises(ValueError, match="probability_mismatch"):
        validate_decision_snapshot(damaged, row)


def test_simulator_state_survives_canonical_ledger_settlement(tmp_path):
    prediction = predictions()[1][1]
    raw = contract(prediction, "home")
    snapshot = build_snapshot(raw, model_hash="legacy-code-id", observed_at_utc="2026-09-09T12:00:00Z")
    req = replace(
        request(),
        league=League.NCAAF,
        away_team=prediction.away_team,
        home_team=prediction.home_team,
        event_id=prediction.event_id,
        event_start_utc=prediction.event_start_utc,
        market_type=MarketType.SPREAD,
        selection="home",
        line=raw["line"],
        model_probability=raw["model_probability"],
        model_version=prediction.model_version,
        model_artifact_hash="legacy-code-id",
        model_input_snapshot_json=json.dumps(snapshot),
    )
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="ncaaf")
    logged = ledger.append_call(req, 0.25, 70)
    [record] = store.records(tier="flat", sport="ncaaf")
    assert json.loads(record["feature_payload_json"])["model_input_snapshot"] == snapshot
    assert replay(record, None)["status"] == "PASS"
    ledger.settle(logged["pick_id"], away_score=20, home_score=30)
    [settled] = store.records(tier="flat", sport="ncaaf")
    assert replay(settled, None)["status"] == "PASS"
    store.close()
