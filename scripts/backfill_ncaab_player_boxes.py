"""Backfill NCAAB player box scores (ESPN game summary) for 2021-22..2025-26 into a RESEARCH tree.

The team-level backfill (``backfill_ncaab_boxscores_history.py``) kept only possession components and
discarded the raw summaries. Player-level availability and returning-production research (quick
261121-hmc lane P) needs the per-athlete box, so this script re-fetches the same ESPN summary and keeps
a compact per-player record.

Same conventions as the team-level backfill, on purpose:
- Events come from the archived scoreboard (``data/research/ncaab_market/scoreboard.jsonl``); the
  Eastern ``date`` of the scoreboard row is kept as ``game_date`` (the PIT cutoff key).
- Plain httpx, one request at a time, a polite pause, no in-memory response cache, the same generic
  User-Agent the repo already uses for this host.
- Output goes to ``data/research/ncaab_external/espn_player_boxes/<date>/<event_id>.json`` (never a
  serving tree). Resumable and append-only: an existing file is never rewritten; every attempt appends
  one line to ``_progress.jsonl`` in the output root.
- Stats are mapped through each block's own ``keys`` array, never by position.

Scope: ``--scope d1`` (default) is every completed game whose two teams both carry an ESPN conference
id; ``--scope non-d1`` is the remaining completed games (a D1 team against a non-D1 opponent), fetched
as a second pass so a D1 team's game log is complete; ``--scope all`` does both, D1 first.

Terminal box scores captured long after the games: history features only, read strictly before the
decision cutoff (games on an earlier Eastern date).

Usage:
    .venv/Scripts/python scripts/backfill_ncaab_player_boxes.py [--seasons 2022 2023 2024 2025 2026]
        [--scope d1|non-d1|all] [--pause 0.6] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_prediction.data_sources.espn import LEAGUE_PATHS, SITE_API
from model_prediction.domain import parse_utc

SCOREBOARD = ROOT / "data" / "research" / "ncaab_market" / "scoreboard.jsonl"
OUT_ROOT = ROOT / "data" / "research" / "ncaab_external" / "espn_player_boxes"
SUMMARY_URL = f"{SITE_API}/{LEAGUE_PATHS['NCAAB']}/summary"
USER_AGENT = "Mozilla/5.0 (compatible; research-data-backfill/1.0)"  # same generic UA as the team backfill
SCHEMA = "ncaab-player-boxes-v1"
MAX_CONSECUTIVE_FAILURES = 25  # abort rather than hammer a provider that is refusing us
RETRY_STATUSES = {403, 429}  # throttling answers: back off, then retry

# ESPN stat key -> (output name(s)). "made-attempted" keys split into two integers.
SPLIT_KEYS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}
INT_KEYS = {
    "minutes": "minutes",
    "points": "points",
    "rebounds": "reb",
    "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb",
    "assists": "ast",
    "turnovers": "tov",
    "steals": "stl",
    "blocks": "blk",
    "fouls": "pf",
}
STAT_FIELDS = (
    "minutes",
    "points",
    "fgm",
    "fga",
    "fg3m",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "tov",
    "stl",
    "blk",
    "pf",
)


class NotFoundError(Exception):
    """The provider answered 404 for this event: a data gap, not a provider fault."""


def iter_events(seasons: set[int], scope: str) -> list[tuple[str, dict[str, Any]]]:
    d1: list[tuple[str, dict[str, Any]]] = []
    other: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    with SCOREBOARD.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for event in row.get("events") or []:
                if event.get("season_year") not in seasons or not event.get("completed"):
                    continue
                event_id = str(event["event_id"])
                if event_id in seen:
                    continue
                seen.add(event_id)
                is_d1 = (
                    event.get("home_conference_id") is not None
                    and event.get("away_conference_id") is not None
                )
                game = {
                    "event_id": event_id,
                    "game_date": str(row["date"]),
                    "season_year": int(event["season_year"]),
                    "event_start_utc": event["start_utc"],
                    "neutral": event.get("neutral"),
                    "d1_vs_d1": is_d1,
                    "home_team_id": str(event["home_team_id"]),
                    "away_team_id": str(event["away_team_id"]),
                    "home_team": event["home_team"],
                    "away_team": event["away_team"],
                    "home_conference_id": event.get("home_conference_id"),
                    "away_conference_id": event.get("away_conference_id"),
                    "home_score": event.get("home_score"),
                    "away_score": event.get("away_score"),
                }
                (d1 if is_d1 else other).append((str(row["date"]), game))
    if scope == "d1":
        return d1
    if scope == "non-d1":
        return other
    return d1 + other


def fetch_summary(client: httpx.Client, event_id: str) -> dict[str, Any]:
    delay = 5.0
    for attempt in range(5):
        response = client.get(SUMMARY_URL, params={"event": event_id})
        if response.status_code == 404:
            raise NotFoundError(f"404 for event {event_id}")
        if response.status_code in RETRY_STATUSES or response.status_code >= 500:
            if attempt == 4:
                response.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")


def _int_or_none(value: Any) -> int | None:
    text = str(value).strip() if value is not None else ""
    if text in ("", "--", "-"):
        return None
    return int(text)


def _split(value: Any) -> tuple[int | None, int | None]:
    text = str(value).strip() if value is not None else ""
    if text in ("", "--", "-", "--/--"):
        return None, None
    made, attempted = text.split("-", 1)
    return int(made), int(attempted)


def _stats_from(keys: list[str], values: list[Any]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    if not values:
        return out
    if len(keys) != len(values):
        raise ValueError("stat_keys_values_length_mismatch")
    for key, value in zip(keys, values, strict=True):
        if key in SPLIT_KEYS:
            made_name, att_name = SPLIT_KEYS[key]
            out[made_name], out[att_name] = _split(value)
        elif key in INT_KEYS:
            out[INT_KEYS[key]] = _int_or_none(value)
    return out


def parse_player_boxes(game: dict[str, Any], summary: dict[str, Any], observed_at_utc: str) -> dict[str, Any]:
    """Compact per-athlete record for both teams of one terminal event."""
    event_id = str(game["event_id"])
    header = (summary.get("header") or {}).get("competitions") or []
    if not header or str(header[0].get("id")) != event_id:
        raise ValueError("summary_event_id_mismatch")
    status = header[0].get("status", {}).get("type", {})
    if not status.get("completed"):
        raise ValueError("summary_event_not_terminal")
    if parse_utc(observed_at_utc) <= parse_utc(str(game["event_start_utc"])):
        raise ValueError("observed_before_start")
    competitors = {str(c.get("id")): c for c in header[0].get("competitors") or []}
    blocks = {
        str((row.get("team") or {}).get("id")): row
        for row in (summary.get("boxscore") or {}).get("players") or []
    }
    teams: dict[str, Any] = {}
    for side in ("home", "away"):
        team_id = str(game[f"{side}_team_id"])
        comp = competitors.get(team_id) or {}
        linescores = comp.get("linescores")
        block = blocks.get(team_id)
        players: list[dict[str, Any]] = []
        totals: dict[str, int | None] = {}
        stat_keys: list[str] = []
        if block is not None:
            by_id: dict[str, dict[str, Any]] = {}
            for stat_block in block.get("statistics") or []:
                keys = [str(k) for k in stat_block.get("keys") or []]
                stat_keys = stat_keys or keys
                if stat_block.get("totals"):
                    totals.update(_stats_from(keys, stat_block["totals"]))
                for entry in stat_block.get("athletes") or []:
                    athlete = entry.get("athlete") or {}
                    athlete_id = athlete.get("id")
                    # ESPN occasionally lists an athlete with no id (seen: DNP rows carrying only a
                    # shortName). Keep the row for the audit; features join on id and skip it.
                    key = str(athlete_id) if athlete_id is not None else f"noid:{len(players)}"
                    record = by_id.get(key)
                    if record is None:
                        record = {
                            "athlete_id": str(athlete_id) if athlete_id is not None else None,
                            "name": athlete.get("displayName") or athlete.get("shortName"),
                            "jersey": athlete.get("jersey"),
                            "position": (athlete.get("position") or {}).get("abbreviation"),
                            "starter": entry.get("starter"),
                            "did_not_play": entry.get("didNotPlay"),
                            "reason": entry.get("reason"),
                            "ejected": entry.get("ejected"),
                            "active": entry.get("active"),
                            "has_stats": bool(entry.get("stats")),
                            **{name: None for name in STAT_FIELDS},
                        }
                        by_id[key] = record
                        players.append(record)
                    for name, value in _stats_from(keys, entry.get("stats") or []).items():
                        if record.get(name) is None:
                            record[name] = value
                    record["has_stats"] = record["has_stats"] or bool(entry.get("stats"))
        teams[side] = {
            "team_id": team_id,
            "team_name": str(game[f"{side}_team"]),
            "conference_id": game.get(f"{side}_conference_id"),
            "score_header": _int_or_none(comp.get("score")),
            "score_scoreboard": game.get(f"{side}_score"),
            "periods": len(linescores) if isinstance(linescores, list) else None,
            "player_box_available": bool(players),
            "stat_keys": stat_keys,
            "totals": totals,
            "players": players,
        }
    source_hash = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": SCHEMA,
        "event_id": event_id,
        "game_date": game["game_date"],
        "season_year": game["season_year"],
        "event_start_utc": game["event_start_utc"],
        "neutral": game.get("neutral"),
        "d1_vs_d1": game.get("d1_vs_d1"),
        "observed_at_utc": observed_at_utc,
        "source": "espn:NCAAB:summary",
        "source_url": SUMMARY_URL,
        "source_sha256": source_hash,
        "status_detail": status.get("detail"),
        "home": teams["home"],
        "away": teams["away"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--scope", choices=("d1", "non-d1", "all"), default="d1")
    ap.add_argument("--pause", type=float, default=0.6, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="stop after N fetch attempts (pilot runs)")
    ap.add_argument("--stride", type=int, default=1, help="take every Nth pending event (pilot sampling)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if max(args.seasons) >= 2027:
        raise SystemExit("season 2027 (2026-27) is locked for this research program")

    events = iter_events(set(args.seasons), args.scope)
    todo = [(day, game) for day, game in events if not (OUT_ROOT / day / f"{game['event_id']}.json").exists()]
    todo = todo[:: max(args.stride, 1)]
    print(
        f"{len(events)} events in scope ({args.scope}), {len(events) - len(todo)} on disk or skipped, "
        f"{len(todo)} to fetch",
        flush=True,
    )
    if args.dry_run:
        return
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    progress = OUT_ROOT / "_progress.jsonl"
    tally: Counter[str] = Counter()
    consecutive_failures = 0
    started = time.time()
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        for i, (day, game) in enumerate(todo, 1):
            if args.limit and i > args.limit:
                break
            path = OUT_ROOT / day / f"{game['event_id']}.json"
            status, reason = "captured", None
            try:
                summary = fetch_summary(client, game["event_id"])
                observed = datetime.now(UTC).isoformat()
                record = parse_player_boxes(game, summary, observed)
                if not (record["home"]["player_box_available"] and record["away"]["player_box_available"]):
                    status = "captured_partial"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(record, handle, sort_keys=True, allow_nan=False)
                    handle.write("\n")
                consecutive_failures = 0
            except FileExistsError:
                status = "existing"
            except NotFoundError as exc:
                status, reason = "not_found", str(exc)
                consecutive_failures = 0
            except (KeyError, TypeError, ValueError) as exc:
                # Fetched fine but unparseable or not terminal: a data gap, not a provider fault.
                status, reason = "missing", f"{type(exc).__name__}: {exc}"
                consecutive_failures = 0
            except (httpx.HTTPError, OSError) as exc:
                status, reason = "error", f"{type(exc).__name__}: {exc}"
                consecutive_failures += 1
            tally[status] += 1
            with progress.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at_utc": datetime.now(UTC).isoformat(),
                            "date": day,
                            "event_id": game["event_id"],
                            "scope": "d1" if game["d1_vs_d1"] else "non-d1",
                            "status": status,
                            "reason": reason,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            if i % 200 == 0:
                rate = i / max(time.time() - started, 1e-9)
                print(f"[{i}/{len(todo)}] {day} {dict(tally)} {rate:.2f}/s", flush=True)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"aborting after {consecutive_failures} consecutive provider errors: {reason}", flush=True
                )
                break
            time.sleep(args.pause)
    print(f"done in {time.time() - started:.0f}s: {dict(tally)}", flush=True)


if __name__ == "__main__":
    main()
