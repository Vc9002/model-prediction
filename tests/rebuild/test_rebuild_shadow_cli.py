"""Tests for the shared multi-sport CLI's run_id handling
(scripts/rebuild_shadow_cli.py), notably --resume-run-id."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "rebuild_shadow_cli.py"
spec = importlib.util.spec_from_file_location("rebuild_shadow_cli", SCRIPT_PATH)
rebuild_shadow_cli = importlib.util.module_from_spec(spec)
sys.modules["rebuild_shadow_cli"] = rebuild_shadow_cli
spec.loader.exec_module(rebuild_shadow_cli)

from model_prediction.rebuild.shadow_ledger import ShadowLedger


class TestResumeRunId:
    def test_without_resume_run_id_each_invocation_mints_a_new_run(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        r2 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        assert r1["run_id"] != r2["run_id"]

    def test_resume_run_id_reuses_the_same_real_ledger_run_row(self, tmp_path):
        r1 = rebuild_shadow_cli.run("nba", "2026-08-06", "late", str(tmp_path), "collect")
        r2 = rebuild_shadow_cli.run(
            "nba", "2026-08-06", "late", str(tmp_path), "collect", resume_run_id=r1["run_id"],
        )
        assert r1["run_id"] == r2["run_id"]

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        rows = ledger.conn.execute("SELECT COUNT(*) as n FROM runs WHERE run_id=?", (r1["run_id"],)).fetchone()
        ledger.close()
        assert rows["n"] == 1  # one real row, not duplicated by the second call

    def test_a_fresh_process_still_fails_closed_on_decision_only_without_predict_state(self, tmp_path):
        # Real, disclosed limitation: --resume-run-id continues the same
        # ledger lineage row, not MLBAdapter's in-memory state from an
        # earlier stage -- a genuinely separate process still correctly
        # requires predict()/match_markets() to have run first.
        r1 = rebuild_shadow_cli.run("mlb", "2026-08-06", "late", str(tmp_path), "collect")
        r2 = rebuild_shadow_cli.run(
            "mlb", "2026-08-06", "late", str(tmp_path), "decide", resume_run_id=r1["run_id"],
        )
        assert r2["stages"]["decide"]["status"] == "ERROR"
