"""ESPN live probable pitchers for daily MLB forecasts.

Pulls starting pitcher ERAs from the ESPN scoreboard probables endpoint.
Free, no API key. Falls back to rolling runs-allowed when ESPN is unavailable.
"""

from __future__ import annotations

import httpx, json
from pathlib import Path


def _pull_espn_probables(date_str: str) -> dict[str, dict]:
    """Pull probable pitchers from ESPN scoreboard for a date.

    Returns dict of {event_id: {home_era, away_era, home_name, away_name}}.
    """
    url = f"http://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={date_str}"
    try:
        resp = httpx.get(url, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception:
        return {}

    result = {}
    for ev in data.get("events", []):
        eid = ev.get("id", "")
        comps = ev.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) < 2:
            continue

        eras = {}
        for c in competitors:
            home_away = c.get("homeAway", "")
            probables = c.get("probables", [])
            sp = next((p for p in probables if p.get("name") == "probableStartingPitcher"), None)
            if sp:
                athlete = sp.get("athlete", {})
                stats = sp.get("statistics", [])
                era_stat = next((s for s in stats if s.get("name") == "ERA"), None)
                era = float(era_stat.get("displayValue", 0)) if era_stat else None
                name = athlete.get("fullName", athlete.get("displayName", "?"))
                eras[home_away] = {"name": name, "era": era}

        if "home" in eras and "away" in eras:
            result[eid] = {
                "home_era": eras["home"]["era"],
                "away_era": eras["away"]["era"],
                "home_starter": eras["home"]["name"],
                "away_starter": eras["away"]["name"],
            }

    return result


def _rolling_runs_allowed(team: str, n: int = 5) -> float | None:
    """Fallback: rolling runs allowed from cached game results."""
    hist = Path("data/historical/mlb_games_all.jsonl")
    if not hist.exists():
        return None
    games = [json.loads(l) for l in hist.read_text().strip().split("\n") if l.strip()]
    team_g = sorted(
        [g for g in games if (g.get("home_team") == team or g.get("away_team") == team)
         and g.get("home_score") is not None],
        key=lambda g: g.get("event_start_utc", ""),
    )[-n:]
    if len(team_g) < n:
        return None
    return sum(g["away_score"] if g["home_team"] == team else g["home_score"] for g in team_g) / n


def espn_pitcher_era_gap(event_id: str, home_team: str, away_team: str,
                         date_str: str = "") -> float:
    """Live probable pitcher ERA gap from ESPN.

    Returns home_starter_ERA - away_starter_ERA.
    Negative = home starter is better (lower ERA).
    Falls back to rolling runs-allowed if ESPN unavailable.
    """
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")

    probables = _pull_espn_probables(date_str)
    entry = probables.get(event_id)
    if entry and entry.get("home_era") is not None and entry.get("away_era") is not None:
        return round(entry["home_era"] - entry["away_era"], 4)

    # Fallback: rolling runs allowed
    hra = _rolling_runs_allowed(home_team)
    ara = _rolling_runs_allowed(away_team)
    if hra and ara:
        return round(hra - ara, 4)
    return 0.0
