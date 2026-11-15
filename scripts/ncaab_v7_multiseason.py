"""NCAAB v7 multi-season re-fit and evaluation, SHADOW ONLY (quick 261115-m5r).

Reads the archives written by quick 261115-1m8 (data/research/ncaab_market/, untracked), calls v6 read-only,
and writes only outputs/ncaab/ncaab_v7_multiseason.{md,json} plus untracked caches. No ledger writes (the
runtime ledger is opened mode=ro), no config or promotion change, v7 stays unregistered.

Stages (repo root; ``OPENBLAS_NUM_THREADS=1 PYTHONPATH=src:scripts .venv/Scripts/python scripts/ncaab_v7_multiseason.py STAGE``):
    components  outcome-free as-of-map replay of v6 and the ridge grid for every game day (cache)
    gate        today's-map v6 must equal the existing PIT projections exactly (no outcome read)
    fit         reads outcomes of the FIT seasons only; selects, fits and freezes the config
    final       the single read of 2024-25, the contaminated line-free windows, the ledger checks, the report

The pinned rule lives in ncaab_v7_multiseason_pin.py and was committed before any outcome was joined.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import ncaab_residual as nr
from ncaab_v7_multiseason_pin import PIN, PIN_SHA256

DATA = REPO / "data" / "research" / "ncaab_market"
OUT = REPO / "outputs" / "ncaab"
COMPONENTS = DATA / "v7ms_components.jsonl"
FROZEN = OUT / "ncaab_v7_multiseason_frozen.json"
FIT_SEASONS = tuple(PIN["windows_by_espn_season_year"]["fit"])
DECISION_SEASONS = tuple(PIN["windows_by_espn_season_year"]["decision"])
STAGE_MAX_SEASON = {"components": 0, "gate": 0, "fit": max(FIT_SEASONS), "final": 2027}


def load_outcomes(stage: str) -> dict[str, dict[str, Any]]:
    """event id -> scores, restricted to the seasons the stage is allowed to read (structural guard)."""
    cap = STAGE_MAX_SEASON[stage]
    if cap == 0:
        raise RuntimeError(f"stage {stage!r} may not read outcomes")
    out: dict[str, dict[str, Any]] = {}
    for e in nr.load_scoreboard_events():
        if int(e["season_year"]) < 2023 or int(e["season_year"]) > cap:
            continue
        out[e["event_id"]] = {
            "margin": int(e["home_score"]) - int(e["away_score"]),
            "total": int(e["home_score"]) + int(e["away_score"]),
        }
    return out


# ----------------------------------------------------------------------------------------- components
def _worker(days: list[str]) -> list[dict[str, Any]]:
    from model_prediction.domain import EASTERN
    from model_prediction.models.basketball import UpcomingGame
    from model_prediction.models.ncaab_v7_multiseason import NCAABV7MultiSeasonModel, build_asof_conferences

    events = nr.load_scoreboard_events()
    games = nr.to_game_records(events)
    order = sorted(range(len(games)), key=lambda i: games[i].start)
    games = [games[i] for i in order]
    starts = [g.start for g in games]
    neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
    asof = os.environ.get("V7MS_TODAY_MAP") != "1"
    model = NCAABV7MultiSeasonModel(build_asof_conferences(events), stats_root=nr.STATS_ROOT, asof_map=asof)
    rows: list[dict[str, Any]] = []
    for day in days:
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN)
        hist = games[: bisect.bisect_left(starts, cutoff)]
        todays = [g for g in by_day.get(day, []) if int(g.season_year or 0) >= 2023]
        if len(hist) < 20 or not todays:
            continue
        up = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=neutral[g.event_id],
            )
            for g in todays
        ]
        by_id = {g.event_id: g for g in todays}
        for row in model.components(hist, up):
            g = by_id[row["event_id"]]
            rows.append(
                {**row, "day": day, "season_year": int(g.season_year or 0), "start": g.event_start_utc}
            )
    return rows


def cmd_components(args: argparse.Namespace) -> int:
    events = nr.load_scoreboard_events()
    days = sorted({nr.et_day(e["start_utc"]) for e in events if e["season_year"] >= 2023})
    if args.limit_days:
        days = days[: args.limit_days]
    if args.stride > 1:
        days = days[:: args.stride]
    # interleaved chunks balance the cost (late-season days carry the longest history)
    chunks = [days[i :: args.workers] for i in range(args.workers)]
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        parts = list(ex.map(_worker, chunks))
    rows = sorted((r for p in parts for r in p), key=lambda r: (r["start"], r["event_id"]))
    path = Path(args.out) if args.out else COMPONENTS
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"components {len(rows)} rows, {len(days)} days, {time.time() - t0:.0f}s -> {path}", flush=True)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """With today's map the replay's v6 margin must equal the existing PIT projections (no outcome read)."""
    proj = {r["event_id"]: r for r in nr._read_jsonl(nr.PROJECTIONS)}
    path = Path(args.components)
    comps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    diffs = [abs(c["v6_mean"] - proj[c["event_id"]]["sp_mean"]) for c in comps if c["event_id"] in proj]
    tdiffs = [
        abs(c["v6_total"] - proj[c["event_id"]]["tp_mean"])
        for c in comps
        if c["event_id"] in proj and "v6_total" in c and "tp_mean" in proj[c["event_id"]]
    ]
    res = {
        "rows_compared": len(diffs),
        "spread_max_abs_diff": max(diffs) if diffs else None,
        "total_max_abs_diff": max(tdiffs) if tdiffs else None,
    }
    print("gate (today's map vs v6_projections.jsonl)", res)
    return 0 if diffs and max(diffs) < 0.011 and (not tdiffs or max(tdiffs) < 0.011) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("components")
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--limit-days", type=int, default=0)
    c.add_argument("--stride", type=int, default=1)
    c.add_argument("--out", default="")
    g = sub.add_parser("gate")
    g.add_argument("--components", required=True)
    for name in ("fit", "final"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    print(f"pin {PIN['id']} sha256 {PIN_SHA256}", flush=True)
    if args.cmd == "components":
        return cmd_components(args)
    if args.cmd == "gate":
        return cmd_gate(args)
    raise SystemExit(f"stage {args.cmd} is added in a later commit")


if __name__ == "__main__":
    raise SystemExit(main())
