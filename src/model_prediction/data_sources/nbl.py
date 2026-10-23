"""Australian NBL results from ESPN's site API (team schedules, keyless).

ESPN's NBL scoreboard only answers single-date queries, so history is built from each club's
season schedule (``season`` is the year the season ends). Unplayed games are skipped from the
completed flag. Rows go to ``processed/nbl/games.jsonl`` in the shared basketball schema.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl"
LEAGUE = "NBL"
SEASONS_BACK = 3


def season_end_year(game_date: date) -> int:
    return game_date.year + 1 if game_date.month >= 8 else game_date.year


def games_path(data_root: str | Path) -> Path:
    return Path(data_root) / "processed" / "nbl" / "games.jsonl"


def normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    competition = (event.get("competitions") or [{}])[0]
    if not competition.get("status", {}).get("type", {}).get("completed"):
        return None
    sides = {c.get("homeAway"): c for c in competition.get("competitors", [])}
    home, away = sides.get("home"), sides.get("away")
    try:
        home_score = int(float((home["score"] or {}).get("value", home["score"])))
        away_score = int(float((away["score"] or {}).get("value", away["score"])))
        row = {
            "event_id": f"nbl:{event['id']}",
            "event_start_utc": str(event["date"]),
            "league": LEAGUE,
            "home_team": str(home["team"]["displayName"]),
            "away_team": str(away["team"]["displayName"]),
            "home_score": home_score,
            "away_score": away_score,
        }
    except (KeyError, TypeError, ValueError):
        return None
    if home_score <= 0 and away_score <= 0:
        return None
    kind = str((event.get("seasonType") or {}).get("abbreviation") or "")
    row["season_type"] = "postseason" if kind in {"post", "playoffs"} else "regular-season"
    row["status"] = "completed"
    return row


class NBLClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30)

    def _json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{BASE_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def team_ids(self) -> list[str]:
        teams = self._json("/teams")["sports"][0]["leagues"][0]["teams"]
        return [str(t["team"]["id"]) for t in teams]

    def season_games(self, season: int) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for team in self.team_ids():
            for event in self._json(f"/teams/{team}/schedule", {"season": season}).get("events", []):
                row = normalize_event(event)
                if row is not None:
                    rows[row["event_id"]] = row
        return list(rows.values())


def refresh_nbl_history(
    data_root: str | Path, game_date: str, client: NBLClient | None = None
) -> dict[str, Any]:
    """Backfill ``SEASONS_BACK`` seasons once, then re-read only the current one."""
    path = games_path(data_root)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row["event_id"])] = row
    current = season_end_year(date.fromisoformat(game_date))
    seasons = [current] if existing else list(range(current - SEASONS_BACK + 1, current + 1))
    source = client or NBLClient()
    errors: list[dict[str, Any]] = []
    added = 0
    for season in seasons:
        try:
            rows = source.season_games(season)
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            errors.append({"season": season, "reason": str(exc)[:200]})
            continue
        for row in rows:
            if row["event_id"] not in existing:
                added += 1
            existing[row["event_id"]] = row
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".jsonl.tmp")
        ordered = sorted(existing.values(), key=lambda r: (r["event_start_utc"], r["event_id"]))
        temporary.write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in ordered) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    return {
        "status": "error" if errors and not existing else "ok",
        "games": len(existing),
        "added": added,
        "seasons": seasons,
        "errors": errors,
    }
