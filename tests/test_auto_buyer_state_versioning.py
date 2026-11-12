"""Conflict-detection tests for the Auto-Buyer and NCAAB paper-trial state files.

A monotonic ``_state_version`` on each file lets a save detect that another
writer (dashboard, scheduled worker, operator hand-edit) advanced the file
on disk between this process's load and save -- and refuse to clobber it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_prediction.portfolio import ncaab_paper_trial as trial
from model_prediction.portfolio.auto_executor import (
    AutoExecutionResult,
    StateVersionConflictError,
    load_auto_buyer_state,
    run_auto_buyer_cycle,
    save_auto_buyer_state,
)


def test_legacy_state_without_version_loads_as_zero_and_saves_as_one(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    test_state_file.write_text(json.dumps({"enabled": False, "mode": "paper"}), encoding="utf-8")

    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
    ):
        state = load_auto_buyer_state()
        assert state["_state_version"] == 0

        save_auto_buyer_state(state)
        on_disk = json.loads(test_state_file.read_text(encoding="utf-8"))
        assert on_disk["_state_version"] == 1
        assert state["_state_version"] == 1

        # Saving the same (now-current) dict again must not self-conflict.
        save_auto_buyer_state(state)
        on_disk_again = json.loads(test_state_file.read_text(encoding="utf-8"))
        assert on_disk_again["_state_version"] == 2


@pytest.mark.parametrize("raw_version", ["abc", -3, True, 1.5])
def test_invalid_state_version_values_load_as_zero(tmp_path: Path, raw_version):
    test_state_file = tmp_path / "auto_buyer_state.json"
    test_state_file.write_text(
        json.dumps({"enabled": False, "mode": "paper", "_state_version": raw_version}),
        encoding="utf-8",
    )

    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
    ):
        state = load_auto_buyer_state()
        assert state["_state_version"] == 0


def test_concurrent_write_between_load_and_save_raises_conflict(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    test_state_file.write_text(json.dumps({"enabled": True, "mode": "paper"}), encoding="utf-8")

    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
    ):
        stale = load_auto_buyer_state()

        # Out-of-band write simulating a concurrent dashboard disable.
        test_state_file.write_text(
            json.dumps({"enabled": False, "mode": "paper", "_state_version": 1}),
            encoding="utf-8",
        )

        with pytest.raises(StateVersionConflictError):
            save_auto_buyer_state(stale)

    on_disk = json.loads(test_state_file.read_text(encoding="utf-8"))
    assert on_disk["enabled"] is False
    assert on_disk["_state_version"] == 1


def test_cycle_end_save_preserves_mid_cycle_operator_change(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    test_state_file.write_text(json.dumps({"enabled": True, "mode": "paper"}), encoding="utf-8")
    paper_root = tmp_path / "paper"

    def _mid_cycle_disable(*_args, **_kwargs):
        test_state_file.write_text(
            json.dumps({"enabled": False, "mode": "paper", "_state_version": 4}),
            encoding="utf-8",
        )
        return AutoExecutionResult(total_evaluated=2)

    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
        patch("model_prediction.portfolio.auto_executor.PAPER_AUTO_BUYER_DATA_ROOT", paper_root),
        patch("model_prediction.portfolio.auto_buyer_ledger.settle_auto_buyer_ledger", lambda **_: None),
        patch(
            "model_prediction.portfolio.auto_executor.AutoPolymarketBuyer.evaluate_and_execute",
            side_effect=_mid_cycle_disable,
        ),
    ):
        run_auto_buyer_cycle(forecast_date="2026-11-11")

    on_disk = json.loads(test_state_file.read_text(encoding="utf-8"))
    assert on_disk["enabled"] is False
    assert on_disk["_state_version"] == 5
    assert on_disk["last_daily_date"] == "2026-11-11"
    assert on_disk["last_run"]["total_evaluated"] == 2


def test_paper_trial_concurrent_write_between_read_and_save_raises_conflict(tmp_path: Path, monkeypatch):
    path = tmp_path / "auto_buyer_ncaab_spread_paper_trial.json"
    settings = {
        "enabled": True,
        "model_id": "ncaab-spread-v6",
        "market_type": "spread",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.08,
    }
    path.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(trial.RuntimePaths, "resolve", lambda: SimpleNamespace(runtime_root=tmp_path))

    original_status = trial.paper_trial_status

    def _stale_status_with_concurrent_write():
        stale = original_status()
        # Out-of-band write simulating a concurrent dashboard/worker save.
        path.write_text(
            json.dumps({**settings, "min_saved_edge": 0.06, "_state_version": 1}),
            encoding="utf-8",
        )
        return stale

    monkeypatch.setattr(trial, "paper_trial_status", _stale_status_with_concurrent_write)

    with pytest.raises(StateVersionConflictError):
        trial.set_paper_trial_enabled(False)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["min_saved_edge"] == 0.06
    assert on_disk["enabled"] is True
    assert on_disk["_state_version"] == 1
