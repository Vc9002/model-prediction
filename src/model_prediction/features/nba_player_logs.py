"""NBA player-level boxscore logs (PIT feed for real per-player impact ratings).

Parses the ESPN boxscore captures under
``data/availability/nba/espn_boxscores/`` (same raw shape as the WNBA
captures parsed by ``features/wnba_player_logs.py`` -- ESPN's site API uses
an identical payload structure across basketball leagues) into per-team
per-game player logs, then builds rolling per-player profiles.

Point-in-time correctness is the caller's contract: a game's log may only
be appended to a player's rolling window after that game's own decision
rows have been built (same discipline as the WNBA four-factors/player-logs
modules).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _display_to_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def _stat_value(raw: list[Any], index: dict[str, int], name: str) -> float:
    i = index.get(name)
    if i is None or i >= len(raw):
        return 0.0
    return _display_to_float(str(raw[i]))


def _stat_attempts(raw: list[Any], index: dict[str, int], name: str) -> float:
    i = index.get(name)
    if i is None or i >= len(raw):
        return 0.0
    try:
        return float(str(raw[i]).split("-")[1])
    except (ValueError, IndexError):
        return 0.0


def parse_nba_player_boxscore(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Parse one raw ESPN summary payload into ``{team displayName: [player rows]}``."""
    box = payload.get("boxscore") or {}
    players = box.get("players") or []
    out: dict[str, list[dict[str, Any]]] = {}
    for team_entry in players:
        team_name = (team_entry.get("team") or {}).get("displayName")
        if not team_name:
            continue
        stat_groups = team_entry.get("statistics") or []
        if not stat_groups:
            continue
        keys = stat_groups[0].get("keys") or []
        index = {name: i for i, name in enumerate(keys)}
        rows: list[dict[str, Any]] = []
        for athlete in stat_groups[0].get("athletes") or []:
            meta = athlete.get("athlete") or {}
            pid = meta.get("id")
            name = meta.get("displayName")
            if pid is None or name is None:
                continue
            raw = athlete.get("stats") or []
            minutes = _stat_value(raw, index, "minutes")
            rows.append(
                {
                    "player_id": str(pid),
                    "name": name,
                    "minutes": minutes,
                    "points": _stat_value(raw, index, "points"),
                    "rebounds": _stat_value(raw, index, "rebounds"),
                    "assists": _stat_value(raw, index, "assists"),
                    "steals": _stat_value(raw, index, "steals"),
                    "blocks": _stat_value(raw, index, "blocks"),
                    "fga": _stat_attempts(raw, index, "fieldGoalsMade-fieldGoalsAttempted"),
                    "fta": _stat_attempts(raw, index, "freeThrowsMade-freeThrowsAttempted"),
                    "turnovers": _stat_value(raw, index, "turnovers"),
                    "starter": bool(athlete.get("starter")),
                    "active": bool(athlete.get("active")) and minutes > 0,
                }
            )
        if rows:
            out[team_name] = rows
    return out


def load_nba_player_boxscores(directory: str | Path) -> dict[str, dict[str, Any]]:
    """Parse every boxscore file into ``{event_id: {team: [player rows]}}``.

    A malformed or missing capture contributes nothing (fail-soft -- the
    caller's feature falls back to the static tier lookup for that game).
    """
    root = Path(directory)
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            continue
        try:
            parsed = parse_nba_player_boxscore(payload)
        except (KeyError, TypeError, ValueError):
            continue
        if parsed:
            out[path.stem] = parsed
    return out


def box_plus_minus_proxy(row: dict[str, Any]) -> float:
    """Simplified box-score-derived per-game point-value proxy.

    Not a full BPM/RAPM (no lineup/possession/on-off data exists in this
    repo for NBA) -- a linear combination of the box-score counting stats
    most correlated with on-court value, scaled to roughly a "points of
    team impact" unit so it is comparable to the static tier constants
    (1.1 / 2.5 / 4.8) it is meant to replace. Weights follow the common
    simple-BPM-style box-score formula (points + assists*1.5 + rebounds*0.7
    + steals*2.0 + blocks*2.0 - turnovers*1.3 - (fga-points/2)*0.3 for
    shooting inefficiency), averaged per 36 minutes so playing time doesn't
    dominate the signal, then divided by 10 to land in a per-game-swing
    range similar to the static tiers.
    """
    minutes = row.get("minutes", 0.0)
    if minutes <= 0:
        return 0.0
    per36 = 36.0 / minutes
    raw = (
        row.get("points", 0.0)
        + 1.5 * row.get("assists", 0.0)
        + 0.7 * row.get("rebounds", 0.0)
        + 2.0 * row.get("steals", 0.0)
        + 2.0 * row.get("blocks", 0.0)
        - 1.3 * row.get("turnovers", 0.0)
        - 0.3 * max(0.0, row.get("fga", 0.0) - row.get("points", 0.0) / 2.0)
    )
    return (raw * per36) / 10.0


def rolling_player_values(
    logs_by_player: dict[str, list[tuple[str, float]]],
    *,
    lookback: int = 15,
    prior_weight: float = 5.0,
    league_prior: float = 1.1,
) -> dict[str, float]:
    """Empirical-Bayes-shrunk rolling per-player point-impact value.

    ``logs_by_player`` maps player_id -> chronological list of
    ``(event_id, bpm_proxy)`` tuples strictly before the decision point the
    caller is computing for. Shrinks toward ``league_prior`` (the static
    rotation-player constant) with ``prior_weight`` pseudo-games, same
    shrinkage shape as ``wnba_player_impact.shrink_player_rating`` so a
    player with few games doesn't get an extreme, noisy value.
    """
    out: dict[str, float] = {}
    for pid, games in logs_by_player.items():
        window = games[-lookback:]
        if not window:
            continue
        n = len(window)
        mean_val = sum(v for _, v in window) / n
        shrunk = (prior_weight * league_prior + n * mean_val) / (prior_weight + n)
        out[pid] = round(shrunk, 3)
    return out
