"""First-seen, terminal-only NCAAB box-score capture: team possession components and player boxes.

One ESPN summary per final game feeds both records. The player record keeps the research schema
(``ncaab-player-boxes-v1``, ``scripts/backfill_ncaab_player_boxes.py``) so the research feature builder
and ``features/ncaab_player_availability.py`` read the live tree. The research builder keys on the
scoreboard fields the summary does not carry (``neutral``, ``d1_vs_d1``, conference ids), so capture copies
them from the same scoreboard it already fetched (``_scoreboard_context``); a game missing from that
scoreboard keeps them ``None``, as the research backfill does for the same case.

Writes are atomic (temp file, fsync, hard link) so a kill never leaves a truncated first-seen record, and
a record that does not parse is quarantined and re-captured rather than trusted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..domain import eastern_today, parse_utc, utc_now
from .espn import LEAGUE_PATHS, SITE_API, ESPNClient

REQUIRED_STATS = (
    "fieldGoalsMade-fieldGoalsAttempted",
    "freeThrowsMade-freeThrowsAttempted",
    "offensiveRebounds",
    "turnovers",
)

PLAYER_BOX_SCHEMA = "ncaab-player-boxes-v1"
PLAYER_BOX_SUMMARY_URL = f"{SITE_API}/{LEAGUE_PATHS['NCAAB']}/summary"
# A summary whose player block never parses is refetched this many times, then left alone (daily runs
# many times a day; without a limit every run would hit ESPN again for the same unparseable game).
MAX_PLAYER_PARSE_ATTEMPTS = 2
_logger = logging.getLogger(__name__)
# ESPN stat key -> output name(s); "made-attempted" keys split into two integers. Same map as the
# research backfill, whose records the feature definitions were fitted on.
_SPLIT_KEYS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}
_INT_KEYS = {
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
_STAT_FIELDS = (
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


def player_box_root(data_root: str | Path) -> Path:
    return Path(data_root) / "features" / "ncaab" / "player_boxes"


def _attempts(value: str) -> int:
    made, attempts = str(value).split("-", 1)
    if not made.isdigit() or not attempts.isdigit() or int(made) > int(attempts):
        raise ValueError("invalid_attempts_stat")
    return int(attempts)


def parse_completed_boxscore(
    game: dict[str, Any], summary: dict[str, Any], observed_at_utc: str
) -> dict[str, Any]:
    """Keep raw possession components; do not infer a possession formula here."""
    event_id = str(game["event_id"])
    if game.get("status") != "completed":
        raise ValueError("ncaab_event_not_terminal")
    header = (summary.get("header") or {}).get("competitions") or []
    if (
        not header
        or str(header[0].get("id")) != event_id
        or not header[0].get("status", {}).get("type", {}).get("completed")
    ):
        raise ValueError("ncaab_summary_not_matching_terminal_event")
    if parse_utc(observed_at_utc) <= parse_utc(str(game["event_start_utc"])):
        raise ValueError("ncaab_boxscore_observed_before_start")
    teams = {
        str(row.get("team", {}).get("id")): row for row in (summary.get("boxscore") or {}).get("teams", [])
    }
    parsed = {}
    for side in ("home", "away"):
        team_id = str(game[f"{side}_team_id"])
        team = teams.get(team_id)
        if team is None:
            raise ValueError(f"ncaab_{side}_team_boxscore_missing")
        stats = {str(stat.get("name")): stat.get("displayValue") for stat in team.get("statistics", [])}
        if any(stats.get(name) in (None, "") for name in REQUIRED_STATS):
            raise ValueError(f"ncaab_{side}_possession_components_missing")
        parsed[side] = {
            "team_id": team_id,
            "team_name": str(game[f"{side}_team"]),
            "points": int(game[f"{side}_score"]),
            "field_goal_attempts": _attempts(stats[REQUIRED_STATS[0]]),
            "free_throw_attempts": _attempts(stats[REQUIRED_STATS[1]]),
            "offensive_rebounds": int(stats["offensiveRebounds"]),
            "turnovers": int(stats["turnovers"]),
        }
        if (
            min(
                parsed[side][name]
                for name in (
                    "points",
                    "field_goal_attempts",
                    "free_throw_attempts",
                    "offensive_rebounds",
                    "turnovers",
                )
            )
            < 0
        ):
            raise ValueError("ncaab_negative_stat")
    source_hash = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": "ncaab-boxscore-components-v1",
        "event_id": event_id,
        "event_start_utc": game["event_start_utc"],
        "observed_at_utc": observed_at_utc,
        "source": "espn:NCAAB:summary",
        "source_sha256": source_hash,
        "status": "completed",
        "home": parsed["home"],
        "away": parsed["away"],
    }


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
        if key in _SPLIT_KEYS:
            made_name, att_name = _SPLIT_KEYS[key]
            out[made_name], out[att_name] = _split(value)
        elif key in _INT_KEYS:
            out[_INT_KEYS[key]] = _int_or_none(value)
    return out


def parse_player_boxes(game: dict[str, Any], summary: dict[str, Any], observed_at_utc: str) -> dict[str, Any]:
    """Compact per-athlete record for both teams of one terminal event.

    A copy of ``scripts/backfill_ncaab_player_boxes.py::parse_player_boxes`` (``src`` must not import
    ``scripts``, and the research script is hash-pinned, so it is not edited);
    ``tests/test_ncaab_player_availability.py`` pins equality on a real summary. Stats are mapped through
    each block's own ``keys`` array, never by position.
    """
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
                            **{name: None for name in _STAT_FIELDS},
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
        "schema": PLAYER_BOX_SCHEMA,
        "event_id": event_id,
        "game_date": game["game_date"],
        "season_year": game["season_year"],
        "event_start_utc": game["event_start_utc"],
        "neutral": game.get("neutral"),
        "d1_vs_d1": game.get("d1_vs_d1"),
        "observed_at_utc": observed_at_utc,
        "source": "espn:NCAAB:summary",
        "source_url": PLAYER_BOX_SUMMARY_URL,
        "source_sha256": source_hash,
        "status_detail": status.get("detail"),
        "home": teams["home"],
        "away": teams["away"],
    }


def _write_first_seen(path: Path, record: dict[str, Any]) -> bool:
    """Write once; False when another writer got there first (first-seen evidence is never rewritten).

    The record is written and fsynced to a temp file in the same directory, then hard-linked to its final
    name: ``os.link`` fails when the destination exists, which keeps first-seen semantics, and the final
    name only ever appears complete. Writing in place left a truncated file after a kill that the
    exists-check then treated as captured forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Not ``*.json``: the index loader and the corrupt-file sweep must never see a half-written temp.
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        except OSError:
            # A filesystem without hard links: rename refuses an existing target on Windows; the exists
            # check covers POSIX, where only a concurrent writer inside this window could be overwritten.
            if path.exists():
                return False
            os.rename(temp, path)
        return True
    finally:
        temp.unlink(missing_ok=True)


