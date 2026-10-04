import json
from dataclasses import replace

import pytest

from model_prediction.coded_market_replay import (
    build_snapshot,
    capture_inputs,
    replay_inputs,
    validate_decision_snapshot,
)
from model_prediction.domain import League, MarketType
from model_prediction.learned_replay import snapshot_hash
from model_prediction.ledger import PickLedger
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_ledger import request


@pytest.mark.parametrize("sport", ["SOCCER", "TENNIS"])
def test_exact_state_rounding_identity_and_settled_ledger(sport, tmp_path):
    from model_prediction.models.soccer import MAX_GOALS, SoccerModel
    from model_prediction.models.tennis import TennisModel

    req = request()
    if sport == "SOCCER":
        inputs = capture_inputs(sport, {"home_rate": 1.8734894753, "away_rate": 0.9254867592})
        matrix = SoccerModel().score_matrix(inputs["home_rate"], inputs["away_rate"])
        home = sum(matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(h))
        away = sum(matrix[h][a] for a in range(MAX_GOALS + 1) for h in range(a))
        expected = {"home": round(home, 6), "away": round(away, 6), "draw": round(1 - home - away, 6)}
        version = "soccer-poisson-dc-v1"
    else:
        inputs = capture_inputs(
            sport,
            {
                "players": ["Away", "Home"],
                "surface": "Clay",
                "overall_ratings": [1564.123456, 1678.987654],
                "surface_ratings": [1842.789012, 1601.234567],
                "surface_counts": [13, 79],
            },
        )
        p = TennisModel().match_probability(
            dict(zip(inputs["players"], inputs["overall_ratings"])),
            dict(zip(((p, "Clay") for p in inputs["players"]), inputs["surface_ratings"])),
            dict(zip(((p, "Clay") for p in inputs["players"]), inputs["surface_counts"])),
            "Away",
            "Home",
            "Clay",
        )
        expected = {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}
        version = "tennis-surface-elo-v1"
    assert replay_inputs(inputs) == expected
    artifact = inputs["code_sha256"][f"models/{sport.lower()}.py"]
    contract = {
        "event_id": "event-1",
        "event_start_utc": req.event_start_utc,
        "home_team": "Home",
        "away_team": "Away",
        "market_type": "moneyline",
        "model_version": version,
        "selection": "home",
        "model_probability": expected["home"],
        "feature_basis": {},
        "inference_inputs": inputs,
    }
    snapshot = build_snapshot(contract, artifact, "2026-09-09T12:00:00Z")
    req = replace(
        req,
        league=League(sport),
        market_type=MarketType.MONEYLINE,
        line=None,
        selection="home",
        event_id="event-1",
        home_team="Home",
        away_team="Away",
        model_version=version,
        model_probability=expected["home"],
        model_artifact_hash=artifact,
        model_input_snapshot_json=json.dumps(snapshot),
    )
    assert validate_decision_snapshot(snapshot, req.as_dict()) == expected["home"]
    for change in (
        {"model_probability": 0.1},
        {"event_id": "other"},
        {"home_team": "Away"},
        {"line": 0.5},
        {"model_artifact_hash": "other"},
    ):
        with pytest.raises(ValueError):
            validate_decision_snapshot(snapshot, req.as_dict() | change)
    altered = json.loads(json.dumps(snapshot))
    altered["inference_inputs"]["code_sha256"][f"models/{sport.lower()}.py"] = "other"
    altered["snapshot_hash"] = snapshot_hash(altered)
    with pytest.raises(ValueError):
        validate_decision_snapshot(altered, req.as_dict())
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(
        tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport=sport.lower()
    )
    logged = ledger.append_call(req, 0.25, 70)
    ledger.settle(logged["pick_id"], home_score=2, away_score=0)
    [settled] = store.records(tier="flat", sport=sport.lower())
    assert replay(settled, None)["status"] == "PASS"
    store.close()
