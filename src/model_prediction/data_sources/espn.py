"""Read-only ESPN public-endpoint client for every supported sport.

ESPN does not provide a point-in-time archive guarantee; every payload used in
a decision must be cached to disk at observation time (see ``ingest.py``).

The MLB-specific feature reconstruction (``ESPNMLBClient``) feeds the
versioned Measured Edge forward path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..domain import League, parse_utc
from ..features.bullpen import bullpen_profile, team_recent_relief_lines
from ..features.park_factors import park_factor
from ..features.weather import resolve_weather
from ..models.mlb import MLBGameFeatures, PitcherForm, TeamForm, feature_hash

SITE_API = "https://site.api.espn.com/apis/site/v2/sports"

# ESPN sport/league path fragments per supported league key.
LEAGUE_PATHS: dict[str, str] = {
    "MLB": "baseball/mlb",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "EPL": "soccer/eng.1",
    "LA_LIGA": "soccer/esp.1",
    "BUNDESLIGA": "soccer/ger.1",
    "SERIE_A": "soccer/ita.1",
    "LIGUE_1": "soccer/fra.1",
    "EREDIVISIE": "soccer/ned.1",
    "PRIMEIRA_LIGA": "soccer/por.1",
    "CHAMPIONSHIP": "soccer/eng.2",
    "MLS": "soccer/usa.1",
    "UCL": "soccer/uefa.champions",
    "WORLD_CUP": "soccer/fifa.world",
    "BRASILEIRAO": "soccer/bra.1",
    "BRAZIL_SERIE_B": "soccer/bra.2",
    "ARGENTINA": "soccer/arg.1",
    "ARGENTINA_2": "soccer/arg.2",
    "COLOMBIA": "soccer/col.1",
    "CHILE": "soccer/chi.1",
    "URUGUAY": "soccer/uru.1",
    "ECUADOR": "soccer/ecu.1",
    "PERU": "soccer/per.1",
    "SUDAMERICANA": "soccer/conmebol.sudamericana",
    "FRIENDLIES": "soccer/fifa.friendly",
    "CLUB_FRIENDLIES": "soccer/club.friendly",
    "LIGA_MX": "soccer/mex.1",
    "NWSL": "soccer/usa.nwsl",
    "SCOTTISH_PREM": "soccer/sco.1",
    "CSL": "soccer/chn.1",
    "ALLSVENSKAN": "soccer/swe.1",
    "AUSTRIAN_BUND": "soccer/aut.1",
    "DANISH_SUPER": "soccer/den.1",
    "RUSSIAN_PREM": "soccer/rus.1",
    "NORWEGIAN_ELITE": "soccer/nor.1",
    "UEL": "soccer/uefa.europa",
    "UECL": "soccer/uefa.conference",
    "ATP": "tennis/atp",
    "WTA": "tennis/wta",
}

# Which league keys belong to each top-level sport for slate grouping.
SPORT_LEAGUES: dict[str, tuple[str, ...]] = {
    "mlb": ("MLB",),
    "nba": ("NBA",),
    "wnba": ("WNBA",),
    "nfl": ("NFL",),
    "ncaaf": ("NCAAF",),
    "soccer": (
        "EPL",
        "LA_LIGA",
        "BUNDESLIGA",
        "SERIE_A",
        "LIGUE_1",
        "EREDIVISIE",
        "PRIMEIRA_LIGA",
        "CHAMPIONSHIP",
        "MLS",
        "UCL",
        "WORLD_CUP",
        "BRASILEIRAO",
        "BRAZIL_SERIE_B",
        "ARGENTINA",
        "ARGENTINA_2",
        "COLOMBIA",
        "CHILE",
        "URUGUAY",
        "ECUADOR",
        "PERU",
        "SUDAMERICANA",
        "FRIENDLIES",
        "CLUB_FRIENDLIES",
        "LIGA_MX",
        "NWSL",
        "SCOTTISH_PREM",
        "CSL",
        "ALLSVENSKAN",
        "AUSTRIAN_BUND",
        "DANISH_SUPER",
        "RUSSIAN_PREM",
        "NORWEGIAN_ELITE",
        "UEL",
        "UECL",
    ),
    "tennis": ("ATP", "WTA"),
}


class ESPNClient:
    """Generic ESPN site-API client covering every configured league."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30)
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_keys: list[str] = []
        self._cache_limit: int = 256

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        if key not in self._cache:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            self._cache[key] = response.json()
        return self._cache[key]

    @staticmethod
    def _league_path(league: str) -> str:
        try:
            return LEAGUE_PATHS[league.upper()]
        except KeyError as error:
            raise ValueError(f"unsupported ESPN league: {league}") from error

    def scoreboard(self, league: str, game_date: str) -> dict[str, Any]:
        return self._get(
            f"{SITE_API}/{self._league_path(league)}/scoreboard",
            {"dates": game_date.replace("-", ""), "limit": 100},
        )

    def summary(self, league: str, event_id: str) -> dict[str, Any]:
        return self._get(f"{SITE_API}/{self._league_path(league)}/summary", {"event": event_id})

    def standings(self, league: str, season: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if season is not None:
            params["season"] = season
        return self._get(f"{SITE_API}/{self._league_path(league)}/standings", params)

    def teams(self, league: str) -> dict[str, Any]:
        return self._get(f"{SITE_API}/{self._league_path(league)}/teams", {"limit": 500})

    def roster(self, league: str, team_id: str, season: int) -> dict[str, Any]:
        return self._get(
            f"{SITE_API}/{self._league_path(league)}/teams/{team_id}/roster",
            {"season": season},
        )

    def team_schedule(self, league: str, team_id: str, season: int) -> dict[str, Any]:
        return self._get(
            f"{SITE_API}/{self._league_path(league)}/teams/{team_id}/schedule",
            {"season": season},
        )

    @staticmethod
    def completed_games(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize completed scoreboard events into flat game records."""
        games: list[dict[str, Any]] = []
        for event in scoreboard.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {}).get("type", {})
            if not status.get("completed"):
                continue
            by_side = {item.get("homeAway"): item for item in competition.get("competitors", [])}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            games.append(
                {
                    "event_id": str(event.get("id")),
                    "event_start_utc": event.get("date"),
                    "away_team": away["team"].get("displayName"),
                    "home_team": home["team"].get("displayName"),
                    "away_team_id": str(away["team"].get("id")),
                    "home_team_id": str(home["team"].get("id")),
                    "away_score": _score(away.get("score")),
                    "home_score": _score(home.get("score")),
                    "status": "completed",
                    "season_type": str(event.get("season", {}).get("slug", "unknown")),
                    "season_year": event.get("season", {}).get("year"),
                    "competition_type": str(competition.get("type", {}).get("abbreviation", "unknown")),
                }
            )
        return games

    @staticmethod
    def completed_tennis_singles_matches(scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize completed singles matches from ESPN's tennis scoreboard.

        Tennis events nest matches under ``groupings`` (one per draw, e.g.
        "Men's Singles", "Women's Doubles") rather than a flat
        ``competitions`` list, and competitors carry an ``athlete`` (singles)
        or ``roster`` (doubles) payload instead of ``team`` — the shape
        ``completed_games`` assumes. Doubles draws are dropped entirely via
        the competition's ``type.slug`` since this project only prices
        1-on-1 markets.

        Combined tournaments (most ATP 500/1000s and all four majors) return
        the SAME event, with BOTH Men's and Women's Singles groupings, from
        BOTH the ``tennis/atp`` and ``tennis/wta`` site-API paths -- verified
        live 2026-07-27 (e.g. ``scores_atp.json`` for Brisbane International
        contains a "Women's Singles" grouping, and ``scores_wta.json`` for
        the same date contains its "Men's Singles" grouping too). The tour
        ("ATP"/"WTA") is therefore derived per match from the competition's
        own ``type.slug`` here, never from which endpoint happened to serve
        it -- tagging by endpoint silently misattributed every WTA player's
        combined-tournament matches to "ATP" (whichever fetch ran first in
        ``SPORT_LEAGUES["tennis"]`` claimed the event_id, and the later
        fetch's identical event_id was deduped away), which then dropped
        real WTA rating history when downstream code filtered to WTA only.
        """
        matches: list[dict[str, Any]] = []
        for event in scoreboard.get("events", []):
            tournament = str(event.get("name", "unknown"))
            surface = _infer_tennis_surface(tournament)
            for grouping in event.get("groupings", []):
                for competition in grouping.get("competitions", []):
                    slug = str(competition.get("type", {}).get("slug", ""))
                    if "singles" not in slug:
                        continue
                    tour = "WTA" if "womens" in slug else "ATP" if "mens" in slug else None
                    if tour is None:
                        continue
                    status = competition.get("status", {}).get("type", {})
                    if not status.get("completed"):
                        continue
                    competitors = competition.get("competitors", [])
                    if len(competitors) != 2:
                        continue
                    winners = [item for item in competitors if item.get("winner")]
                    losers = [item for item in competitors if not item.get("winner")]
                    if len(winners) != 1 or len(losers) != 1:
                        continue
                    winner, loser = winners[0], losers[0]
                    winner_athlete = winner.get("athlete") or {}
                    loser_athlete = loser.get("athlete") or {}
                    if not winner_athlete.get("displayName") or not loser_athlete.get("displayName"):
                        continue
                    match_date = competition.get("date") or event.get("date")
                    matches.append(
                        {
                            "event_id": f"{event.get('id')}:{competition.get('id')}",
                            "event_start_utc": match_date,
                            "match_date": match_date,
                            "tournament": tournament,
                            "round": str(competition.get("round", {}).get("displayName", "unknown")),
                            "surface": surface,
                            "league": tour,
                            "winner": winner_athlete.get("displayName"),
                            "loser": loser_athlete.get("displayName"),
                            "winner_id": str(winner.get("id", "")),
                            "loser_id": str(loser.get("id", "")),
                            "status": "completed",
                        }
                    )
        return matches


# ESPN's tennis scoreboard has no surface field at all, so surface is
# inferred from well-known tournament names; anything unmatched defaults to
# Hard (the ATP/WTA tour's most common surface and TennisModel's own default).
_CLAY_TOURNAMENT_HINTS = (
    "roland garros",
    "french open",
    "monte-carlo",
    "monte carlo",
    "madrid",
    "internazionali",
    "rome",
    "barcelona",
    "munich",
    "estoril",
    "geneva",
    "hamburg",
    "bastad",
    "kitzbuhel",
    "umag",
    "gstaad",
    "buenos aires",
    "rio de janeiro",
    "santiago",
    "cordoba",
    "cordoba open",
    "houston",
    "marrakech",
    "bucharest",
    "parma",
    "båstad",
    "swedish open",
    "croatia open",
)
_GRASS_TOURNAMENT_HINTS = (
    "wimbledon",
    "queen's",
    "queens club",
    "halle",
    "terra wortmann",
    "eastbourne",
    "mallorca",
    "newport",
    "s-hertogenbosch",
    "libema",
    "nottingham",
    "birmingham",
    "berlin",
)


def _infer_tennis_surface(tournament: str) -> str:
    name = tournament.casefold()
    if any(hint in name for hint in _CLAY_TOURNAMENT_HINTS):
        return "Clay"
    if any(hint in name for hint in _GRASS_TOURNAMENT_HINTS):
        return "Grass"
    return "Hard"


class ESPNMLBClient:
    """Read-only ESPN MLB client. ESPN does not provide a point-in-time archive guarantee."""

    def __init__(
        self,
        site_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb",
        common_base_url: str = "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb",
        athlete_base_url: str = "https://site.api.espn.com/apis/common/v3/sports/baseball/mlb",
        client: httpx.Client | None = None,
    ) -> None:
        self.site_base_url = site_base_url.rstrip("/")
        self.common_base_url = common_base_url.rstrip("/")
        self.athlete_base_url = athlete_base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_keys: list[str] = []
        self._cache_limit: int = 256

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        if key not in self._cache:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            self._cache[key] = response.json()
            self._cache_keys.append(key)
            while len(self._cache_keys) > self._cache_limit:
                expired = self._cache_keys.pop(0)
                self._cache.pop(expired, None)
        return self._cache[key]

    def scoreboard(self, game_date: str) -> dict[str, Any]:
        return self._get(
            f"{self.site_base_url}/scoreboard", {"dates": game_date.replace("-", ""), "limit": 100}
        )

    def summary(self, event_id: str) -> dict[str, Any]:
        return self._get(f"{self.site_base_url}/summary", {"event": event_id})

    def schedule(self, team_id: str, season: int) -> dict[str, Any]:
        return self._get(f"{self.site_base_url}/teams/{team_id}/schedule", {"season": season})

    def player_gamelog(self, player_id: str, season: int) -> dict[str, Any]:
        return self._get(f"{self.common_base_url}/athletes/{player_id}/gamelog", {"season": season})

    def athlete(self, player_id: str) -> dict[str, Any]:
        return self._get(f"{self.athlete_base_url}/athletes/{player_id}")

    def reconstructed_features(self, event: dict[str, Any]) -> MLBGameFeatures:
        competition = event["competitions"][0]
        start = parse_utc(event["date"])
        season = start.year
        teams = {item["homeAway"]: item for item in competition["competitors"]}
        away, home = teams["away"], teams["home"]
        away_probable = _probable(away)
        home_probable = _probable(home)
        if away_probable is None or home_probable is None:
            raise ValueError(f"event {event['id']} has an unresolved probable starter")
        away_schedule = self.schedule(str(away["team"]["id"]), season)
        home_schedule = self.schedule(str(home["team"]["id"]), season)
        away_gamelog = self.player_gamelog(str(away_probable["playerId"]), season)
        home_gamelog = self.player_gamelog(str(home_probable["playerId"]), season)
        away_profile = self.athlete(str(away_probable["playerId"]))
        home_profile = self.athlete(str(home_probable["playerId"]))
        starter_confirmed = all(
            _starter_status(probable) == "confirmed" for probable in (away_probable, home_probable)
        )
        source_payloads = [event, away_schedule, home_schedule, away_gamelog, home_gamelog]
        source_ids = tuple(_payload_hash(payload) for payload in source_payloads)
        # Wire the full feature pipeline — every field degrades to neutral when
        # the underlying source is unavailable so the model works with partial data.
        home_name = home["team"]["displayName"]
        park = park_factor(home_name)
        # ESPN's own weather fields (competition.situation.weather,
        # summary gameInfo.weather) are empirically always empty for MLB, live
        # or completed -- confirmed by direct inspection, not just in docs.
        # Open-Meteo (live forecast for upcoming games, historical archive for
        # past ones) is the only source that actually returns real conditions,
        # and resolve_weather() picks the right endpoint from event["date"].
        weather = resolve_weather(home_name, event["date"])
        away_name = away["team"]["displayName"]
        # Real relief-appearance history (mlb_statsapi.py's boxscore
        # snapshots) for each team's last 10 completed games strictly before
        # this one -- replaces the old always-neutral bullpen_profile(None),
        # confirmed by direct inspection: that call never had real data wired
        # in at all, every MLB game got a flat 1.0 bullpen factor regardless
        # of real bullpen strength.
        away_bullpen = bullpen_profile(team_recent_relief_lines(away_name, start))
        home_bullpen = bullpen_profile(team_recent_relief_lines(home_name, start))
        features = MLBGameFeatures(
            event_id=str(event["id"]),
            event_start_utc=event["date"],
            decision_timestamp_utc=(start - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            away_team=away_name,
            home_team=home_name,
            away_form=parse_team_form(away_schedule, str(away["team"]["id"]), start),
            home_form=parse_team_form(home_schedule, str(home["team"]["id"]), start),
            away_starter=parse_pitcher_form(away_gamelog, away_profile, start),
            home_starter=parse_pitcher_form(home_gamelog, home_profile, start),
            away_bullpen_weakness=away_bullpen["bullpen_weakness_index"],
            home_bullpen_weakness=home_bullpen["bullpen_weakness_index"],
            away_bullpen_status=away_bullpen["status"],
            home_bullpen_status=home_bullpen["status"],
            park_factor=park["park_factor"],
            park_factor_status=park["status"],
            weather_factor=weather["weather_run_factor"],
            weather_status=weather["status"],
            starter_confirmed=starter_confirmed,
            starter_status="confirmed" if starter_confirmed else "probable",
            source_snapshot_ids=source_ids,
        )
        return replace(features, feature_snapshot_hash=feature_hash(features))


def parse_team_form(schedule: dict[str, Any], team_id: str, decision: datetime) -> TeamForm:
    games = []
    for event in schedule.get("events", []):
        if parse_utc(event["date"]) >= decision:
            continue
        competition = event["competitions"][0]
        if not competition.get("status", {}).get("type", {}).get("completed"):
            continue
        competitors = competition.get("competitors", [])
        team = next((item for item in competitors if str(item["team"]["id"]) == team_id), None)
        opponent = next((item for item in competitors if str(item["team"]["id"]) != team_id), None)
        if team is None or opponent is None:
            continue
        games.append(
            (
                parse_utc(event["date"]),
                _score(team.get("score")),
                _score(opponent.get("score")),
            )
        )
    recent = sorted(games, key=lambda item: item[0])[-10:]
    return TeamForm(
        tuple(item[1] for item in recent),
        tuple(item[2] for item in recent),
        sum(item[1] > item[2] for item in recent),
        sum(item[1] < item[2] for item in recent),
        "available" if recent else "unavailable_from_source",
    )


def parse_pitcher_form(
    gamelog: dict[str, Any], profile_payload: dict[str, Any], decision: datetime
) -> PitcherForm:
    names = gamelog.get("names", [])
    events = gamelog.get("events", {})
    rows = []
    for season_type in gamelog.get("seasonTypes", []):
        for category in season_type.get("categories", []):
            if category.get("type") != "event":
                continue
            for item in category.get("events", []):
                event = events.get(str(item["eventId"]), {})
                game_date = event.get("gameDate")
                if not game_date or parse_utc(game_date) >= decision:
                    continue
                stats = dict(zip(names, item.get("stats", []), strict=False))
                rows.append((parse_utc(game_date), stats))
    rows.sort(key=lambda item: item[0])
    last_five = rows[-5:]
    profile = profile_payload.get("athlete", profile_payload)
    bats_throws = profile.get("displayBatsThrows", "")
    throwing_hand = bats_throws.split("/")[-1] if "/" in bats_throws else None
    return PitcherForm(
        player_id=str(profile.get("id", "")),
        name=profile.get("displayName", "Unknown starter"),
        throwing_hand=throwing_hand,
        starts_before_game=len(rows),
        season_innings=sum(_baseball_innings(stats.get("innings")) for _, stats in rows),
        season_earned_runs=sum(_integer(stats.get("earnedRuns")) for _, stats in rows),
        season_strikeouts=sum(_integer(stats.get("strikeouts")) for _, stats in rows),
        season_walks=sum(_integer(stats.get("walks")) for _, stats in rows),
        season_batters_faced=sum(_integer(stats.get("battersFaced")) for _, stats in rows),
        last_five_innings=sum(_baseball_innings(stats.get("innings")) for _, stats in last_five),
        last_five_earned_runs=sum(_integer(stats.get("earnedRuns")) for _, stats in last_five),
        last_five_strikeouts=sum(_integer(stats.get("strikeouts")) for _, stats in last_five),
        last_five_walks=sum(_integer(stats.get("walks")) for _, stats in last_five),
        last_five_batters_faced=sum(_integer(stats.get("battersFaced")) for _, stats in last_five),
    )


def parse_pregame_and_closing_markets(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary.get("pickcenter"):
        return {}
    pick = summary["pickcenter"][0]
    moneyline = pick.get("moneyline", {})
    spread = pick.get("pointSpread", {})
    total = pick.get("total", {})
    return {
        "provider": pick.get("provider", {}).get("name", "unknown"),
        "moneyline": {
            side: {
                "decision_odds": _odds(moneyline.get(side, {}).get("open", {}).get("odds")),
                "closing_odds": _odds(moneyline.get(side, {}).get("close", {}).get("odds")),
            }
            for side in ("away", "home")
        },
        "spread": {
            side: {
                "decision_line": _line(spread.get(side, {}).get("open", {}).get("line")),
                "decision_odds": _odds(spread.get(side, {}).get("open", {}).get("odds")),
                "closing_line": _line(spread.get(side, {}).get("close", {}).get("line")),
                "closing_odds": _odds(spread.get(side, {}).get("close", {}).get("odds")),
            }
            for side in ("away", "home")
        },
        "total": {
            side: {
                "decision_line": _line(total.get(side, {}).get("open", {}).get("line")),
                "decision_odds": _odds(total.get(side, {}).get("open", {}).get("odds")),
                "closing_line": _line(total.get(side, {}).get("close", {}).get("line")),
                "closing_odds": _odds(total.get(side, {}).get("close", {}).get("odds")),
            }
            for side in ("over", "under")
        },
        "snapshot_hash": _payload_hash(pick),
    }


def _probable(competitor: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item for item in competitor.get("probables", []) if item.get("name") == "probableStartingPitcher"),
        None,
    )


def _starter_status(probable: dict[str, Any]) -> str:
    raw = probable.get("status") or probable.get("starterStatus") or probable.get("type") or "probable"
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("state") or raw.get("description") or "probable"
    return "confirmed" if str(raw).strip().casefold() == "confirmed" else "probable"


def _score(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    return int(float(value))


def _integer(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _baseball_innings(value: Any) -> float:
    try:
        whole, _, outs = str(value).partition(".")
        return int(whole) + (int(outs or 0) / 3)
    except (TypeError, ValueError):
        return 0.0


def _odds(value: Any) -> int | None:
    try:
        return int(str(value).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _line(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).lower().lstrip("ou"))
    except ValueError:
        return None


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Convenience re-export so League enum users can map to ESPN paths.
def league_path(league: League | str) -> str:
    key = league.value if isinstance(league, League) else str(league)
    return LEAGUE_PATHS[key.upper()]
