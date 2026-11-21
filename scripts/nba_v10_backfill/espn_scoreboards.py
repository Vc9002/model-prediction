"""NBA v10 research game index from ESPN daily scoreboards (quick-261120-nbf).

Writes ``<root>/games/scoreboard_index.jsonl`` in the ``data/historical/nba_games_all.jsonl``
row schema (``ESPNClient.completed_games`` output plus ``league``), one row per completed
event, for every calendar date 2007-10-01 .. --end. July-September are skipped except in
2020 (bubble restart) and July 2021 (2020-21 Finals). Never touches the live history file.

Resumable: dates already processed are listed in ``games/scoreboard_progress.json``.

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe \
        scripts/nba_v10_backfill/espn_scoreboards.py [--end 2026-11-19]
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from model_prediction.data_sources.espn import ESPNClient
from model_prediction.data_sources.nba_backfill_common import (
    Fetcher,
    append_jsonl,
    read_json,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)

_logger = logging.getLogger("nba_v10.scoreboards")
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}&limit=200"
# Priority order: the box-score/odds window first, then recent seasons, then the old tail.
CHUNKS = (
    (date(2016, 7, 1), date(2024, 6, 30)),
    (date(2024, 7, 1), None),
    (date(2007, 10, 1), date(2016, 6, 30)),
)


def _in_scan(d: date) -> bool:
    if d.year == 2020 or (d.year == 2021 and d.month == 7):
        return True  # 2020 bubble restart; the 2020-21 Finals ran to 2021-07-20
    return not (7 <= d.month <= 9 and not (d.month == 9 and d.day >= 28))


def dates(end: date) -> list[date]:
    out: list[date] = []
    for start, stop in CHUNKS:
        cur, last = start, stop or end
        while cur <= min(last, end):
            if _in_scan(cur):
                out.append(cur)
            cur += timedelta(days=1)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--end", default="2026-11-19")
    parser.add_argument("--interval", type=float, default=0.35)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = resolve_out_root(args.out_root)
    games_dir = root / "games"
    index_path = games_dir / "scoreboard_index.jsonl"
    progress_path = games_dir / "scoreboard_progress.json"
    progress = read_json(progress_path, {"done": {}, "failed": []})
    done: dict[str, int] = progress["done"]
    fetcher = Fetcher(args.interval)
    todo = [d for d in dates(date.fromisoformat(args.end)) if d.isoformat() not in done]
    _logger.info("scoreboard dates to fetch: %d (done %d)", len(todo), len(done))
    failed: set[str] = set(progress.get("failed", []))
    for i, d in enumerate(todo):
        status, payload = fetcher.get_json(URL.format(d=d.strftime("%Y%m%d")))
        key = d.isoformat()
        if status != 200 or payload is None:
            failed.add(key)
            _logger.warning("scoreboard %s failed status=%s", key, status)
            continue
        games = ESPNClient.completed_games(payload)
        n_events = len(payload.get("events") or [])
        for g in games:
            g["league"] = "NBA"
            g["scoreboard_date"] = key
        append_jsonl(index_path, games)
        # Done is recorded after the append; a crash between the two can duplicate a date's rows,
        # which the consumers dedupe by event_id.
        done[key] = n_events
        failed.discard(key)
        if i % 25 == 0 or i == len(todo) - 1:
            progress.update(done=done, failed=sorted(failed), updated_at_utc=utc_now_iso())
            write_json_atomic(progress_path, progress)
            _logger.info("[%d/%d] %s events=%d completed=%d", i + 1, len(todo), key, n_events, len(games))
    progress.update(done=done, failed=sorted(failed), updated_at_utc=utc_now_iso())
    write_json_atomic(progress_path, progress)
    _logger.info("done: dates=%d failed=%d requests=%d", len(done), len(failed), fetcher.requests)


if __name__ == "__main__":
    main()
