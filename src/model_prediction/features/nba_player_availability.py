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


def compute_nba_matchup_availability(
    data_root: str | Path,
    home_team: str,
    away_team: str,
    observed_at: datetime | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculates point-in-time availability point adjustments for an NBA matchup.

    Returns:
        availability_points_gap: Positive means Home margin increases.
        availability_total_loss: Positive number of points to reduce from game total.
        home_points_lost: Points lost by home team.
        away_points_lost: Points lost by away team.
        home_injuries: List of injured home players.
        away_injuries: List of injured away players.
    """
    snap = snapshot if snapshot is not None else load_latest_nba_injury_snapshot(data_root, observed_at=observed_at)
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
    home_norm = home_team.casefold().strip()
    away_norm = away_team.casefold().strip()

    home_injuries: list[dict[str, Any]] = []
    away_injuries: list[dict[str, Any]] = []

    for entry in entries:
        t_name = str(entry.get("team", "")).casefold().strip()
        t_abbr = str(entry.get("team_abbreviation", "")).casefold().strip()
        is_home = (t_name in home_norm or home_norm in t_name) or (t_abbr and t_abbr in home_norm)
        is_away = (t_name in away_norm or away_norm in t_name) or (t_abbr and t_abbr in away_norm)

        if is_home:
            home_injuries.append(entry)
        elif is_away:
            away_injuries.append(entry)

    def _team_loss(injury_list: list[dict[str, Any]]) -> float:
        total_loss = 0.0
        for item in injury_list:
            status = str(item.get("status") or item.get("raw_status") or "Available")
            prob = STATUS_PROBABILITIES.get(status, 1.0)
            if prob >= 1.0:
                continue
            player_name = str(item.get("player_name", ""))
            position = str(item.get("position", ""))
            impact = _estimate_player_impact(player_name, position)
            player_loss = (1.0 - prob) * impact
            total_loss += player_loss
        return min(MAX_TEAM_LOSS_PTS, total_loss)

    home_loss = _team_loss(home_injuries)
    away_loss = _team_loss(away_injuries)

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
