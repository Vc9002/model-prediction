"""Tests for the shared multi-sport CLI's run_id handling
(scripts/rebuild_shadow_cli.py), notably --resume-run-id."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "rebuild_shadow_cli.py"
spec = importlib.util.spec_from_file_location("rebuild_shadow_cli", SCRIPT_PATH)
rebuild_shadow_cli = importlib.util.module_from_spec(spec)
sys.modules["rebuild_shadow_cli"] = rebuild_shadow_cli
spec.loader.exec_module(rebuild_shadow_cli)

from model_prediction.rebuild.cli import main
from model_prediction.rebuild.config import default_repo_root
from model_prediction.rebuild.safety import RebuildSafetyError
from model_prediction.rebuild.shadow_ledger import ShadowLedger
from model_prediction.rebuild.sport_adapter import StageResult


@pytest.mark.parametrize("flag", ["--execute", "--live", "--real-order", "--promote"])
def test_live_execution_flags_fail_explicitly(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag, "--sport", "mlb", "--date", "2026-08-06"])
    assert exc.value.code == 2
    assert "shadow-only" in capsys.readouterr().err


def test_direct_run_rejects_repo_production_root_before_opening_ledger():
    protected = default_repo_root() / "data" / "main" / "rebuild_must_not_create"
    with pytest.raises(RebuildSafetyError):
        rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(protected), "collect")
    assert not (protected / "shadow.db").exists()


def test_stage_exception_finalizes_run_and_stage_as_error(tmp_path):
    class ExplodingAdapter:
        def collect(self, date, run_id=None):
            raise RuntimeError("real collector failure")

    with (
        patch("model_prediction.rebuild.cli.build_adapter", return_value=ExplodingAdapter()),
        pytest.raises(RuntimeError, match="real collector failure"),
    ):
        rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")

    ledger = ShadowLedger(tmp_path / "shadow.db")
    run = dict(ledger.conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone())
    stage = ledger.get_stage_result(run["run_id"], "collect")
    ledger.close()
    assert run["status"] == "ERROR"
    assert run["finished_at"] is not None
    assert run["duration_seconds"] is not None
    assert run["error"] == "real collector failure"
    assert stage["status"] == "ERROR"
    assert stage["error"] == "real collector failure"
    assert stage["row_count"] is None


def test_successful_stage_records_duration_mode_and_honest_rows(tmp_path):
    class PredictingAdapter:
        def predict(self, date, horizon, run_id=None):
            return StageResult("predict", "SUCCESS", {"games_predicted": 3})

    with patch("model_prediction.rebuild.cli.build_adapter", return_value=PredictingAdapter()):
        report = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "predict")

    ledger = ShadowLedger(tmp_path / "shadow.db")
    stage = ledger.get_stage_result(report["run_id"], "predict")
    run = ledger.get_run(report["run_id"])
    ledger.close()
    assert stage["duration_seconds"] is not None
    assert stage["mode"] == "partial"
    assert stage["row_count"] == 3
    assert run["status"] == "SUCCESS"
    assert run["finished_at"] is not None


class TestResumeRunId:
    def test_without_resume_run_id_each_invocation_mints_a_new_run(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        r2 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        assert r1["run_id"] != r2["run_id"]

    def test_resume_run_id_reuses_the_same_real_ledger_run_row(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        r2 = rebuild_shadow_cli.run(
            "nba",
            "2026-08-06",
            "late",
            str(tmp_path),
            "collect",
            resume_run_id=r1["run_id"],
        )
        assert r1["run_id"] == r2["run_id"]

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        rows = ledger.conn.execute(
            "SELECT COUNT(*) as n FROM runs WHERE run_id=?", (r1["run_id"],)
        ).fetchone()
        ledger.close()
        assert rows["n"] == 1  # one real row, not duplicated by the second call

    def test_a_fresh_process_still_fails_closed_on_decision_only_without_predict_state(self, tmp_path):
        # Real, disclosed limitation: --resume-run-id continues the same
        # ledger lineage row, not MLBAdapter's in-memory state from an
        # earlier stage -- a genuinely separate process still correctly
        # requires predict()/match_markets() to have run first.
        r1 = rebuild_shadow_cli.run("mlb", "2026-08-06", "late", str(tmp_path), "predict")
        r2 = rebuild_shadow_cli.run(
            "mlb",
            "2026-08-06",
            "late",
            str(tmp_path),
            "decide",
            resume_run_id=r1["run_id"],
        )
        assert r2["stages"]["decide"]["status"] == "ERROR"


class TestTrueResumeSkipsCompletedResumableStages:
    """Task 5: true resume system -- a resumed invocation must genuinely
    skip stages already recorded SUCCESS for that run_id, not just reuse
    the ledger row and blindly re-run everything. Directly manipulates
    run_stages (rather than depending on a real network collect()
    succeeding in this offline test environment) to deterministically
    exercise the skip path itself."""

    def test_collect_is_skipped_when_already_recorded_success(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        run_id = r1["run_id"]

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        ledger.record_stage_result(run_id, "collect", "SUCCESS", {"games": 3})
        ledger.close()

        r2 = rebuild_shadow_cli.run(
            "nba",
            "2026-08-06",
            "late",
            str(tmp_path),
            "collect",
            resume_run_id=run_id,
        )

        assert r2["stages"]["collect"]["status"] == "SKIPPED_ALREADY_COMPLETE"

    def test_collect_is_not_skipped_without_resume_run_id(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        run_id = r1["run_id"]

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        ledger.record_stage_result(run_id, "collect", "SUCCESS")
        ledger.close()

        # No resume_run_id passed -- a brand-new run_id is minted, so there
        # is genuinely nothing recorded for it to skip.
        r2 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")

        assert r2["stages"]["collect"]["status"] != "SKIPPED_ALREADY_COMPLETE"

    def test_predict_is_never_skipped_even_if_marked_complete(self, tmp_path):
        # predict/match_markets/decide are deliberately excluded from
        # RESUMABLE_STAGES -- a resumed process has no real MLBAdapter
        # in-memory state, so skipping predict() here would let
        # match_markets()/decide() silently run against nothing rather
        # than correctly failing closed.
        r1 = rebuild_shadow_cli.run("mlb", "2026-08-06", "late", str(tmp_path), "predict")
        run_id = r1["run_id"]

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        ledger.record_stage_result(run_id, "predict", "SUCCESS")
        ledger.close()

        r2 = rebuild_shadow_cli.run(
            "mlb",
            "2026-08-06",
            "late",
            str(tmp_path),
            "predict",
            resume_run_id=run_id,
        )

        assert r2["stages"]["predict"]["status"] != "SKIPPED_ALREADY_COMPLETE"

    def test_stage_result_is_recorded_in_the_ledger_after_a_real_run(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        completed = ledger.get_completed_stages(r1["run_id"])
        ledger.close()

        assert completed.get("collect") == r1["stages"]["collect"]["status"]


def test_symlinked_repo_local_data_root_is_rejected(tmp_path):
    """A research worktree shares the canonical checkout's data/ tree via a
    symlink; a data_root supplied THROUGH that symlink resolves into the
    live production tree, so the lexical (unresolved) path must be
    rejected even though the resolved path escapes the repo. Pins the
    2026-08-17 worktree safety-gap fix."""
    from model_prediction.rebuild.safety import assert_runtime_data_root

    canonical_data = tmp_path / "canonical" / "data"
    canonical_data.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "data").symlink_to(canonical_data)
    protected = worktree / "data" / "main" / "rebuild_must_not_create"
    with pytest.raises(RebuildSafetyError):
        assert_runtime_data_root(str(protected), str(worktree))


def test_external_tmp_root_still_allowed(tmp_path):
    from model_prediction.rebuild.safety import assert_runtime_data_root

    external = tmp_path / "external_root"
    assert assert_runtime_data_root(str(external), str(tmp_path / "repo")) == external.resolve()
