"""NBA v10 research box scores 2016-17 .. 2023-24 from ESPN summaries (quick-261120-nbf).

Commands:
    fetch   one ESPN summary per completed regular/play-in/post-season event in the research
            scoreboard index (ESPN season_year 2017..2024). Stores
            ``games/espn_boxscores/<event_id>.json`` = ``{event_id, fetched_at_utc, payload}`` with
            the payload trimmed (``nba_backfill_box.KEEP_KEYS``) -- the shape
            ``features.nba_player_logs.load_nba_player_boxscores`` reads -- and the play-by-play as
            ``games/espn_plays/<event_id>.json.gz``. Resumable: an event whose file exists is skipped.
    build   stream every stored file into ``games/player_box.jsonl`` and ``games/team_box.jsonl``
            (rewritten from scratch; deterministic, event_id order).

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe \
        scripts/nba_v10_backfill/espn_boxscores.py fetch|build
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from pathlib import Path
from typing import Any

from model_prediction.data_sources.nba_backfill_box import parse_player_rows, parse_team_rows, trim_payload
from model_prediction.data_sources.nba_backfill_common import (
    Fetcher,
    read_json,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)

_logger = logging.getLogger("nba_v10.boxscores")
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}"
SEASONS = range(2017, 2025)  # ESPN season_year = spring year: 2016-17 .. 2023-24
SKIP_SEASON_TYPES = {"preseason", "off-season"}
SKIP_COMPETITION_TYPES = {"ALLSTAR"}


def load_index(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    path = root / "games" / "scoreboard_index.jsonl"
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            out[str(row["event_id"])] = row
    return out


def targets(index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        r
        for r in index.values()
        if r.get("season_year") in SEASONS
        and r.get("season_type") not in SKIP_SEASON_TYPES
        and r.get("competition_type") not in SKIP_COMPETITION_TYPES
    ]
    return sorted(rows, key=lambda r: (r.get("event_start_utc") or "", r["event_id"]))


def fetch(root: Path, interval: float) -> None:
    box_dir = root / "games" / "espn_boxscores"
    plays_dir = root / "games" / "espn_plays"
    box_dir.mkdir(parents=True, exist_ok=True)
    plays_dir.mkdir(parents=True, exist_ok=True)
    progress_path = root / "games" / "boxscore_progress.json"
    progress = read_json(progress_path, {"failed": {}})
    todo = [r for r in targets(load_index(root)) if not (box_dir / f"{r['event_id']}.json").exists()]
    _logger.info("summaries to fetch: %d", len(todo))
    fetcher = Fetcher(interval)
    fetched = 0
    for i, row in enumerate(todo):
        eid = row["event_id"]
        status, payload = fetcher.get_json(URL.format(eid=eid))
        if status != 200 or payload is None or not (payload.get("boxscore") or {}).get("players"):
            progress["failed"][eid] = {"status": status, "at": utc_now_iso()}
            _logger.warning("summary %s failed/empty status=%s", eid, status)
            continue
        plays = payload.get("plays")
        if plays:
            with gzip.open(plays_dir / f"{eid}.json.gz", "wt", encoding="utf-8") as fh:
                json.dump({"event_id": eid, "plays": plays}, fh)
        body = {"event_id": eid, "fetched_at_utc": utc_now_iso(), "payload": trim_payload(payload)}
        tmp = box_dir / f"{eid}.json.tmp"
        tmp.write_text(json.dumps(body), encoding="utf-8")
        tmp.replace(box_dir / f"{eid}.json")
        progress["failed"].pop(eid, None)
        fetched += 1
        if fetched % 100 == 0 or i == len(todo) - 1:
            progress.update(updated_at_utc=utc_now_iso(), fetched_this_run=fetched)
            write_json_atomic(progress_path, progress)
            _logger.info("[%d/%d] fetched=%d failed=%d", i + 1, len(todo), fetched, len(progress["failed"]))
    progress.update(updated_at_utc=utc_now_iso(), fetched_this_run=fetched)
    write_json_atomic(progress_path, progress)
    _logger.info("done fetched=%d failed=%d requests=%d", fetched, len(progress["failed"]), fetcher.requests)


def build(root: Path) -> None:
    box_dir = root / "games" / "espn_boxscores"
    player_path = root / "games" / "player_box.jsonl"
    team_path = root / "games" / "team_box.jsonl"
    n_games = n_players = n_teams = bad = 0
    with (
        player_path.with_suffix(".tmp").open("w", encoding="utf-8") as pf,
        team_path.with_suffix(".tmp").open("w", encoding="utf-8") as tf,
    ):
        for path in sorted(box_dir.glob("*.json"), key=lambda p: int(p.stem)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bad += 1
                continue
            payload = data.get("payload") or {}
            players = parse_player_rows(payload, path.stem)
            teams = parse_team_rows(payload, path.stem)
            if not players or len(teams) != 2:
                bad += 1
            for row in players:
                pf.write(json.dumps(row, sort_keys=True) + "\n")
            for row in teams:
                tf.write(json.dumps(row, sort_keys=True) + "\n")
            n_games += 1
            n_players += len(players)
            n_teams += len(teams)
    player_path.with_suffix(".tmp").replace(player_path)
    team_path.with_suffix(".tmp").replace(team_path)
    _logger.info("built games=%d player_rows=%d team_rows=%d bad=%d", n_games, n_players, n_teams, bad)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("fetch", "build"))
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--interval", type=float, default=0.4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = resolve_out_root(args.out_root)
    if args.command == "fetch":
        fetch(root, args.interval)
    else:
        build(root)


if __name__ == "__main__":
    main()
