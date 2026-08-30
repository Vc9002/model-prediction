"""Dashboard jobs module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import UTC, datetime

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore[assignment]


from model_prediction.dashboard.common import (
    _ACTION_LOCK,
    _CACHE,
    _CACHE_LOCK,
    _JOBS,
    _JOBS_LOCK,
    _LAST_ACTION,
    DASH_DIR,
    JOBS_FILE,
    ROOT,
    _log,
    _runner_env,
)
from model_prediction.dashboard.orders import (
    _action_command,
)

# ── SECTION: Jobs & Actions ─────────────────────────────────────────


def _job_status_for_returncode(returncode: int) -> str:
    """Map a job's exit code to a dashboard status.

    75 is the daily_lock convention (LOCK_BUSY_EXIT): the supervisor
    records the run as skipped when another run holds the lease. A manual
    trigger landing during the scheduled run is a coalesced skip, not a
    failure — lock refusal must not be routine scheduling behavior.
    """
    if returncode == 75:
        return "skipped"
    return "ok" if returncode == 0 else "failed"


def start_action(name: str, payload: dict) -> dict:
    """Launch a whitelisted action as a background job; return its id at once.

    Actions like `daily` legitimately run for minutes (slate discovery plus
    ~200 BBO snapshots). Holding the HTTP request open that long is what made
    the browser show "Failed to fetch" — so the POST returns immediately and
    the page polls /api/job.
    """
    if not _ACTION_LOCK.acquire(blocking=False):
        running = next((j for j in _JOBS.values() if j["status"] == "running"), None)
        return {
            "status": "busy",
            "error": "another action is already running",
            "job_id": running["job_id"] if running else None,
        }
    try:
        command = _action_command(name, payload)
    except (ValueError, RuntimeError) as error:
        _ACTION_LOCK.release()
        return {"status": "failed", "error": str(error)}
    job_id = f"{name}-{int(time.time())}"
    job = {
        "job_id": job_id,
        "action": name,
        "status": "running",
        "command": " ".join(command[-8:]),
        "output_tail": "",
        "started_at": datetime.now(UTC).isoformat()[:19],
        "started_monotonic": time.time(),
        "seconds": 0.0,
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        while len(_JOBS) > 20:
            _JOBS.pop(next(iter(_JOBS)))
    _log(f"job started: {job_id} :: {job['command']}")
    _persist_jobs()

    def _work() -> None:
        started = time.time()
        chunks: list[str] = []
        process = None
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=_runner_env(),
            )
            assert process.stdout is not None
            for line in process.stdout:
                chunks.append(line)
                if len(chunks) > 4000:
                    chunks = chunks[-2000:]
                with _JOBS_LOCK:
                    job["output_tail"] = "".join(chunks)[-12000:]
                    job["seconds"] = round(time.time() - started, 1)
            returncode = process.wait(timeout=3600)
            status = _job_status_for_returncode(returncode)
        except Exception as error:  # noqa: BLE001 - job failure is reported to the UI, not raised
            status, returncode = "failed", -1
            chunks.append(f"\n{type(error).__name__}: {error}\n")
        finally:
            # A finished (or failed) job must leave NOTHING behind: kill any
            # still-running child, reap it, and close the pipe.
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                if process.stdout is not None:
                    process.stdout.close()
        with _JOBS_LOCK:
            job.update(
                {
                    "status": status,
                    "returncode": returncode,
                    "seconds": round(time.time() - started, 1),
                    "output_tail": "".join(chunks)[-12000:],
                }
            )
        _LAST_ACTION[name] = dict(job)
        _log(f"job finished: {job_id} :: {status} in {job['seconds']}s")
        _persist_jobs()
        with _CACHE_LOCK:
            _CACHE.clear()
        _ACTION_LOCK.release()

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "job_id": job_id, "command": job["command"]}


def job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            # Server may have restarted mid-job; answer from the on-disk record
            # instead of a bare 404 so the page can explain what happened.
            disk = _load_persisted_jobs().get(job_id)
            if disk:
                if disk.get("status") == "running":
                    disk["status"] = "interrupted"
                    disk["error"] = (
                        "the dashboard server restarted while this job was running; "
                        "the underlying CLI run may still have completed — check the ledger/summary"
                    )
                return disk
            return {"status": "unknown", "error": "no such job"}
        snapshot = {key: value for key, value in job.items() if key != "started_monotonic"}
        if snapshot["status"] == "running":
            snapshot["seconds"] = round(time.time() - job["started_monotonic"], 1)
        return snapshot


def _persist_jobs() -> None:
    try:
        DASH_DIR.mkdir(exist_ok=True)
        with _JOBS_LOCK:
            payload = {
                job_id: {k: v for k, v in job.items() if k != "started_monotonic"}
                for job_id, job in _JOBS.items()
            }
        JOBS_FILE.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass


def _load_persisted_jobs() -> dict:
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _hydrate_jobs() -> None:
    """Load persisted job history into memory at server start.

    _persist_jobs() serializes ONLY the in-memory _JOBS dict, so without
    this the first persist after a restart would overwrite jobs.json with
    just the post-restart jobs and wipe all history. A restart also means
    any job persisted as 'running' was interrupted (its thread is gone)
    and monotonic timestamps are meaningless across processes — normalize
    both rather than restore them as if they were still valid.
    """
    for job in _load_persisted_jobs().values():
        if job.get("status") == "running":
            job["status"] = "interrupted"
            job["error"] = (
                "the dashboard server restarted while this job was running; "
                "the underlying CLI run may still have completed — check the ledger/summary"
            )
        job.pop("started_monotonic", None)
        with _JOBS_LOCK:
            _JOBS[job.get("job_id", "")] = job


def _latest_persisted_action(action: str) -> dict | None:
    matches = [
        job
        for job in _load_persisted_jobs().values()
        if job.get("action") == action and job.get("status") != "running"
    ]
    if not matches:
        return None
    return max(matches, key=lambda job: str(job.get("started_at") or ""))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_REBUILD_VIEWS = {"status", "sports", "benchmark", "economics", "runs", "health", "shadow-picks"}
