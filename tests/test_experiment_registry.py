"""Tests for the experiment registry (consolidation B/C, item 9)."""

from __future__ import annotations

import pytest

from model_prediction.experiment_registry import (
    list_experiments,
    record,
    show,
    void,
)


def test_record_round_trips_all_fields(tmp_path) -> None:
    row = record(
        model_id="mlb-elo-trend-lr-v9",
        incumbent_id="mlb-elo-trend-lr-v8",
        dataset_hash="deadbeef",
        feature_schema_hash="cafef00d",
        fold_definition={"split": "60/20/20", "seed": 42},
        hyperparameters={"lr": 0.01, "reg": "l2"},
        calibrator="platt",
        oof_metrics={"brier": 0.211, "units": 56.4},
        artifact_hashes={"artifact": "abc123"},
        verdict="promote",
        git_sha="test-sha",
        repo_root=tmp_path,
    )

    assert row["status"] == "completed"
    assert row["experiment_id"].startswith("exp-")
    fetched = show(row["experiment_id"], repo_root=tmp_path)
    assert fetched["oof_metrics"] == {"brier": 0.211, "units": 56.4}
    assert fetched["fold_definition"]["seed"] == 42
    assert fetched["artifact_hashes"] == {"artifact": "abc123"}


def test_void_keeps_the_record_with_a_reason(tmp_path) -> None:
    row = record(model_id="mlb-v9", repo_root=tmp_path)
    voided = void(row["experiment_id"], "parity correction invalidated results", repo_root=tmp_path)

    assert voided["status"] == "void"
    assert "parity" in voided["void_reason"]
    # Still in the registry — invalidated results are kept, never deleted.
    assert show(row["experiment_id"], repo_root=tmp_path)["status"] == "void"


def test_void_unknown_id_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="no experiment"):
        void("exp-does-not-exist", "nope", repo_root=tmp_path)


def test_list_filters_by_model_and_orders_newest_first(tmp_path) -> None:
    a = record(model_id="mlb-v9", repo_root=tmp_path)
    record(model_id="wnba-v5", repo_root=tmp_path)

    rows = list_experiments(repo_root=tmp_path)
    assert len(rows) == 2 and rows[0]["experiment_id"] != a["experiment_id"]

    mlb_rows = list_experiments(model_id="mlb-v9", repo_root=tmp_path)
    assert [r["experiment_id"] for r in mlb_rows] == [a["experiment_id"]]


def test_bad_status_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="status"):
        record(model_id="x", repo_root=tmp_path, status="archived")
