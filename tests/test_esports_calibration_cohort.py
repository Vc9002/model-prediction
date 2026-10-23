"""Regression checks for the post-merge esports calibration cohort."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from model_prediction import calibration_pipeline as pipeline


def row(event="e1", **overrides):
    return {
        "league": "CS2",
        "event_id": event,
        "model_version": "cs2-tiered-elo-v6",
        "market_type": "moneyline",
        "selection": "home",
        "line": "",
        "status": "settled",
        "result": "win",
        "model_probability": 0.6,
        "created_at_utc": "2026-10-01T10:00:00Z",
        **overrides,
    }


def test_latest_decision_per_contract_is_ordered_by_timestamp():
    first = row(model_probability=0.55)
    revised = row(model_probability=0.65, created_at_utc="2026-10-01T11:00:00Z")
    earlier = row("e0", created_at_utc="2026-09-30T10:00:00Z")
    other_selection = row(selection="away")
    assert pipeline._esports_calibration_rows([revised, first, earlier, other_selection]) == [
        earlier,
        other_selection,
        revised,
    ]


def test_line_representation_does_not_duplicate_contracts():
    first = row(line="1.5")
    revised = row(line=1.5, created_at_utc="2026-10-02T00:00:00Z")
    assert pipeline._esports_calibration_rows([first, revised]) == [revised]


@pytest.mark.parametrize(
    "override",
    [
        {"created_at_utc": ""},
        {"created_at_utc": "2026-10-01T10:00:00"},
        {"event_id": ""},
        {"selection": ""},
        {"model_version": ""},
        {"line": "nan"},
    ],
)
def test_unidentifiable_or_untimed_esports_is_not_calibration_evidence(override):
    assert pipeline._esports_calibration_rows([row(**override)]) == []


def test_other_sports_are_preserved():
    original = {"league": "MLB", "model_probability": 0.0}
    assert pipeline._esports_calibration_rows([original, original]) == [original, original]


def test_extract_preserves_zero_probability_and_separate_tiers(monkeypatch, tmp_path):
    rows = [row(model_probability=0.0, probability=0.9)]
    ledger = SimpleNamespace(rows=lambda: rows)
    monkeypatch.setattr(
        pipeline, "MultiSportPickLedger", lambda *a, **k: SimpleNamespace(_ledgers={"cs2": ledger})
    )
    monkeypatch.setattr(pipeline, "existing_research_ledgers", lambda *_: [])
    groups = pipeline.extract_settled_model_predictions(tmp_path)
    assert groups[("cs2", "moneyline", "cs2-tiered-elo-v6", "flat")] == ([0.0], [1])
    assert groups[("cs2", "moneyline", "cs2-tiered-elo-v6", "main")] == ([0.0], [1])


def test_canonical_flat_artifact_beats_larger_retired_esports_tier(monkeypatch, tmp_path):
    groups = {
        ("cs2", "moneyline", "cs2-tiered-elo-v6", "research"): ([0.6] * 80, [1, 0] * 40),
        ("cs2", "moneyline", "cs2-tiered-elo-v6", "flat"): ([0.6] * 40, [1, 0] * 20),
    }
    monkeypatch.setattr(pipeline, "extract_settled_model_predictions", lambda **_: groups)
    report = pipeline.run_full_calibration_pipeline(
        data_root=tmp_path, output_dir=tmp_path / "out", min_sample=20, update_artifacts=True
    )
    assert len(report["models"]) == 2
    payload = json.loads(
        (tmp_path / "calibration/models/cs2-tiered-elo-v6.moneyline_calibrator.json").read_text()
    )
    assert payload["sample_size"] == 40


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"market_type": "moneyline "}, {"market_type": "moneyline"}),
        ({"line": -0.0}, {"line": 0.0}),
        ({"market_type": "", "market": "moneyline"}, {"market_type": "moneyline"}),
    ],
)
def test_equivalent_market_identity_cannot_inflate_sample(first, second):
    assert (
        len(
            pipeline._esports_calibration_rows(
                [row(**first), row(created_at_utc="2026-10-02T00:00:00Z", **second)]
            )
        )
        == 1
    )


def test_insufficient_flat_revokes_stale_research_artifact(monkeypatch, tmp_path):
    groups = {
        ("cs2", "moneyline", "cs2-tiered-elo-v6", "research"): ([0.6] * 80, [1, 0] * 40),
        ("cs2", "moneyline", "cs2-tiered-elo-v6", "flat"): ([0.6] * 19, [1] * 19),
    }
    monkeypatch.setattr(pipeline, "extract_settled_model_predictions", lambda **_: groups)
    target = tmp_path / "calibration/models/cs2-tiered-elo-v6.moneyline_calibrator.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"champion_method": "platt", "sample_size": 80}))
    pipeline.run_full_calibration_pipeline(
        data_root=tmp_path, output_dir=tmp_path / "out", min_sample=20, update_artifacts=True
    )
    payload = json.loads(target.read_text())
    assert payload["champion_method"] == "identity"
    assert payload["sample_size"] == 19
    assert payload["reason"] == "canonical_flat_insufficient_evidence"


def test_empty_eligible_flat_retains_authority(monkeypatch, tmp_path):
    invalid = row(created_at_utc="")
    ledger = SimpleNamespace(rows=lambda: [invalid])
    monkeypatch.setattr(
        pipeline, "MultiSportPickLedger", lambda *a, **k: SimpleNamespace(_ledgers={"cs2": ledger})
    )
    monkeypatch.setattr(pipeline, "existing_research_ledgers", lambda *_: [])
    groups = pipeline.extract_settled_model_predictions(tmp_path)
    assert groups[("cs2", "moneyline", "cs2-tiered-elo-v6", "flat")] == ([], [])
    pipeline.run_full_calibration_pipeline(
        data_root=tmp_path, output_dir=tmp_path / "out", update_artifacts=True
    )
    artifact = json.loads(
        (tmp_path / "calibration/models/cs2-tiered-elo-v6.moneyline_calibrator.json").read_text()
    )
    assert artifact["sample_size"] == 0 and artifact["champion_method"] == "identity"
