"""First-seen, terminal-only NCAAB team box-score capture for future research."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..domain import parse_utc, utc_now
from .espn import ESPNClient

REQUIRED_STATS = (
    "fieldGoalsMade-fieldGoalsAttempted",
    "freeThrowsMade-freeThrowsAttempted",
    "offensiveRebounds",
    "turnovers",
)


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


def capture_completed_boxscores(
    data_root: str | Path, game_date: str, client: ESPNClient | None = None, max_workers: int = 4
) -> dict[str, Any]:
    """Capture each final game once; report gaps and never rewrite first-seen evidence."""
    espn = client or ESPNClient()
    board = espn.scoreboard("NCAAB", game_date)
    completed = espn.completed_games(board)
    root = Path(data_root) / "features" / "ncaab" / "boxscores" / game_date
    if max_workers < 1:
        raise ValueError("max_workers_must_be_positive")

    def capture_one(game: dict[str, Any]) -> tuple[str, str, str | None]:
        event_id = str(game["event_id"])
        path = root / f"{event_id}.json"
        if path.exists():
            return event_id, "existing", None
        try:
            observed = utc_now().isoformat()
            summary = espn.summary("NCAAB", event_id)
            record = parse_completed_boxscore(game, summary, observed)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return event_id, "missing", str(exc)
        root.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, allow_nan=False)
                handle.write("\n")
            return event_id, "captured", None
        except FileExistsError:
            return event_id, "existing", None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        outcomes = list(pool.map(capture_one, completed))
    captured = sum(status == "captured" for _, status, _ in outcomes)
    existing = sum(status == "existing" for _, status, _ in outcomes)
    missing = [
        {"event_id": event_id, "reason": str(reason)}
        for event_id, status, reason in outcomes
        if status == "missing"
    ]
    return {
        "date": game_date,
        "completed": len(completed),
        "captured": captured,
        "existing": existing,
        "missing": missing,
        "path": str(root),
    }
