"""EuroLeague results from the free, keyless official live API (v2).

``/v2/competitions/E/seasons/E<year>/games`` returns every game of a season with
``local`` (home) and ``road`` (away) clubs and final scores. Unplayed games have
``played: false`` and no usable score and are skipped, never guessed. Neutral-site
games (Final Four) are kept but flagged so callers can drop them.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api-live.euroleague.net/v2"
LEAGUE = "EUROLEAGUE"
COMPETITION_CODE = "E"
BOOTSTRAP_SEASONS = 5
PAGE_SIZE = 200


# Polymarket club name -> official club code. Sponsors rename clubs between seasons
# ("Kosner"/"Cazoo" Baskonia), so identity is the stable API club code and unknown
# names fail closed rather than fuzzy-matching.
POLYMARKET_CLUB_CODES = {
    "anadolu efes sk": "IST",
    "besiktas jk": "BES",
    "bayern munich": "MUN",
    "dubai basketball": "DUB",
    "maccabi tel-aviv": "TEL",
    "real madrid": "MAD",
    "panathinaikos bc": "PAN",
    "baskonia vitoria-gasteiz": "BAS",
    "olimpia milano": "MIL",
    "fc barcelona": "BAR",
    "kk partizan belgrade": "PAR",
    "kk crvena zvezda belgrade": "RED",
    "bc zalgiris kaunas": "ZAL",
    "asvel lyon-villeurbanne": "ASV",
    "bc olympiakos piraeus": "OLY",
    "fenerbahce istanbul": "ULK",
    "paris basketball": "PRS",
    "valencia basket": "PAM",
    "hapoel tel-aviv": "HTA",
    "virtus bologna": "VIR",
    "as monaco": "MCO",
    "alba berlin": "BER",
}


def resolve_club(name: str, code_to_name: dict[str, str]) -> str | None:
    """Canonical history name for a Polymarket club name, or None when unknown."""
    code = POLYMARKET_CLUB_CODES.get(" ".join(str(name).casefold().split()))
    return code_to_name.get(code) if code else None


def season_code(game_date: date) -> int:
    """EuroLeague season year: the season starting in July of ``year``."""
    return game_date.year if game_date.month >= 7 else game_date.year - 1


def normalize_game(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("played"):
        return None
    local, road = row.get("local") or {}, row.get("road") or {}
    try:
        home_score, away_score = int(local["score"]), int(road["score"])
        home = str(local["club"]["name"]).strip()
        away = str(road["club"]["name"]).strip()
        start = str(row["utcDate"])
    except (KeyError, TypeError, ValueError):
        return None
    if home_score <= 0 and away_score <= 0:
        return None
    return {
        "event_id": f"euroleague:{row['identifier']}",
        "event_start_utc": start,
        "league": LEAGUE,
        "home_team": home,
        "away_team": away,
        "home_code": str(local["club"]["code"]),
        "away_code": str(road["club"]["code"]),
        "home_score": home_score,
        "away_score": away_score,
        "season_type": {
            "RS": "regular-season",
            "PO": "postseason",
            "PI": "postseason",
            "FF": "postseason",
        }.get(str((row.get("phaseType") or {}).get("code")), "unknown"),
        "season_year": int((row.get("season") or {}).get("year") or 0) or None,
        "competition_type": "NEUTRAL" if row.get("isNeutralVenue") else "STD",
        "status": "completed",
        "source": "euroleague_v2",
    }


class EuroLeagueClient:
    def __init__(self, client: httpx.Client | None = None, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30)

    def season_games(self, year: int) -> list[dict[str, Any]]:
        games: list[dict[str, Any]] = []
        offset = 0
        for _ in range(20):
            response = self.client.get(
                f"{self.base_url}/competitions/{COMPETITION_CODE}/seasons/{COMPETITION_CODE}{year}/games",
                params={"limit": PAGE_SIZE, "offset": offset},
            )
            response.raise_for_status()
            page = response.json().get("data", [])
            games.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return [game for game in map(normalize_game, games) if game is not None]


def games_path(data_root: str | Path) -> Path:
    return Path(data_root) / "processed" / "euroleague" / "games.jsonl"


def club_names(data_root: str | Path) -> dict[str, str]:
    """Club code -> canonical (latest) club name from the cached results."""
    path = games_path(data_root)
    if not path.exists():
        return {}
    names: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            names[row["home_code"]] = row["home_team"]
            names[row["away_code"]] = row["away_team"]
    return names


def refresh_euroleague_history(
    data_root: str | Path,
    game_date: str,
    client: EuroLeagueClient | None = None,
) -> dict[str, Any]:
    """Backfill ``BOOTSTRAP_SEASONS`` seasons once, then re-read only the current one."""
    path = games_path(data_root)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row["event_id"])] = row
    current = season_code(date.fromisoformat(game_date))
    years = [current] if existing else list(range(current - BOOTSTRAP_SEASONS + 1, current + 1))
    source = client or EuroLeagueClient()
    errors: list[dict[str, Any]] = []
    added = 0
    for year in years:
        try:
            rows = source.season_games(year)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            errors.append({"year": year, "reason": str(exc)})
            continue
        for row in rows:
            if row["event_id"] not in existing:
                added += 1
            existing[row["event_id"]] = row
    # One name per club code (latest game wins) so sponsor renames share one rating.
    latest: dict[str, str] = {}
    for row in sorted(existing.values(), key=lambda r: r["event_start_utc"]):
        latest[row["home_code"]] = row["home_team"]
        latest[row["away_code"]] = row["away_team"]
    for row in existing.values():
        row["home_team"], row["away_team"] = latest[row["home_code"]], latest[row["away_code"]]
    if added or path.exists():
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
        "years": years,
        "errors": errors,
    }
