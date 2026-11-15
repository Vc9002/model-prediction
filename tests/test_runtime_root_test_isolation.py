"""Under pytest, runtime paths must never resolve to the operator's runtime root."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from model_prediction import runtime_paths
from model_prediction.runtime_paths import RuntimePaths

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = """
import os
import tests.conftest
from model_prediction.data_sources import polymarket_execute
from model_prediction.portfolio import auto_buyer_ledger, auto_executor
from model_prediction.runtime_paths import RuntimePaths
print(os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT", "UNSET"))
print(RuntimePaths.resolve().runtime_root)
print(auto_executor.AUTO_BUYER_CYCLE_HISTORY_PATH)
print(auto_executor.AUTO_BUYER_STATE_FILE)
print(auto_buyer_ledger.AUTO_BUYER_JSONL_PATH)
"""


def test_conftest_keeps_import_time_paths_off_any_runtime_root(tmp_path) -> None:
    """The shell sets the variable and the repo .env may set it again; neither may reach
    the path constants three modules compute at import, nor a later resolve()."""
    shell_root = tmp_path / "shell-runtime-root"
    shell_root.mkdir()
    env = {**os.environ, "MODEL_PREDICTION_RUNTIME_ROOT": str(shell_root)}
    env.pop("MODEL_PREDICTION_SKIP_DOTENV", None)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")])
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=True
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 5
    # Not merely "not the shell's root": a .env may name a different, real one.
    assert lines[0] == "UNSET"
    repo_data = (REPO_ROOT / "data").resolve()
    assert Path(lines[1]).resolve() == repo_data
    for line in lines[2:]:
        assert repo_data in Path(line).resolve().parents, f"{line} is outside the repo data directory"


def test_skip_flag_disables_the_env_file_fallback(tmp_path, monkeypatch) -> None:
    paths = RuntimePaths.for_test(tmp_path)
    monkeypatch.setattr(runtime_paths, "_default_repo_root", lambda: paths.repo_root)
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    (paths.repo_root / ".env").write_text(
        f'MODEL_PREDICTION_RUNTIME_ROOT="{paths.runtime_root}"\n', encoding="utf-8"
    )

    monkeypatch.setenv("MODEL_PREDICTION_SKIP_DOTENV", "1")
    assert RuntimePaths.resolve().runtime_root == paths.repo_root / "data"

    # Production never sets the flag, so the fallback is what operators get.
    monkeypatch.delenv("MODEL_PREDICTION_SKIP_DOTENV")
    assert RuntimePaths.resolve().runtime_root == paths.runtime_root
