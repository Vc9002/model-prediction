"""The cancel-only backstop worker and the governed manual buyer script (quick-261125-hih Task 3).

Nothing here can reach the exchange. The worker builds its executor only through
``auto_buyer_ledger._default_live_executor``, which every test replaces: either with ``_Strict``,
which records calls and raises on anything but a per-id ``order_snapshots`` read and a per-id
``cancel``, or with a factory that fails the test if it is called at all. ``httpx.request`` raises.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from model_prediction import auto_buyer_cancel_worker as worker
from model_prediction.daily_lock import acquire_lock, release_lock
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger
from model_prediction.run_supervisor import _VENV_PYTHON, DEFAULT_WORKER_TIMEOUTS, WORKERS

ROOT = Path(__file__).resolve().parents[1]


class _Strict:
    """A per-id exchange: order_snapshots and cancel only. Any other attribute fails the test."""

    def __init__(self, terminal: tuple[str, ...] = (), fail_cancel: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.terminal = set(terminal)
        self.fail_cancel = set(fail_cancel)

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        self.calls.append(("order_snapshots", list(order_ids)))
        return {
            "status": "live",
            "orders": [
                {
                    "order_id": oid,
                    "order_state": "ORDER_STATE_EXPIRED" if oid in self.terminal else "ORDER_STATE_NEW",
                    "cum_quantity": 0.0,
                }
                for oid in order_ids
            ],
        }

    def cancel(self, order_id: str, *, user_command: bool, market_slug: str | None = None) -> dict[str, Any]:
        assert user_command is True
        self.calls.append(("cancel", order_id))
        if order_id in self.fail_cancel:
            raise RuntimeError("exchange said no")
        return {"status": "cancelled", "order_id": order_id}

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the backstop may only read and cancel by id, not call {name}")

    @property
    def cancelled(self) -> list[str]:
        return [arg for name, arg in self.calls if name == "cancel"]


def _never_built(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("no executor may be built (and no network call made) when nothing is due")


def _row(fid: str, *, start_seconds: float, order_id: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "order_id": order_id or f"ioc-{fid}",
        "order_ids": [order_id or f"ioc-{fid}"],
        "pick_id": f"pick-{fid}",
        "event_start_utc": iso_utc(utc_now() + timedelta(seconds=start_seconds)),
        "market_slug": f"asc-cbb-aaa-bbb-2026-11-25-pos-3pt5-{fid}",
        "fallback_order_id": fid,
        "fallback_resting_shares": 5.0,
        "fallback_reconciled": False,
        "status": "open",
        "result": "open",
        "shares": 2.0,
        "cost_usd": 1.0,
        **extra,
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(root))
    # The worker demotes its own process; never this test process.
    monkeypatch.setattr(worker, "enable_background_priority", lambda *a, **k: True)

    def _no_http(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("tests must never reach the network")

    monkeypatch.setattr(httpx, "request", _no_http)
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", _never_built)
    return root


def _write(root: Path, rows: list[dict[str, Any]], *, enabled: Any = True) -> Path:
    ledger = root / "auto_buyer_ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if enabled is not None:
        (root / "auto_buyer_state.json").write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    return ledger


def _run(capsys) -> tuple[int, dict[str, Any]]:
    code = worker.main()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return code, json.loads(lines[-1])


def _exchange(monkeypatch, **kwargs: Any) -> _Strict:
    exchange = _Strict(**kwargs)
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    return exchange


# ---------------------------------------------------------------- worker


def test_nothing_due_builds_no_executor_and_exits_zero(runtime, capsys):
    ledger = _write(runtime, [_row("gtd-1", start_seconds=600.0)])
    before = ledger.read_bytes()
    code, out = _run(capsys)
    assert code == 0
    assert out["status"] == "ok" and out["candidates"] == 0 and out["enabled"] is True
    assert ledger.read_bytes() == before


def test_due_fallback_is_cancelled_by_id_and_the_ledger_is_untouched(runtime, monkeypatch, capsys):
    ledger = _write(
        runtime,
        [_row("gtd-1", start_seconds=20.0), _row("gtd-2", start_seconds=600.0)],
    )
    before = ledger.read_bytes()
    exchange = _exchange(monkeypatch)
    code, out = _run(capsys)
    assert code == 0
    assert exchange.cancelled == ["gtd-1"]
    assert exchange.calls == [("order_snapshots", ["gtd-1"]), ("cancel", "gtd-1")]
    assert out["cancelled"] == ["gtd-1"] and out["reason"] == "backstop_tipoff"
    assert ledger.read_bytes() == before


def test_disabled_buyer_cancels_every_pending_fallback(runtime, monkeypatch, capsys):
    _write(
        runtime,
        [_row("gtd-1", start_seconds=36000.0), _row("gtd-2", start_seconds=600.0)],
        enabled=False,
    )
    exchange = _exchange(monkeypatch)
    code, out = _run(capsys)
    assert code == 0
    assert sorted(exchange.cancelled) == ["gtd-1", "gtd-2"]
    assert out["reason"] == "backstop_kill_switch" and out["enabled"] is False


@pytest.mark.parametrize("state_text", [None, "{not json", json.dumps({"enabled": "true"}), json.dumps([1])])
def test_missing_or_unreadable_state_counts_as_disabled(runtime, monkeypatch, capsys, state_text):
    _write(runtime, [_row("gtd-1", start_seconds=36000.0)], enabled=None)
    if state_text is not None:
        (runtime / "auto_buyer_state.json").write_text(state_text, encoding="utf-8")
    exchange = _exchange(monkeypatch)
    code, out = _run(capsys)
    assert code == 0
    assert exchange.cancelled == ["gtd-1"]
    assert out["enabled"] is False


def test_memo_stops_a_resend_on_the_next_run(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0), _row("gtd-2", start_seconds=10.0)])
    exchange = _exchange(monkeypatch, terminal=("gtd-2",))
    code, out = _run(capsys)
    assert code == 0
    assert exchange.cancelled == ["gtd-1"] and out["already_terminal"] == ["gtd-2"]
    memo = json.loads((runtime / worker.MEMO_FILE_NAME).read_text(encoding="utf-8"))
    assert set(memo["order_ids"]) == {"gtd-1", "gtd-2"}
    # Second run: both ids are memoised, so nothing is due and no executor is built.
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", _never_built)
    code, out = _run(capsys)
    assert code == 0 and out["candidates"] == 0


def test_corrupt_memo_counts_as_empty(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])
    (runtime / worker.MEMO_FILE_NAME).write_text("{broken", encoding="utf-8")
    exchange = _exchange(monkeypatch)
    code, _out = _run(capsys)
    assert code == 0 and exchange.cancelled == ["gtd-1"]


def test_memo_prunes_entries_older_than_three_days(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])
    old = iso_utc(utc_now() - timedelta(days=4))
    (runtime / worker.MEMO_FILE_NAME).write_text(
        json.dumps({"order_ids": {"gtd-1": old, "gtd-9": iso_utc(utc_now())}}), encoding="utf-8"
    )
    exchange = _exchange(monkeypatch)
    code, _out = _run(capsys)
    assert code == 0 and exchange.cancelled == ["gtd-1"]
    memo = json.loads((runtime / worker.MEMO_FILE_NAME).read_text(encoding="utf-8"))
    assert set(memo["order_ids"]) == {"gtd-1", "gtd-9"}
    assert memo["order_ids"]["gtd-1"] != old


def test_failed_cancel_exits_one_and_is_not_memoised(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0), _row("gtd-2", start_seconds=20.0)])
    exchange = _exchange(monkeypatch, fail_cancel=("gtd-1",))
    code, out = _run(capsys)
    assert code == 1
    assert out["status"] == "error" and [f["order_id"] for f in out["failed"]] == ["gtd-1"]
    assert exchange.cancelled == ["gtd-1", "gtd-2"]
    memo = json.loads((runtime / worker.MEMO_FILE_NAME).read_text(encoding="utf-8"))
    assert set(memo["order_ids"]) == {"gtd-2"}
    # The failed id is retried on the next run.
    retry = _exchange(monkeypatch)
    code, _out = _run(capsys)
    assert code == 0 and retry.cancelled == ["gtd-1"]


def test_cancel_helper_failure_exits_one(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])

    def boom(_root):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", boom)
    code, out = _run(capsys)
    assert code == 1 and out["status"] == "error" and "no credentials" in out["error"]


def test_own_lock_held_skips_and_exits_zero(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])

    def must_not_run(**_k: Any) -> Any:
        raise AssertionError("the worker must skip while its own lock is held")

    monkeypatch.setattr(auto_buyer_ledger, "cancel_resting_live_fallbacks", must_not_run)
    held = acquire_lock(runtime / "locks" / worker.LOCK_FILE_NAME)
    assert held is not None
    try:
        code, out = _run(capsys)
    finally:
        release_lock(held)
    assert code == 0 and out["status"] == "skipped"


def test_daily_lock_held_does_not_stop_the_backstop(runtime, monkeypatch, capsys):
    """The daily job holds daily.lock for 17-49 minutes; the backstop must keep running through it."""
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])
    exchange = _exchange(monkeypatch)
    held = acquire_lock(runtime / "locks" / "daily.lock")
    assert held is not None
    try:
        code, _out = _run(capsys)
    finally:
        release_lock(held)
    assert code == 0 and exchange.cancelled == ["gtd-1"]
    assert worker.LOCK_FILE_NAME != "daily.lock"


def test_missing_runtime_root_fails_closed_without_reading_the_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("MODEL_PREDICTION_SKIP_DOTENV", "1")
    monkeypatch.setattr(worker, "enable_background_priority", lambda *a, **k: True)

    def must_not_read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the ledger must not be read without an explicit runtime root")

    monkeypatch.setattr(auto_buyer_ledger, "read_auto_buyer_ledger", must_not_read)
    monkeypatch.setattr(auto_buyer_ledger, "cancel_resting_live_fallbacks", must_not_read)
    with pytest.raises(RuntimeError, match="MODEL_PREDICTION_RUNTIME_ROOT"):
        worker.main()


# ---------------------------------------------------------------- scheduling


def test_supervisor_registers_the_backstop_worker():
    assert WORKERS["auto-buyer-cancel"] == [_VENV_PYTHON, "-m", "model_prediction.auto_buyer_cancel_worker"]
    # The worker stops starting cancels at its own deadline, well inside the supervisor's kill.
    assert DEFAULT_WORKER_TIMEOUTS["auto-buyer-cancel"] == 115.0
    # The deadline is only checked BEFORE a call starts, so the run can end one full exchange call
    # (15 s timeout x 3 attempts plus backoff) after it: the real bound is deadline + one call.
    assert (
        worker.RUN_DEADLINE_SECONDS + worker.MAX_EXCHANGE_CALL_SECONDS
        < DEFAULT_WORKER_TIMEOUTS["auto-buyer-cancel"]
    )


def test_windows_task_script_registers_the_backstop_every_two_minutes():
    script = (ROOT / "scripts" / "register_windows_tasks.ps1").read_text(encoding="utf-8")
    assert re.search(
        r"Register-WorkerTask 'ModelPrediction-AutoBuyerCancel' 'auto-buyer-cancel' \$autoBuyerCancelTrigger",
        script,
    )
    block = script[script.index("$autoBuyerCancelTrigger = ") :]
    assert "-Daily -At '08:00'" in block.splitlines()[0]
    repetition = block.splitlines()[1]
    assert "-RepetitionInterval (New-TimeSpan -Minutes 2)" in repetition
    assert "-RepetitionDuration (New-TimeSpan -Hours 18)" in repetition
    # Re-running the whole script must keep the live S4U principal of every worker task.
    assert re.search(r"\[string\]\$LogonType = 'S4U'", script)


def test_standalone_registration_snippet_registers_only_the_backstop():
    snippet = (ROOT / "scripts" / "register_auto_buyer_cancel_task.ps1").read_text(encoding="utf-8")
    assert len(re.findall(r"(?<!Un)Register-ScheduledTask ", snippet)) == 1
    assert "$NewTaskName = 'ModelPrediction-AutoBuyerCancel'" in snippet
    assert "Register-ScheduledTask -TaskName $NewTaskName" in snippet
    assert "Get-ScheduledTask -TaskName 'ModelPrediction-AutoBuyer'" in snippet
    assert "run auto-buyer-cancel" in snippet
    assert "Start-ScheduledTask" not in snippet
    assert "Unregister-ScheduledTask -TaskName 'ModelPrediction-AutoBuyer'" not in snippet
    # A non-admin S4U run must stop before it changes any task.
    guard = re.search(_ADMIN_GUARD.format(cond=r"-not "), snippet, re.MULTILINE)
    assert guard and guard.start() < snippet.index("Register-ScheduledTask -TaskName")
    # Replace in one call: a registration that fails must never leave the backstop deleted.
    assert "Unregister-ScheduledTask" not in snippet
    assert re.search(r"Register-ScheduledTask -TaskName \$NewTaskName[^|]*-Force\b", snippet)
    # The copied settings would carry the source task's Enabled flag; the backstop is always enabled.
    assert "$settings.Enabled = $true" in snippet
    assert "-Settings $settings" in snippet


# The guard must be a live `if (<cond>(... IsInRole(Administrator))) { throw ... }`, not a mention.
_ADMIN_GUARD = (
    r"^if \({cond}\(\[Security\.Principal\.WindowsPrincipal\]\[Security\.Principal\.WindowsIdentity\]"
    r"::GetCurrent\(\)\)\.IsInRole\(\[Security\.Principal\.WindowsBuiltInRole\]::Administrator\)\) \{{\n"
    r"    throw "
)


def test_full_registration_script_checks_admin_before_touching_any_task():
    script = (ROOT / "scripts" / "register_windows_tasks.ps1").read_text(encoding="utf-8")
    guard = re.search(_ADMIN_GUARD.format(cond=r"\$LogonType -eq 'S4U' -and -not "), script, re.MULTILINE)
    assert guard
    assert guard.start() < script.index("Register-WorkerTask '")
    assert guard.start() < script.index("Unregister-ScheduledTask -TaskName 'ModelPrediction-BackupOffsite'")


def test_health_has_a_skip_escalation_for_the_backstop():
    from model_prediction.system_health import _SKIP_ESCALATION

    assert "auto-buyer-cancel" in _SKIP_ESCALATION


# ---------------------------------------------------------------- governed manual script (C3)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "auto_polymarket_buyer_script", ROOT / "scripts" / "auto_polymarket_buyer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    total_evaluated = 0
    whitelisted_count = 0
    rejected_blacklist = 0
    rejected_low_edge = 0
    rejected_started = 0
    rejected_budget = 0
    rejected_dedup = 0
    total_spend_usd = 0.0
    submitted_orders: ClassVar[list[dict[str, Any]]] = []
    dry_run_orders: ClassVar[list[dict[str, Any]]] = []


@pytest.fixture
def script(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(root))
    module = _load_script()
    built: list[dict[str, Any]] = []

    def config(**kwargs: Any) -> dict[str, Any]:
        built.append(kwargs)
        return kwargs

    class Buyer:
        def __init__(self, config: Any) -> None:
            self.config = config

        def evaluate_and_execute(self) -> _FakeResult:
            # While a live run is under way it must hold auto_buyer.lock (the scheduled worker's lock).
            probe = acquire_lock(root / "locks" / "auto_buyer.lock")
            module.LOCK_PROBES.append(probe is None)
            release_lock(probe)
            return _FakeResult()

    monkeypatch.setattr(module, "AutoExecutionConfig", config)
    monkeypatch.setattr(module, "AutoPolymarketBuyer", Buyer)
    module.LOCK_PROBES = []
    module.BUILT = built
    module.ROOT = root
    return module


@pytest.mark.parametrize(
    "state",
    [
        {"enabled": True, "cohorts": []},
        {"enabled": True, "cohorts": [{"model_id": "x"}]},
        {"enabled": False},
        {"enabled": "true"},
        {},
    ],
)
def test_execute_refuses_under_cohorts_or_unless_enabled_is_true(script, monkeypatch, capsys, state):
    monkeypatch.setattr(script, "load_auto_buyer_state", lambda: dict(state))
    code = script.main(["--execute"])
    assert code != 0
    assert script.BUILT == []
    assert "REFUSED" in capsys.readouterr().err


def test_execute_refuses_while_the_buyer_lock_is_held(script, monkeypatch, capsys):
    monkeypatch.setattr(script, "load_auto_buyer_state", lambda: {"enabled": True})
    held = acquire_lock(script.ROOT / "locks" / "auto_buyer.lock")
    try:
        code = script.main(["--execute"])
    finally:
        release_lock(held)
    assert code != 0 and script.BUILT == []
    assert "auto_buyer.lock" in capsys.readouterr().err


def test_allowed_execute_runs_under_the_buyer_lock(script, monkeypatch):
    monkeypatch.setattr(script, "load_auto_buyer_state", lambda: {"enabled": True})
    assert script.main(["--execute", "--json"]) == 0
    assert script.BUILT[0]["execute_live"] is True
    assert script.LOCK_PROBES == [True]
    # Released afterwards.
    probe = acquire_lock(script.ROOT / "locks" / "auto_buyer.lock")
    assert probe is not None
    release_lock(probe)


def test_dry_run_is_unchanged_under_cohorts(script, monkeypatch):
    monkeypatch.setattr(script, "load_auto_buyer_state", lambda: {"enabled": True, "cohorts": []})
    assert script.main(["--json"]) == 0
    assert script.BUILT[0]["execute_live"] is False
    assert script.LOCK_PROBES == [False]


# ---------------------------------------------------------------- review fixes (Task 3 review)


def test_transient_state_read_failure_is_retried_before_cancelling_all(runtime, monkeypatch, capsys):
    """A half-written hand edit or a sharing violation must not read as the kill switch."""
    _write(runtime, [_row("gtd-1", start_seconds=36000.0)], enabled=None)
    state = runtime / "auto_buyer_state.json"
    state.write_text('{"enabled": tr', encoding="utf-8")
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        state.write_text(json.dumps({"enabled": True}), encoding="utf-8")

    monkeypatch.setattr(worker, "_sleep", fake_sleep)
    code, out = _run(capsys)
    assert code == 0
    assert out["enabled"] is True and out["candidates"] == 0
    assert len(sleeps) == 1


def test_persistently_unreadable_state_is_retried_then_cancels_all(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=36000.0)], enabled=None)
    (runtime / "auto_buyer_state.json").write_text("{broken", encoding="utf-8")
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "_sleep", sleeps.append)
    exchange = _exchange(monkeypatch)
    code, out = _run(capsys)
    assert code == 0
    assert out["enabled"] is False and exchange.cancelled == ["gtd-1"]
    assert len(sleeps) == worker.STATE_READ_ATTEMPTS - 1
    assert all(0.1 <= s <= 0.5 for s in sleeps)


def test_run_deadline_stops_new_cancels_and_exits_cleanly(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0), _row("gtd-2", start_seconds=25.0)])
    exchange = _exchange(monkeypatch)
    # Clock reads: run start, before the order read, before cancel 1, before cancel 2.
    ticks = iter([0.0, 10.0, 20.0, 100.0, 110.0, 120.0])
    monkeypatch.setattr(worker, "_monotonic", lambda: next(ticks))
    code, out = _run(capsys)
    assert code == 0
    assert exchange.cancelled == ["gtd-1"]
    assert out["deadline_skipped"] == ["gtd-2"] and out["note"] == "deadline"
    memo = json.loads((runtime / worker.MEMO_FILE_NAME).read_text(encoding="utf-8"))
    assert set(memo["order_ids"]) == {"gtd-1"}


def test_deadline_before_the_order_read_sends_nothing(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0)])
    exchange = _exchange(monkeypatch)
    ticks = iter([0.0, 100.0, 110.0, 120.0])
    monkeypatch.setattr(worker, "_monotonic", lambda: next(ticks))
    code, out = _run(capsys)
    assert code == 0 and exchange.calls == []
    assert out["deadline_skipped"] == ["gtd-1"]


def _deadline_after_first(monkeypatch) -> None:
    # Clock reads: run start, before the order read, before cancel 1, before cancel 2.
    ticks = iter([0.0, 10.0, 20.0, 100.0, 110.0, 120.0])
    monkeypatch.setattr(worker, "_monotonic", lambda: next(ticks))


def test_deadline_skip_of_a_started_game_exits_non_zero(runtime, monkeypatch, capsys):
    # gtd-2's game began 10 s ago and its fallback is still resting: skipping it must be visible.
    _write(runtime, [_row("gtd-1", start_seconds=-20.0), _row("gtd-2", start_seconds=-10.0)])
    exchange = _exchange(monkeypatch)
    _deadline_after_first(monkeypatch)
    code, out = _run(capsys)
    assert exchange.cancelled == ["gtd-1"]
    assert out["deadline_skipped"] == ["gtd-2"] and out["deadline_skipped_started"] == ["gtd-2"]
    assert code == 1 and out["status"] == "error"


def test_deadline_skip_under_the_kill_switch_exits_non_zero(runtime, monkeypatch, capsys):
    _write(
        runtime,
        [_row("gtd-1", start_seconds=600.0), _row("gtd-2", start_seconds=600.0)],
        enabled=False,
    )
    exchange = _exchange(monkeypatch)
    _deadline_after_first(monkeypatch)
    code, out = _run(capsys)
    assert exchange.cancelled == ["gtd-1"]
    assert out["deadline_skipped"] == ["gtd-2"]
    assert code == 1 and out["status"] == "error"


def test_deadline_skip_of_a_game_not_yet_started_stays_ok_while_enabled(runtime, monkeypatch, capsys):
    _write(runtime, [_row("gtd-1", start_seconds=20.0), _row("gtd-2", start_seconds=25.0)])
    _exchange(monkeypatch)
    _deadline_after_first(monkeypatch)
    code, out = _run(capsys)
    assert code == 0 and out["status"] == "ok" and out["deadline_skipped"] == ["gtd-2"]
