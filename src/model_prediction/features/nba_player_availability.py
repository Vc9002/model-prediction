"""NBA Point-in-Time Player Availability Feature Engine.

Extracts point-in-time player availability and injury impact for upcoming NBA matchups:
- Scans data/availability/nba/espn_injury_snapshots/ for observations strictly <= decision time.
- Applies status-conditional active probabilities:
    Out/IR: 0.0 (100% loss)
    Doubtful: 0.25 (75% loss)
    Questionable / Day-to-Day: 0.50 (50% loss)
    Probable: 0.90 (10% loss)
- Computes:
    availability_points_gap: (away_points_lost - home_points_lost) for spread adjustment
    availability_total_loss: (home_points_lost + away_points_lost) for total points shrinkage
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain import parse_utc

# Status conditional probabilities of playing
STATUS_PROBABILITIES: dict[str, float] = {
    "Out": 0.0,
    "IR": 0.0,
    "Injured Reserve": 0.0,
    "Doubtful": 0.25,
    "Questionable": 0.50,
    "Day-To-Day": 0.50,
    "Probable": 0.90,
    "Active": 1.0,
}

# Superstar & Franchise Anchor Tier (worth ~4.5 - 5.5 pts to spread)
FRANCHISE_ANCHORS: set[str] = {
    "Nikola Jokic",
    "Giannis Antetokounmpo",
    "Luka Doncic",
    "Shai Gilgeous-Alexander",
    "Jayson Tatum",
    "Stephen Curry",
    "Joel Embiid",
    "Anthony Edwards",
    "Devin Booker",
    "Donovan Mitchell",
    "Anthony Davis",
    "LeBron James",
    "Kevin Durant",
    "Victor Wembanyama",
    "Jalen Brunson",
    "Tyrese Haliburton",
    "Ja Morant",
    "Trae Young",
    "De'Aaron Fox",
    "Damian Lillard",
    "Bam Adebayo",
    "Jimmy Butler",
    "Kawhi Leonard",
    "Paul George",
    "Zion Williamson",
    "Chet Holmgren",
    "Tyrese Maxey",
    "Paolo Banchero",
}

# Secondary All-Star & Key Starter Tier (~2.5 - 3.2 pts)
KEY_STARTERS: set[str] = {
    "Jaylen Brown",
    "Kristaps Porzingis",
    "Derrick White",
    "Jrue Holiday",
    "Jamal Murray",
    "Michael Porter Jr.",
    "Aaron Gordon",
    "Kyrie Irving",
    "Domantas Sabonis",
    "Jalen Williams",
    "Karl-Anthony Towns",
    "Rudy Gobert",
    "Mikal Bridges",
    "OG Anunoby",
    "Myles Turner",
    "Pascal Siakam",
    "Scottie Barnes",
    "Immanuel Quickley",
    "Desmond Bane",
    "Jaren Jackson Jr.",
    "Brandon Ingram",
    "CJ McCollum",
    "Lauri Markkanen",
    "Collin Sexton",
    "Alperen Sengun",
    "Jalen Green",
    "Fred VanVleet",
    "Cade Cunningham",
    "Jaden Ivey",
    "LaMelo Ball",
    "Miles Bridges",
    "Darius Garland",
    "Evan Mobley",
    "Jarrett Allen",
    "Tyler Herro",
    "Franz Wagner",
    "Jalen Suggs",
    "Anfernee Simons",
    "Jerami Grant",
    "Deandre Ayton",
    "Jordan Poole",
    "Kyle Kuzma",
}

MAX_TEAM_LOSS_PTS = 12.0


def _estimate_player_impact(player_name: str, position: str) -> float:
    """Estimates point impact of a player relative to replacement."""
    clean_name = " ".join(player_name.casefold().split())
    for anchor in FRANCHISE_ANCHORS:
        if clean_name == " ".join(anchor.casefold().split()):
            return 4.8
    for starter in KEY_STARTERS:
        if clean_name == " ".join(starter.casefold().split()):
            return 2.5
    # Default rotation player
    return 1.1


def _estimate_player_impact_real(
    player_name: str,
    position: str,
    player_value_lookup: Mapping[str, float] | None,
) -> float:
    """v8 candidate (Phase 2, real-data variant): looks up a box-score-derived
    rolling per-player point-impact value (``features.nba_player_logs.rolling_player_values``)
    keyed by player name, built from real ESPN boxscore history instead of the
    hardcoded franchise-anchor/key-starter name lists.

    Falls back to the static tier value when the player has no rolling
    history (rookie, name-matching miss, or boxscore capture gap) -- a
    strict superset of the old behavior, never a guess for missing data.
    """
    if player_value_lookup:
        clean_name = " ".join(player_name.casefold().split())
        value = player_value_lookup.get(clean_name)
        if value is not None:
            return value
    return _estimate_player_impact(player_name, position)


def _estimate_player_impact_dynamic(
    player_name: str,
    position: str,
    team_off_rating: float | None,
    league_avg_off_rating: float | None,
) -> float:
    """v8 candidate: scales the static tier value by the player's own team's current
    offensive strength relative to league average, instead of returning a flat constant
    for every team a tiered player happens to play on.

    Rationale (Phase 2 of the NBA v8 overhaul research): a franchise-anchor injury on a
    106-point-per-game offense should not cost the same expected points as the same tier
    on a 120-point-per-game offense. This is a structural improvement over the fully flat
    lookup, not a per-player-rated replacement for it (no NBA box-score/player data source
    exists in this repo to build a real RAPM/EPM-style rating -- see deviation note).
    Falls back to the flat static value when team context is unavailable, so it is a
    strict superset of the old behavior rather than a new unvalidated number for every case.
    """
    base = _estimate_player_impact(player_name, position)
    if not team_off_rating or not league_avg_off_rating or league_avg_off_rating <= 0:
        return base
    scale = max(0.6, min(1.4, team_off_rating / league_avg_off_rating))
    return round(base * scale, 3)


def load_latest_nba_injury_snapshot(
    data_root: str | Path,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Finds the most recent valid point-in-time injury snapshot strictly before observed_at."""
    cutoff = observed_at or datetime.now(UTC)
    snap_dir = Path(data_root) / "availability" / "nba" / "espn_injury_snapshots"
    if not snap_dir.exists():
        return None

    candidates: list[tuple[datetime, Path]] = []
    for file_path in snap_dir.glob("*/*.json"):
        try:
            # Filename contains stamp: nba-injuries-YYYYMMDDTHHMMSS+0000-...
            stem = file_path.stem
            parts = stem.split("-")
            if len(parts) >= 3:
                stamp_str = parts[2]
                file_dt = parse_utc(stamp_str)
                if file_dt <= cutoff:
                    candidates.append((file_dt, file_path))
        except (ValueError, TypeError):
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    for file_dt, file_path in candidates:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            observed = parse_utc(str(payload["observed_at_utc"]))
            digest = str(payload["snapshot_sha256"])
            if (
                observed.replace(microsecond=0) != file_dt
                or observed > cutoff
                or digest[:12] != file_path.stem.split("-")[-1]
            ):
                continue
            canonical = json.dumps(
                {key: value for key, value in payload.items() if key != "snapshot_sha256"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if hashlib.sha256(canonical).hexdigest() != digest:
                continue
            return payload
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


def _normalize_team(value: str) -> str:
    return " ".join(value.casefold().split())


def _team_matches(entry_team: str, entry_abbr: str, matchup_team: str) -> bool:
    """Decide whether an injury-feed row belongs to ``matchup_team``.

    Exact matches only. The previous substring test (``abbr in team_name``)
    charged other teams' injuries to a matchup whenever a two- or three-letter
    abbreviation happened to appear inside a name: "orl" sits inside
    "new orleans pelicans", "phi" inside "memphis grizzlies", "gs" and "sa"
    inside "sacramento kings", "den" inside "golden state warriors". On
    2026-11-13 that priced a Grizzlies/Pelicans total on seven Magic and four
    76ers injuries (quick-261113-jrq). Nickname equality is kept because the
    scoreboard and the injury feed spell some cities differently ("Los Angeles
    Clippers" vs "LA Clippers") and NBA nicknames are unique; an abbreviation
    counts only when the matchup string itself is that abbreviation.
    """
    target = _normalize_team(matchup_team)
    name = _normalize_team(entry_team)
    abbr = _normalize_team(entry_abbr)
    if not target:
        return False
    if name and name == target:
        return True
    if name and target.split()[-1] == name.split()[-1] and " " in name and " " in target:
        return True
    return bool(abbr) and abbr == target


def compute_nba_matchup_availability(
    data_root: str | Path,
    home_team: str,
    away_team: str,
    observed_at: datetime | None = None,
    snapshot: Mapping[str, Any] | None = None,
    home_off_rating: float | None = None,
    away_off_rating: float | None = None,
    league_avg_off_rating: float | None = None,
    use_dynamic_impact: bool = False,
    use_real_player_ratings: bool = False,
    player_value_lookup: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Calculates point-in-time availability point adjustments for an NBA matchup.

    ``use_dynamic_impact`` (v8 candidate, default off so v7 serving stays byte-identical):
    scales each injured player's static tier value by their own team's current offensive
    rating relative to league average (see ``_estimate_player_impact_dynamic``), instead of
    a flat constant regardless of team context. Requires ``home_off_rating``/
    ``away_off_rating``/``league_avg_off_rating``; silently falls back to the static value
    for any side missing context.

    Returns:
        availability_points_gap: Positive means Home margin increases.
        availability_total_loss: Positive number of points to reduce from game total.
        home_points_lost: Points lost by home team.
        away_points_lost: Points lost by away team.
        home_injuries: List of injured home players.
        away_injuries: List of injured away players.
    """
    snap = (
        snapshot
        if snapshot is not None
        else load_latest_nba_injury_snapshot(data_root, observed_at=observed_at)
    )
    if not snap:
        return {
            "availability_points_gap": 0.0,
            "availability_total_loss": 0.0,
            "home_points_lost": 0.0,
            "away_points_lost": 0.0,
            "home_injuries": [],
            "away_injuries": [],
        }

    entries = snap.get("entries", [])

    home_injuries: list[dict[str, Any]] = []
    away_injuries: list[dict[str, Any]] = []

    for entry in entries:
        t_name = str(entry.get("team", ""))
        t_abbr = str(entry.get("team_abbreviation", ""))
        if _team_matches(t_name, t_abbr, home_team):
            home_injuries.append(entry)
        elif _team_matches(t_name, t_abbr, away_team):
            away_injuries.append(entry)

    def _team_loss(injury_list: list[dict[str, Any]], team_off_rating: float | None) -> float:
        total_loss = 0.0
        for item in injury_list:
            status = str(item.get("status") or item.get("raw_status") or "Available")
            prob = STATUS_PROBABILITIES.get(status, 1.0)
            if prob >= 1.0:
                continue
            player_name = str(item.get("player_name", ""))
            position = str(item.get("position", ""))
            if use_real_player_ratings:
                impact = _estimate_player_impact_real(player_name, position, player_value_lookup)
            elif use_dynamic_impact:
                impact = _estimate_player_impact_dynamic(
                    player_name, position, team_off_rating, league_avg_off_rating
                )
            else:
                impact = _estimate_player_impact(player_name, position)
            player_loss = (1.0 - prob) * impact
            total_loss += player_loss
        return min(MAX_TEAM_LOSS_PTS, total_loss)

    home_loss = _team_loss(home_injuries, home_off_rating)
    away_loss = _team_loss(away_injuries, away_off_rating)

    # Away loss minus home loss: If away loses 4 pts and home loses 1 pt,
    # Home's expected margin increases by +3.0 pts.
    gap = round(away_loss - home_loss, 2)
    # Total scoring loss is the sum of offensive reductions.
    total_loss = round(home_loss + away_loss, 2)

    return {
        "availability_points_gap": gap,
        "availability_total_loss": total_loss,
        "home_points_lost": round(home_loss, 2),
        "away_points_lost": round(away_loss, 2),
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
    }
