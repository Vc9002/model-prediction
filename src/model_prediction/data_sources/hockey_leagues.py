"""Completed-game feeds for hockey leagues ESPN/NHL do not cover: Liiga, AHL, SHL.

All three are the leagues' own undocumented public JSON feeds (keyless; the AHL one is the
HockeyTech feed with its public client key). Rows use the hockey history schema
(``start``, ``league``, ``home_team``, ``away_team``, scores, ``completed``). Unplayed games
are skipped from the feed's own finished flag, never guessed. Team names are folded to ASCII
the way Polymarket spells them (SHL: ``Bjoerkloeven``, ``Froelunda``).
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Any

import httpx

from ..domain import utc_now

AHL_FEED = "https://lscluster.hockeytech.com/feed/index.php"
AHL_KEY = "ccb91f29d6744675"
LIIGA_URL = "https://liiga.fi/api/v2/games"
SHL_BASE = "https://www.shl.se/api/sports-v2"
SHL_SERIES = "qQ9-bb0bzEWUk"
SHL_REGULAR = "qQ9-af37Ti40B"
SEASONS_BACK = 3

_SWEDISH = {"ö": "oe", "ä": "ae", "å": "aa", "ü": "ue", "Ö": "Oe", "Ä": "Ae", "Å": "Aa", "Ü": "Ue"}


def fold_name(raw: str) -> str:
    """Repair double-encoded UTF-8, then spell the name the way Polymarket does."""
    text = str(raw).strip()
    try:
        text = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    for char, replacement in _SWEDISH.items():
        text = text.replace(char, replacement)
    return " ".join(unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").split())


def _row(
    prefix: str, league: str, game_id: Any, start: str, home: str, away: str, home_score: Any, away_score: Any
) -> dict:
    return {
        "event_id": f"{prefix}:{game_id}",
        "start": start,
        "league": league,
        "home_team": fold_name(home),
        "away_team": fold_name(away),
        "home_score": int(home_score),
        "away_score": int(away_score),
        "completed": True,
        "source": prefix,
        "observed_at_utc": utc_now().isoformat(),
    }


class HockeyLeagueClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30)

    def _json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------- Liiga
    def liiga_games(self, season_end_year: int) -> list[dict]:
        rows = []
        for tournament in ("runkosarja", "playoffs"):
            for game in self._json(LIIGA_URL, {"tournament": tournament, "season": season_end_year}):
                if not game.get("ended"):
                    continue
                home, away = game["homeTeam"], game["awayTeam"]
                if home.get("goals") is None or away.get("goals") is None:
                    continue
                rows.append(
                    _row(
                        "liiga",
                        "LIIGA",
                        f"{season_end_year}-{tournament}-{game['id']}",
                        game["start"],
                        home["teamName"],
                        away["teamName"],
                        home["goals"],
                        away["goals"],
                    )
                )
        return rows

    # ---------------------------------------------------------------- AHL
    def _ahl(self, view: str, **params: Any) -> dict:
        return self._json(
            AHL_FEED,
            {
                "feed": "modulekit",
                "view": view,
                "key": AHL_KEY,
                "fmt": "json",
                "client_code": "ahl",
                **params,
            },
        )["SiteKit"]

    def ahl_regular_season_ids(self) -> list[str]:
        seasons = self._ahl("seasons")["Seasons"]
        regular = [
            s for s in seasons if s.get("playoff") == "0" and "Regular Season" in s.get("season_name", "")
        ]
        return [s["season_id"] for s in sorted(regular, key=lambda s: int(s["season_id"]), reverse=True)][
            :SEASONS_BACK
        ]

    def ahl_games(self, season_id: str) -> list[dict]:
        names = {t["id"]: t["name"] for t in self._ahl("teamsbyseason", season_id=season_id)["Teamsbyseason"]}
        rows = []
        for game in self._ahl("schedule", season_id=season_id)["Schedule"]:
            if game.get("final") != "1" or game.get("game_status") != "Final":
                continue
            home = game.get("home_team_name") or names.get(game["home_team"])
            away = game.get("visiting_team_name") or names.get(game["visiting_team"])
            if not home or not away:
                continue
            rows.append(
                _row(
                    "ahl",
                    "AHL",
                    game["game_id"],
                    game["date_time_played"],
                    home,
                    away,
                    game["home_goal_count"],
                    game["visiting_goal_count"],
                )
            )
        return rows

    # ---------------------------------------------------------------- SHL
    def shl_season_uuids(self) -> list[str]:
        seasons = self._json(f"{SHL_BASE}/season-series-game-types-filter")["season"]
        return [s["uuid"] for s in sorted(seasons, key=lambda s: s["code"], reverse=True)][:SEASONS_BACK]

    def shl_games(self, season_uuid: str) -> list[dict]:
        payload = self._json(
            f"{SHL_BASE}/game-schedule",
            {
                "seasonUuid": season_uuid,
                "seriesUuid": SHL_SERIES,
                "gameTypeUuid": SHL_REGULAR,
                "gamePlace": "all",
                "played": "all",
            },
        )
        rows = []
        for game in payload.get("gameInfo", []):
            if game.get("state") != "post-game":
                continue
            home, away = game["homeTeamInfo"], game["awayTeamInfo"]
            if home.get("score") is None or away.get("score") is None:
                continue
            rows.append(
                _row(
                    "shl",
                    "SHL",
                    game["uuid"],
                    game["rawStartDateTime"],
                    home["names"]["long"],
                    away["names"]["long"],
                    home["score"],
                    away["score"],
                )
            )
        return rows


def refresh_other_hockey_leagues(
    existing: dict[str, dict], game_date: date, client: HockeyLeagueClient | None = None
) -> dict:
    """Merge Liiga, AHL and SHL results into the hockey history; one failure never blocks the rest."""
    source = client or HockeyLeagueClient()
    season_end = game_date.year + 1 if game_date.month >= 8 else game_date.year
    jobs: list[tuple[str, Any]] = [
        ("liiga", lambda y=y: source.liiga_games(y))
        for y in range(season_end - SEASONS_BACK + 1, season_end + 1)
    ]
    errors: list[dict] = []
    try:
        jobs += [("ahl", lambda s=s: source.ahl_games(s)) for s in source.ahl_regular_season_ids()]
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        errors.append({"league": "ahl", "reason": str(exc)})
    try:
        jobs += [("shl", lambda s=s: source.shl_games(s)) for s in source.shl_season_uuids()]
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        errors.append({"league": "shl", "reason": str(exc)})
    added = dict.fromkeys(("liiga", "ahl", "shl"), 0)
    for league, job in jobs:
        try:
            rows = job()
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"league": league, "reason": str(exc)[:200]})
            continue
        for row in rows:
            if row["event_id"] not in existing:
                added[league] += 1
            existing[row["event_id"]] = row
    return {"added": added, "errors": errors}
