import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from model_prediction.domain import League, MarketType
from model_prediction.esports import NeutralElo
from model_prediction.esports_replay import (
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


def snapshot(title="LOL"):
    book = NeutralElo(
        k=20,
        ratings={"a": 1751.23456789, "b": 1520.11223344, "unrelated": 2500},
        platt_intercept=0.05,
        platt_slope=0.81,
        temperature=1.18,
        games_played={"a": 2, "b": 70},
        last_match_utc={"a": "2026-08-01T10:00:00Z", "b": "2026-09-08T10:00:00Z"},
    )
    ref = datetime(2026, 9, 9, 12, tzinfo=UTC)
    inputs = capture_inputs(book, ["a", "b"], ref)
    p = book.probability("a", "b", ref)
    assert inputs["book_state"]["ratings"] == {"a": 1751.23456789, "b": 1520.11223344}
    assert replay_inputs(inputs) == {"home": round(p, 6), "away": round(1 - p, 6)}
    return build_snapshot(
        {
            "title": title,
            "teams": ["Alpha", "Beta"],
            "source_team_ids": ["a", "b"],
            "inference_inputs": inputs,
            "model_version": title.lower() + "-tiered-elo-v6",
            "artifact_hash": "source-artifact",
            "event_id": "event-1",
            "event_start_utc": request().event_start_utc,
            "sides": [
                {"team": "Alpha", "model_probability": round(p, 6)},
                {"team": "Beta", "model_probability": round(1 - p, 6)},
            ],
        }
    )


@pytest.mark.parametrize("title", ["CS2", "DOTA2", "LOL", "VALORANT", "RAINBOW_SIX"])
def test_full_serving_adjustments_and_identity_for_each_title(title):
    evidence = snapshot(title)
    row = {
        **evidence,
        "league": title,
        "model_artifact_hash": evidence["artifact_hash"],
        "selection": "home",
        "line": None,
        "model_probability": evidence["probabilities"]["home"],
    }
    assert validate_decision_snapshot(evidence, row) == row["model_probability"]
    with pytest.raises(ValueError, match="identity"):
        validate_decision_snapshot(evidence, row | {"home_team": "Beta", "away_team": "Alpha"})
    with pytest.raises(ValueError, match="identity"):
        validate_decision_snapshot(evidence, row | {"league": "MLB"})


def test_esports_inputs_survive_ledger_settlement(tmp_path):
    evidence = snapshot()
    req = replace(
        request(),
        league=League.LOL,
        market_type=MarketType.MONEYLINE,
        line=None,
        home_team="Alpha",
        away_team="Beta",
        selection="home",
        event_id=evidence["event_id"],
        event_start_utc=evidence["event_start_utc"],
        model_probability=evidence["probabilities"]["home"],
        model_version=evidence["model_version"],
        model_artifact_hash=evidence["artifact_hash"],
        model_input_snapshot_json=json.dumps(evidence),
    )
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="lol")
    logged = ledger.append_call(req, 0.25, 70)
    [record] = store.records(tier="flat", sport="lol")
    assert replay(record, None)["status"] == "PASS"
    ledger.settle(logged["pick_id"], away_score=1, home_score=2)
    [settled] = store.records(tier="flat", sport="lol")
    assert json.loads(settled["feature_payload_json"])["model_input_snapshot"] == evidence
    assert replay(settled, None)["status"] == "PASS"
    store.close()


def test_remove_open_rows_survives_stale_code_hash_under_sqlite_authority(tmp_path, monkeypatch):
    """2026-09-14 fix: an additive, unrelated esports.py change (new BO3
    settlement helpers) changed that file's hash. Every already-open esports
    pick's captured snapshot then carried a stale code_sha256, and clearing
    them for daily re-forecast (`remove_open_rows`) called into the sqlite
    mirror's fail-hard `_mirror_row`, which re-validates the snapshot via
    replay and raised `esports_replay_code_mismatch` -- uncaught above this
    call, this took down the ENTIRE multi-sport daily pipeline (see
    cli/daily.py's `_clear_today_open`), not just esports. Removal discards
    a stale, unsettled row without asserting any new fact about it, so it
    must succeed regardless of whether replay still reproduces the old code
    -- append/settle on a FRESH capture (self-consistent within the same
    process) must still validate normally.
    """
    evidence = snapshot()
    req = replace(
        request(),
        league=League.LOL,
        market_type=MarketType.MONEYLINE,
        line=None,
        home_team="Alpha",
        away_team="Beta",
        selection="home",
        event_id=evidence["event_id"],
        event_start_utc=evidence["event_start_utc"],
        model_probability=evidence["probabilities"]["home"],
        model_version=evidence["model_version"],
        model_artifact_hash=evidence["artifact_hash"],
        model_input_snapshot_json=json.dumps(evidence),
    )
    store = RuntimeLedgerStore(RuntimePaths.for_test(tmp_path))
    ledger = PickLedger(tmp_path / "picks.xlsx", tier="flat", mirror=store, authority="sqlite", sport="lol")
    logged = ledger.append_call(req, 0.25, 70)

    # Simulate a later, unrelated code change invalidating this pick's
    # already-captured replay hash (exactly what code_hashes() would see).
    import model_prediction.esports_replay as esports_replay_module

    monkeypatch.setattr(
        esports_replay_module,
        "code_hashes",
        lambda: {"esports.py": "changed", "features/elo_ratings.py": "changed"},
    )

    # Must not raise -- a stale replay hash on a row being discarded is not
    # a real mutation failure.
    removed = ledger.remove_open_rows(
        [logged["pick_id"]], reason="re-forecast replacement", allow_staked_removal=True
    )
    assert removed == [logged["pick_id"]]
    [record] = store.records(tier="flat", sport="lol")
    assert record["status"] == "removed"

    # A genuinely NEW capture in the same (now code-hash-mismatched) process
    # must still fail validation normally -- this fix only exempts removal.
    with pytest.raises(ValueError, match="esports_replay_code_mismatch"):
        ledger.append_call(req, 0.25, 70)
    store.close()
