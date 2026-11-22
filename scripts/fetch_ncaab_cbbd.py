"""Pull CollegeBasketballData.com (CBBD) season-level history into the research cache.

Phases (each cached; reruns cost zero calls for anything already fetched):
  teams  one call, all teams (CBBD id -> ESPN id via sourceId)
  games  /games per season, a few date windows each
  lines  /lines per season
  box    /games/teams per season (possession inputs FGA, FTA, OREB, TOV)

Season = spring year (2014 = 2013-14). Stops before any call once ``X-CallLimit-Remaining`` is
below the floor (200), and after ``--max-calls`` new calls in this run. The key is read by the
project's dotenv loader into ``os.environ`` and is never printed or written.

Usage:
    .venv/Scripts/python scripts/fetch_ncaab_cbbd.py --phase teams games lines --seasons 2014 ... 2026
    .venv/Scripts/python scripts/fetch_ncaab_cbbd.py --phase box --seasons 2014 ... 2021 --max-calls 120
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_prediction.data_sources.ncaab_cbbd import CBBDAuthError, CBBDClient, CBBDQuotaError
from model_prediction.runtime_paths import load_dotenv_into_environ

PHASES = ("teams", "games", "lines", "box")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", nargs="+", choices=PHASES, default=["teams", "games", "lines"])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2014, 2027)))
    ap.add_argument("--max-calls", type=int, default=250, help="new API calls allowed in this run")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv_into_environ(ROOT / ".env")
    try:
        client = CBBDClient()
    except CBBDAuthError as exc:
        print(f"no key: {exc}")
        return 2

    def guard() -> None:
        if client.calls_made >= args.max_calls:
            raise CBBDQuotaError(f"run budget of {args.max_calls} new calls used")

    try:
        if "teams" in args.phase:
            guard()
            print(
                f"teams: {len(client.teams())} rows; calls {client.calls_made}, remaining {client.remaining}"
            )
        for season in args.seasons:
            for phase, method in (
                ("games", client.games),
                ("lines", client.lines),
                ("box", client.game_teams),
            ):
                if phase not in args.phase:
                    continue
                guard()
                before = client.calls_made
                rows = method(season)
                print(
                    f"{season} {phase}: {len(rows)} rows, {client.calls_made - before} new calls; "
                    f"total {client.calls_made}, remaining {client.remaining}",
                    flush=True,
                )
                if client.calls_made > before:
                    time.sleep(args.pause)
    except (CBBDQuotaError, CBBDAuthError) as exc:
        print(f"stopped: {exc}; calls this run {client.calls_made}, remaining {client.remaining}")
        return 3
    print(f"done; calls this run {client.calls_made}, remaining {client.remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
