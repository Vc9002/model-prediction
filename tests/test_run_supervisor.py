"""Tests for the run supervisor (consolidation A-2)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from model_prediction import run_supervisor
from model_prediction.filelock import lock_exclusive, unlock
from model_prediction.run_supervisor import WORKERS, RunSupervisor


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path, monkeypatch) -> None:
    """The supervisor is operational: it must only run against an
    external runtime root, so every test gets its own isolated one."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))


def _supervisor(tmp_path) -> RunSupervisor:
    """Supervisor rooted at a tmp repo with a fast heartbeat for tests."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    return RunSupervisor(
        repo_root=repo,
        db_path=tmp_path / "runs.db",
        heartbeat_interval_seconds=0.05,
    )


def test_successful_run_records_completed_row(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    code = sup.run_worker("daily", command=[sys.executable, "-c", "print('hello from worker')"])

    assert code == 0
    rows = sup.latest_runs()
    assert len(rows) == 1
    row = rows[0]
    assert row["worker"] == "daily"
    assert row["status"] == "completed"
    assert row["exit_code"] == 0
    assert row["started_at_utc"] and row["finished_at_utc"]
    assert row["heartbeat_at_utc"]
    assert row["git_sha"] == "unknown"  # tmp repo is not a git checkout
    log_text = (tmp_path / "runtime" / "logs" / "supervisor" / f"{row['run_id']}.log").read_text()
    assert "hello from worker" in log_text
    sup.close()


_ENV_PROBE = (
    "import os; "
    "print('OPENBLAS=' + os.environ.get('OPENBLAS_NUM_THREADS', '<unset>')); "
    "print('OMP=' + os.environ.get('OMP_NUM_THREADS', '<unset>'))"
)


def test_worker_env_caps_openblas_threads(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    sup = _supervisor(tmp_path)
    code = sup.run_worker("daily", command=[sys.executable, "-c", _ENV_PROBE])

    assert code == 0
    row = sup.latest_runs()[0]
    log_text = (tmp_path / "runtime" / "logs" / "supervisor" / f"{row['run_id']}.log").read_text()
    assert "OPENBLAS=1" in log_text
    # OpenMP stays uncapped: xgboost/sklearn training needs its parallelism.
    assert "OMP=<unset>" in log_text
    sup.close()


def test_worker_env_joins_pythonpath_with_the_platform_separator(tmp_path, monkeypatch) -> None:
    """A ":" join breaks on Windows, where it also splits a drive letter (src:C:\tools)."""
    sup = _supervisor(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert sup._worker_env("run-1", tmp_path / "w.log")["PYTHONPATH"] == "src"
    monkeypatch.setenv("PYTHONPATH", os.path.join(os.path.abspath(os.sep), "tools"))
    joined = sup._worker_env("run-1", tmp_path / "w.log")["PYTHONPATH"]
    assert joined == os.pathsep.join(["src", os.path.join(os.path.abspath(os.sep), "tools")])
    sup.close()


def test_worker_env_keeps_operator_openblas_value(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4")
    sup = _supervisor(tmp_path)
    code = sup.run_worker("daily", command=[sys.executable, "-c", _ENV_PROBE])

    assert code == 0
    row = sup.latest_runs()[0]
    log_text = (tmp_path / "runtime" / "logs" / "supervisor" / f"{row['run_id']}.log").read_text()
    assert "OPENBLAS=4" in log_text
    sup.close()


def test_failed_run_records_failure_and_returns_worker_exit_code(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    code = sup.run_worker("production", command=[sys.executable, "-c", "import sys; sys.exit(3)"])

    assert code == 3
    row = sup.latest_runs()[0]
    assert row["status"] == "failed"
    assert row["exit_code"] == 3
    sup.close()


def test_worker_exit_75_is_recorded_skipped_and_still_returned(tmp_path) -> None:
    # daily_lock.py exits 75 for a coalesced lock skip; that is never a failure.
    sup = _supervisor(tmp_path)
    code = sup.run_worker("daily", command=[sys.executable, "-c", "import sys; sys.exit(75)"])

    assert code == 75
    row = sup.latest_runs()[0]
    assert row["status"] == "skipped"
    assert row["exit_code"] == 75
    assert "lock busy" in row["note"]
    assert row["finished_at_utc"]
    sup.close()


def test_exit_codes_other_than_75_keep_their_status(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    assert sup.run_worker("daily", command=[sys.executable, "-c", "import sys; sys.exit(1)"]) == 1
    assert sup.latest_runs()[0]["status"] == "failed"
    assert sup.run_worker("daily", command=[sys.executable, "-c", "pass"]) == 0
    assert sup.latest_runs()[0]["status"] == "completed"
    sup.close()


def test_timeout_killed_run_stays_failed_even_if_the_exit_code_were_75(tmp_path, monkeypatch) -> None:
    # The 75 mapping applies only when no timeout kill happened (note is None).
    import subprocess

    real_wait = subprocess.Popen.wait
    calls = {"n": 0}

    def _wait(self, timeout=None):
        if self.args[0] != sys.executable:  # leave the supervisor's git probe alone
            return real_wait(self, timeout=timeout)
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return 75

    monkeypatch.setattr(subprocess.Popen, "wait", _wait)
    sup = _supervisor(tmp_path)
    code = sup.run_worker(
        "daily", command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=0.2
    )

    assert code == 75
    row = sup.latest_runs()[0]
    assert row["status"] == "failed"
    assert "exceeded timeout" in row["note"]
    sup.close()


def test_lease_contention_skips_and_records_the_skip(tmp_path) -> None:
    sup = _supervisor(tmp_path)

    # Hold the worker's lease the way another supervisor process would.
    lock_path = sup._lease_path("daily")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        lock_exclusive(handle, blocking=False)

        code = sup.run_worker("daily", command=[sys.executable, "-c", "print('never')"])

        assert code == 75  # daily_lock convention: LOCK_BUSY_EXIT
        row = sup.latest_runs()[0]
        assert row["status"] == "skipped"
        assert "lease held" in row["note"]
        assert row["exit_code"] is None
        unlock(handle)
    sup.close()


def test_lease_contention_does_not_close_the_active_run(tmp_path) -> None:
    """A second scheduled launch must not label the live owner as crashed."""
    sup = _supervisor(tmp_path)
    sup._insert_run(
        {
            "run_id": "daily-active",
            "worker": "daily",
            "command": "[]",
            "status": "started",
            "started_at_utc": "2026-10-26T16:10:00+00:00",
            "heartbeat_at_utc": "2026-10-26T16:39:55+00:00",
            "git_sha": "unknown",
        }
    )

    lock_path = sup._lease_path("daily")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        lock_exclusive(handle, blocking=False)
        code = sup.run_worker("daily", command=[sys.executable, "-c", "print('never')"])
        unlock(handle)

    assert code == 75
    rows = {row["run_id"]: row for row in sup.latest_runs(limit=2)}
    assert rows["daily-active"]["status"] == "started"
    assert rows["daily-active"]["note"] is None
    assert any(row["status"] == "skipped" for row in rows.values())
    sup.close()


def test_heartbeat_advances_while_worker_runs(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    code = sup.run_worker("daily", command=[sys.executable, "-c", "import time; time.sleep(0.4)"])

    assert code == 0
    row = sup.latest_runs()[0]
    started = row["started_at_utc"]
    heartbeat = row["heartbeat_at_utc"]
    assert heartbeat >= started  # heartbeat thread wrote while running
    sup.close()


def test_latest_runs_lists_newest_first_and_filters_by_worker(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
    time.sleep(0.01)  # keep started_at_utc ordering deterministic
    sup.run_worker("production", command=[sys.executable, "-c", "pass"])

    all_rows = sup.latest_runs()
    assert [r["worker"] for r in all_rows] == ["production", "daily"]
    daily_rows = sup.latest_runs(worker="daily")
    assert [r["worker"] for r in daily_rows] == ["daily"]
    sup.close()


def test_orphaned_started_rows_are_closed_on_next_run(tmp_path) -> None:
    """Burn-in contract: a SIGKILLed supervisor can't write its own failed
    row. The next run for the same worker must close the orphaned
    'started' row truthfully (the lease proves no live supervisor owns
    it)."""
    sup = _supervisor(tmp_path)
    sup._insert_run(
        {
            "run_id": "daily-orphan",
            "worker": "daily",
            "command": "[]",
            "status": "started",
            "started_at_utc": "2026-08-14T00:00:00+00:00",
            "heartbeat_at_utc": "2026-08-14T00:00:00+00:00",
            "git_sha": "unknown",
        }
    )
    code = sup.run_worker("daily", command=[sys.executable, "-c", "pass"])
    assert code == 0

    rows = sup.latest_runs(limit=2)
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["daily-orphan"]["status"] == "failed"
    assert "did not complete" in by_id["daily-orphan"]["note"]
    assert any(r["status"] == "completed" for r in rows)
    sup.close()


def test_leases_live_under_the_runtime_root(tmp_path, monkeypatch) -> None:
    """Consolidation pre-fix: supervisor lease files are mutable runtime
    state and must resolve through RuntimePaths, not repo data/locks."""
    from model_prediction.runtime_paths import RuntimePaths

    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    sup = RunSupervisor(repo_root=repo, heartbeat_interval_seconds=0.05)
    expected = RuntimePaths(repo_root=repo, runtime_root=runtime).lock_root
    assert sup._lease_path("daily").parent == expected
    sup.close()


def test_worker_registry_commands_exist_on_disk() -> None:
    """The three real workers must map to launchers that exist in the repo, per platform."""
    repo = Path(__file__).resolve().parents[1]
    launcher_shells = {"win32": "powershell", "other": "bash"}
    shell = launcher_shells["win32" if sys.platform == "win32" else "other"]
    for worker in ("daily", "production", "rebuild-shadow"):
        assert worker in WORKERS
        command = WORKERS[worker]
        if worker == "production":
            assert Path(command[0]).as_posix().endswith((".venv/bin/python", ".venv/Scripts/python.exe"))
            assert Path(command[0]).is_file()
            continue
        assert command[0] == shell
        script = command[-1]
        assert (repo / script).is_file(), f"{worker} launcher missing: {script}"


def test_unknown_worker_rejected(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    try:
        sup.run_worker("does-not-exist")
    except ValueError as exc:
        assert "unknown worker" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown worker")
    finally:
        sup.close()


def test_fails_closed_without_runtime_env(tmp_path, monkeypatch) -> None:
    """Consolidation P0-1: an env-less operational invocation must raise
    instead of silently creating a repo-local second runtime."""
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    repo = tmp_path / "repo"
    with pytest.raises(RuntimeError, match="MODEL_PREDICTION_RUNTIME_ROOT"):
        RunSupervisor(repo_root=repo)
    assert not (repo / "data" / "runs.db").exists()
    assert not (repo / "data" / "ledgers" / "ledgers.db").exists()


# Python source run as `python -c`, so this module never touches ctypes.WinDLL at import time
# and non-Windows collection is unaffected. Prints the process's own memory priority and class.
_PRIORITY_PROBE = """
import ctypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetCurrentProcess.restype = ctypes.c_void_p
k32.GetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
k32.GetProcessInformation.restype = ctypes.c_int
k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
k32.GetPriorityClass.restype = ctypes.c_uint32


