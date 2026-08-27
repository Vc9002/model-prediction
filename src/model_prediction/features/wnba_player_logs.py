"""WNBA player-level boxscore logs (PIT feed for the lineup-impact features).

The ESPN boxscore files already captured for the four-factors pace signal
also carry per-athlete lines: minutes, points, attempts, turnovers,
starter/active flags. This module parses them into per-team per-game
player logs and builds the strictly-prior profiles that
``wnba_player_impact.compute_lineup_impact`` consumes — the plan's
"projected lineups/minutes/injuries" P0 without a commercial projection
feed: minutes are projected from each player's own recent rolling log,
and absences are inferred from a recently-active player with zero minutes
in the team's most recent game (a PIT-valid proxy for injury/rest, not a
claim about the actual injury report).

Positional athlete stat arrays are mapped through ESPN's own ``keys``
array, so the parser never assumes a column order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Stat names we read from ESPN's `keys` array (lowercase full names —
# the abbreviations live in the parallel `labels`/`names` arrays).
# Missing keys simply skip.


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
    """Attempt side of a made-attempt pair ("4-7" -> 7.0)."""
    i = index.get(name)
    if i is None or i >= len(raw):
        return 0.0
    try:
        return float(str(raw[i]).split("-")[1])
    except (ValueError, IndexError):
        return 0.0


def parse_wnba_player_boxscore(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Parse the boxscore payload into per-team player rows.

    Returns ``{team_abbr: [{player_id, name, minutes, points, fga, fta,
    assists, turnovers, starter, active}, ...]}``. Teams with no parseable
    player block are omitted.
    """
    box = payload.get("boxscore") or {}
    players = box.get("players") or []
    out: dict[str, list[dict[str, Any]]] = {}
    for team_entry in players:
        # Key by displayName: the row builder's GameRecord carries full
        # team names ("Connecticut Sun"), not ESPN abbreviations ("CON")
        # — the abbreviation keying produced zero log matches.
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
            # DNP rows carry zero minutes; keep them (they mark absences).
            rows.append(
                {
                    "player_id": str(pid),
                    "name": name,
                    "minutes": minutes,
                    "points": _stat_value(raw, index, "points"),
                    "fga": _stat_attempts(raw, index, "fieldGoalsMade-fieldGoalsAttempted"),
                    "fta": _stat_attempts(raw, index, "freeThrowsMade-freeThrowsAttempted"),
                    "assists": _stat_value(raw, index, "assists"),
                    "turnovers": _stat_value(raw, index, "turnovers"),
                    "starter": bool(athlete.get("starter")),
                    "active": bool(athlete.get("active")) and minutes > 0,
                }
            )
        if rows:
            out[team_name] = rows
    return out


def load_wnba_player_boxscores(directory: str | Path) -> dict[str, dict[str, Any]]:
    """Parse every boxscore file into ``{event_id: parsed}``.

    Files that fail to parse contribute nothing (the same fail-soft
    convention as ``load_wnba_boxscore_files``); the caller's features
    fall back to priors.
    """
    root = Path(directory)
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # The capture file wraps the ESPN response under a "payload" key
        # alongside provenance; the parser wants the boxscore itself.
        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            continue
        parsed = parse_wnba_player_boxscore(payload)
        if parsed:
            out[path.stem] = parsed
    return out


