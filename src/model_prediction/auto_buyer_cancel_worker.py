"""Supervisor worker ``auto-buyer-cancel``: the cancel-only backstop for live resting fallbacks.

Scheduled every 2 minutes from 08:00 for 18 hours (``scripts/register_windows_tasks.ps1``). A live
cohort buy whose IOC leg fills only in part rests the remainder as a GOOD_TILL_DATE order that the
exchange expires at the event start minus 90 s (quick-261125-hih D-01). That expiry is the primary
mechanism; this worker is the backstop for an exchange that did not honour it and for the kill switch
(D-02):

- live trading on (``enabled`` true and ``live_enabled`` true or absent): cancel, by order id, every
  unreconciled live fallback whose event starts within ``START_LEAD_SECONDS`` (the operator's 30 s
  spec);
- buyer disabled, ``live_enabled`` false, or the state file missing or unreadable: cancel every
  unreconciled live fallback.

It only cancels. It never places an order, never calls a cancel-all or batch endpoint, and never
writes the ledger: there is no ledger lock, so restating fills stays with the reconcile that runs under
auto_buyer.lock. It takes its OWN lock (auto_buyer_cancel.lock), neither of the buyer's two, because
a daily job or a buyer cycle can hold those for up to ~50 minutes and the backstop has to keep running through it. With nothing due it builds no executor
and makes no network call.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .daily_lock import acquire_lock, release_lock
from .domain import iso_utc, parse_utc, utc_now
from .runtime_paths import RuntimePaths
from .stealth_runtime import enable_background_priority

LOCK_FILE_NAME = "auto_buyer_cancel.lock"
MEMO_FILE_NAME = "auto_buyer_cancel_worker_memo.json"
START_LEAD_SECONDS = 30.0
# Ids this worker already cancelled or found terminal are never re-sent (the gateway's rate limit is
# shared with the buyer). Three days outlives any fallback, which expires at its game's start.
MEMO_RETENTION = timedelta(days=3)
# One failed read of the state file (a sharing violation, or a hand edit caught mid-save) must not
# read as the kill switch and cancel every resting fallback; only a persistent failure does.
STATE_READ_ATTEMPTS = 3
STATE_READ_RETRY_SECONDS = 0.3
# One exchange GET or cancel can take about 46 s (15 s timeout x 3 attempts plus backoff). The deadline
# is only checked BEFORE a call starts, so a call begun just under it still runs to its end: the
# deadline plus one full call must stay inside the supervisor's 115 s kill
# (run_supervisor.DEFAULT_WORKER_TIMEOUTS). Ids not reached are retried on the next 2-minute run.
MAX_EXCHANGE_CALL_SECONDS = 46.0
RUN_DEADLINE_SECONDS = 60.0
_sleep = time.sleep
_monotonic = time.monotonic


def _buyer_enabled(state_path: Path) -> bool:
    """Whether live fallbacks may stand: ``enabled`` and, if present, ``live_enabled`` are the
    boolean true in the canonical runtime state file (``live_switch_on``).

    Read directly rather than through ``load_auto_buyer_state``: that loader falls back to the repo
    seed file when the runtime file is missing, and this worker must fail toward cancelling. A read
    that fails is retried; a file that stays missing or unreadable counts as disabled.
    """
    from .portfolio.auto_buyer_ledger import live_switch_on

    for attempt in range(STATE_READ_ATTEMPTS):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if attempt + 1 < STATE_READ_ATTEMPTS:
                _sleep(STATE_READ_RETRY_SECONDS)
            continue
        return isinstance(data, dict) and live_switch_on(data)
    return False


def _load_memo(path: Path, now: datetime) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("order_ids") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, dict):
        return {}
    kept: dict[str, str] = {}
    for order_id, stamp in entries.items():
        try:
            if now - parse_utc(str(stamp)) <= MEMO_RETENTION:
                kept[str(order_id)] = str(stamp)
        except (TypeError, ValueError):
            continue
    return kept


def _save_memo(path: Path, memo: dict[str, str]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"order_ids": memo}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _run(paths: RuntimePaths) -> int:
    from .portfolio import auto_buyer_ledger

    deadline = _monotonic() + RUN_DEADLINE_SECONDS
    now = utc_now()
    root = paths.runtime_root
    enabled = _buyer_enabled(root / "auto_buyer_state.json")
    reason = "backstop_tipoff" if enabled else "backstop_kill_switch"
    memo_path = root / MEMO_FILE_NAME
    memo = _load_memo(memo_path, now)
    try:
        summary: dict[str, Any] = auto_buyer_ledger.cancel_resting_live_fallbacks(
            data_root=root,
            now=now,
            cancel_all=not enabled,
            start_lead_seconds=START_LEAD_SECONDS,
            skip_order_ids=frozenset(memo),
            reason=reason,
            stop_before=lambda: _monotonic() >= deadline,
        )
    except Exception as exc:  # noqa: BLE001 -- reported as a failed run, never a silent pass
        error = f"{type(exc).__name__}: {exc}"[:300]
        print(
            json.dumps({"status": "error", "enabled": enabled, "reason": reason, "error": error}), flush=True
        )
        return 1
    done = [*summary.get("cancelled", []), *summary.get("already_terminal", [])]
    result: dict[str, Any] = {"enabled": enabled, **summary}
    if summary.get("deadline_skipped"):
        result["note"] = "deadline"
    if done:
        memo.update({str(order_id): iso_utc(now) for order_id in done})
        try:
            _save_memo(memo_path, memo)
        except OSError as exc:
            # Benign: the next run reads these ids back as terminal before it would cancel again.
            result["memo_error"] = f"{type(exc).__name__}: {exc}"[:300]
    # A deadline skip while the buyer is off, or of a game that has already started, leaves a live
    # order resting that should be gone: the supervisor and system health must see a failed run.
    skipped_unsafe = bool(summary.get("deadline_skipped")) and (
        not enabled or bool(summary.get("deadline_skipped_started"))
    )
    failed = bool(summary.get("failed")) or skipped_unsafe
    print(json.dumps({"status": "error" if failed else "ok", **result}, default=str), flush=True)
    return 1 if failed else 0


def main() -> int:
    enable_background_priority()
    # Operational entry point: fail closed without MODEL_PREDICTION_RUNTIME_ROOT, never repo data/.
    paths = RuntimePaths.resolve(require_external_runtime=True)
    lock = acquire_lock(paths.lock_root / LOCK_FILE_NAME)
    if lock is None:
        print(json.dumps({"status": "skipped", "reason": "AUTO_BUYER_CANCEL_ALREADY_RUNNING"}), flush=True)
        return 0
    try:
        return _run(paths)
    finally:
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())
