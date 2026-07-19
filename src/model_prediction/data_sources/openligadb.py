"""OpenLigaDB soccer data collector — free, no API key, full historical seasons.

Covers German Bundesliga 1/2/3, Austrian Bundesliga, Swiss Super League,
and many more. Provides complete match data with scores going back to 2002.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "https://api.openligadb.de"

# Leagues relevant to Polymarket US (German-speaking + nearby)
LEAGUES = {
    "BUNDESLIGA": ("bl1", 2025),
    "BUNDESLIGA_2": ("bl2", 2025),
    "LIGA_3": ("bl3", 2025),
    "AUSTRIAN_BUNDESLIGA": ("ÖFB", 2025),
    "SWISS_SUPER_LEAGUE": ("asl", 2025),
}


def collect_openliga_scores(
    data_root: str | Path = "data",
) -> dict[str, Any]:
    """Fetch full-season match data from OpenLigaDB and append to historical file."""
    client = httpx.Client(timeout=30)
    historical_path = Path(data_root) / "historical" / "soccer_games_all.jsonl"
    historical_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids: set[str] = set()
    if historical_path.exists():
        with historical_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        existing_ids.add(str(json.loads(line).get("event_id", "")))
                    except json.JSONDecodeError:
                        continue

    results: dict[str, Any] = {}
    total_new = 0

    for league_name, (shortcut, season) in sorted(LEAGUES.items()):
        try:
            response = client.get(f"{BASE_URL}/getmatchdata/{shortcut}/{season}", timeout=60)
            response.raise_for_status()
            matches = response.json()
        except Exception as exc:
            results[league_name] = {"status": "error", "error": str(exc)[:100]}
            continue

        new_games = 0
        with historical_path.open("a", encoding="utf-8") as handle:
            for match in matches:
                if not match.get("matchIsFinished"):
                    continue

                t1 = match.get("team1", {})
                t2 = match.get("team2", {})
                home_team = t1.get("teamName", "")
                away_team = t2.get("teamName", "")
                match_date = match.get("matchDateTimeUTC", "") or match.get("matchDateTime", "")

                score_str = ""
                for result in match.get("matchResults", []):
                    if result.get("resultName") == "Endergebnis":
                        h = result.get("pointsTeam1")
                        a = result.get("pointsTeam2")
                        if h is not None and a is not None:
                            score_str = f"{h}-{a}"
                        break

                if not score_str or not home_team or not away_team:
                    continue

                h_score, a_score = score_str.split("-")
                event_id = f"openligadb:{shortcut}:{match.get('matchID','')}"

                if event_id in existing_ids:
                    continue

                game = {
                    "event_id": event_id,
                    "event_start_utc": match_date,
                    "league": league_name,
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_score": int(a_score),
                    "home_score": int(h_score),
                }
                handle.write(json.dumps(game, sort_keys=True, separators=(",", ":")) + "\n")
                existing_ids.add(event_id)
                new_games += 1

        results[league_name] = {
            "status": "ok",
            "matches_returned": len(matches),
            "new_games": new_games,
            "season": f"{season}/{season+1}",
        }
        total_new += new_games

    results["total_new_games"] = total_new
    results["historical_path"] = str(historical_path)
    return results
