"""Void KBO/NPB research picks whose game was never played on its slate date.

Root-caused 2026-08-31. Every open KBO and NPB row -- 31 kbo + 3 npb at the
time of writing -- is a real Polymarket contract on a real scheduled game that
the official league source still lists with **no score**: a bare `TeamA vs
TeamB` play cell (KBO) or a `*` placeholder score (NPB). Postponed games are
replayed on a new date under a new game_id; the original calendar row stays
scoreless forever. So `find_international_baseball_result` returns None on
every settle run, forever, and the pick can never leave `open`.

This is NOT the slate builder emitting wrong dates or flipped home/away -- the
earlier hypothesis. Verified against the live source: every one of the 34 rows
matches a scheduled-but-scoreless game at its own date in its own orientation
(`parse_kbo_unplayed_rows` / `parse_npb_unplayed_calendar`). The events are
genuine; only their results do not exist.

Voiding, never deletion, is the sanctioned repair: `PickLedger.void` settles
the row as a PUSH at zero P&L and stamps a `void_reason`, so the record that
the pick was made survives in full. Both tiers here are research
(`RESEARCH_LEDGER_SPORTS`) and hold zero real money.

Per `docs/ROADMAP.md` and `docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md` D.1, stale
open rows are cleared only by an identity-scoped, operator-approved script --
never a bulk sweep. Every row is therefore re-verified against the live source
at run time, and a row that fails verification is reported and left alone. The
grace window exists because the source draws no distinction between "postponed"
and "not played yet"; only elapsed time can.

    env MODEL_PREDICTION_RUNTIME_ROOT=~/model-prediction-runtime \\
        MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite \\
        PYTHONPATH=src:. .venv/bin/python \\
        scripts/void_postponed_international_baseball.py [--apply]
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.international_baseball import (
    international_baseball_team_id,
    international_baseball_unplayed_index,
)
from model_prediction.research_ledgers import research_ledger

LEAGUES = ("kbo", "npb")
TIERS = ("research", "gated_research")
# KST and JST are both UTC+9; the league's calendar day is the local one.
LEAGUE_UTC_OFFSET = dt.timedelta(hours=9)
DEFAULT_GRACE_HOURS = 72


def _start(event_start_utc: str) -> dt.datetime | None:
    # A naive timestamp is rejected rather than assumed UTC: this script's
    # whole job is proving a game is old enough, and a wrong-by-nine-hours
    # start could void a row still inside its grace window.
    try:
        return parse_utc(str(event_start_utc))
    except ValueError:
        return None


def _local_game_date(start: dt.datetime) -> str:
    return (start + LEAGUE_UTC_OFFSET).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    parser.add_argument("--tiers", nargs="+", default=list(TIERS))
    parser.add_argument(
        "--grace-hours",
        type=int,
        default=DEFAULT_GRACE_HOURS,
        help="leave rows this recent alone; the source cannot distinguish "
        "'postponed' from 'not played yet' (default: 72, matching the "
        "stale_open_rows health check)",
    )
    parser.add_argument("--apply", action="store_true", help="without this, only report")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(hours=args.grace_hours)
    reason_date = now.date().isoformat()
    voided = skipped_recent = unverified = 0

    for league in args.leagues:
        rows_by_tier = {}
        for tier in args.tiers:
            ledger = research_ledger(data_root, league, gated=(tier == "gated_research"))
            rows_by_tier[tier] = (ledger, [r for r in ledger.rows() if r.get("status") == "open"])
        if not any(rows for _, rows in rows_by_tier.values()):
            print(f"{league}: no open rows")
            continue

        years = {
            str(r.get("event_start_utc"))[:4]
            for _, rows in rows_by_tier.values()
            for r in rows
            if str(r.get("event_start_utc"))[:4].isdigit()
        }
        unplayed: set[tuple[str, str, str]] = set()
        for year in sorted(years):
            unplayed |= international_baseball_unplayed_index(league, int(year))
        print(f"{league}: {len(unplayed)} scheduled-but-scoreless games at source across {sorted(years)}")

        for tier, (ledger, rows) in rows_by_tier.items():
            for row in rows:
                pick_id = row["pick_id"]
                start_raw = str(row.get("event_start_utc") or "")
                start = _start(start_raw)
                game_date = _local_game_date(start) if start is not None else None
                home = international_baseball_team_id(data_root, league, str(row.get("home_team") or ""))
                away = international_baseball_team_id(data_root, league, str(row.get("away_team") or ""))
                label = f"  {tier:15s} {pick_id} {game_date} {row.get('away_team')} @ {row.get('home_team')}"
                if game_date is None or home is None or away is None:
                    print(f"{label}  SKIP unresolved identity (start={start_raw!r})")
                    unverified += 1
                    continue
                if (game_date, away, home) not in unplayed:
                    print(f"{label}  SKIP source shows no unplayed game -- settle, do not void")
                    unverified += 1
                    continue
                if start >= cutoff:
                    print(f"{label}  SKIP inside the {args.grace_hours}h grace window")
                    skipped_recent += 1
                    continue
                reason = (
                    f"voided {reason_date}: game never played on its slate date -- the "
                    f"official {league.upper()} source still lists {away} @ {home} on "
                    f"{game_date} with no score (postponed; makeup games are replayed "
                    f"under a new game_id on a new date), so no result for this contract "
                    f"can ever exist and the row would stay open forever."
                )
                if not args.apply:
                    print(f"{label}  [dry run] would void")
                    voided += 1
                    continue
                result = ledger.void(pick_id, reason)
                print(f"{label}  -> {result['status']}/{result['result']} pnl={result['pnl_units']}")
                voided += 1

    verb = "would void" if not args.apply else "voided"
    print(
        f"\n{verb} {voided}; {skipped_recent} inside grace window; "
        f"{unverified} left open (unverified at source)"
    )
    if not args.apply:
        print("dry run -- re-run with --apply to void")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