def _usable_record(path: Path) -> bool:
    """True when ``path`` holds a parseable JSON object; a file that does not parse is quarantined.

    A kill mid-write (before writes were atomic) or disk damage leaves a file the exists-check would
    count as captured forever. It is renamed aside (``*.corrupt-<stamp>``, kept for the audit, not matched
    by the loader's ``*.json``) so the caller captures the game again.
    """
    if not path.exists():
        return False
    try:
        if isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
            return True
    except OSError:
        # Unreadable right now (lock, antivirus): not evidence of corruption, so never move it.
        _logger.warning("NCAAB box record unreadable, left in place: %s", path, exc_info=True)
        return True
    except ValueError:
        pass
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    try:
        path.replace(path.with_name(f"{path.name}.corrupt-{stamp}"))
    except OSError:
        _logger.warning("unparseable NCAAB box record could not be quarantined: %s", path, exc_info=True)
        return True  # never loop on a file we cannot move; the loader skips it either way
    _logger.warning("quarantined unparseable NCAAB box record %s", path)
    return False


def _parse_failure_marker(player_path: Path) -> Path:
    return player_path.with_name(f".{player_path.stem}.parse_failed")


def _parse_failures(marker: Path) -> int:
    try:
        return int(json.loads(marker.read_text(encoding="utf-8"))["attempts"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _mark_parse_failure(marker: Path, attempts: int, reason: str) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"attempts": attempts, "reason": reason, "last_attempt_utc": utc_now().isoformat()}),
            encoding="utf-8",
        )
    except OSError:
        _logger.warning("could not record the NCAAB player parse failure at %s", marker, exc_info=True)


