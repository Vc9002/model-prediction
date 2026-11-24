from __future__ import annotations

import os
import subprocess
import sys
import time

from model_prediction.daily_lock import LOCK_BUSY_EXIT, acquire_lock


def test_daily_lock_refuses_a_second_process(tmp_path) -> None:
    path = tmp_path / "daily.lock"
    first = acquire_lock(path)
    assert first is not None
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(["src", "."])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "model_prediction.daily_lock",
                "--lock",
                str(path),
                "--",
                sys.executable,
                "-c",
                "pass",
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
    env["PYTHONPATH"] = os.pathsep.join(["src", "."])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "model_prediction.daily_lock",
            "--lock",
            str(path),
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(7)",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7


def test_daily_lock_waits_for_current_writer_then_runs_child(tmp_path) -> None:
    """A scheduled daily run must queue behind an active auto-buyer instead
    of exiting 75 and losing the entire forecast cycle."""
    path = tmp_path / "daily.lock"
    marker = tmp_path / "ran.txt"
    first = acquire_lock(path)
    assert first is not None
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(["src", "."])
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "model_prediction.daily_lock",
            "--lock",
            str(path),
            "--wait-seconds",
            "2",
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
        ],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert process.poll() is None
        first.close()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        if not first.closed:
            first.close()

    assert process.returncode == 0, (stdout, stderr)
    assert marker.read_text(encoding="utf-8") == "ran"


def test_daily_lock_child_output_reaches_the_parent_streams(tmp_path) -> None:
    # The scheduled daily runs `run_supervisor -> powershell run_daily.ps1 -> daily_lock -> powershell
    # run_daily.ps1`, and the supervisor log is daily_lock's stdout. On Windows the package's Popen
    # patch adds CREATE_NO_WINDOW, which gives a child with no explicit std handles a hidden console
    # of its own: every supervisor daily log was 0 bytes until 2026-11-24.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(["src", "."])
    child = "import sys; print('CHILD-OUT', flush=True); sys.stderr.write('CHILD-ERR\\n')"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "model_prediction.daily_lock",
            "--lock",
            str(tmp_path / "daily.lock"),
            "--",
            sys.executable,
            "-c",
            child,
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "CHILD-OUT" in result.stdout, (result.stdout, result.stderr)
    assert "CHILD-ERR" in result.stderr, (result.stdout, result.stderr)
