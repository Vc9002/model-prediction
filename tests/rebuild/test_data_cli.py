"""Tests for the `rebuild-data` CLI scaffold.

Every sport is currently NOT_IMPLEMENTED (see data_foundation.py's module
docstring) -- these tests cover the harness itself: argument parsing, the
forbidden-live-flags guard, and that `run()` reports honestly rather than
crashing or silently pretending to do work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from model_prediction.rebuild.data_cli import _parser, main, run
from model_prediction.rebuild.data_foundation import SUPPORTED_DATA_SPORTS


@pytest.mark.parametrize("flag", ["--execute", "--live", "--real-order", "--promote"])
def test_live_execution_flags_fail_explicitly(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag, "backfill", "--sport", "mlb"])
    assert exc.value.code == 2
    assert "shadow-only" in capsys.readouterr().err


@pytest.mark.parametrize("sport", SUPPORTED_DATA_SPORTS)
def test_backfill_reports_not_implemented_for_every_registered_sport(sport, tmp_path):
    report = run("backfill", sport, str(tmp_path), status="data_foundation")
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["sport"] == sport
    assert report["operation"] == "backfill"


@pytest.mark.parametrize("sport", SUPPORTED_DATA_SPORTS)
def test_audit_reports_not_implemented_for_every_registered_sport(sport, tmp_path):
    report = run("audit", sport, str(tmp_path), status="data_foundation")
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["operation"] == "audit"


def test_unsupported_sport_is_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["backfill", "--sport", "cricket"])
    assert exc.value.code == 2


def test_command_is_required():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["--sport", "mlb"])
    assert exc.value.code == 2


def test_installed_console_script_runs_end_to_end():
    # Exercises the real pyproject.toml [project.scripts] entry against the
    # real repo-local config/rebuild.yaml, not an injected data_root -- the
    # one thing the direct `run()` tests above deliberately can't cover.
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": f"{repo_root / 'src'}:{repo_root}"}
    result = subprocess.run(
        [sys.executable, "-m", "model_prediction.rebuild.data_cli", "audit", "--sport", "mlb"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {
        "sport": "mlb",
        "operation": "audit",
        "status": "NOT_IMPLEMENTED",
        "reason": (
            "no data foundation is registered for mlb yet "
            "(config/rebuild.yaml sports.mlb.status='prospective_validation'); "
            "see data_foundation.py's module docstring"
        ),
    }
