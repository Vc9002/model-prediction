import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.domain import League, MarketType
from model_prediction.features.player_availability import _load_priors
from model_prediction.learned_replay import build_snapshot, replay_snapshot, snapshot_hash
from model_prediction.ledger import PickLedger
from model_prediction.models.learned_market import LearnedMarketArtifact, artifact_hash
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.wnba_availability_evaluation import adjust_home_probability, build_and_save_priors
from tests.test_ledger import request


def make_snapshot(*, gap=4.0, sigma=10.0, event_start="2026-09-10T22:00:00Z"):
    raw = {
        "method": "logistic_regression",
        "model_version": "wnba-test",
        "sport": "wnba",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability", "new_dynamic_feature"],
                "coefficients": [0.0, 0.0],
                "intercept": 0.0,
            }
        },
    }
    raw["artifact_hash"] = artifact_hash(raw)
    artifact = LearnedMarketArtifact(raw)
    proposed = adjust_home_probability(0.5, gap, sigma)
    applied = abs(proposed - 0.5) >= 0.05
    return build_snapshot(
        artifact,
        {"elo_probability": 0.61234567891234, "new_dynamic_feature": 1.2345678912345},
        event_id="event-1",
        observed_at_utc="2026-09-09T12:00:00Z",
        event_start_utc=event_start,
        base_home_probability=0.5,
        served_home_probability=proposed if applied else 0.5,
        adjustment={
            "method": "wnba_probit_v1",
            "applied": applied,
            "points_gap": gap,
            "margin_sigma": sigma,
            "minimum_delta": 0.05,
        },
    )


@pytest.mark.parametrize("gap", [0.0, 0.1, 4.0, -4.0])
def test_complete_transform_replays_without_external_state(gap):
    snapshot = make_snapshot(gap=gap)
    assert replay_snapshot(snapshot) == snapshot["served_home_probability"]
    assert snapshot["adjustment"]["applied"] == (abs(gap) == 4.0)


@pytest.mark.parametrize("change", ["features", "artifact", "served", "nan", "policy", "sigma"])
def test_corruption_and_invalid_transform_fail_closed(change):
    snapshot = make_snapshot()
    if change == "features":
        snapshot["features"]["elo_probability"] = 0.1
    elif change == "artifact":
        snapshot["artifact"]["market_models"]["moneyline"]["intercept"] = 2.0
        snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    elif change in {"served", "nan"}:
        snapshot["served_home_probability"] = 0.99 if change == "served" else "nan"
        snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    elif change == "policy":
        snapshot["adjustment"]["applied"] = False
        snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    else:
        snapshot["adjustment"]["margin_sigma"] = 0.0
        snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    with pytest.raises(ValueError):
        replay_snapshot(snapshot)


def test_dynamic_features_and_adjustment_survive_sqlite_settlement_and_export(tmp_path):
    original = request()
    snapshot = make_snapshot(event_start=original.event_start_utc)
    req = replace(
        original,
        league=League.WNBA,
        market_type=MarketType.MONEYLINE,
        line=None,
        selection="home",
        model_version=snapshot["model_version"],
        model_artifact_hash=snapshot["artifact_hash"],
        model_probability=snapshot["served_home_probability"],
        model_input_snapshot_json=json.dumps(snapshot),
        elo_probability=0.612346,
    )
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="main", mirror=store, authority="sqlite", sport="wnba")
    logged = ledger.append_call(req, 0.25, 70)
    [record] = store.records(tier="main", sport="wnba")
    payload = json.loads(record["feature_payload_json"])
    assert payload["feature_payload_schema_version"] == "ledger-row-features-v2"
    assert payload["features"]["new_dynamic_feature"] == "1.2345678912345"
    assert payload["features"]["elo_probability"] == "0.61234567891234"
    assert payload["model_input_snapshot"] == snapshot
    assert json.loads(ledger.rows()[0]["model_input_snapshot_json"]) == snapshot
    assert replay(record, LearnedMarketArtifact(snapshot["artifact"]))["status"] == "PASS"
    ledger.settle(logged["pick_id"], away_score=70, home_score=80)
    [settled] = store.records(tier="main", sport="wnba")
    assert json.loads(settled["feature_payload_json"])["model_input_snapshot"] == snapshot
    assert replay(settled, LearnedMarketArtifact(snapshot["artifact"]))["status"] == "PASS"
    store.close()


def test_decision_mismatch_fails_before_logging(tmp_path):
    original = request()
    snapshot = make_snapshot(event_start=original.event_start_utc)
    req = replace(original, model_input_snapshot_json=json.dumps(snapshot))
    with pytest.raises(ValueError, match="identity"):
        PickLedger(tmp_path / "picks.xlsx").append_call(req, 0.25, 70)


