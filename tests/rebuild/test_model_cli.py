"""Tests for the `rebuild-model` CLI scaffold. Mirrors test_data_cli.py --
every sport is currently NOT_IMPLEMENTED (see model_lifecycle.py's module
docstring)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from model_prediction.rebuild.model_cli import _parser, main, run
from model_prediction.rebuild.model_lifecycle import SUPPORTED_MODEL_SPORTS


@pytest.mark.parametrize("flag", ["--execute", "--live", "--real-order", "--promote"])
def test_live_execution_flags_fail_explicitly(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag, "train", "--sport", "mlb"])
    assert exc.value.code == 2
    assert "shadow-only" in capsys.readouterr().err


@pytest.mark.parametrize("sport", SUPPORTED_MODEL_SPORTS)
def test_train_reports_not_implemented_for_every_registered_sport(sport, tmp_path):
    report = run("train", sport, str(tmp_path), status="data_foundation")
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["sport"] == sport
    assert report["operation"] == "train"


@pytest.mark.parametrize("sport", SUPPORTED_MODEL_SPORTS)
def test_compare_reports_not_implemented_for_every_registered_sport(sport, tmp_path):
    report = run("compare", sport, str(tmp_path), status="data_foundation")
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["operation"] == "compare"


def test_unsupported_sport_is_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["train", "--sport", "cricket"])
    assert exc.value.code == 2


def test_command_is_required():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["--sport", "mlb"])
    assert exc.value.code == 2


def test_installed_console_script_runs_end_to_end():
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": f"{repo_root / 'src'}:{repo_root}"}
    result = subprocess.run(
        [sys.executable, "-m", "model_prediction.rebuild.model_cli", "compare", "--sport", "mlb"],
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
        "operation": "compare",
        "status": "NOT_IMPLEMENTED",
        "reason": (
            "no model lifecycle is registered for mlb yet "
            "(config/rebuild.yaml sports.mlb.status='prospective_validation'); "
            "see model_lifecycle.py's module docstring"
        ),
    }
