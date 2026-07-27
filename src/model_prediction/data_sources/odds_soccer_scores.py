"""Collect soccer scores from The Odds API (free tier, last 3 days).
Appends to the existing historical soccer JSONL file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from .the_odds_api import TheOddsAPIClient

# The Odds API sport keys for smaller Polymarket soccer leagues.
# ESPN already covers: EPL, LA_LIGA, BUNDESLIGA, SERIE_A, MLS, UCL, WORLD_CUP.
# These are additional leagues visible on Polymarket US.
ODDS_SOCCER_KEYS: dict[str, str] = {
    "BRAZIL_SERIE_B": "soccer_brazil_serie_b",
    "K_LEAGUE_1": "soccer_korea_kleague1",
    "ELITESERIEN": "soccer_norway_eliteserien",
    "CSL": "soccer_china_superleague",
    "SUPERLIGA": "soccer_denmark_superliga",
    "LIGA_MX": "soccer_mexico_ligamx",
    "BRASILEIRAO": "soccer_brazil_campeonato",
    "ARGENTINA": "soccer_argentina_primera_division",
    "EREDIVISIE": "soccer_netherlands_eredivisie",
    "LIGUE_1": "soccer_france_ligue_one",
    "CHAMPIONSHIP": "soccer_efl_champ",
    "COPA_LIBERTADORES": "soccer_conmebol_copa_libertadores",
}


def _redact_api_key(text: str, api_key: str) -> str:
    """Strip a leaked API key out of exception text before it is stored or logged.

    httpx sends the key as a query param, so an HTTP-error exception's message
    (which includes the full request URL) embeds it verbatim -- this must never
    reach the daily JSON log or CLI output unredacted.
    """
    if not api_key:
        return text
    return text.replace(api_key, "***REDACTED***")


def collect_soccer_scores(
    api_key: str | None = None,
    data_root: str | Path | None = None,
    days_from: int = 3,
) -> dict[str, Any]:
    """Fetch recent scores for all configured soccer leagues from The Odds API."""
    if data_root is None:
        data_root = PROJECT_ROOT / "data"
    if api_key is None:
        api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return {"status": "no_api_key", "error": "THE_ODDS_API_KEY not set"}

    client = TheOddsAPIClient(api_key)
    historical_path = Path(data_root) / "historical" / "soccer_games_all.jsonl"
    historical_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing event IDs for dedup
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
    for league_name, odds_key in sorted(ODDS_SOCCER_KEYS.items()):
        try:
            matches = client.scores(league_name, days_from=days_from)
        except Exception as exc:
            results[league_name] = {
                "status": "error",
                "error": _redact_api_key(str(exc), api_key)[:100],
            }
            continue

        new_games = 0
        with historical_path.open("a", encoding="utf-8") as handle:
            for match in matches:
                if not match.get("completed"):
                    continue
                home = match.get("home_team", "")
                away = match.get("away_team", "")
                commence = match.get("commence_time", "")
                scores = match.get("scores") or []
                if len(scores) < 2:
                    continue

                # Scores: list of {name, score} — match by team name
                home_score = away_score = None
                for s in scores:
                    if s.get("name") == home:
                        home_score = s.get("score")
                    elif s.get("name") == away:
                        away_score = s.get("score")
                if home_score is None or away_score is None:
                    continue

                # Deterministic digest: Python's built-in hash() is randomized
                # per process, which made every daily run mint a NEW id for the
                # same game and re-append it (dedup never fired across runs).
                digest = hashlib.sha1(f"{home}|{away}".encode()).hexdigest()[:8]
                event_id = f"oddsapi:{odds_key}:{commence[:10]}:{digest}"
                if event_id in existing_ids:
                    continue

                game = {
                    "event_id": event_id,
                    "event_start_utc": commence,
                    "league": league_name,
                    "away_team": away,
                    "home_team": home,
                    "away_score": int(str(away_score)),
                    "home_score": int(str(home_score)),
                }
                handle.write(json.dumps(game, sort_keys=True, separators=(",", ":")) + "\n")
                existing_ids.add(event_id)
                new_games += 1

        results[league_name] = {
            "status": "ok",
            "matches_returned": len(matches),
            "new_games": new_games,
        }
        total_new += new_games

    results["total_new_games"] = total_new
    results["historical_path"] = str(historical_path)
    return results
