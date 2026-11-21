"""Build the derived inputs of the NBA v10 player layer (research only, read-only sources).

  python scripts/nba_v10/player_build_data.py box       # box_games.jsonl (2016-17 .. 2026-02-22)
  python scripts/nba_v10/player_build_data.py injuries  # injury_index.jsonl from parsed report rows

Sources (never written): the research backfill under data/research/nba_v10/games/ (ESPN box
scores 2016-17 .. 2023-24 plus the scoreboard index) and the live ESPN summary captures under
data/availability/nba/espn_boxscores/ (2024-01 onward). Research rows win on overlap. Every
event is cut at HOLDOUT_START (2026-02-23): the locked holdout is never loaded.

Outputs go to data/research/nba_v10/player/ with a provenance file next to each.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.features.nba_player_ratings import HOLDOUT_START, STAT_KEYS

RESEARCH = Path("E:/MP/data/research/nba_v10")
CAPTURES = Path("E:/MP/data/availability/nba/espn_boxscores")
OUT = RESEARCH / "player"

# player_box.jsonl / ESPN summary key for each STAT_KEYS entry.
_RESEARCH_KEYS = {
    "pts": "points",
    "fg3m": "fg3m",
    "fga": "fga",
    "fta": "fta",
    "ast": "assists",
    "tov": "turnovers",
    "oreb": "oreb",
    "dreb": "dreb",
    "stl": "steals",
    "blk": "blocks",
    "pf": "fouls",
}
_ESPN_KEYS = {
    "pts": ("points", 0),
    "fg3m": ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", 0),
    "fga": ("fieldGoalsMade-fieldGoalsAttempted", 1),
    "fta": ("freeThrowsMade-freeThrowsAttempted", 1),
    "ast": ("assists", 0),
    "tov": ("turnovers", 0),
    "oreb": ("offensiveRebounds", 0),
    "dreb": ("defensiveRebounds", 0),
    "stl": ("steals", 0),
    "blk": ("blocks", 0),
    "pf": ("fouls", 0),
}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _espn_stat(raw: list[Any], index: dict[str, int], key: str, part: int) -> float:
    i = index.get(key)
    if i is None or i >= len(raw):
        return 0.0
    text = str(raw[i])
    if "-" in text and key.count("-") == 1:
        pieces = text.split("-")
        return _num(pieces[part]) if len(pieces) == 2 else 0.0
    return _num(text)


def _scoreboard() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with (RESEARCH / "games" / "scoreboard_index.jsonl").open(encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            if row["season_type"] == "preseason" or row["scoreboard_date"] >= HOLDOUT_START:
                continue
            if row["season_year"] < 2017:
                continue
            # All-Star and exhibition sides carry non-franchise ids (> 30).
            if int(row["home_team_id"]) > 30 or int(row["away_team_id"]) > 30:
                continue
            out[str(row["event_id"])] = row
    return out


def _research_players(events: set[str]) -> dict[str, list[list[Any]]]:
    out: dict[str, list[list[Any]]] = defaultdict(list)
    with (RESEARCH / "games" / "player_box.jsonl").open(encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            eid = str(row["event_id"])
            if eid not in events:
                continue
            minutes = 0.0 if row.get("did_not_play") else _num(row.get("minutes"))
            out[eid].append(
                [
                    str(row["player_id"]),
                    row.get("name") or "",
                    1 if row.get("home_away") == "home" else 0,
                    minutes,
                    1 if row.get("starter") else 0,
                    *[_num(row.get(_RESEARCH_KEYS[k])) for k in STAT_KEYS],
                ]
            )
    return out


def _capture_players(path: Path, home_name: str, away_name: str) -> list[list[Any]] | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return None
    comp = ((payload.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None
    rows: list[list[Any]] = []
    sides = 0
    for team_entry in (payload.get("boxscore") or {}).get("players") or []:
        team_name = (team_entry.get("team") or {}).get("displayName")
        if team_name == home_name:
            home = 1
        elif team_name == away_name:
            home = 0
        else:
            return None
        sides += 1
        group = (team_entry.get("statistics") or [{}])[0]
        index = {name: i for i, name in enumerate(group.get("keys") or [])}
        for athlete in group.get("athletes") or []:
            meta = athlete.get("athlete") or {}
            if meta.get("id") is None:
                continue
            raw = athlete.get("stats") or []
            dnp = bool(athlete.get("didNotPlay")) or not raw
            minutes = 0.0 if dnp else _espn_stat(raw, index, "minutes", 0)
            stats = (
                [0.0] * len(STAT_KEYS) if dnp else [_espn_stat(raw, index, *_ESPN_KEYS[k]) for k in STAT_KEYS]
            )
            rows.append(
                [
                    str(meta["id"]),
                    meta.get("displayName") or "",
                    home,
                    minutes,
                    1 if athlete.get("starter") else 0,
                ]
                + stats
            )
    return rows if sides == 2 else None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _team_minutes_ok(players: list[list[Any]]) -> bool:
    # Rounded ESPN minutes can sum to 223-232 in regulation; 215 rejects partial captures.
    for side in (0, 1):
        total = sum(p[3] for p in players if p[2] == side)
        if total < 215.0:
            return False
    return True


def cmd_box() -> None:
    games = _scoreboard()
    research = _research_players(set(games))
    source: dict[str, str] = {}
    players: dict[str, list[list[Any]]] = {}
    for eid, rows in research.items():
        if _team_minutes_ok(rows):
            players[eid] = rows
            source[eid] = "research_backfill"
    for path in sorted(CAPTURES.glob("*.json")):
        eid = path.stem
        if eid in players or eid not in games:
            continue
        g = games[eid]
        try:
            rows_c = _capture_players(path, g["home_team"], g["away_team"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, IndexError):
            rows_c = None
        if rows_c and _team_minutes_ok(rows_c):
            players[eid] = rows_c
            source[eid] = "live_capture"
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "box_games.jsonl"
    by_season: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with out_path.open("w", encoding="utf-8") as handle:
        for eid, g in sorted(
            games.items(), key=lambda kv: (kv[1]["scoreboard_date"], kv[1]["event_start_utc"], kv[0])
        ):
            rows = players.get(eid)
            by_season[f"{g['season_year']}/{g['season_type']}"][0] += 1
            by_season[f"{g['season_year']}/{g['season_type']}"][1] += 1 if rows else 0
            handle.write(
                json.dumps(
                    {
                        "event_id": eid,
                        "date": g["scoreboard_date"],
                        "start_utc": g["event_start_utc"],
                        "season_year": g["season_year"],
                        "season_type": g["season_type"],
                        "home_id": str(g["home_team_id"]),
                        "away_id": str(g["away_team_id"]),
                        "home_name": g["home_team"],
                        "away_name": g["away_team"],
                        "home_score": g["home_score"],
                        "away_score": g["away_score"],
                        "box_source": source.get(eid),
                        "players": rows,
                    }
                )
                + "\n"
            )
    prov = {
        "built_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": "scripts/nba_v10/player_build_data.py box",
        "cutoff": f"scoreboard_date < {HOLDOUT_START} (locked holdout never loaded)",
        "player_columns": ["player_id", "name", "home", "minutes", "starter", *STAT_KEYS],
        "sources": {
            "scoreboard": str(RESEARCH / "games" / "scoreboard_index.jsonl"),
            "research_player_box": str(RESEARCH / "games" / "player_box.jsonl"),
            "live_captures": str(CAPTURES),
        },
        "pit_note": "Box rows are post-game facts; a game's rows may only inform decisions on later ET dates.",
        "games_by_season_type": {k: {"games": v[0], "with_box": v[1]} for k, v in sorted(by_season.items())},
        "sha256": _sha(out_path),
    }
    (OUT / "box_games.provenance.json").write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(prov["games_by_season_type"], indent=1))


def cmd_injuries() -> None:
    """One row per game date: every report that listed the date (observed time + matchups),
    and per team the status content each time it changed. ``None`` content = NOT YET SUBMITTED."""
    rows_dir = RESEARCH / "injury_reports" / "rows"
    reports: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    content: dict[tuple[str, str, str], list[list[str]] | None] = {}
    tricode_names: dict[str, str] = {}
    files = sorted(rows_dir.glob("*.jsonl.gz"))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                r = json.loads(raw)
                gd, obs = r["game_date"], r["observed_at_utc"]
                # All-Star rows carry team 'Non-NBA Team' with no tricode.
                if gd >= HOLDOUT_START or not r.get("team_tricode") or not r.get("away_tricode"):
                    continue
                tricode_names[r["team_tricode"]] = r["team"]
                reports[gd][obs].add((r["away_tricode"], r["home_tricode"]))
                key = (gd, obs, r["team"])
                if key not in content:
                    content[key] = []
                players = content[key]
                if r["status"] == "NOT YET SUBMITTED":
                    content[key] = None
                elif players is not None:
                    players.append([r["player_name"], r["status"]])
    by_date_team: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for (gd, obs, team), players in sorted(content.items()):
        entry = None if players is None else sorted(players)
        history = by_date_team[gd][team]
        if history and history[-1][1] == entry:
            continue
        history.append([obs, entry])

    def matchup(away: str, home: str) -> str:
        return f"{tricode_names.get(away, away)}@{tricode_names.get(home, home)}"

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "injury_index.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for gd in sorted(reports):
            handle.write(
                json.dumps(
                    {
                        "game_date": gd,
                        "reports": [
                            [obs, sorted(matchup(a, h) for a, h in m)]
                            for obs, m in sorted(reports[gd].items())
                        ],
                        "teams": by_date_team[gd],
                    }
                )
                + "\n"
            )
    prov = {
        "built_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": "scripts/nba_v10/player_build_data.py injuries",
        "source_files": [p.name for p in files],
        "first_game_date": min(reports) if reports else None,
        "last_game_date": max(reports) if reports else None,
        "tricode_names": dict(sorted(tricode_names.items())),
        "observed_time": "observed_at_utc of the parsed report row (report header time)",
        "sha256": _sha(out_path),
    }
    (OUT / "injury_index.provenance.json").write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in prov.items() if k != "tricode_names"}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["box", "injuries"])
    args = parser.parse_args()
    cmd_box() if args.command == "box" else cmd_injuries()


if __name__ == "__main__":
    main()
