"""Run supervisor: launchd is a trigger, not the application architecture.

Every scheduled job funnels through one entry point. The supervisor gives a
run its identity (unique ``run_id``), holds a per-worker lease, heartbeats
while the worker runs, records success/failure, and exposes the run history
to health monitoring — so "is the job loaded?" matters less than "when was
its most recent successful run and what did it produce?".

Workers are the existing scripts/entrypoints, unchanged; this module only
adds the run-state protocol around them::

    python -m model_prediction.run_supervisor run daily
    python -m model_prediction.run_supervisor run production
    python -m model_prediction.run_supervisor run rebuild-shadow
    python -m model_prediction.run_supervisor status [worker]
    python -m model_prediction.run_supervisor runs [LIMIT]

Run state lives in a small SQLite database (WAL mode), one row per run —
the foundation the truthful-health work (next consolidation phase) reads
instead of file mtimes.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

# Worker name -> command. The launchd plists call the supervisor with the
# worker name; the command mapping lives HERE, not in three separate plists
# and scripts that each re-decide what "run" means.
WORKERS: dict[str, list[str]] = {
    "daily": ["bash", "scripts/run_daily.sh"],
    "production": [
        ".venv/bin/python",
        "-m",
        "model_prediction.cli_production",
        "predict",
    ],
    "rebuild-shadow": ["bash", "scripts/run_rebuild.sh"],
}

_RUN_STATUSES = ("started", "completed", "failed", "skipped")
_LOCK_BUSY_EXIT = 75  # matches daily_lock.py's convention

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    worker            TEXT NOT NULL,
    command           TEXT NOT NULL,
    status            TEXT NOT NULL,
    started_at_utc    TEXT NOT NULL,
    finished_at_utc   TEXT,
    heartbeat_at_utc  TEXT,
    pid               INTEGER,
    exit_code         INTEGER,
    git_sha           TEXT,
    log_path          TEXT,
    note              TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_worker_started
    ON runs (worker, started_at_utc DESC);
"""


