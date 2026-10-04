"""Qualification claims require registered identity, not names or file presence."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from model_prediction import qualification_registry as qualification
from model_prediction.model_lifecycle import (
    ModelLifecycleContract,
    challenger_identity_errors,
    load_challenger_evidence,
    validate_lifecycle_contract,
)
from model_prediction.production_registry import ProductionModelEntry


def entry(model_id="candidate", **kwargs):
    return ProductionModelEntry(
        model_id=model_id, sport="MLB", market="moneyline", implementation="json_artifact", **kwargs
    )


@pytest.mark.parametrize("model_id", ["mlb-moneyline-v9-frozen", "wnba-moneyline-v5"])
def test_names_cannot_claim_prospective_capture(monkeypatch, model_id):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        qualification, "load_challenger_evidence", lambda *a, **k: pytest.fail("unbound evidence read")
    )
    summary = next(
        s for s in qualification.generate_qualification_registry(root) if s.challenger_model_id == model_id
    )
    assert summary.build_status == "planned"
    assert summary.challenger_artifact_hash is None
    assert summary.live_prospective_n == 0
    assert summary.qualification_errors


@pytest.mark.parametrize(
    "candidate,reason",
    [
        (None, "not registered"),
        (entry(load_error="bad hash"), "unavailable"),
        (entry(), "artifact hash"),
        (replace(entry(artifact_hash="a" * 64), market="spread"), "sport/market"),
    ],
)
def test_lifecycle_surfaces_challenger_failure_without_removing_champion(candidate, reason):
    champion = entry("champion", artifact_hash="b" * 64)
    contract = ModelLifecycleContract(
        sport="MLB", market="moneyline", champion_model_id="champion", challenger_model_id="candidate"
    )
    entries = {"champion": champion}
    if candidate is not None:
        entries["candidate"] = candidate
    errors = validate_lifecycle_contract(contract, entries_by_id=entries)
    assert any(reason in e for e in errors)
    assert champion.available


def test_evidence_without_expected_artifact_hash_never_reads_ledger(tmp_path, monkeypatch):
    from model_prediction import champion_challenger

    monkeypatch.setattr(
        champion_challenger, "load_settled_predictions", lambda *a, **k: pytest.fail("unbound ledger read")
    )
    assert load_challenger_evidence("MLB", "moneyline", "candidate", repo_root=tmp_path) == []


def test_module_import_is_implementation_not_validation(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(qualification, "_load_verified_offline_evaluation", lambda *a, **k: None)
    summary = next(
        s
        for s in qualification.generate_qualification_registry(root)
        if s.challenger_model_id == "cfb-structural-v2"
    )
    assert summary.build_status == "implemented"
    assert summary.verdict == "CONTINUE"


def test_operator_serving_basis_survives_registry_reporting():
    root = Path(__file__).resolve().parents[1]
    summary = next(
        s
        for s in qualification.generate_qualification_registry(root)
        if s.sport == "MLB" and s.market == "total"
    )
    assert summary.champion_promotion_basis == "operator_predictive_promotion"
    assert summary.to_dict()["champion_promotion_evidence_level"] == "predictive_only"
    assert summary.live_prospective_n == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("frozen_at_utc", None),
        ("frozen_at_utc", "2026-01-01T00:00:00"),
        ("frozen_at_utc", "2999-01-01T00:00:00Z"),
        ("promotion_protocol_hash", None),
        ("promotion_protocol_hash", "not-a-hash"),
    ],
)
def test_freeze_metadata_is_required(field, value):
    candidate = entry(
        artifact_hash="a" * 64, frozen_at_utc="2026-01-01T00:00:00Z", promotion_protocol_hash="b" * 64
    )
    assert challenger_identity_errors("candidate", candidate, "MLB", "moneyline", require_freeze=True) == []
    bad = replace(candidate, **{field: value})
    assert challenger_identity_errors("candidate", bad, "MLB", "moneyline", require_freeze=True)


@pytest.mark.parametrize(
    "change",
    [
        {"artifact_hash": "wrong"},
        {"model_id": "wrong"},
        {"evidence_origin": "synthetic"},
        {"n_evaluated": "500"},
    ],
)
def test_offline_report_requires_matching_identity(tmp_path, change):
    path = tmp_path / "outputs/research/candidate_offline_evaluation.json"
    path.parent.mkdir(parents=True)
    report = {
        "model_id": "candidate",
        "artifact_hash": "a" * 64,
        "verdict": "VALIDATED_OFFLINE",
        "n_evaluated": 500,
        "evidence_origin": "historical_backtest",
    }
    path.write_text(json.dumps(report))
    assert qualification._load_verified_offline_evaluation("candidate", tmp_path, "a" * 64)
    path.write_text(json.dumps({**report, **change}))
    assert qualification._load_verified_offline_evaluation("candidate", tmp_path, "a" * 64) is None


def test_evidence_filters_market_and_hash_and_ignores_row_claimed_freeze(tmp_path, monkeypatch):
    from model_prediction import champion_challenger

    monkeypatch.setattr(
        champion_challenger,
        "load_settled_predictions",
        lambda *a, **k: [
            {
                "event_id": "game",
                "result": "win",
                "event_start_utc": "2026-01-03T12:00:00Z",
                "settled_at_utc": "2026-01-04T00:00:00Z",
            }
        ],
    )
    row = {
        "sport": "MLB",
        "market": "moneyline",
        "model_id": "candidate",
        "model_artifact_hash": "a" * 64,
        "event_id": "game",
        "prediction_created_at": "2026-01-03T11:00:00Z",
        "feature_snapshot_observed_at": "2026-01-03T10:00:00Z",
        "market_snapshot_observed_at": "2026-01-03T10:00:00Z",
        "candidate_frozen_at": "2026-01-01T00:00:00Z",
    }
    path = tmp_path / "data/horizon_observations.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in [row, {**row, "market": "spread"}, {**row, "model_artifact_hash": "wrong"}, []]
        )
    )
    rows = load_challenger_evidence("MLB", "moneyline", "candidate", "a" * 64, repo_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["evidence_origin"] != "live_prospective"
    rows = load_challenger_evidence(
        "MLB", "moneyline", "candidate", "a" * 64, "2026-01-01T00:00:00Z", tmp_path
    )
    assert rows[0]["evidence_origin"] == "live_prospective"
    path.write_text(json.dumps({**row, "evidence_origin": "synthetic"}))
    rows = load_challenger_evidence(
        "MLB", "moneyline", "candidate", "a" * 64, "2026-01-01T00:00:00Z", tmp_path
    )
    assert rows[0]["evidence_origin"] == "synthetic"
