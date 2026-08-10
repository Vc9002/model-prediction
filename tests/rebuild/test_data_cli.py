"""Tests for the `rebuild-data` CLI scaffold.

Only `mlb` (MLB v3, research-only) is wired to a real backend; every other
sport is NOT_IMPLEMENTED (see data_foundation.py's module docstring). These
tests cover the harness itself: argument parsing, the forbidden-live-flags
guard, that stub sports report honestly, and that the real `mlb` backend is
actually reachable end-to-end without a network call (audit on an empty
data_root).
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
from model_prediction.runtime_paths import RuntimePaths

STUB_SPORTS = tuple(sport for sport in SUPPORTED_DATA_SPORTS if sport != "mlb")


@pytest.mark.parametrize("flag", ["--execute", "--live", "--real-order", "--promote"])
def test_live_execution_flags_fail_explicitly(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag, "backfill", "--sport", "nba"])
    assert exc.value.code == 2
    assert "shadow-only" in capsys.readouterr().err


@pytest.mark.parametrize("sport", STUB_SPORTS)
def test_backfill_reports_not_implemented_for_every_stub_sport(sport, tmp_path):
    report = run("backfill", sport, str(tmp_path), status="data_foundation", repo_root=str(tmp_path))
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["sport"] == sport
    assert report["operation"] == "backfill"


@pytest.mark.parametrize("sport", STUB_SPORTS)
def test_audit_reports_not_implemented_for_every_stub_sport(sport, tmp_path):
    report = run("audit", sport, str(tmp_path), status="data_foundation", repo_root=str(tmp_path))
    assert report["status"] == "NOT_IMPLEMENTED"
    assert report["operation"] == "audit"


def _mlb_v3_data_root(tmp_path: Path) -> str:
    # MLBV3DataBoundary requires data_root to be exactly what RuntimePaths
    # resolves for the given repo_root -- mirrors how data_cli.py's main()
    # always derives both from the same RebuildConfig.
    return str(RuntimePaths.resolve(repo_root=tmp_path).rebuild_root)


def test_mlb_v3_audit_against_empty_data_root_is_honest_no_data(tmp_path):
    # No network call: NO_DATA is a real, structural answer to "what has
    # been captured under this data_root", not a stub -- matches
    # docs/rebuild/MLB_V3_DATA.md's documented audit contract.
    report = run(
        "audit", "mlb", _mlb_v3_data_root(tmp_path), status="prospective_validation",
        repo_root=str(tmp_path), season=2026,
    )
    assert report["status"] == "NO_DATA"


def test_mlb_v3_backfill_requires_start_and_end(tmp_path):
    with pytest.raises(ValueError, match="requires --start and --end"):
        run(
            "backfill", "mlb", _mlb_v3_data_root(tmp_path), status="prospective_validation",
            repo_root=str(tmp_path), provider="mlb_stats", start=None, end=None, tables=None, force=False,
        )


def test_mlb_v3_audit_requires_season(tmp_path):
    with pytest.raises(ValueError, match="requires --season"):
        run(
            "audit", "mlb", _mlb_v3_data_root(tmp_path), status="prospective_validation",
            repo_root=str(tmp_path), season=None,
        )


def test_mlb_backfill_without_version_v3_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["backfill", "--sport", "mlb", "--start", "2026-08-01", "--end", "2026-08-01"])
    assert exc.value.code == 2
    assert "--version v3" in capsys.readouterr().err


def test_non_mlb_version_flag_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["backfill", "--sport", "nba", "--version", "v3"])
    assert exc.value.code == 2
    assert "not meaningful" in capsys.readouterr().err


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
        [sys.executable, "-m", "model_prediction.rebuild.data_cli", "audit", "--sport", "nba"],
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
        "sport": "nba",
        "operation": "audit",
        "status": "NOT_IMPLEMENTED",
        "reason": (
            "no data foundation is registered for nba yet "
            "(config/rebuild.yaml sports.nba.status='data_foundation'); "
            "see data_foundation.py's module docstring"
        ),
    }
