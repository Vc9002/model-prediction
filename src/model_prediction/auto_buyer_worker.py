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
from .runtime_paths import RuntimePaths


def main() -> int:
    paths = RuntimePaths.resolve(require_external_runtime=False)
    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        # A forecast currently owns the writer lock. The next interval will
        # retry; this is an expected coordination skip, not a buyer failure.
        print(json.dumps({"status": "skipped", "reason": "DAILY_WRITER_ALREADY_RUNNING"}))
        return 0
    try:
        eastern_date = utc_now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        result = run_auto_buyer_cycle(forecast_date=eastern_date)
        print(json.dumps({"status": "ok", **result}, default=str))
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
