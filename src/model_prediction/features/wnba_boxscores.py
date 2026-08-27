"""WNBA ESPN boxscore loader feeding point-in-time team game logs.

Raw ESPN boxscore captures under ``data/availability/wnba/espn_boxscores/``
carry team-level stat groups (FG/FGA, 3P/3PA, FT/FTA, OReb, DReb, TOV) but
not the final score -- scores come from the games file. This module parses
one boxscore payload into per-team stat dicts and merges the score into the
four-factors game-log shape the totals builder needs. Point-in-time
correctness is the caller's contract: only boxscores of games already
completed (strictly prior) may be passed on; the totals builder enforces
that by appending each game's log to per-team deques only after the row for
that game has been built (same pattern as the 2026-08-18 last-10 fix).

A malformed or missing boxscore contributes no game log; the four-factors
module then falls back to the league pace prior (its empty-log behavior),
so a capture gap degrades to the league baseline, never to a guess.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# ESPN team-stat group names in a boxscore payload
_FG_GROUP = "fieldGoalsMade-fieldGoalsAttempted"
_3P_GROUP = "threePointFieldGoalsMade-threePointFieldGoalsAttempted"
_FT_GROUP = "freeThrowsMade-freeThrowsAttempted"
_TOV_GROUP = "totalTurnovers"  # includes team turnovers (Hollinger possession formula)
_TOV_FALLBACK_GROUP = "turnovers"
_OREB_GROUP = "offensiveRebounds"
_DREB_GROUP = "defensiveRebounds"


def _split_made_attempt(value: str) -> tuple[float, float]:
    """Parse ESPN's '33-69' made-attempt string into floats (0.0 on junk)."""
    try:
        made, attempt = str(value).split("-", 1)
        return float(made), float(attempt)
    except (ValueError, TypeError):
        return 0.0, 0.0


def parse_wnba_boxscore_team_stats(raw: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Parse one raw boxscore file into ``{team displayName: stat dict}``.

    Stat dict keys mirror what ``wnba_pace_four_factors.compute_team_four_factors``
    reads (minus the score, which the caller merges in).
    """
    boxscore = raw["payload"]["boxscore"]
    teams: dict[str, dict[str, float]] = {}
    for entry in boxscore.get("teams", []):
        name = str(entry["team"]["displayName"])
        stats = {s.get("name"): s.get("displayValue") for s in entry.get("statistics", [])}
        fgm, fga = _split_made_attempt(stats.get(_FG_GROUP, "0-0"))
        fg3m, _ = _split_made_attempt(stats.get(_3P_GROUP, "0-0"))
        _, fta = _split_made_attempt(stats.get(_FT_GROUP, "0-0"))
        tov_value = stats.get(_TOV_GROUP) or stats.get(_TOV_FALLBACK_GROUP)
        teams[name] = {
            "fgm": fgm,
            "fga": fga,
            "fg3m": fg3m,
            "fta": fta,
            "tov": float(tov_value) if tov_value is not None else 0.0,
            "oreb": float(stats.get(_OREB_GROUP) or 0.0),
            "dreb": float(stats.get(_DREB_GROUP) or 0.0),
        }
    return teams


def load_wnba_boxscore_files(directory: Path) -> dict[str, dict[str, dict[str, float]]]:
    """Load every ``*.json`` in ``directory`` as ``{event_id: {team: stats}}``.

    Files that are missing, malformed, or lack a parseable team section are
    skipped -- the game simply contributes no pace information (league prior).
    """
    result: dict[str, dict[str, dict[str, float]]] = {}
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        try:
            result[path.stem] = parse_wnba_boxscore_team_stats(raw)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def build_wnba_four_factors_logs(
    home_team: str,
    away_team: str,
    home_score: float,
    away_score: float,
    team_stats: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]] | None:
    """Merge a parsed boxscore with its final score into four-factors game logs.

    Returns ``{team displayName: log}`` where each log carries the keys
    ``wnba_pace_four_factors`` reads: points, opp_points, fgm, fga, fg3m,
    fta, tov, oreb, opp_dreb. Returns ``None`` when the boxscore does not
    cover both teams (no partial credit -- an incomplete capture is not
    usable for either side's pace).
    """
    stats = dict(team_stats)
    home_stats = stats.get(home_team)
    away_stats = stats.get(away_team)
    if home_stats is None or away_stats is None:
        return None
    logs: dict[str, dict[str, float]] = {}
    for team, mine, opponent, scored, allowed in (
        (home_team, home_stats, away_stats, home_score, away_score),
        (away_team, away_stats, home_stats, away_score, home_score),
    ):
        logs[team] = {
            "points": float(scored),
            "opp_points": float(allowed),
            "fgm": float(mine["fgm"]),
            "fga": float(mine["fga"]),
            "fg3m": float(mine["fg3m"]),
            "fta": float(mine["fta"]),
            "tov": float(mine["tov"]),
            "oreb": float(mine["oreb"]),
            "opp_dreb": float(opponent["dreb"]),
        }
    return logs
