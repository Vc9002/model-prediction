"""The Auto-Buyer's own writer lock, separate from the daily job's daily.lock.

Until 2026-11-25 the 15-minute buyer worker waited on daily.lock, which the daily job holds for its
whole 20-50 minute run (settle, ingest, calibrate, forecast). A long daily run blocked live settlement
and buys for tips inside that window, and Task Scheduler's IgnoreNew then dropped the next trigger.

This lock guards only what the buyer and the daily job actually share: the Auto-Buyer ledgers
(live and paper ``auto_buyer_ledger.jsonl`` / ``auto_buyer_picks.xlsx``), the paper-trial ledgers,
the cycle history, and every buy pass (dedup and the daily/game caps read the ledger, then orders are
placed and recorded). Holders take it at their entry points and hold it for the whole operation:

- the scheduled buyer worker (``auto_buyer_worker``), for the whole cycle;
- the daily job, around its steps 11b (reconcile + settle), 12 (buy cycle) and 12b (reconcile);
- ``cli settle --all-unsettled``, around its Auto-Buyer reconcile + settle block;
- the dashboard's manual run, settle and NCAAB paper-trial run routes (non-blocking, 409 when busy).

Lock order (every acquisition follows it; nothing acquires a lock to its left while holding one to
its right, and every wait is bounded):

    supervisor lease  ->  daily.lock  ->  auto_buyer.lock  ->  state-file lock

The daily job holds daily.lock and briefly takes auto_buyer.lock; the buyer worker holds only its
supervisor lease and auto_buyer.lock and never asks for daily.lock; the state-file lock
(``auto_executor.write_versioned_state_file``) is a leaf held for one compare-and-replace, so the
dashboard kill switch never queues behind a multi-minute cycle. No cycle in the wait-for graph, so no
deadlock; a bounded wait turns any unforeseen contention into a visible skip instead of a hang.

The lock is not re-entrant: a second handle in the same process blocks on Windows (msvcrt byte-range
locks belong to the handle). Take it only at entry points, never inside ``run_auto_buyer_cycle`` or
the ledger functions it calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from .daily_lock import LOCK_BUSY_EXIT, acquire_lock, release_lock
from .runtime_paths import RuntimePaths

__all__ = [
    "AUTO_BUYER_LOCK_NAME",
    "AUTO_BUYER_LOCK_WAIT_SECONDS",
    "LOCK_BUSY_EXIT",
    "auto_buyer_lock_path",
    "hold_auto_buyer_lock",
]

AUTO_BUYER_LOCK_NAME = "auto_buyer.lock"
# A buyer cycle ran 60-460 s on 2026-11-25; the daily job's three buyer steps are one cycle plus two
# reconciles. 600 s covers either holder, and wait + cycle stays inside the worker's 15-minute cadence
# far more often than the old 1,200 s daily.lock wait did.
AUTO_BUYER_LOCK_WAIT_SECONDS = 600.0


def auto_buyer_lock_path(paths: RuntimePaths | None = None) -> Path:
    """``<runtime_root>/locks/auto_buyer.lock`` (repo ``data/locks`` without a runtime root)."""
    resolved = paths if paths is not None else RuntimePaths.resolve(require_external_runtime=False)
    return resolved.lock_root / AUTO_BUYER_LOCK_NAME


@contextmanager
def hold_auto_buyer_lock(
    wait_seconds: float | None = None, *, path: Path | None = None
) -> Iterator[IO[str] | None]:
    """Yield the held lock handle, or None when another holder kept it past ``wait_seconds``
    (default AUTO_BUYER_LOCK_WAIT_SECONDS). The caller must skip its Auto-Buyer work on None; the
    handle is released on exit either way.
    """
    wait = AUTO_BUYER_LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    handle = acquire_lock(path if path is not None else auto_buyer_lock_path(), wait_seconds=wait)
    try:
        yield handle
    finally:
        release_lock(handle)