def _scoreboard_context(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per event id: the scoreboard-only fields the research player record carries.

    ``ESPNClient.completed_games`` drops them. Same definitions as the research scoreboard
    (``scripts/ncaab_espn_lines.py::slim_event``, ``backfill_ncaab_player_boxes.py::iter_events``): neutral
    site, each team's ``conferenceId``, and D1-vs-D1 as both ids present.
    """
    out: dict[str, dict[str, Any]] = {}
    for event in (board or {}).get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c.get("team") or {} for c in competition.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            continue
        home_conf, away_conf = teams["home"].get("conferenceId"), teams["away"].get("conferenceId")
        out[str(event.get("id"))] = {
            "neutral": bool(competition.get("neutralSite")),
            "home_conference_id": home_conf,
            "away_conference_id": away_conf,
            "d1_vs_d1": home_conf is not None and away_conf is not None,
        }
    return out


def capture_completed_boxscores(
    data_root: str | Path, game_date: str, client: ESPNClient | None = None, max_workers: int = 4
) -> dict[str, Any]:
    """Capture each final game once; report gaps and never rewrite first-seen evidence.

    The team record and the player record come from the same summary: it is fetched once, only when
    either file is absent, and each record is parsed and written on its own so a failure in one never
    blocks the other. ``captured``/``existing``/``missing`` keep describing the team record (daily.py
    derives its status from ``missing``); the player record reports under ``player_boxes``.
    """
    espn = client or ESPNClient()
    board = espn.scoreboard("NCAAB", game_date)
    completed = espn.completed_games(board)
    context = _scoreboard_context(board)
    root = Path(data_root) / "features" / "ncaab" / "boxscores" / game_date
    player_root = player_box_root(data_root) / game_date
    if max_workers < 1:
        raise ValueError("max_workers_must_be_positive")

    def capture_one(game: dict[str, Any]) -> tuple[str, tuple[str, str | None], tuple[str, str | None]]:
        event_id = str(game["event_id"])
        path = root / f"{event_id}.json"
        player_path = player_root / f"{event_id}.json"
        marker = _parse_failure_marker(player_path)
        team_done, player_done = _usable_record(path), _usable_record(player_path)
        if team_done and player_done:
            return event_id, ("existing", None), ("existing", None)
        if team_done:
            # Only the player record would trigger this fetch: stop when its parse has failed enough times.
            failures = _parse_failures(marker)
            if failures >= MAX_PLAYER_PARSE_ATTEMPTS:
                return event_id, ("existing", None), ("missing", f"player_parse_failed_{failures}_attempts")
        try:
            observed = utc_now().isoformat()
            summary = espn.summary("NCAAB", event_id)
            # Stamped after the fetch: the record must not claim to be known before it was.
            fetched = utc_now().isoformat()
        except (KeyError, TypeError, ValueError, OSError, httpx.HTTPError) as exc:
            # HTTPError (429, 5xx, transport) is per game: one bad fetch must not abort the pool and mark
            # the whole day's capture "error" in daily.
            failed = ("missing", str(exc) or type(exc).__name__)
            return (
                event_id,
                ("existing", None) if team_done else failed,
                ("existing", None) if player_done else failed,
            )
        team_outcome: tuple[str, str | None] = ("existing", None)
        if not team_done:
            try:
                record = parse_completed_boxscore(game, summary, observed)
                team_outcome = ("captured", None) if _write_first_seen(path, record) else ("existing", None)
            except (KeyError, TypeError, ValueError, OSError) as exc:
                team_outcome = ("missing", str(exc))
        player_outcome: tuple[str, str | None] = ("existing", None)
        if not player_done:
            try:
                # Written even when ESPN lists no players, as the research backfill does: "fetched,
                # no usable box" (the feature's BOX_QUALITY) must stay distinct from "never fetched".
                player_record = parse_player_boxes(
                    {**game, **context.get(event_id, {}), "game_date": game_date}, summary, fetched
                )
            except (KeyError, TypeError, ValueError) as exc:
                reason = str(exc) or type(exc).__name__
                player_outcome = ("missing", reason)
                _mark_parse_failure(marker, _parse_failures(marker) + 1, reason)
            else:
                try:
                    if _write_first_seen(player_path, player_record):
                        player_outcome = ("captured", None)
                        marker.unlink(missing_ok=True)
                except OSError as exc:
                    player_outcome = ("missing", str(exc))
        return event_id, team_outcome, player_outcome

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        outcomes = list(pool.map(capture_one, completed))

    def tally(index: int) -> dict[str, Any]:
        return {
            "captured": sum(o[index][0] == "captured" for o in outcomes),
            "existing": sum(o[index][0] == "existing" for o in outcomes),
            "missing": [
                {"event_id": o[0], "reason": str(o[index][1])} for o in outcomes if o[index][0] == "missing"
            ],
        }

    team = tally(1)
    return {
        "date": game_date,
        "completed": len(completed),
        **team,
        "path": str(root),
        "player_boxes": {**tally(2), "path": str(player_root)},
    }


def backfill_player_boxes(
    data_root: str | Path,
    since: str,
    until: str | None = None,
    client: ESPNClient | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Capture every final from ``since`` through ``until`` (default: today, Eastern) once per date.

    The one-time catch-up for the dates before the live capture existed (daily only ever captures the
    previous day). Idempotent: each date goes through ``capture_completed_boxscores``, so existing records
    are not refetched or rewritten and a rerun only fills gaps. Records are stamped with the observation
    time of the fetch, never backdated, so a backfilled game is invisible to any decision made before it.
    A date that fails is reported and the walk continues.
    """
    first = date.fromisoformat(since)
    last = date.fromisoformat(until) if until else eastern_today()
    if first > last:
        raise ValueError("since_after_until")
    days: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    day = first
    while day <= last:
        iso = day.isoformat()
        # One client per date unless the caller supplies one: ESPNClient caches every summary it fetches,
        # which across a month of finals is hundreds of MB.
        espn = client or ESPNClient()
        try:
            result = capture_completed_boxscores(data_root, iso, espn, max_workers)
            players = result["player_boxes"]
            days.append(
                {
                    "date": iso,
                    "completed": result["completed"],
                    "captured": result["captured"],
                    "existing": result["existing"],
                    "missing": result["missing"],
                    "player_boxes_captured": players["captured"],
                    "player_boxes_existing": players["existing"],
                    "player_boxes_missing": players["missing"],
                }
            )
        except (KeyError, TypeError, ValueError, OSError, httpx.HTTPError) as exc:
            _logger.warning("NCAAB player-box backfill failed for %s", iso, exc_info=True)
            errors.append({"date": iso, "reason": str(exc) or type(exc).__name__})
        finally:
            if client is None:
                espn.client.close()
        day += timedelta(days=1)
    return {
        "since": first.isoformat(),
        "until": last.isoformat(),
        "days": days,
        "captured": sum(d["captured"] for d in days),
        "player_boxes_captured": sum(d["player_boxes_captured"] for d in days),
        "player_boxes_missing": sum(len(d["player_boxes_missing"]) for d in days),
        "errors": errors,
    }
