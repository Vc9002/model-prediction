"""NBA v10 research ESPN line archive, 2016-17 .. 2026-27 to date (quick-261120-nbf).

For every completed non-preseason event in the research scoreboard index (ESPN season_year
2017..2027, newest first so the 2019-2026 opening-line window lands first): the core-odds
provider list, trimmed and parsed by ``nba_backfill_odds``, plus the teamrankings (provider
1002) spread and total movement histories when that provider is listed. Appends one record per
event to ``espn_odds/espn_lines.jsonl``; resumable (event ids already in the file are skipped).

Closing values are a benchmark, never decision inputs (no capture time). Movement entries carry
``lineDate``; consumers must keep only entries before tip (``movement_summary`` does).

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe scripts/nba_v10_backfill/espn_odds.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from model_prediction.data_sources.nba_backfill_common import (
    Fetcher,
    append_jsonl,
    resolve_out_root,
    utc_now_iso,
)
from model_prediction.data_sources.nba_backfill_odds import (
    TEAMRANKINGS_ID,
    movement_summary,
    select_lines,
    trim_item,
    trim_movement,
)

_logger = logging.getLogger("nba_v10.odds")
CORE = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{eid}/competitions/{eid}/odds"
)
SEASONS = range(2017, 2028)


def load_targets(root: Path) -> list[dict]:
    rows: dict[str, dict] = {}
    with (root / "games" / "scoreboard_index.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if (
                r.get("season_year") in SEASONS
                and r.get("season_type") not in ("preseason", "off-season")
                and r.get("competition_type") != "ALLSTAR"
            ):
                rows[str(r["event_id"])] = r
    return sorted(rows.values(), key=lambda r: (r.get("event_start_utc") or "", r["event_id"]), reverse=True)


def done_ids(path: Path) -> set[str]:
    out: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.add(str(json.loads(line)["event_id"]))
                except (ValueError, KeyError):
                    continue
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--interval", type=float, default=0.4)
    parser.add_argument("--min-season", type=int, default=2017)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = resolve_out_root(args.out_root)
    out_path = root / "espn_odds" / "espn_lines.jsonl"
    done = done_ids(out_path)
    todo = [
        r
        for r in load_targets(root)
        if r["event_id"] not in done and (r.get("season_year") or 0) >= args.min_season
    ]
    _logger.info("events to fetch: %d (done %d)", len(todo), len(done))
    fetcher = Fetcher(args.interval)
    for i, ev in enumerate(todo):
        eid = ev["event_id"]
        base = CORE.format(eid=eid)
        status, payload = fetcher.get_json(base + "?limit=100")
        if status is None:
            _logger.warning("odds %s fetch failed; leaving for resume", eid)
            continue
        items = (payload or {}).get("items") or []
        providers = [trim_item(it) for it in items]
        record = {
            "event_id": eid,
            "season_year": ev.get("season_year"),
            "season_type": ev.get("season_type"),
            "event_start_utc": ev.get("event_start_utc"),
            "away_team": ev.get("away_team"),
            "home_team": ev.get("home_team"),
            "fetched_at_utc": utc_now_iso(),
            "http_status": status,
            "providers": providers,
            "selected": select_lines(providers),
        }
        if any(p["provider_id"] == TEAMRANKINGS_ID for p in providers):
            for key, hist in (("spread", 1), ("total", 2)):
                m_status, m_payload = fetcher.get_json(
                    f"{base}/{TEAMRANKINGS_ID}/history/{hist}/movement?limit=300"
                )
                items_m = trim_movement(m_payload) if m_status == 200 else []
                record[f"tr_{key}_movement"] = items_m
                record[f"tr_{key}_movement_status"] = m_status
                record[f"tr_{key}_summary"] = movement_summary(items_m, ev.get("event_start_utc"))
        append_jsonl(out_path, [record])
        if i % 100 == 0 or i == len(todo) - 1:
            _logger.info(
                "[%d/%d] %s season=%s books=%d req=%d",
                i + 1,
                len(todo),
                eid,
                ev.get("season_year"),
                record["selected"]["n_books"],
                fetcher.requests,
            )
    _logger.info("done requests=%d retries=%d", fetcher.requests, fetcher.retries)


if __name__ == "__main__":
    main()
