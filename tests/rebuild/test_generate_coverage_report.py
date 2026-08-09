"""Tests for real coverage/missingness report generation
(scripts/generate_coverage_report.py, FOUNDATION_COMPLETION.md Phase 7)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "generate_coverage_report.py"
spec = importlib.util.spec_from_file_location("generate_coverage_report", SCRIPT_PATH)
generate_coverage_report = importlib.util.module_from_spec(spec)
sys.modules["generate_coverage_report"] = generate_coverage_report
spec.loader.exec_module(generate_coverage_report)


class TestGenerateMLBCoverage:
    def test_writes_a_real_coverage_and_missingness_file_per_horizon(self, tmp_path):
        out_root = tmp_path / "outputs"
        generate_coverage_report.generate_mlb_coverage(str(tmp_path / "data"), "2026-08-06", str(out_root))

        for horizon in ("early", "mid", "late"):
            coverage_path = out_root / "coverage" / f"mlb_{horizon}.json"
            missingness_path = out_root / "missingness" / f"mlb_{horizon}.json"
            assert coverage_path.exists()
            assert missingness_path.exists()

            coverage = json.loads(coverage_path.read_text())
            assert coverage["generated_from_code"] is True
            assert coverage["sport"] == "mlb"
            assert coverage["horizon"] == horizon
            # No real scoreboard data in this empty tmp_path -- zero games
            # is the honest real result, not a fabricated placeholder.
            assert coverage["total_games"] == 0