def test_future_input_snapshot_is_rejected_before_logging(tmp_path):
    original = request()
    snapshot = make_snapshot(event_start=original.event_start_utc)
    snapshot["observed_at_utc"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    snapshot["snapshot_hash"] = snapshot_hash(snapshot)
    req = replace(
        original,
        league=League.WNBA,
        market_type=MarketType.MONEYLINE,
        line=None,
        selection="home",
        model_version=snapshot["model_version"],
        model_artifact_hash=snapshot["artifact_hash"],
        model_probability=snapshot["served_home_probability"],
        model_input_snapshot_json=json.dumps(snapshot),
    )
    with pytest.raises(ValueError, match="future"):
        PickLedger(tmp_path / "picks.xlsx").append_call(req, 0.25, 70)


def test_late_refresh_preserves_earlier_point_in_time_prior(tmp_path, monkeypatch):
    from model_prediction import wnba_availability_evaluation as module

    class Client:
        def scoreboard(self, *_):
            return {"events": [{"id": "one"}]}

    monkeypatch.setattr(module, "build_research_priors", lambda **_: {"players": [{"player_name": "A"}]})
    early = datetime(2026, 8, 23, 12, tzinfo=UTC)
    late = datetime(2026, 8, 24, 3, tzinfo=UTC)
    first = build_and_save_priors(
        store=None, client=Client(), game_date="2026-08-23", data_root=tmp_path, observed_at=early
    )
    second = build_and_save_priors(
        store=None, client=Client(), game_date="2026-08-23", data_root=tmp_path, observed_at=late
    )
    assert first["prior_snapshot_path"] != second["prior_snapshot_path"]
    assert (
        _load_priors(tmp_path, "2026-08-23", datetime(2026, 8, 23, 20, tzinfo=UTC))["observed_at_utc"]
        == early.isoformat()
    )
    assert (
        json.loads((tmp_path / "player_priors/wnba/2026-08-23.json").read_text())["observed_at_utc"]
        == late.isoformat()
    )


def test_conflicting_priors_at_same_timestamp_are_rejected(tmp_path):
    directory = tmp_path / "player_priors/wnba/snapshots/2026-08-23"
    directory.mkdir(parents=True)
    payload = {
        "schema_version": "1",
        "sport": "wnba",
        "observed_at_utc": "2026-08-23T12:00:00Z",
        "valid_from": "2026-08-23",
        "valid_through": "2026-08-23",
        "players": [],
    }
    (directory / "a.json").write_text(json.dumps(payload))
    changed = copy.deepcopy(payload)
    changed["players"] = [{"player_name": "Different"}]
    (directory / "b.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="PRIORS_CONFLICT"):
        _load_priors(tmp_path, "2026-08-23", datetime(2026, 8, 23, 20, tzinfo=UTC))


def test_serving_producer_captures_adjustment_and_unchanged_model_inputs(tmp_path, monkeypatch):
    from model_prediction import learned_forward
    from model_prediction.features.base import FeatureStore
    from tests.test_learned_forward import FakeESPN, _write_artifact, _write_history

    _write_history(tmp_path)
    source = tmp_path / "processed/mlb/games.jsonl"
    destination = tmp_path / "processed/wnba/games.jsonl"
    destination.parent.mkdir(parents=True)
    destination.write_text(source.read_text().replace('"MLB"', '"WNBA"'))
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=True, sport="wnba")
    raw = json.loads(artifact_path.read_text())
    raw["market_models"]["moneyline"]["intercept"] = 0.0
    raw["artifact_hash"] = artifact_hash(raw)
    artifact_path.write_text(json.dumps(raw))

    class Client:
        def scoreboard(self, league, day):
            return FakeESPN().scoreboard("MLB", day)

    monkeypatch.setattr(
        learned_forward, "matchup_player_availability", lambda **_: {"availability_points_gap": 4.0}
    )
    monkeypatch.setattr(learned_forward, "historical_margin_sigma", lambda *_: 10.0)
    learned_forward._slate_cache.clear()
    monkeypatch.setattr(learned_forward.build_learned_moneyline_slate, "_wnba_sigma_cache", {}, raising=False)
    candidates, skipped, _ = learned_forward.build_learned_moneyline_slate(
        sport="wnba",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=Client(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    assert not skipped
    snapshot = candidates[0].model_input_snapshot
    assert snapshot["adjustment"]["applied"] is True
    assert snapshot["base_home_probability"] == 0.5
    assert replay_snapshot(snapshot) == pytest.approx(candidates[0].home_probability, abs=0.000001)
    assert "availability_points_gap" not in snapshot["features"]
