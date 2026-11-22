"""Build the season-keyed NCAAF conference membership table for cfb-structural-v5.1.

v5's ``is_conference_matchup`` comes from the static team table in ``data_sources/cfb_data.py``, which describes
one alignment for every season: 39 of 340 2026 FBS conference games (Pac-12 rebuild, NIU/UTEP to the MWC, La Tech
to the Sun Belt; FIU, Sacramento State and Missouri State not in the table at all) were labelled non-conference,
and training rows from 2016-2023 carried the 2025 alignment.

Source: the CFBD ``/lines`` season files already cached under ``data/research/ncaaf_v7/cfbd/raw/lines`` (one file
per season, 2014-2026). Each game record names both teams' conference and classification FOR THAT SEASON, keyed by
the CFBD team id, which is the ESPN team id. Membership of a season is fixed and announced before the season starts,
so a (season, team) -> conference table carries no outcome information; only membership is kept, never scores.

Names: 7,724 history rows (the 2016-2025 backfill) carry no ESPN team id. ``aliases`` maps every ESPN display name
seen in history to its id, built by joining history rows to CFBD games on the event id (CFBD game id = ESPN event
id). A name that maps to two ids is dropped (never guessed). Serving passes the slate event's ESPN ids directly.

Writes ``config/ncaaf/cfb_conference_membership.json`` (sha256 recorded in the v5.1 artifact) and prints the check
against ESPN's own ``competitions[0].conferenceCompetition`` flag on the cached 2026 scoreboards.

Usage:
  PYTHONPATH=src python scripts/cfb_v51_build_conference_map.py --mp-data E:/MP/data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "config" / "ncaaf" / "cfb_conference_membership.json"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return folded.replace("'", "").replace("’", "").lower().strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(mp_data: Path) -> dict[str, Any]:
    lines_dir = mp_data / "research" / "ncaaf_v7" / "cfbd" / "raw" / "lines"
    seasons: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    conflicts: list[dict[str, Any]] = []
    # event id -> ((home id, home school), (away id, away school)) as CFBD lists them
    game_teams: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {}
    sources = []
    for path in sorted(lines_dir.glob("year-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        meta_path = path.with_name(path.name.replace(".json", ".meta.json"))
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        sources.append(
            {"file": path.name, "sha256": _sha(path), "fetched_at_utc": meta.get("fetched_at_utc")}
        )
        for g in json.loads(path.read_text(encoding="utf-8")):
            season = str(g["season"])
            game_teams[str(g["id"])] = (
                (str(g["homeTeamId"]), str(g["homeTeam"])),
                (str(g["awayTeamId"]), str(g["awayTeam"])),
            )
            for side in ("home", "away"):
                tid = str(g[f"{side}TeamId"])
                entry = {
                    "conference": str(g.get(f"{side}Conference") or ""),
                    "classification": str(g.get(f"{side}Classification") or ""),
                }
                prior = seasons[season].get(tid)
                if prior is not None and prior != entry:
                    conflicts.append({"season": season, "team_id": tid, "a": prior, "b": entry})
                    continue
                seasons[season][tid] = entry
    if conflicts:
        raise SystemExit(f"{len(conflicts)} season/team conference conflicts in CFBD: {conflicts[:5]}")

    # Aliases: ESPN display name -> ESPN team id, from history rows (ids on the row, else the CFBD game by event id).
    # CFBD and ESPN disagree on home/away at some neutral sites, so a CFBD-joined row is oriented by name
    # (the ESPN display name starts with the CFBD school name) and skipped when neither orientation matches.
    name_ids: dict[str, Counter[str]] = defaultdict(Counter)
    unoriented = 0
    for rel in ("processed/ncaaf/games.jsonl", "historical/ncaaf_games_all.jsonl"):
        path = mp_data / rel
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            home_name, away_name = str(row.get("home_team") or ""), str(row.get("away_team") or "")
            if not home_name or not away_name:
                continue
            if row.get("home_team_id") and row.get("away_team_id"):
                ids = (str(row["home_team_id"]), str(row["away_team_id"]))
            else:
                game = game_teams.get(str(row.get("event_id")))
                if game is None:
                    continue
                (h_id, h_school), (a_id, a_school) = game

                def starts(name: str, school: str) -> bool:
                    # "Hawaii Rainbow Warriors" vs CFBD "Hawai'i", "San Jose" vs "San José"
                    name, school = _fold(name), _fold(school)
                    return name == school or name.startswith(school + " ")

                if starts(home_name, h_school) and starts(away_name, a_school):
                    ids = (h_id, a_id)
                elif starts(home_name, a_school) and starts(away_name, h_school):
                    ids = (a_id, h_id)
                else:
                    unoriented += 1
                    continue
            name_ids[home_name][ids[0]] += 1
            name_ids[away_name][ids[1]] += 1
    aliases = {name: next(iter(ids)) for name, ids in sorted(name_ids.items()) if len(ids) == 1}
    ambiguous = sorted(name for name, ids in name_ids.items() if len(ids) > 1)
    print("rows skipped (no name orientation against CFBD):", unoriented)

    table = {
        "schema": "cfb-conference-membership-v1",
        "source": "CFBD /lines season files (per-game team conference and classification for that season)",
        "source_files": sources,
        "independent_conferences": ["FBS Independents"],
        "seasons": {s: dict(sorted(v.items())) for s, v in sorted(seasons.items())},
        "aliases": aliases,
        "ambiguous_names_dropped": ambiguous,
    }
    return table


def espn_check(mp_data: Path, table: dict[str, Any]) -> dict[str, Any]:
    """Agreement with ESPN's conferenceCompetition on the cached 2026 scoreboards (FBS-involved games)."""
    from model_prediction.features.cfb_conference import ConferenceMembership

    membership = ConferenceMembership(table)
    counts: Counter[str] = Counter()
    mismatches = []
    seen: set[str] = set()
    for path in sorted((mp_data / "raw" / "ncaaf").glob("*/scores_ncaaf.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            eid = str(ev.get("id"))
            if eid in seen:
                continue
            seen.add(eid)
            comp = (ev.get("competitions") or [{}])[0]
            flag = comp.get("conferenceCompetition")
            if flag is None:
                continue
            teams = {c.get("homeAway"): c.get("team") or {} for c in comp.get("competitors", [])}
            home, away = teams.get("home") or {}, teams.get("away") or {}
            season = int((ev.get("season") or {}).get("year") or 2026)
            ours = membership.is_conference_matchup(
                season,
                home_name=str(home.get("displayName") or ""),
                away_name=str(away.get("displayName") or ""),
                home_team_id=str(home.get("id") or "") or None,
                away_team_id=str(away.get("id") or "") or None,
            )
            fbs = any(membership.is_fbs(season, str(t.get("id") or "")) for t in (home, away))
            if not fbs:
                counts["not_fbs_involved"] += 1
                continue
            counts[f"espn_{bool(flag)}_ours_{ours.flag}"] += 1
            if bool(flag) != ours.flag:
                mismatches.append(
                    {
                        "event_id": eid,
                        "home": home.get("displayName"),
                        "away": away.get("displayName"),
                        "espn": bool(flag),
                        "ours": ours.flag,
                        "home_conf": ours.home_conference,
                        "away_conf": ours.away_conference,
                    }
                )
    return {"counts": dict(sorted(counts.items())), "mismatches": mismatches}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp-data", default="E:/MP/data")
    args = ap.parse_args()
    mp_data = Path(args.mp_data)
    table = build(mp_data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print("wrote", OUT, "sha256", _sha(OUT))
    print("seasons", {s: len(v) for s, v in table["seasons"].items()})
    print("aliases", len(table["aliases"]), "ambiguous dropped", table["ambiguous_names_dropped"])
    check = espn_check(mp_data, table)
    print("ESPN conferenceCompetition check:", json.dumps(check["counts"]))
    for m in check["mismatches"]:
        print("  mismatch", m)


if __name__ == "__main__":
    main()
