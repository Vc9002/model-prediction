from __future__ import annotations

from typing import Any

import httpx

from ..domain import League
from ..entities import EntityRegistry

SPORT_KEYS = {
    "MLB": "baseball_mlb",
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "NFL": "americanfootball_nfl",
    "WORLD_CUP": "soccer_fifa_world_cup",
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


class TheOddsAPIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.the-odds-api.com/v4",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("THE_ODDS_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _safe_get(self, url: str, **kwargs) -> httpx.Response:
        """GET + raise_for_status, with API key redacted from any error message."""
        try:
            response = self.client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            # Both transport errors and HTTP status errors embed the URL —
            # redact the API key from the message before re-raising.
            #
            # Real bug found 2026-08-04 (first live daily run after this
            # redaction landed): `type(exc)(msg)` reconstructs the SAME
            # exception class with only the redacted message as a single
            # positional arg. That's fine for a plain transport error, but
            # httpx.HTTPStatusError.__init__ requires `request`/`response` as
            # additional keyword-only args -- reconstructing it this way
            # raised its own TypeError instead of the redacted error,
            # breaking soccer score collection for every league on this
            # provider. Every real caller catches the httpx.HTTPError base
            # class (mlb_market_odds.py, cli.py), not a specific subtype, and
            # httpx.HTTPError itself only takes a message -- raise that
            # directly instead of trying to preserve the exact original type.
            msg = str(exc).replace(str(self.api_key), "[REDACTED]")
            raise httpx.HTTPError(msg) from None

    def odds(self, league: str, markets: str = "h2h,spreads,totals") -> list[dict[str, Any]]:
        sport = SPORT_KEYS[league.upper()]
        return self._odds_for_sport(sport, markets)

    def active_tennis_sports(self) -> list[dict[str, Any]]:
        response = self._safe_get(
            f"{self.base_url}/sports",
            params={"apiKey": self.api_key},
        )
        response.raise_for_status()
        return [
            sport
            for sport in response.json()
            if sport.get("active", True)
            and (
                str(sport.get("key", "")).startswith("tennis_")
                or str(sport.get("group", "")).casefold() == "tennis"
            )
        ]

    def tennis_odds(
        self, sport_key: str, markets: str = "h2h,spreads,totals"
    ) -> list[dict[str, Any]]:
        if not sport_key.startswith("tennis_"):
            raise ValueError("tennis sport key must start with 'tennis_'")
        return self._odds_for_sport(sport_key, markets)

    def _odds_for_sport(self, sport: str, markets: str) -> list[dict[str, Any]]:
        response = self._safe_get(
            f"{self.base_url}/sports/{sport}/odds",
            params={
                "apiKey": self.api_key,
                "regions": "us",
                "markets": markets,
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
        )
        response.raise_for_status()
        return response.json()

    def scores(self, league: str, days_from: int = 3) -> list[dict[str, Any]]:
        sport = SPORT_KEYS[league.upper()]
        response = self._safe_get(
            f"{self.base_url}/sports/{sport}/scores",
            params={"apiKey": self.api_key, "daysFrom": days_from, "dateFormat": "iso"},
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def normalize_event_teams(
        event: dict[str, Any], league: League, registry: EntityRegistry
    ) -> dict[str, Any]:
        normalized = dict(event)
        away = registry.resolve(league, event["away_team"])
        home = registry.resolve(league, event["home_team"])
        normalized["canonical_away_team_id"] = away.canonical_team_id
        normalized["canonical_home_team_id"] = home.canonical_team_id
        normalized["source_away_team"] = event["away_team"]
        normalized["source_home_team"] = event["home_team"]
        return normalized
