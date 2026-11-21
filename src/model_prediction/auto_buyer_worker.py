"""Short, independently scheduled Auto-Buyer cycle.

The daily forecast is intentionally long-running.  This worker only settles
paper positions, refreshes active market quotes, and evaluates the already
generated candidate rows, so purchase timing is no longer gated by forecast
runtime.
"""

from __future__ import annotations

import json
import time
from typing import Any
from zoneinfo import ZoneInfo

from .daily_lock import acquire_lock
from .domain import utc_now
from .portfolio.auto_executor import run_auto_buyer_cycle
from .portfolio.ncaab_paper_trial import run_ncaab_spread_paper_trial
from .pretip_close_capture import capture_pretip_closes
from .runtime_paths import RuntimePaths
from .stealth_runtime import enable_background_priority

# The forecast holds the writer lock for 13-38 minutes and this worker starts
# ten minutes after it. Skipping outright meant picks written by a refresh waited
# for a later slot before any buyer saw them -- every post-forecast cycle from
# 2026-11-09 to 11-11 was a skip. Waiting runs the cycle the moment the forecast
# releases the lock; the bound keeps it clear of the next 30-minute trigger.
FORECAST_LOCK_WAIT_SECONDS = 1200.0

# The pre-tip close capture runs after the buys and must finish well inside the supervisor's
# auto-buyer timeout (run_supervisor.DEFAULT_WORKER_TIMEOUTS["auto-buyer"], 1500 s, which
# includes the lock wait above); a killed worker would read as a failed buyer run.
PRETIP_CAPTURE_MAX_SECONDS = 120.0
WORKER_TIMEOUT_SECONDS = 1500.0
PRETIP_CAPTURE_SAFETY_MARGIN_SECONDS = 120.0


def _pretip_close_capture(paths: Any, started: float) -> dict[str, Any]:
    """NCAAB pre-tip close snapshots (market data only). Never raises; never fails the cycle."""
    try:
        budget = min(
            PRETIP_CAPTURE_MAX_SECONDS,
            WORKER_TIMEOUT_SECONDS - PRETIP_CAPTURE_SAFETY_MARGIN_SECONDS - (time.monotonic() - started),
        )
        if budget < 5.0:
            return {"status": "skipped", "reason": "worker_time_budget_exhausted"}
        return capture_pretip_closes(
            ledgers_db=paths.ledgers_db, runtime_root=paths.runtime_root, max_capture_seconds=budget
        )
    except Exception as exc:  # noqa: BLE001 -- accounting must never change the buyer's exit code
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}


def main() -> int:
    started = time.monotonic()
    enable_background_priority()
    paths = RuntimePaths.resolve(require_external_runtime=False)
    lock = acquire_lock(paths.lock_root / "daily.lock", wait_seconds=FORECAST_LOCK_WAIT_SECONDS)
    if lock is None:
        # A forecast still owns the writer lock after the wait. The next interval
        # will retry; this is an expected coordination skip, not a buyer failure.
        print(json.dumps({"status": "skipped", "reason": "DAILY_WRITER_ALREADY_RUNNING"}), flush=True)
        return 0
    try:
        eastern_date = utc_now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        live_error = None
        try:
            result = run_auto_buyer_cycle(forecast_date=eastern_date)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            live_error = str(exc)
            result = {"live_auto_buyer": {"status": "error", "reason": live_error}}
        try:
            result["ncaab_spread_paper_trial"] = run_ncaab_spread_paper_trial()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            result["ncaab_spread_paper_trial"] = {"status": "error", "reason": str(exc)}
        failed = live_error is not None or result["ncaab_spread_paper_trial"]["status"] == "error"
        # After the buys, and deliberately not part of `failed`: CLV accounting only.
        result["pretip_close_capture"] = _pretip_close_capture(paths, started)
        print(json.dumps({"status": "error" if failed else "ok", **result}, default=str), flush=True)
        return 1 if failed else 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