class RunSupervisor:
    """Lease + heartbeat + outcome recording for one scheduled worker."""

    def __init__(
        self,
        repo_root: Path | str | None = None,
        db_path: Path | str | None = None,
        *,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else PROJECT_ROOT
        # Repo data root for now; moves under the runtime root with the
        # rest of the mutable state in the data-plane consolidation phase.
        self.db_path = (
            Path(db_path) if db_path else self.repo_root / "data" / "runs.db"
        )
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.lock_dir = self.repo_root / "data" / "locks"
        self.log_dir = self.repo_root / "data" / "logs" / "supervisor"
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------- database

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _insert_run(self, row: dict[str, Any]) -> None:
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders})", row
            )

    def _update_run(self, run_id: str, **fields: Any) -> None:
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        with self.conn:
            self.conn.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = :run_id",
                {**fields, "run_id": run_id},
            )

    def latest_runs(self, worker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent runs (optionally for one worker), newest first."""
        query = "SELECT * FROM runs"
        params: tuple[Any, ...] = ()
        if worker is not None:
            query += " WHERE worker = ?"
            params = (worker,)
        query += " ORDER BY started_at_utc DESC LIMIT ?"
        params = (*params, int(limit))
        rows = self.conn.execute(query, params).fetchall()
        columns = [description[0] for description in self.conn.execute(
            "SELECT * FROM runs LIMIT 0"
        ).description]
        return [dict(zip(columns, row)) for row in rows]

    # ---------------------------------------------------------------- lease

    def _lease_path(self, worker: str) -> Path:
        return self.lock_dir / f"supervisor-{worker}.lock"

    def _acquire_lease(self, worker: str) -> Any:
        """Return the open lock file, or None when another run holds it."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        # Deliberately no context manager: the flock lives on this fd for
        # the whole run, so the handle must outlive this scope.
        handle = open(self._lease_path(worker), "w", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        handle.write(str(os.getpid()))
        handle.flush()
        return handle

    # ------------------------------------------------------------------ run

    def run_worker(
        self,
        worker: str,
        *,
        command: Sequence[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> int:
        """Run one worker to completion and record the outcome.

        Returns the worker's exit code, or ``_LOCK_BUSY_EXIT`` when the
        worker's lease was already held (the run is recorded as skipped).
        """
        cmd = list(command) if command else WORKERS.get(worker)
        if not cmd:
            raise ValueError(f"unknown worker: {worker}; expected one of {sorted(WORKERS)}")

        lease = self._acquire_lease(worker)
        if lease is None:
            # Overlap protection: record the skip so monitoring can tell
            # "another run was still going" apart from "the job never fired".
            self._insert_run(
                {
                    "run_id": self._new_run_id(worker),
                    "worker": worker,
                    "command": json.dumps(cmd),
                    "status": "skipped",
                    "started_at_utc": datetime.now(UTC).isoformat(),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                    "heartbeat_at_utc": datetime.now(UTC).isoformat(),
                    "git_sha": self._git_sha(),
                    "note": f"lease held by another {worker} run",
                }
            )
            return _LOCK_BUSY_EXIT

        run_id = self._new_run_id(worker)
        started_at = datetime.now(UTC).isoformat()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{run_id}.log"
        try:
            with log_path.open("wb") as log_handle:
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.repo_root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=self._worker_env(),
                )
                self._insert_run(
                    {
                        "run_id": run_id,
                        "worker": worker,
                        "command": json.dumps(cmd),
                        "status": "started",
                        "started_at_utc": started_at,
                        "heartbeat_at_utc": started_at,
                        "pid": proc.pid,
                        "git_sha": self._git_sha(),
                        "log_path": str(log_path),
                    }
                )
                stop_heartbeat = threading.Event()

                def _heartbeat() -> None:
                    # Own connection: sqlite3 forbids sharing the main
                    # thread's connection across threads.
                    conn = sqlite3.connect(self.db_path, timeout=10.0)
                    conn.execute("PRAGMA busy_timeout=5000")
                    while not stop_heartbeat.wait(self.heartbeat_interval_seconds):
                        with conn:
                            conn.execute(
                                "UPDATE runs SET heartbeat_at_utc = ? "
                                "WHERE run_id = ?",
                                (datetime.now(UTC).isoformat(), run_id),
                            )
                    conn.close()

                heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
                heartbeat_thread.start()
                try:
                    exit_code = proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    exit_code = proc.wait()
                    note = f"worker exceeded timeout {timeout_seconds}s and was killed"
                else:
                    note = None
                finally:
                    stop_heartbeat.set()
                    heartbeat_thread.join(timeout=self.heartbeat_interval_seconds + 1)
            self._update_run(
                run_id,
                status="completed" if exit_code == 0 else "failed",
                finished_at_utc=datetime.now(UTC).isoformat(),
                exit_code=exit_code,
                note=note,
            )
            return exit_code
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)
            lease.close()

    # -------------------------------------------------------------- helpers

    def _worker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = "src" + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
        return env

    @staticmethod
    def _new_run_id(worker: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{worker}-{stamp}-{uuid.uuid4().hex[:6]}"

    def _git_sha(self) -> str:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.repo_root,
            )
            return out.stdout.strip() or "unknown"
        except (OSError, subprocess.CalledProcessError):
            return "unknown"


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ("run", "status", "runs"):
        print(
            "usage: python -m model_prediction.run_supervisor "
            "{run <worker>|status [worker]|runs [LIMIT]}",
            file=sys.stderr,
        )
        return 2

    supervisor = RunSupervisor()
    try:
        cmd = args[0]
        if cmd == "run":
            if len(args) < 2:
                print("usage: run <worker>", file=sys.stderr)
                return 2
            return supervisor.run_worker(args[1])
        if cmd == "status":
            worker = args[1] if len(args) > 1 else None
            rows = supervisor.latest_runs(worker=worker, limit=1)
            if not rows:
                print("no runs recorded")
                return 1
            row = rows[0]
            print(
                f"{row['worker']}: {row['status']} "
                f"(run {row['run_id']}, started {row['started_at_utc']}"
                + (f", exit {row['exit_code']}" if row.get("exit_code") is not None else "")
                + ")"
            )
            if row.get("note"):
                print(f"  note: {row['note']}")
            return 0
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        rows = supervisor.latest_runs(limit=limit)
        if not rows:
            print("no runs recorded")
            return 0
        for row in rows:
            print(
                f"{row['worker']:<16} {row['status']:<10} "
                f"{row['started_at_utc']:<28} "
                + (f"exit={row['exit_code']} " if row.get("exit_code") is not None else "")
                + row["run_id"]
            )
        return 0
    finally:
        supervisor.close()


if __name__ == "__main__":
    sys.exit(main())
