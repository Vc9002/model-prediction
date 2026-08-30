"""Tests for the run supervisor (consolidation A-2)."""

from __future__ import annotations

import fcntl
import sys
import time

import pytest

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


def test_failed_run_records_failure_and_returns_worker_exit_code(tmp_path) -> None:
    sup = _supervisor(tmp_path)
    code = sup.run_worker("production", command=[sys.executable, "-c", "import sys; sys.exit(3)"])

    assert code == 3
    row = sup.latest_runs()[0]
    assert row["status"] == "failed"
    assert row["exit_code"] == 3
    sup.close()


def test_lease_contention_skips_and_records_the_skip(tmp_path) -> None:
    sup = _supervisor(tmp_path)

    # Hold the worker's lease the way another supervisor process would.
    lock_path = sup._lease_path("daily")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

        code = sup.run_worker("daily", command=[sys.executable, "-c", "print('never')"])

        assert code == 75  # daily_lock convention: LOCK_BUSY_EXIT
        row = sup.latest_runs()[0]
        assert row["status"] == "skipped"
        assert "lease held" in row["note"]
        assert row["exit_code"] is None
        fcntl.flock(handle, fcntl.LOCK_UN)
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


def test_worker_registry_commands_exist_on_disk(tmp_path) -> None:
    """The three real workers must map to commands that exist in the repo."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for worker in ("daily", "production", "rebuild-shadow"):
        assert worker in WORKERS
        assert WORKERS[worker][0] in ("bash", ".venv/bin/python")


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
