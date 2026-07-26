from __future__ import annotations

import os
import subprocess
import sys

from model_prediction.daily_lock import LOCK_BUSY_EXIT, acquire_lock


def test_daily_lock_refuses_a_second_process(tmp_path) -> None:
    path = tmp_path / "daily.lock"
    first = acquire_lock(path)
    assert first is not None
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src:."
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "model_prediction.daily_lock",
                "--lock",
                str(path),
                "--",
                "true",
            ],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        first.close()

    assert result.returncode == LOCK_BUSY_EXIT
    assert "DAILY_WRITER_ALREADY_RUNNING" in result.stdout


def test_daily_lock_propagates_child_exit_code(tmp_path) -> None:
    path = tmp_path / "daily.lock"
    env = dict(os.environ)
    env["PYTHONPATH"] = "src:."
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "model_prediction.daily_lock",
            "--lock",
            str(path),
            "--",
            "bash",
            "-c",
            "exit 7",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7
