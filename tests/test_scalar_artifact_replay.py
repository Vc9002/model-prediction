import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.domain import League, MarketType
from model_prediction.ledger import PickLedger
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.models.mlb_first_inning import FirstInningGameRow, MLBFirstInningModel
from model_prediction.research_incumbent_evaluation import replay
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.scalar_artifact_replay import (
    build_snapshot,
    replay_snapshot,
    validate_decision_snapshot,
)
from tests.test_ledger import request


def artifact(version):
    return json.loads(
        (Path(__file__).resolve().parents[1] / "config/models" / (version + ".json")).read_text()
    )


@pytest.mark.parametrize("market", ["spread", "total"])
def test_normal_producer_captures_unrounded_distribution_and_exact_signed_line(market):
    art = artifact(f"wnba-{market}-margin-v2")
    model = BasketballModel(
        sport="wnba",
        league="WNBA",
        version=art["model_version"],
        margin_sd=13.37,
        total_sd=16,
        elo_weight=0.52,
        trend_weight=0.44,
        rest_weight=0.2,
    )
    game = UpcomingGame(
        event_id="x",
        event_start_utc=request().event_start_utc,
        home_team="Home",
        away_team="Away",
        spread_away_line=3.5,
        total_line=160.5,
    )
    prediction = next(p for p in model.predict_games([], [game]) if p.market_type == market)
    snap = build_snapshot(
        art,
        prediction.feature_basis,
        prediction.inference_inputs,
        event_id="x",
        event_start_utc=game.event_start_utc,
        observed_at_utc="2026-09-09T12:00:00Z",
    )
    assert all(prediction.probabilities[key] == value for key, value in replay_snapshot(snap).items())
    selection = "home" if market == "spread" else "over"
    row = {
        "model_version": art["model_version"],
        "model_artifact_hash": art["artifact_hash"],
        "league": "WNBA",
        "event_id": "x",
        "event_start_utc": game.event_start_utc,
        "market_type": market,
        "selection": selection,
        "line": -3.5 if market == "spread" else 160.5,
        "model_probability": prediction.probabilities[selection],
    }
    assert validate_decision_snapshot(snap, row) == row["model_probability"]
    with pytest.raises(ValueError, match="line_mismatch"):
        validate_decision_snapshot(snap, row | {"line": row["line"] + 1})


def test_nrfi_complete_artifact_features_replay_and_survive_settlement(tmp_path):
    art = artifact("mlb-nrfi-v2")
    features = dict(zip(art["feature_names"], art["scaler_mean"], strict=True))
    features[art["feature_names"][0]] += 0.123456789
    model = MLBFirstInningModel.from_dict(art)
    p = model.predict_p_nrfi(
        FirstInningGameRow(None, request().event_start_utc, "Home", "Away", "Venue", features, 0, 0)
    )
    base = request()
    snap = build_snapshot(
        art,
        features,
        {},
        event_id="event-1",
        event_start_utc=base.event_start_utc,
        observed_at_utc="2026-09-09T12:00:00Z",
    )
    assert replay_snapshot(snap) == {"nrfi": round(p, 4), "yrfi": round(1 - p, 4)}
    req = replace(
        base,
        league=League.MLB,
        market_type=MarketType.NRFI,
        selection="nrfi",
        line=0.5,
        event_id="event-1",
        model_version=art["model_version"],
        model_artifact_hash=art["artifact_hash"],
        model_probability=round(p, 4),
        model_input_snapshot_json=json.dumps(snap),
    )
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="mlb")
    logged = ledger.append_call(req, 0.25, 70)
    [record] = store.records(tier="flat", sport="mlb")
    assert replay(record, None)["status"] == "PASS"
    assert json.loads(record["feature_payload_json"])["model_input_snapshot"] == snap
    ledger.settle(logged["pick_id"], away_score=0, home_score=0)
    [settled] = store.records(tier="flat", sport="mlb")
    assert json.loads(settled["feature_payload_json"])["model_input_snapshot"] == snap
    assert replay(settled, None)["status"] == "PASS"
    missing = copy.deepcopy(features)
    del missing[art["feature_names"][0]]
    with pytest.raises(KeyError):
        build_snapshot(
            art,
            missing,
            {},
            event_id="x",
            event_start_utc=base.event_start_utc,
            observed_at_utc="2026-09-09T12:00:00Z",
        )
    store.close()


def test_nrfi_started_event_is_not_backdated_to_look_pregame(monkeypatch):
    from model_prediction.cli import forecast as cli
    from model_prediction.models import mlb_first_inning_live

    monkeypatch.setattr(
        mlb_first_inning_live,
        "live_first_inning_features",
        lambda **_: pytest.fail("started event must be skipped before features"),
    )
    client = SimpleNamespace(
        scoreboard=lambda *_: {
            "events": [
                {
                    "id": "past",
                    "date": "2000-01-01T12:00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {"homeAway": "home", "team": {"displayName": "Home"}},
                                {"homeAway": "away", "team": {"displayName": "Away"}},
                            ]
                        }
                    ],
                }
            ]
        }
    )
    result = cli._forecast_mlb_nrfi_flat("2000-01-01", False, {}, None, None, None, None, client=client)
    assert result["scheduled_events"] == 1 and result["nrfi_candidates"] == 0 and result["logged"] == 0
