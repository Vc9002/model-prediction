"""Import-time path constants must never bind to the operator's runtime root under pytest."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_conftest_drops_the_runtime_root_before_the_package_is_imported(tmp_path) -> None:
    live_root = tmp_path / "live-runtime-root"
    live_root.mkdir()
    probe = (
        "import tests.conftest\n"
        "from model_prediction.portfolio import auto_buyer_ledger, auto_executor\n"
        "print(auto_executor.AUTO_BUYER_CYCLE_HISTORY_PATH)\n"
        "print(auto_executor.AUTO_BUYER_STATE_FILE)\n"
        "print(auto_buyer_ledger.AUTO_BUYER_JSONL_PATH)\n"
    )
    env = {**os.environ, "MODEL_PREDICTION_RUNTIME_ROOT": str(live_root)}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")])
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=True
    )
    paths = [Path(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(paths) == 3
    for path in paths:
        assert live_root not in path.parents, f"{path} is under the env runtime root"