def build_wnba_player_logs(
    home_team: str,
    away_team: str,
    player_box: dict[str, list[dict[str, Any]]],
    team_stats: dict[str, dict[str, float]],
) -> dict[str, dict[str, Any]] | None:
    """One strictly-prior log entry per team for a finished game.

    ``player_box`` is the parsed output for one event; ``team_stats`` is
    the same event's parsed team boxscore (points/fga/fta/oreb/tov per
    team) used to attribute a per-game team defensive rating to every
    player minute. The caller appends the returned per-team entries only
    AFTER the game's own decision rows are built (same PIT discipline as
    the four-factors logs).
    """

    def _poss(stats: dict[str, float] | None) -> float:
        if not stats:
            return 0.0
        return (
            stats.get("fga", 0.0)
            + 0.44 * stats.get("fta", 0.0)
            - stats.get("oreb", 0.0)
            + stats.get("tov", 0.0)
        )

    def _drtg(opp_points: float, opp_poss: float) -> float:
        return (opp_points / opp_poss * 100.0) if opp_poss > 0 else 101.5

    if home_team not in player_box or away_team not in player_box:
        return None
    hs, as_ = team_stats.get(home_team), team_stats.get(away_team)
    home_drtg = _drtg((as_ or {}).get("points", 0.0), _poss(as_))
    away_drtg = _drtg((hs or {}).get("points", 0.0), _poss(hs))
    return {
        home_team: {"players": player_box[home_team], "team_drtg": home_drtg},
        away_team: {"players": player_box[away_team], "team_drtg": away_drtg},
    }


def team_player_profiles(
    logs: list[dict[str, Any]],
    *,
    lookback: int = 10,
    min_mean_minutes: float = 8.0,
) -> tuple[list[Any], list[str]]:
    """Rolling player profiles + recently-missing names from prior logs.

    Profiles carry per-100-possession points (offense), minutes-weighted
    team defensive rating (defense), shrunk through
    ``wnba_player_impact.shrink_player_rating``. Returns
    ``(profiles, recently_missing_names)`` where a recently-missing player
    is one who appeared in the window but logged zero minutes in the most
    recent game — the PIT-valid absence proxy.
    """
    from .wnba_player_impact import WNBAPlayerProfile, shrink_player_rating

    window = logs[-lookback:]
    agg: dict[str, dict[str, Any]] = {}
    for entry in window:
        entry_drtg = float(entry.get("team_drtg") or 101.5)
        for row in entry.get("players", []):
            pid = row["player_id"]
            a = agg.setdefault(
                pid,
                {
                    "name": row["name"],
                    "games": 0,
                    "minutes": 0.0,
                    "points": 0.0,
                    "poss": 0.0,
                    "drtg_weighted": 0.0,
                    "last_minutes": 0.0,
                    "usage_num": 0.0,
                    "usage_den": 0.0,
                },
            )
            a["games"] += 1
            a["minutes"] += row["minutes"]
            a["points"] += row["points"]
            # Team possession share is approximated from the player's own
            # minutes against a fixed league pace prior; this is a
            # research proxy, not a bookkeeping identity.
            a["poss"] += 79.5 * (row["minutes"] / 200.0) if row["minutes"] > 0 else 0.0
            a["drtg_weighted"] += row["minutes"] * entry_drtg
            a["usage_num"] += row["fga"] + 0.44 * row["fta"] + row["turnovers"]
            a["last_minutes"] = row["minutes"]

    profiles: list[Any] = []
    missing: list[str] = []
    for pid, a in agg.items():
        if a["games"] <= 0:
            continue
        mean_minutes = a["minutes"] / a["games"]
        pts_per_100 = (a["points"] / a["poss"] * 100.0) if a["poss"] > 0 else 101.5
        obs_def = a["drtg_weighted"] / a["minutes"] if a["minutes"] > 0 else 101.5
        off, deff, net = shrink_player_rating(pts_per_100, obs_def, a["games"])
        profiles.append(
            WNBAPlayerProfile(
                player_name=a["name"],
                team_name="",
                minutes_per_game=round(mean_minutes, 1),
                off_rating_shrunk=off,
                def_rating_shrunk=deff,
                net_rating=net,
                usage_rate=round(a["usage_num"] / max(1.0, a["games"] * 6.0), 3),
                true_shooting_pct=0.52,
                pace_factor=1.0,
                sample_games=a["games"],
            )
        )
        if a["games"] >= 2 and a["last_minutes"] == 0.0 and mean_minutes >= min_mean_minutes:
            missing.append(a["name"])

    profiles.sort(key=lambda p: p.minutes_per_game, reverse=True)
    return profiles, missing
