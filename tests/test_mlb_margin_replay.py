import json
from dataclasses import replace
from pathlib import Path

import pytest

from model_prediction.domain import League, MarketType
from model_prediction.ledger import PickLedger
from model_prediction.mlb_margin_replay import build_snapshot, replay_snapshot, validate_decision_snapshot
from model_prediction.models.mlb import MeasuredEdgeMarginModel
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_ledger import request
from tests.test_mlb_distribution_methods import _features, _spec


@pytest.mark.parametrize("line", [-1.5, 1.0])
def test_exact_spread_simulation_and_settled_replay(line, tmp_path):
    spec = _spec(tmp_path)
    req = request()
    features = replace(_features(), event_start_utc=req.event_start_utc)
    artifact_path = Path(__file__).parents[1] / "config/models/measured-edge-margin-v3.json"
    model = MeasuredEdgeMarginModel(artifact_path, spec)
    output = model.predict(features, line)
    snapshot = build_snapshot(features, spec, model.raw, line, output.spread, "2026-09-09T12:00:00Z")
    values = replay_snapshot(snapshot)
    for side in ("away", "home"):
        assert values["served_probabilities"][side] == round(
            model.calibrate_selected_side(values["raw_probabilities"][side]), 6
        )
    assert sum(values["raw_probabilities"].values()) == pytest.approx(1)
    req = replace(
        req,
        league=League.MLB,
        market_type=MarketType.SPREAD,
        selection="home",
        line=-line,
        event_id=features.event_id,
        home_team="Home",
        away_team="Away",
        model_version=model.raw["model_version"],
        model_artifact_hash=model.raw["artifact_hash"],
        model_probability=values["served_probabilities"]["home"],
        model_input_snapshot_json=json.dumps(snapshot),
    )
    assert validate_decision_snapshot(snapshot, req.as_dict()) == req.model_probability
    with pytest.raises(ValueError, match="line"):
        validate_decision_snapshot(snapshot, req.as_dict() | {"line": line})
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="mlb")
    logged = ledger.append_call(req, 0.25, 70)
    ledger.settle(logged["pick_id"], away_score=1, home_score=2)
    [row] = store.records(tier="flat", sport="mlb")
    assert replay(row, None)["status"] == "PASS"
    store.close()
