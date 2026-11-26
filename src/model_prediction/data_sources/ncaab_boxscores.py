"""First-seen, terminal-only NCAAB box-score capture: team possession components and player boxes.

One ESPN summary per final game feeds both records. The player record keeps the research schema
(``ncaab-player-boxes-v1``, ``scripts/backfill_ncaab_player_boxes.py``) so the research feature builder
and ``features/ncaab_player_availability.py`` read the live tree unchanged.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..domain import parse_utc, utc_now
from .espn import LEAGUE_PATHS, SITE_API, ESPNClient

REQUIRED_STATS = (
    "fieldGoalsMade-fieldGoalsAttempted",
    "freeThrowsMade-freeThrowsAttempted",
    "offensiveRebounds",
    "turnovers",
)

PLAYER_BOX_SCHEMA = "ncaab-player-boxes-v1"
PLAYER_BOX_SUMMARY_URL = f"{SITE_API}/{LEAGUE_PATHS['NCAAB']}/summary"
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
    """Write once; False when another writer got there first (first-seen evidence is never rewritten)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except FileExistsError:
        return False
    return True


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
    root = Path(data_root) / "features" / "ncaab" / "boxscores" / game_date
    player_root = player_box_root(data_root) / game_date
    if max_workers < 1:
        raise ValueError("max_workers_must_be_positive")

    def capture_one(game: dict[str, Any]) -> tuple[str, tuple[str, str | None], tuple[str, str | None]]:
        event_id = str(game["event_id"])
        path = root / f"{event_id}.json"
        player_path = player_root / f"{event_id}.json"
        team_done, player_done = path.exists(), player_path.exists()
        if team_done and player_done:
            return event_id, ("existing", None), ("existing", None)
        try:
            observed = utc_now().isoformat()
            summary = espn.summary("NCAAB", event_id)
            # Stamped after the fetch: the record must not claim to be known before it was.
            fetched = utc_now().isoformat()
        except (KeyError, TypeError, ValueError, OSError) as exc:
            failed = ("missing", str(exc))
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
                player_record = parse_player_boxes({**game, "game_date": game_date}, summary, fetched)
                if _write_first_seen(player_path, player_record):
                    player_outcome = ("captured", None)
            except (KeyError, TypeError, ValueError, OSError) as exc:
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
