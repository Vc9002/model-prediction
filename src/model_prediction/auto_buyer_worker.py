"""Short, independently scheduled Auto-Buyer cycle.

The daily forecast is intentionally long-running.  This worker only settles
paper positions, refreshes active market quotes, and evaluates the already
generated candidate rows, so purchase timing is no longer gated by forecast
runtime.
"""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from .daily_lock import acquire_lock
from .domain import utc_now
from .portfolio.auto_executor import run_auto_buyer_cycle
from .portfolio.ncaab_paper_trial import run_ncaab_spread_paper_trial
from .runtime_paths import RuntimePaths
from .stealth_runtime import enable_background_priority


def main() -> int:
    enable_background_priority()
    paths = RuntimePaths.resolve(require_external_runtime=False)
    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        # A forecast currently owns the writer lock. The next interval will
        # retry; this is an expected coordination skip, not a buyer failure.
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
        print(json.dumps({"status": "error" if failed else "ok", **result}, default=str), flush=True)
        return 1 if failed else 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