class MemoryPriorityInformation(ctypes.Structure):
    _fields_ = [("MemoryPriority", ctypes.c_ulong)]


info = MemoryPriorityInformation()
handle = k32.GetCurrentProcess()
if not k32.GetProcessInformation(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
    print("PRIORITY error=%d" % ctypes.get_last_error(), flush=True)
else:
    print("PRIORITY mem=%d class=0x%x" % (info.MemoryPriority, k32.GetPriorityClass(handle)), flush=True)
"""

# Stubs RunSupervisor (a real one would resolve the live runtime root and copy multi-GB legacy
# DBs) and drives main() the way `python -m model_prediction.run_supervisor run <worker>` does.
# The probe source arrives as sys.argv[1]; the stub's run_worker launches it as a grandchild and
# relays its output.
_SUPERVISOR_DRIVER = """
import subprocess
import sys

import model_prediction.run_supervisor as rs


class _StubSupervisor:
    def __init__(self) -> None:
        pass

    def run_worker(self, worker: str) -> int:
        child = subprocess.run(
            [sys.executable, "-c", sys.argv[1]], check=False, capture_output=True, text=True
        )
        # Relay instead of inheriting the handle: with the real import chain loaded the
        # grandchild's inherited stdout never reached the test's pipe.
        sys.stdout.write(child.stdout)
        sys.stdout.flush()
        return child.returncode

    def close(self) -> None:
        pass


rs.RunSupervisor = _StubSupervisor
sys.exit(rs.main(["run", "probe"]))
"""

_PRIORITY_LINE = re.compile(r"PRIORITY mem=(\d+) class=0x([0-9a-f]+)")


def _child_env(repo_root: Path) -> dict[str, str]:
    """Child env that can never reach the live runtime root or the operator's .env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_PREDICTION_")}
    # Guard against a future import that loads .env (runtime root, Polymarket credentials).
    env["MODEL_PREDICTION_SKIP_DOTENV"] = "1"
    # A single entry: the child gets exactly this PYTHONPATH, whatever the supervisor test env holds.
    env["PYTHONPATH"] = str(repo_root / "src")
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process priority inheritance")
def test_main_run_worker_tree_inherits_low_priority() -> None:
    """The scheduled path is `python -m model_prediction.run_supervisor run <worker>`; the operator
    approved low memory priority for every worker tree on 2026-11-23 (quick-261123-oy5). A python
    grandchild of main()'s worker launch must be born BELOW_NORMAL with memory priority 2."""
    repo_root = Path(__file__).resolve().parents[1]
    env = _child_env(repo_root)

    baseline = subprocess.run(
        [sys.executable, "-c", _PRIORITY_PROBE],
        env=env,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    base = _PRIORITY_LINE.search(baseline.stdout)
    # A broken probe must fail, not hide behind the skip below.
    assert base is not None, f"priority probe failed: stdout={baseline.stdout!r} stderr={baseline.stderr!r}"
    if (int(base.group(1)), int(base.group(2), 16)) != (5, 0x20):
        pytest.skip(
            "pytest's children are already demoted (baseline "
            f"{baseline.stdout.strip()!r}); inheritance would mask a missing demotion in main()"
        )

    result = subprocess.run(
        [sys.executable, "-c", _SUPERVISOR_DRIVER, _PRIORITY_PROBE],
        env=env,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    detail = f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 0, detail
    matches = _PRIORITY_LINE.findall(result.stdout)
    assert len(matches) == 1, detail
    mem, cls = int(matches[0][0]), int(matches[0][1], 16)
    assert mem == 2, detail
    assert cls == 0x4000, detail


class _StubSupervisor:
    """Stands in for RunSupervisor so main() never touches a runtime root."""

    events: list[str]

    def __init__(self, *args, **kwargs) -> None:
        pass

    def run_worker(self, worker, **kwargs) -> int:
        type(self).events.append(f"run_worker:{worker}")
        return 0

    def latest_runs(self, worker=None, limit=20) -> list[dict]:
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def _stubbed_main(monkeypatch) -> list[str]:
    events: list[str] = []

    class Stub(_StubSupervisor):
        pass

    Stub.events = events

    def _background() -> bool:
        events.append("background")
        return True

    monkeypatch.setattr(run_supervisor, "enable_background_priority", _background)
    monkeypatch.setattr(run_supervisor, "RunSupervisor", Stub)
    return events


def test_main_run_demotes_once_before_the_worker(_stubbed_main) -> None:
    assert run_supervisor.main(["run", "w"]) == 0
    assert _stubbed_main == ["background", "run_worker:w"]


def test_main_status_runs_and_usage_never_demote(_stubbed_main) -> None:
    assert run_supervisor.main(["status"]) == 1
    assert run_supervisor.main(["status", "daily"]) == 1
    assert run_supervisor.main(["runs"]) == 0
    assert run_supervisor.main(["runs", "5"]) == 0
    # The usage error must return before the demotion, pinning the call after the check.
    assert run_supervisor.main(["run"]) == 2
    assert run_supervisor.main([]) == 2
    assert "background" not in _stubbed_main
    assert not any(e.startswith("run_worker:") for e in _stubbed_main)


def test_notify_operator_disabled_by_default(monkeypatch) -> None:
    """Operator notifications are permanently disabled by default."""
    from model_prediction.run_supervisor import notify_operator

    monkeypatch.delenv("MODEL_PREDICTION_OPERATOR_NOTIFICATIONS", raising=False)
    assert notify_operator("System Health: DEGRADED", "reason", "warning") is False


def test_notify_operator_suppressed_by_env_toggle(monkeypatch) -> None:
    """The operator removed macOS/slack notifications; the toggle suppresses the send entirely."""
    from model_prediction.run_supervisor import notify_operator

    monkeypatch.setenv("MODEL_PREDICTION_OPERATOR_NOTIFICATIONS", "0")
    assert notify_operator("System Health: DEGRADED", "reason", "warning") is False
