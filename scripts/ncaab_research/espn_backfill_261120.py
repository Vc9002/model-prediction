"""Backward extension of the NCAAB ESPN scoreboard + closing-line archive (protocol 261120 confirmation window).

Reuses scripts/ncaab_espn_lines.py UNCHANGED (Fetcher, slim_event, build_record, the pinned selection rule
ncaab-espn-lines-rule-1) by pointing its module-level paths and season tables at NEW files, so the existing
archive (espn_lines.jsonl, whose sha256 earlier reports cite) is never appended to. The rule text is the same
object, so every record carries the same rule hash; the backfill population is recorded here and in
progress.json instead of in the rule text.

Population:
    scoreboard  season years 2016..2022 (2015-16 .. 2021-22), Nov 1 .. Apr 10 each; 2016, 2017 and 2021 (COVID)
                are history only for the confirmation replay.
    odds        season years 2022, 2020, 2019, 2018 (newest first), every completed D1 scoreboard event.

Writes only data/research/ncaab_market/backfill_261120/{scoreboard.jsonl, espn_lines.jsonl, progress.json}.
Single worker, paced GETs (0.4 s), generic User-Agent, append-only and resumable: re-run the same command after
any interruption. NEVER joins outcomes: scores are only used to know an event is completed.

    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/espn_backfill_261120.py all
    (or: scoreboard | fetch | summary)

Expected cost: about 1,130 scoreboard dates (~8 min) and about 23,000 odds events (~2.5-3 h, network bound).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import ncaab_espn_lines as el  # the pinned fetch/selection code, reused unchanged

BACKFILL_DIR = el.DATA_DIR / "backfill_261120"
SCOREBOARD_SEASONS = (2016, 2017, 2018, 2019, 2020, 2021, 2022)
ODDS_SEASONS = (2022, 2020, 2019, 2018)


def configure() -> None:
    """Point the reused module at the backfill files and seasons (never at the 2023+ archive)."""
    el.DATA_DIR = BACKFILL_DIR
    el.SCOREBOARD_PATH = BACKFILL_DIR / "scoreboard.jsonl"
    el.LINES_PATH = BACKFILL_DIR / "espn_lines.jsonl"
    el.PROGRESS_PATH = BACKFILL_DIR / "progress.json"
    el.read_lines.__defaults__ = (el.LINES_PATH,)  # its default was bound to the 2023+ file at import
    el.SEASON_WINDOWS = {s: (date(s - 1, 11, 1), date(s, 4, 10)) for s in SCOREBOARD_SEASONS}
    el.ODDS_SEASONS = ODDS_SEASONS
    el.ODDS_ORDER = ODDS_SEASONS
    BACKFILL_DIR.mkdir(parents=True, exist_ok=True)
    el.merge_progress(
        "population",
        {
            "protocol": "ncaab-research-protocol-261120 (confirmation_backcast)",
            "scoreboard_season_years": list(SCOREBOARD_SEASONS),
            "odds_season_years": list(ODDS_SEASONS),
            "selection_rule": "ncaab-espn-lines-rule-1 applied unchanged (its population text describes the 2023+ "
            "archive; this file records the backfill population)",
        },
    )


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def summary() -> dict[str, Any]:
    """Outcome-free coverage by season: events, lined events, provider counts, file hashes."""
    events = el.load_events()
    stored = el.read_lines()
    by_season: dict[int, Counter[str]] = defaultdict(Counter)
    providers: Counter[str] = Counter()
    for ev in events:
        rec = stored.get(ev["event_id"])
        c = by_season[ev["season_year"]]
        c["events"] += 1
        if rec is None:
            c["not_fetched"] += 1
            continue
        sel = rec.get("selected") or {}
        c["spread_line"] += sel.get("home_spread_line") is not None
        c["total_line"] += sel.get("total_line") is not None
        c[f"reason_{rec.get('no_line_reason')}"] += rec.get("no_line_reason") is not None
        for p in rec.get("providers") or []:
            providers[str(p.get("provider_name"))] += 1
    out = {
        "updated_at_utc": el.utc_now(),
        "by_season": {str(s): dict(c) for s, c in sorted(by_season.items())},
        "providers_top": dict(providers.most_common(25)),
        "scoreboard_sha256": _sha(el.SCOREBOARD_PATH),
        "lines_sha256": _sha(el.LINES_PATH),
        "rule_sha256": el._rule_hash(),
    }
    el.merge_progress("summary", out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("scoreboard", "fetch", "summary", "all"))
    ap.add_argument("--interval", type=float, default=el.DEFAULT_INTERVAL_S)
    args = ap.parse_args(argv)
    configure()
    if args.cmd in ("scoreboard", "all"):
        el.cmd_scoreboard(args)
    if args.cmd in ("fetch", "all"):
        el.cmd_fetch(args)
    if args.cmd in ("summary", "all"):
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
