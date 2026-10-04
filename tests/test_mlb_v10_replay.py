import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.domain import League, MarketType, parse_utc
from model_prediction.ledger import PickLedger
from model_prediction.mlb_v10_replay import build_snapshot, replay_snapshot, validate_decision_snapshot
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from tests.test_ledger import request
from tests.test_mlb_structural_v10 import _mock_feature_vector

ARTIFACT = Path(__file__).parents[1] / "config/models/research/mlb_structural_v10_frozen.json"


@pytest.mark.parametrize("line", [8.0, 8.5])
def test_full_v10_artifact_feature_probability_replay(line, tmp_path):
    artifact = json.loads(ARTIFACT.read_text())
    req = request()
    features = replace(_mock_feature_vector(), game_start_utc=req.event_start_utc)
    snapshot = build_snapshot(artifact, features, line, "2026-09-09T12:00:00Z")
    values = replay_snapshot(snapshot)
    assert sum(values["probabilities"].values()) == pytest.approx(1, abs=1e-4)
    assert (values["probabilities"]["push"] > 0) == (line == 8.0)
    req = replace(
        req,
        league=League.MLB,
        market_type=MarketType.TOTAL,
        selection="over",
        line=line,
        event_id=features.event_id,
        home_team=features.home_team,
        away_team=features.away_team,
        model_version=artifact["model_version"],
        model_artifact_hash=snapshot["artifact_hash"],
        model_probability=values["probabilities"]["over"],
        model_input_snapshot_json=json.dumps(snapshot),
    )
    assert validate_decision_snapshot(snapshot, req.as_dict()) == req.model_probability
    with pytest.raises(ValueError, match="identity"):
        validate_decision_snapshot(snapshot, req.as_dict() | {"line": line + 1})
    tampered = json.loads(json.dumps(snapshot))
    tampered["artifact"]["model_weights"]["away_intercept"] += 0.1
    with pytest.raises(ValueError, match="artifact"):
        replay_snapshot(tampered)
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="mlb")
    logged = ledger.append_call(req, 0.25, 70)
    ledger.settle(logged["pick_id"], away_score=4, home_score=4)
    [row] = store.records(tier="flat", sport="mlb")
    assert replay(row, None)["status"] == "PASS"
    store.close()


def test_actual_shadow_runner_captures_inputs_and_refuses_backdating(monkeypatch, tmp_path):
    import scripts.mlb_v10_prospective_shadow as module

    features = _mock_feature_vector()
    state = SimpleNamespace(
        consensus_line=8.5, consensus_price_no_vig=0.5, book_count=3, sharp_consensus_line=8.5
    )
    monkeypatch.setattr(
        module,
        "MLBv10FeatureExtractor",
        lambda **kw: SimpleNamespace(extract_features_for_matchup=lambda **kw: features),
    )
    monkeypatch.setattr(module, "MarketQuoteWarehouse", lambda **kw: None)
    monkeypatch.setattr(
        module,
        "MarketStateVectorBuilder",
        lambda **kw: SimpleNamespace(build_state_vector=lambda **kw: state),
    )
    monkeypatch.setattr(module.RuntimePaths, "resolve", lambda: RuntimePaths.for_test(tmp_path))
    monkeypatch.setattr(module, "utc_now", lambda: parse_utc(features.as_of_utc))
    runner = module.MLBPersistentShadowRunner(ARTIFACT)

    def capture():
        return runner.generate_pregame_prediction(
            features.event_id, features.home_team, features.away_team, features.game_start_utc
        )

    record = capture()
    assert record is not None
    evidence = record.model_input_snapshot
    values = replay_snapshot(evidence)
    assert values["probabilities"] == {"over": record.p_over, "under": record.p_under, "push": record.p_push}
    assert evidence["features"] == features.to_dict()
    assert evidence["historical_source_observation_times_verified"] is False
    monkeypatch.setattr(module, "utc_now", lambda: parse_utc(features.game_start_utc))
    assert capture() is None
    monkeypatch.setattr(module, "utc_now", lambda: parse_utc(features.as_of_utc) - timedelta(seconds=1))
    assert capture() is None
