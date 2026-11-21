"""Write PROVENANCE.json for each NBA v10 research dataset from what is on disk (quick-261120-nbf).

Counts are computed by streaming the stored files, never typed by hand, so re-running after a
resumed backfill refreshes them.

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe scripts/nba_v10_backfill/provenance.py
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from model_prediction.data_sources.nba_backfill_common import (
    read_json,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)

CODE = "branch nba-v10/backfill, scripts/nba_v10_backfill/ (quick-261120-nbf)"
PIT_NOTE_LINES = (
    "Closing lines carry no capture time: benchmark and anchoring research only, never a decision input."
)


def _jsonl(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _season_label(season_year: int | None) -> str:
    return f"{season_year - 1}-{str(season_year)[2:]}" if season_year else "unknown"


def games(root: Path) -> dict[str, Any]:
    index: dict[str, dict[str, Any]] = {}
    for row in _jsonl(root / "games" / "scoreboard_index.jsonl"):
        index[str(row["event_id"])] = row
    sb_progress = read_json(root / "games" / "scoreboard_progress.json", {"done": {}, "failed": []})
    by_season: dict[str, Counter[str]] = defaultdict(Counter)
    for row in index.values():
        by_season[_season_label(row.get("season_year"))][str(row.get("season_type"))] += 1
    box_dir = root / "games" / "espn_boxscores"
    box_ids = {p.stem for p in box_dir.glob("*.json")} if box_dir.exists() else set()
    plays = {p.name.split(".")[0] for p in (root / "games" / "espn_plays").glob("*.json.gz")}
    box_by_season: Counter[str] = Counter()
    target_by_season: Counter[str] = Counter()
    for eid, row in index.items():
        sy = row.get("season_year")
        if (
            sy in range(2017, 2025)
            and row.get("season_type") not in ("preseason", "off-season")
            and row.get("competition_type") != "ALLSTAR"
        ):
            target_by_season[_season_label(sy)] += 1
            if eid in box_ids:
                box_by_season[_season_label(sy)] += 1
    n_player = sum(1 for _ in _jsonl(root / "games" / "player_box.jsonl"))
    n_team = sum(1 for _ in _jsonl(root / "games" / "team_box.jsonl"))
    failed = read_json(root / "games" / "boxscore_progress.json", {"failed": {}}).get("failed", {})
    dates = sorted(sb_progress["done"])
    return {
        "dataset": "NBA games index (2007-08 .. 2026-27 to date) and ESPN box scores 2016-17 .. 2023-24",
        "sources": [
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD",
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>",
        ],
        "license": "ESPN public (undocumented) site API; ESPN terms of use apply; internal research use only",
        "code": CODE,
        "generated_at_utc": utc_now_iso(),
        "files": {
            "scoreboard_index.jsonl": "completed events, data/historical/nba_games_all.jsonl row schema + scoreboard_date (ET)",
            "espn_boxscores/<event_id>.json": "{event_id, fetched_at_utc, payload} with payload trimmed to "
            "boxscore/header/gameInfo/format/meta/pickcenter/injuries; readable by "
            "features.nba_player_logs.load_nba_player_boxscores",
            "espn_plays/<event_id>.json.gz": "play-by-play list from the same summary",
            "player_box.jsonl": "one row per player per game (superset of parse_nba_player_boxscore keys)",
            "team_box.jsonl": "one row per team per game (team box + linescores)",
        },
        "scoreboard": {
            "dates_scanned": len(dates),
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
            "dates_failed": sb_progress.get("failed", []),
            "skipped_dates": "July 1 - Sept 27 of every year except 2020 (no NBA games outside the 2020 bubble)",
            "completed_events": len(index),
            "events_by_season_and_type": {k: dict(v) for k, v in sorted(by_season.items())},
        },
        "boxscores": {
            "population": "completed non-preseason, non-All-Star events with ESPN season_year 2017..2024",
            "target_by_season": dict(sorted(target_by_season.items())),
            "stored_by_season": dict(sorted(box_by_season.items())),
            "stored_total": len(box_ids),
            "plays_files": len(plays),
            "failed_or_empty": failed,
            "player_rows": n_player,
            "team_rows": n_team,
        },
        "pit_note": "Box scores are post-game facts; use a game's rows only for decisions after it ended. "
        "The summary 'injuries' block is a hindsight snapshot, not a PIT availability source.",
    }


def closing_lines(root: Path) -> dict[str, Any]:
    meta = read_json(root / "closing_lines" / "raw" / "download_meta.json", {})
    stats = read_json(root / "closing_lines" / "normalize_stats.json", {})
    rows = list(_jsonl(root / "closing_lines" / "nba_closing_lines_2007_2026.jsonl"))
    dates = sorted(r["date"] for r in rows)
    mm = sum(1 for _ in _jsonl(root / "closing_lines" / "nba_open_close_2025_26.jsonl"))
    return {
        "dataset": "NBA closing lines 2007-08 .. 2025-26 (Kaggle) + 2025-26 open/close",
        "source": "Kaggle: cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024 (CC0-1.0) + "
        "minmaxer/nba-closing-lines-ats-2025-26 (CC BY 4.0)",
        "source_urls": [m["url"] for m in meta.values()],
        "license": {ref: m["licence"] for ref, m in meta.items()},
        "raw_files": {m["zip"]: {"sha256": m["sha256"], "bytes": m["bytes"]} for m in meta.values()},
        "downloaded_at_utc": min((m["downloaded_at_utc"] for m in meta.values()), default=None),
        "code": CODE,
        "generated_at_utc": utc_now_iso(),
        "schema": "nba_closing_lines_2024_2026.jsonl keys (event_id, date, away_team, home_team, spread, total, "
        "moneyline_away, moneyline_home, whos_favored) + research fields. moneyline_* are blank as in the "
        "existing file; moneylines are converted to ml_implied_prob_* (raw, vig included) and "
        "ml_devig_prob_home. home_spread is signed (negative = home favoured).",
        "matching": "ESPN event_id from the research scoreboard index: same ET date (+/-1 day) and identical "
        "final scores, else learned source-code -> ESPN team id map on date +/-1",
        "rows": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "match_methods": stats.get("match_methods"),
        "per_season": stats.get("per_season"),
        "open_close_2025_26_rows": mm,
        "open_close_2025_26_matching": stats.get("minmaxer"),
        "note": PIT_NOTE_LINES,
    }


def espn_odds(root: Path) -> dict[str, Any]:
    per: dict[str, Counter[str]] = defaultdict(Counter)
    fetched: list[str] = []
    for rec in _jsonl(root / "espn_odds" / "espn_lines.jsonl"):
        s = per[_season_label(rec.get("season_year"))]
        sel = rec["selected"]
        s["events"] += 1
        s["with_books"] += sel["n_books"] > 0
        s["close_spread"] += sel["close_home_spread"] is not None
        s["close_total"] += sel["close_total"] is not None
        s["close_ml"] += sel["close_ml_devig_home"] is not None
        s["open_books_spread"] += sel["open_home_spread_books"] is not None
        s["opening_provider"] += sel["opening_provider_home_spread"] is not None
        s["tr_spread_pre_tip"] += bool((rec.get("tr_spread_summary") or {}).get("n_pre_tip"))
        s["tr_total_pre_tip"] += bool((rec.get("tr_total_summary") or {}).get("n_pre_tip"))
        fetched.append(rec["fetched_at_utc"])
    return {
        "dataset": "ESPN NBA line archive 2016-17 .. 2026-27 to date (close, open, timestamped movement)",
        "source": "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/<id>/competitions/<id>/odds"
        " and .../odds/1002/history/{1,2}/movement (teamrankings spread/total movement)",
        "license": "ESPN public (undocumented) core API; ESPN terms of use apply; internal research use only",
        "code": CODE,
        "fetched_at_utc_range": [min(fetched), max(fetched)] if fetched else None,
        "generated_at_utc": utc_now_iso(),
        "semantics": {
            "close_*": "median across sportsbooks (excludes Opening, consensus, numberfire, teamrankings, "
            "accuscore, live items); close block when present and within 3 pts of current, else current. "
            "No capture time: benchmark only.",
            "open_*_books": "median of per-book open blocks (present from about 2023-24).",
            "opening_provider_*": "ESPN 'Opening' pseudo-provider (2016-17 era).",
            "tr_*_movement": "teamrankings line history with lineDate (UTC). Only entries before tip are usable; "
            "series run past tip. The spread 'line' is AWAY-signed: its negation matched the book-median "
            "close_home_spread within 1.5 pts on 93-99% of games per season 2016-17..2022-23 (checked "
            "2026-11-21; the as-stored value is not negated).",
            "prices": "all prices stored as implied probabilities; no American odds stored",
        },
        "per_season": {k: dict(v) for k, v in sorted(per.items())},
    }


def injury_reports(root: Path) -> dict[str, Any]:
    prog = read_json(root / "injury_reports" / "pdf_progress.json", {"days": {}})
    days = prog["days"]
    schemes: Counter[str] = Counter(str(v.get("scheme")) for v in days.values())
    quarter_days = sorted(d for d, v in days.items() if v.get("scheme") == "quarter")
    pdfs = sum(v.get("found", 0) for v in days.values())
    stored = sum(v.get("stored", 0) for v in days.values())
    size = sum(p.stat().st_size for p in (root / "injury_reports" / "pdfs").rglob("*.pdf"))
    parse = read_json(root / "injury_reports" / "parse_progress.json", {"months": {}})["months"]
    tot: Counter[str] = Counter()
    for m in parse.values():
        tot.update({k: v for k, v in m.items() if isinstance(v, int)})
    by_season: Counter[str] = Counter()
    for d, v in days.items():
        y, mo = int(d[:4]), int(d[5:7])
        by_season[f"{y}-{str(y + 1)[2:]}" if mo >= 8 else f"{y - 1}-{str(y)[2:]}"] += v.get("found", 0)
    return {
        "dataset": "Official NBA injury reports 2021-22 onward (raw PDFs + parsed PIT rows)",
        "source": "https://ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<HHAM|HH_MMAM>.pdf",
        "license": "NBA official public injury report; NBA.com terms of use apply; internal research use only",
        "code": CODE,
        "generated_at_utc": utc_now_iso(),
        "observed_at": "filename slot (ET) -> UTC, raised to the report header time when later "
        "(e.g. the 11PM file is headed 11:30 PM); rows carry observed_at_utc",
        "days_scanned": len(days),
        "first_day": min(days) if days else None,
        "last_day": max(days) if days else None,
        "days_by_scheme": dict(schemes),
        "first_quarter_hour_day": quarter_days[0] if quarter_days else None,
        "pdfs_found": pdfs,
        "pdfs_stored": stored,
        "pdf_bytes": size,
        "pdfs_by_season": dict(sorted(by_season.items())),
        "parse": {
            "months_parsed": sorted(parse),
            "pdfs_parsed": tot["pdfs"],
            "distinct_parsed": tot["distinct"],
            "rows": tot["rows"],
            "unplaced_fragments": tot["unplaced"],
            "inconsistent_rows": tot["inconsistent"],
            "pdf_parse_errors": tot["errors"],
            "row_failure_rate": round(tot["inconsistent"] / tot["rows"], 6) if tot["rows"] else None,
        },
        "gaps": "days with scheme null had no report under any probed slot (off-season, All-Star break); "
        "a CDN failure stops the run instead of recording a gap",
        "known_parse_issues": "All-Star game rows parse as matchup UNK@UNK / team 'Non-NBA Team' and are "
        "flagged consistent=false (96 rows in 2022-02); July files cover Summer League games",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=None)
    args = parser.parse_args()
    root = resolve_out_root(args.out_root)
    for name, fn in (
        ("games", games),
        ("closing_lines", closing_lines),
        ("espn_odds", espn_odds),
        ("injury_reports", injury_reports),
    ):
        if (root / name).exists():
            write_json_atomic(root / name / "PROVENANCE.json", fn(root))
            print(f"wrote {root / name / 'PROVENANCE.json'}")


if __name__ == "__main__":
    main()
