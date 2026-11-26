"""auto_buyer.lock: the buyer's own writer lock, separate from daily.lock (ops 2026-11-25).

Holders are real separate processes where it matters (a lock held by another handle in this process
would also block on Windows, but a subprocess is what production looks like).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from model_prediction import auto_buyer_worker as worker
from model_prediction.daily_lock import acquire_lock

ROOT = Path(__file__).resolve().parents[1]

_HOLDER = """
import sys, time
sys.path.insert(0, {src!r})
from model_prediction.daily_lock import acquire_lock
handle = acquire_lock(sys.argv[1])
print("held" if handle else "busy", flush=True)
sys.stdin.read()
"""


@contextmanager
def _held_by_another_process(path: Path):
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(src=str(ROOT / "src")), str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def _is_held(path: Path) -> bool:
    probe = acquire_lock(path)
    if probe is None:
        return True
    probe.close()
    return False


@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(worker, "enable_background_priority", lambda: None)
    # Old and new names: the wait must be short whichever lock the worker asks for.
    monkeypatch.setattr(worker, "LOCK_WAIT_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(worker, "FORECAST_LOCK_WAIT_SECONDS", 0.5, raising=False)
    calls: dict[str, Any] = {"cycles": 0, "held_during_cycle": None}

    def cycle(**_kwargs):
        calls["cycles"] += 1
        calls["held_during_cycle"] = _is_held(tmp_path / "locks" / "auto_buyer.lock")
        return {"live_auto_buyer": {"status": "ok"}}

    monkeypatch.setattr(worker, "run_auto_buyer_cycle", cycle)
    monkeypatch.setattr(worker, "run_ncaab_spread_paper_trial", lambda: {"status": "ok"})
    if hasattr(worker, "run_nba_ml_v6_paper_trial"):
        monkeypatch.setattr(worker, "run_nba_ml_v6_paper_trial", lambda: {"status": "ok"})
    monkeypatch.setattr(worker, "_pretip_close_capture", lambda *_a: {"status": "skipped"})
    (tmp_path / "locks").mkdir()
    return tmp_path, calls


def test_worker_runs_while_the_daily_job_holds_daily_lock(worker_env, capsys):
    root, calls = worker_env
    with _held_by_another_process(root / "locks" / "daily.lock"):
        code = worker.main()
    assert code == 0
    assert calls["cycles"] == 1


def test_worker_holds_auto_buyer_lock_for_the_whole_cycle(worker_env):
    root, calls = worker_env
    assert worker.main() == 0
    assert calls["held_during_cycle"] is True
    assert not _is_held(root / "locks" / "auto_buyer.lock")


def test_worker_lock_wait_timeout_exits_75_and_runs_nothing(worker_env, capsys):
    root, calls = worker_env
    with _held_by_another_process(root / "locks" / "auto_buyer.lock"):
        code = worker.main()
    assert code == 75  # run_supervisor records `skipped`; system_health counts the streak
    assert calls["cycles"] == 0
    assert json.loads(capsys.readouterr().out.strip())["reason"] == "AUTO_BUYER_LOCK_BUSY"


# ------------------------------------------------------------------ daily steps 11b / 12 / 12b


def _daily_stubs(monkeypatch, record: list[str], lock_path: Path):
    from model_prediction.cli import daily
    from model_prediction.portfolio import auto_buyer_ledger

    monkeypatch.setattr("model_prediction.auto_buyer_lock.auto_buyer_lock_path", lambda *_a: lock_path)
    monkeypatch.setattr(daily, "_auto_buyer_cycle_owns_live_reconcile", lambda: False)
    monkeypatch.setattr(daily, "_daily_cancels_all_resting", lambda: False)
    monkeypatch.setattr(daily, "_auto_buyer_is_decoupled", lambda: False)

    def reconcile(*_a, **_k):
        record.append(f"reconcile held={_is_held(lock_path)}")
        return {}

    def settle(*_a, **_k):
        record.append(f"settle held={_is_held(lock_path)}")
        return {"status": "ok"}

    def step12(_date):
        record.append(f"cycle held={_is_held(lock_path)}")
        return {"status": "ok"}

    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", reconcile)
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", settle)
    monkeypatch.setattr(daily, "_run_auto_buyer_step12", step12)
    return daily


def test_daily_buyer_steps_run_under_auto_buyer_lock(monkeypatch, tmp_path):
    record: list[str] = []
    lock_path = tmp_path / "locks" / "auto_buyer.lock"
    daily = _daily_stubs(monkeypatch, record, lock_path)
    args = type("Args", (), {"skip_settlement": False, "date": "2026-11-25"})()
    laps: list[str] = []
    settle_result, cycle_result, reconcile_result = daily._run_auto_buyer_steps(args, tmp_path, laps.append)
    assert record == ["reconcile held=True", "settle held=True", "cycle held=True", "reconcile held=True"]
    assert settle_result == {"status": "ok"}
    assert cycle_result == {"status": "ok"}
    assert reconcile_result == {"status": "ok"}
    assert laps == ["auto_buyer_settle_11b", "auto_buyer_cycle_12", "fallback_reconcile_12b"]
    assert not _is_held(lock_path)


def test_daily_buyer_steps_skip_when_the_worker_holds_the_lock(monkeypatch, tmp_path):
    record: list[str] = []
    lock_path = tmp_path / "locks" / "auto_buyer.lock"
    daily = _daily_stubs(monkeypatch, record, lock_path)
    monkeypatch.setattr(daily, "AUTO_BUYER_LOCK_DAILY_STEPS_WAIT_SECONDS", 0.3)
    args = type("Args", (), {"skip_settlement": False, "date": "2026-11-25"})()
    lock_path.parent.mkdir(parents=True)
    with _held_by_another_process(lock_path):
        started = time.monotonic()
        results = daily._run_auto_buyer_steps(args, tmp_path, lambda _name: None)
    assert record == []
    assert all(r == {"status": "skipped", "reason": "auto_buyer_lock_busy"} for r in results)
    assert time.monotonic() - started < 60


# ------------------------------------------------------------------ cli settle


def test_cli_settle_rewrites_the_auto_buyer_ledger_only_under_the_lock(monkeypatch, tmp_path):
    from model_prediction.cli import settle as settle_cli
    from model_prediction.portfolio import auto_buyer_ledger

    lock_path = tmp_path / "locks" / "auto_buyer.lock"
    monkeypatch.setattr("model_prediction.auto_buyer_lock.auto_buyer_lock_path", lambda *_a: lock_path)
    seen: list[bool] = []
    monkeypatch.setattr(
        auto_buyer_ledger,
        "reconcile_pending_auto_buyer_fallbacks",
        lambda *_a, **_k: seen.append(_is_held(lock_path)) or {"reconciled": 0},
    )
    monkeypatch.setattr(
        auto_buyer_ledger,
        "settle_auto_buyer_ledger",
        lambda *_a, **_k: seen.append(_is_held(lock_path)) or {"settled": 0},
    )
    out = settle_cli._settle_auto_buyer_books(tmp_path)
    assert seen == [True, True]
    assert out == {
        "auto_buyer_fallback_reconciliation": {"reconciled": 0},
        "auto_buyer_settlement": {"settled": 0},
    }

    seen.clear()
    monkeypatch.setattr("model_prediction.auto_buyer_lock.AUTO_BUYER_LOCK_SETTLE_WAIT_SECONDS", 0.3)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _held_by_another_process(lock_path):
        out = settle_cli._settle_auto_buyer_books(tmp_path)
    assert seen == []
    assert out == {"auto_buyer_settlement": {"status": "skipped", "reason": "auto_buyer_lock_busy"}}


# ------------------------------------------------------------------ daily supervisor budget


def _recording_lock(waits: list[float | None]):
    @contextmanager
    def hold(wait_seconds: float | None = None, **_kwargs: Any):
        waits.append(wait_seconds)
        yield object()

    return hold


def test_daily_job_waits_are_short_and_fit_the_supervisor_timeout(monkeypatch, tmp_path):
    """The daily job can wait for auto_buyer.lock twice (cli settle, then steps 11b/12/12b). The old
    600 s each could push a long daily run past its 3,600 s supervisor timeout."""
    from model_prediction import auto_buyer_lock
    from model_prediction.cli import daily
    from model_prediction.cli import settle as settle_cli
    from model_prediction.portfolio import auto_buyer_ledger
    from model_prediction.run_supervisor import DEFAULT_WORKER_TIMEOUTS

    waits: list[float | None] = []
    monkeypatch.setattr(auto_buyer_lock, "hold_auto_buyer_lock", _recording_lock(waits))
    monkeypatch.setattr(daily, "hold_auto_buyer_lock", _recording_lock(waits))
    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda *_a, **_k: {})
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *_a, **_k: {})
    monkeypatch.setattr(daily, "_auto_buyer_cycle_owns_live_reconcile", lambda: False)
    monkeypatch.setattr(daily, "_daily_cancels_all_resting", lambda: False)
    monkeypatch.setattr(daily, "_auto_buyer_is_decoupled", lambda: False)
    monkeypatch.setattr(daily, "_run_auto_buyer_step12", lambda _date: {"status": "ok"})

    settle_cli._settle_auto_buyer_books(tmp_path)
    args = type("Args", (), {"skip_settlement": False, "date": "2026-11-25"})()
    daily._run_auto_buyer_steps(args, tmp_path, lambda _name: None)

    assert waits == [
        auto_buyer_lock.AUTO_BUYER_LOCK_SETTLE_WAIT_SECONDS,
        daily.AUTO_BUYER_LOCK_DAILY_STEPS_WAIT_SECONDS,
    ]
    assert auto_buyer_lock.AUTO_BUYER_LOCK_SETTLE_WAIT_SECONDS <= 120.0  # a skip there is safe
    assert daily.AUTO_BUYER_LOCK_DAILY_STEPS_WAIT_SECONDS < auto_buyer_lock.AUTO_BUYER_LOCK_WAIT_SECONDS
    # 50 minutes of daily work (the longest seen) plus both waits stays under the supervisor timeout.
    assert 50 * 60 + sum(waits) < DEFAULT_WORKER_TIMEOUTS["daily"]
    # The buyer worker keeps the full wait: it must not skip its cycle for a daily step.
    assert worker.LOCK_WAIT_SECONDS == auto_buyer_lock.AUTO_BUYER_LOCK_WAIT_SECONDS == 600.0


# ------------------------------------------------------------------ dashboard routes


def _post(routes, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    from model_prediction.dashboard.common import _DASHBOARD_TOKEN

    body = json.dumps(payload or {"confirm": True}).encode("utf-8")
    handler = routes.Handler.__new__(routes.Handler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(body)), "X-Dashboard-Token": _DASHBOARD_TOKEN}
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.do_POST()
    return handler.send_response.call_args[0][0], json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_dashboard_run_lock_is_the_buyer_lock_not_daily_lock(monkeypatch, tmp_path):
    from model_prediction.dashboard import routes

    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    assert routes._auto_buyer_run_lock_path() == tmp_path / "locks" / "auto_buyer.lock"


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/api/auto-buyer/settle?mode=live", "settle_auto_buyer_ledger"),
        ("/api/auto-buyer/ncaab-paper/run", None),
    ],
)
def test_dashboard_ledger_writes_refuse_while_the_buyer_lock_is_held(monkeypatch, tmp_path, path, target):
    from model_prediction.dashboard import routes
    from model_prediction.portfolio import ncaab_paper_trial

    lock_path = tmp_path / "locks" / "auto_buyer.lock"
    monkeypatch.setattr(routes, "_auto_buyer_run_lock_path", lambda: lock_path)
    ran: list[bool] = []
    if target:
        monkeypatch.setattr(routes, target, lambda **_k: ran.append(_is_held(lock_path)) or {"status": "ran"})
    else:
        monkeypatch.setattr(
            ncaab_paper_trial,
            "run_ncaab_spread_paper_trial",
            lambda: ran.append(_is_held(lock_path)) or {"status": "ran"},
        )
    lock_path.parent.mkdir(parents=True)
    with _held_by_another_process(lock_path):
        code, body = _post(routes, path)
    assert code == 409
    assert body["status"] == "refused"
    assert ran == []
    code, body = _post(routes, path)
    assert (code, body) == (200, {"status": "ran"})
    assert ran == [True]


# ------------------------------------------------------------------ state file (leaf lock)


def test_two_near_simultaneous_state_writers_never_both_win(monkeypatch, tmp_path):
    """Both load version 0; without the leaf lock both re-read 0 and both write version 1 (the second
    silently overwrites the first). With it, exactly one saves and the other gets a conflict."""
    from model_prediction.portfolio import auto_executor

    path = tmp_path / "auto_buyer_state.json"
    real_read = auto_executor.read_state_file_version

    def slow_read(p):
        version = real_read(p)
        time.sleep(0.4)  # widen the read -> replace window
        return version

    monkeypatch.setattr(auto_executor, "read_state_file_version", slow_read)
    outcomes: dict[str, str] = {}

    def save(name: str) -> None:
        state = {"enabled": name == "kill", "writer": name, auto_executor.STATE_VERSION_KEY: 0}
        try:
            auto_executor.write_versioned_state_file(path, state, temp_path=tmp_path / f"{name}.tmp")
            outcomes[name] = "saved"
        except auto_executor.StateVersionConflictError:
            outcomes[name] = "conflict"

    threads = [threading.Thread(target=save, args=(n,)) for n in ("cycle", "kill")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes.values()) == ["conflict", "saved"]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    winner = next(n for n, o in outcomes.items() if o == "saved")
    assert on_disk["writer"] == winner
    assert on_disk[auto_executor.STATE_VERSION_KEY] == 1


# ------------------------------------------------------------------ concurrent ledger writers

_LEDGER_WRITER = """
import json, sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from model_prediction.auto_buyer_lock import hold_auto_buyer_lock
lock, ledger, name, n = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
for i in range(n):
    with hold_auto_buyer_lock(60, path=lock) as held:
        assert held is not None
        rows = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        time.sleep(0.05)  # a settle reads, calls the network, then rewrites
        rows.append(json.dumps({{"writer": name, "i": i}}))
        tmp = ledger.with_name(ledger.name + "." + name + ".tmp")
        tmp.write_text("\\n".join(rows) + "\\n", encoding="utf-8")
        tmp.replace(ledger)
"""


def test_concurrent_read_modify_write_ledger_writers_lose_nothing_under_the_lock(tmp_path):
    lock, ledger = tmp_path / "locks" / "auto_buyer.lock", tmp_path / "auto_buyer_ledger.jsonl"
    script = _LEDGER_WRITER.format(src=str(ROOT / "src"))
    procs = [
        subprocess.Popen([sys.executable, "-c", script, str(lock), str(ledger), name, "8"])
        for name in ("worker", "daily")
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 16
    assert sorted((r["writer"], r["i"]) for r in rows) == sorted(
        (name, i) for name in ("worker", "daily") for i in range(8)
    )
