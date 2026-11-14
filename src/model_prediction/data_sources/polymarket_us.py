from __future__ import annotations

import json
import logging
import math
import random
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import zip_longest
from pathlib import Path
from threading import Event, Lock
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..domain import iso_utc, parse_utc, utc_now

# Verified against GET /v2/leagues on the live gateway (2026-07-16;
# South American/friendlies additions re-verified 2026-07-27 via
# GET /v2/sports, which lists all 61 currently operational soccer leagues).
LEAGUE_SLUGS = {
    "MLB": "mlb",
    "NBA": "nba",
    "WNBA": "wnba",
    "NFL": "nfl",
    "NCAAF": "cfb",
    "CFB": "cfb",
    "NHL": "nhl",
    "AHL": "ahl",
    "KHL": "khl",
    "DEL": "del",
    "SHL": "shl",
    "SNHL": "snhl",
    "CEHL": "cehl",
    "LIIGA": "liiga",
    "DARTS_PDC": "pdcdarts",
    "DARTS_MODUS": "modus",
    "NCAAB": "cbb",
    "WNCAAB": "wcbb",
    "EUROLEAGUE": "eurolg",
    "LNB": "lnb",
    "ABA": "aba",
    "DENBL": "denbl",
    "AUTBL": "autbl",
    "SLNBL": "slnbl",
    "FRA2": "fra2",
    "ACB": "acb",
    "BBL": "bbl",
    "NBL": "nbl",
    "BCL": "bcl",
    "EUROCUP": "eurocup",
    "EPL": "epl",
    "LA_LIGA": "lal",
    "BUNDESLIGA": "bun",
    "SERIE_A": "sea",
    "LIGUE_1": "lg1",
    "PRIMEIRA_LIGA": "ligpor",
    "CHAMPIONSHIP": "eflch",
    "USL_CHAMPIONSHIP": "uslc",
    "NORWEGIAN_ELITE": "nor1",
    "LIGA_EXPANSION_MX": "lexp",
    "UCL": "ucl",
    "UEFA": "uefa",
    "UEFA_NATIONS": "unl",
    "CONCACAF_CHAMPIONS": "concacaf",
    "EREDIVISIE": "ned1",
    "MLS": "mls",
    "WORLD_CUP": "fwc",
    "BRASILEIRAO": "bra",
    "BRAZIL_SERIE_B": "brb",
    "ARGENTINA": "lpa",
    "ARGENTINA_2": "arg2",
    "COLOMBIA": "lco",
    "CHILE": "pdc",
    "URUGUAY": "uru1",
    "ECUADOR": "ecu1",
    "PERU": "pl1",
    "SUDAMERICANA": "sud",
    "FRIENDLIES": "intf",
    "CLUB_FRIENDLIES": "clbf",
    "LIGA_MX": "lmx",
    "NWSL": "nwsl",
    "SCOTTISH_PREM": "scp",
    "CSL": "csl",
    "ALLSVENSKAN": "alsv",
    "AUSTRIAN_BUND": "atbl",
    "DANISH_SUPER": "sld",
    "RUSSIAN_PREM": "rpl",
    "UEL": "uel",
    "UECL": "uecl",
    "BELGIAN_PRO": "bpl",
    "TURKISH_SUPER": "tsl",
    "SAUDI_PRO": "spl",
    "GREEK_SUPER": "gsl",
    "SWISS_SUPER": "ssl",
    "A_LEAGUE": "aleague",
    "J_LEAGUE": "j1",
    "2_BUNDESLIGA": "bun2",
    "LA_LIGA_2": "lal2",
    "SERIE_B": "seb",
    "LIGUE_2": "l2",
    "LIBERTADORES": "lib",
    "FA_CUP": "facup",
    "EFL_CUP": "efl",
    "COPA_DEL_REY": "cdr",
    "DFB_POKAL": "dfb",
    "COPPA_ITALIA": "coppa",
    "WTA": "wta",
    "ATP": "atp",
    # "itfm"/"itfw" are now dead league slugs on the live gateway (0 events
    # as of 2026-08-03, superseded by these -- verified via GET /v2/sports
    # and GET /v2/leagues/<slug>/events, real ITF singles matches present
    # under seriesSlug itf-mens-2026 / itf-womens-2026).
    "ITF_MEN": "itfme",
    "ITF_WOMEN": "itfwo",
    "LOL": "lol",
    "CS2": "cs2",
    "KBO": "kbo",
    "NPB": "npb",
    "COD": "cod",
    "VALORANT": "valorant",
    "DOTA2": "dota2",
    "ROCKET_LEAGUE": "rl",
    "OVERWATCH": "ow",
    "RAINBOW_SIX": "r6",
}

# Gateway coverage per sport key. Ligue 1, Primeira Liga, and Championship
# were activated on the Polymarket US gateway in 2026-08.
POLYMARKET_SPORT_LEAGUES: dict[str, tuple[str, ...]] = {
    "mlb": ("MLB",),
    "nba": ("NBA",),
    "wnba": ("WNBA",),
    "nfl": ("NFL",),
    "ncaaf": ("NCAAF",),
    # WORLD_CUP dropped 2026-07: tournament is over, no games left to trade.
    "soccer": (
        "EPL",
        "LA_LIGA",
        "BUNDESLIGA",
        "SERIE_A",
        "LIGUE_1",
        "PRIMEIRA_LIGA",
        "CHAMPIONSHIP",
        "USL_CHAMPIONSHIP",
        "NORWEGIAN_ELITE",
        "LIGA_EXPANSION_MX",
        "UCL",
        "UEFA",
        "MLS",
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
        "UEL",
        "UECL",
        "BELGIAN_PRO",
        "TURKISH_SUPER",
        "SAUDI_PRO",
        "GREEK_SUPER",
        "SWISS_SUPER",
        "A_LEAGUE",
        "J_LEAGUE",
        "2_BUNDESLIGA",
        "LA_LIGA_2",
        "SERIE_B",
        "LIGUE_2",
        "LIBERTADORES",
        "FA_CUP",
        "EFL_CUP",
        "COPA_DEL_REY",
        "DFB_POKAL",
        "COPPA_ITALIA",
    ),
    # ATP added 2026-08-03: live-verified against GET /v2/sports (real
    # operational league, 88 events including real matches with executable
    # spread markets) -- the 2026-07-16 "ATP has no Polymarket US league"
    # note is stale. ESPN already has a matching ATP scoreboard
    # (LEAGUE_PATHS["ATP"]), closing the gap that previously made WTA the
    # only tour tennis_forward.py could ever price.
    "tennis": ("WTA", "ATP", "ITF_MEN", "ITF_WOMEN"),
    "esports": (
        "LOL",
        "CS2",
        "COD",
        "VALORANT",
        "DOTA2",
        "ROCKET_LEAGUE",
        "OVERWATCH",
        "RAINBOW_SIX",
    ),
    "kbo": ("KBO",),
    "npb": ("NPB",),
    "hockey": (
        "NHL",
        "AHL",
        "KHL",
        "DEL",
        "SHL",
        "SNHL",
        "CEHL",
        "LIIGA",
    ),
    "darts": (
        "DARTS_PDC",
        "DARTS_MODUS",
    ),
    "basketball": (
        "NBA",
        "WNBA",
        "NCAAB",
        "WNCAAB",
        "EUROLEAGUE",
        "LNB",
        "ABA",
        "DENBL",
        "AUTBL",
        "SLNBL",
        "FRA2",
        "ACB",
        "BBL",
        "NBL",
    ),
    "ncaab": ("NCAAB",),
    "wncaab": ("WNCAAB",),
}

# Esports leagues captured for BBO but priced by no model yet (2026-07-27
# audit gap: the mismatch between the 8 captured leagues and the 4 priced
# ones was silent -- no test caught the drift). Capture is deliberate
# (market evidence for future models), but the deferral must be named:
# the pinning test fails if a league is added to capture without landing
# in TITLE_SPECS or this set, or if a model starts pricing a league that
# is not captured. Remove a name when its model ships.
CAPTURED_UNPRICED_ESPORTS_LEAGUES: frozenset[str] = frozenset({"COD", "ROCKET_LEAGUE", "OVERWATCH"})

# Sports whose prospective BBO capture feeds the daily qualification/promotion
# pipeline (config/model.yaml, models/registry.py). Do not derive this from
# "every league in POLYMARKET_SPORT_LEAGUES" or from registry.MODEL_SPECS
# wholesale.
# Esports, soccer, and tennis are research-only, but prospective BBO capture
# must begin before profitability can ever be tested; capturing prices does
# not qualify a model or authorize execution. Tennis capture only ever
# produces usable rows for the WTA league within it -- Polymarket US has no
# ATP market, and ITF has no matching ESPN scoreboard to build a prediction
# from (see tennis_forward.py) -- but ITF/WTA snapshots are still captured
# here in case that coverage gap closes later.
BBO_CAPTURE_SPORTS = {
    "mlb",
    "nba",
    "wnba",
    "nfl",
    "ncaaf",
    "soccer",
    "esports",
    "kbo",
    "npb",
    "tennis",
    "hockey",
    "darts",
    "basketball",
    "ncaab",
    "wncaab",
}

# Leagues whose league-feed events carry only the winner market while the
# per-event detail page lists spreads/totals (live-verified 2026-10-21 per
# league; AHL/KHL/DEL/SHL, the smaller soccer leagues, tennis ITF, Overwatch
# and the smaller European basketball leagues list winner markets only).
FULL_GAME_LINE_LEAGUES = frozenset(
    {
        "NBA",
        "WNBA",
        "NFL",
        "NCAAF",
        "NHL",
        "EPL",
        "LA_LIGA",
        "BUNDESLIGA",
        "SERIE_A",
        "LIGUE_1",
        "MLS",
        "LIGA_MX",
        "UCL",
        "UEFA",
        "UEL",
        "UECL",
        "SUDAMERICANA",
        "LIBERTADORES",
        "EUROLEAGUE",
        "LOL",
        "RAINBOW_SIX",
        "CS2",
        "VALORANT",
        "DOTA2",
        "NCAAB",
        "WNCAAB",
        "ATP",
        "WTA",
    }
)
HYDRATED_LEAGUES = frozenset({"MLB"}) | FULL_GAME_LINE_LEAGUES
# The generic V2 SPREAD/TOTAL types also cover quarter, half, period and
# team-total markets (e.g. an NBA first-quarter total at 58.5 beside the 224.5
# game total). For these leagues only the legacy full-game types are priced; the
# models forecast full-game spreads and totals.
FULL_GAME_LINE_TYPES = frozenset(
    {
        "basketball_team_full_game_spread",
        "basketball_team_full_game_total",
        "football_team_full_game_spread",
        "football_team_full_game_total",
        "hockey_team_full_game_spread",
        "hockey_team_full_game_total",
        "soccer_team_full_game_spread",
        "soccer_team_full_game_total",
        "tennis_match_games_spread",
        "tennis_match_total_games",
        "esports_series_game_handicap",
        "esports_series_total_games",
        "esports_series_map_handicap",
        "esports_series_total_maps",
    }
)

MARKET_TYPES = {
    "SPORTS_MARKET_TYPE_MONEYLINE": "moneyline",
    "SPORTS_MARKET_TYPE_SPREAD": "spread",
    "SPORTS_MARKET_TYPE_TOTAL": "total",
    # Soccer's win market is not one combined 2-team market like moneyline --
    # it's three separate Yes/No markets per event (home wins / draw / away
    # wins), each with its own market_slug. The classifier reads
    # sportsMarketTypeV2 first, falling back to sportsMarketType only when
    # V2 is absent (see _normalize_event) -- verified live 2026-07-27 that
    # soccer win markets actually populate sportsMarketTypeV2 as
    # SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME (sportsMarketType separately carries
    # the more descriptive "soccer_team_full_time_winner", kept below as a
    # defensive fallback in case some response omits V2). Neither key was
    # previously recognized, so these markets were silently dropped entirely.
    # Known consumer-side gap (2026-07-27 audit, still open): models/soccer.py
    # emits market_type="btts" predictions, but no BTTS Polymarket type
    # string has ever been observed in live captures (a 3-day snapshot sweep
    # 2026-08-26 showed only team_win/spread/total), so no mapping exists.
    # Unrecognized strings are logged once per run (_log_unknown_market_type)
    # instead of vanishing silently -- when a BTTS market appears, add its
    # string here with the date it was first observed.
    "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME": "team_win",
    "soccer_team_full_time_winner": "team_win",
    # Live-verified 2026-09-01 from GET /v1/events/slug/<mlb-event>.
    # The contract asks whether any run is scored in the first inning:
    # Yes is YRFI and No is NRFI. The V2 field is only the generic
    # SPORTS_MARKET_TYPE_PROP, so classification must fall through to this
    # specific legacy field rather than treating every prop as interchangeable.
    "baseball_team_first_inning_run": "nrfi",
    "hockey_team_full_game_winner": "moneyline",
    "hockey_puck_line": "spread",
    "hockey_total_goals": "total",
    "darts_match_winner": "moneyline",
    "darts_leg_spread": "spread",
    "darts_total_legs": "total",
    "basketball_team_full_game_winner": "moneyline",
    "basketball_point_spread": "spread",
    "basketball_total_points": "total",
}

logger = logging.getLogger(__name__)

# Per-process dedup so an unknown market type is logged once per run, not
# once per event (the daily slates carry thousands of markets). Bounded by
# the gateway's actual type vocabulary, so the set stays small even in the
# long-running dashboard process.
_UNKNOWN_MARKET_TYPES_LOGGED: set[str] = set()


def _log_unknown_market_type(raw_type: str | None, league: str) -> None:
    """Unrecognized market types are dropped from capture (see MARKET_TYPES)
    -- make that visible in the daily log instead of silent, so a new market
    type (e.g. a BTTS market) is discoverable from the log line instead of
    disappearing with no trace."""
    key = f"{league}:{raw_type}"
    if key in _UNKNOWN_MARKET_TYPES_LOGGED:
        return
    _UNKNOWN_MARKET_TYPES_LOGGED.add(key)
    if raw_type == "SPORTS_MARKET_TYPE_PROP":
        logger.debug(
            "generic Polymarket prop dropped from capture: %r (league %s)",
            raw_type,
            league,
        )
        return
    logger.warning(
        "unrecognized Polymarket market type dropped from capture: %r (league %s)",
        raw_type,
        league,
    )


def probability_to_american(probability: float) -> int:
    if not 0 < probability < 1:
        raise ValueError("price probability must be between 0 and 1")
    if probability == 0.5:
        return 100
    if probability > 0.5:
        return -round(100 * probability / (1 - probability))
    return round(100 * (1 - probability) / probability)


@dataclass(frozen=True)
class SportSlateResult:
    """Return shape for ``PolymarketUSClient.sport_slate``.

    ``events`` keeps the historical dict[league, list[event]] shape so every
    existing consumer (event counting, BBO capture) keeps working unchanged.
    ``errors`` is populated only for leagues whose fetch actually failed, so
    a real outage stays distinguishable from a genuinely empty slate.
    """

    events: dict[str, list[dict[str, Any]]]
    errors: dict[str, str]


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying a 429: Retry-After when numeric, else jittered exponential backoff."""
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.strip().isdigit():
        return min(float(retry_after), 10.0)
    base = min(8.0, 0.5 * (2**attempt))
    return base + random.uniform(0.0, base / 4)


def _connect_retry_delay_seconds(attempt: int) -> float:
    """Jittered exponential backoff for DNS/connect failures (no response to inspect)."""
    base = min(8.0, 0.5 * (2**attempt))
    return base + random.uniform(0.0, base / 4)


class PolymarketUSClient:
    """Read-only client for the unauthenticated Polymarket US public gateway."""

    def __init__(
        self,
        base_url: str = "https://gateway.polymarket.us",
        client: httpx.Client | None = None,
        max_attempts: int = 6,
        prefer_bbo: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self.prefer_bbo = prefer_bbo
        self._last_bbo_request = 0.0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # The gateway throttles bursts from the 16-worker snapshot pool with
        # plain 429s (no Retry-After header observed in practice); a short
        # bounded backoff recovers most of these in place instead of losing
        # the market's book snapshot for the day (2026-08-24, ~1,700/day
        # dropped book fetches -- concentrated in ITF tennis and esports,
        # the highest-market-count leagues).
        # 2026-10-04: 1,686 throttled responses in one daily run; the old
        # 4-attempt ~3.5s budget ran out while the pool was still bursting.
        # Honor Retry-After when given, otherwise back off with jitter so the
        # 16 workers stop retrying in lockstep.
        for attempt in range(self.max_attempts):
            try:
                response = self.client.get(f"{self.base_url}{path}", params=params)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # DNS resolution (getaddrinfo) and connect failures arrive as
                # exceptions, not responses. A burst of them (1,810 in one daily
                # run, 2026-10-22) lost almost every book snapshot, so retry
                # with backoff before surfacing the error.
                if attempt < self.max_attempts - 1:
                    time.sleep(_connect_retry_delay_seconds(attempt))
                    continue
                raise
            if response.status_code == 429 and attempt < self.max_attempts - 1:
                time.sleep(_retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable")  # pragma: no cover

    def events(self, league: str, limit: int = 500) -> list[dict[str, Any]]:
        """Fetch all events for a league, paginating until exhaustion.

        The Polymarket Gamma API returns at most *limit* events per page.
        When we receive fewer than the requested limit we have reached the
        last page and stop.
        """
        slug = LEAGUE_SLUGS[league.upper()]
        all_events: list[dict[str, Any]] = []
        offset = 0
        # A real sport-league slate is a few hundred events at most; a
        # hard page cap turns an API that ignores `offset` (returning the
        # same full page forever) into a clear error instead of an
        # unbounded, ever-growing-memory network hang in the daily
        # pipeline -- matches this codebase's convention of bounding every
        # other `while True` loop (see audit.py's lock-wait timeout).
        max_pages = 50
        for _ in range(max_pages):
            payload = self._get(
                f"/v2/leagues/{slug}/events",
                {"limit": limit, "offset": offset, "section": "general", "type": "sport"},
            )
            page = payload.get("events", [])
            if not page:
                break
            all_events.extend(page)
            if len(page) < limit:
                break
            offset += limit
        else:
            raise RuntimeError(
                f"polymarket_us events() for league={league!r} did not terminate within "
                f"{max_pages} pages ({max_pages * limit} events) -- the API may be ignoring "
                "the offset parameter"
            )
        return all_events

    def slate(
        self,
        league: str,
        game_date: date,
        timezone_name: str = "America/New_York",
    ) -> list[dict[str, Any]]:
        timezone = ZoneInfo(timezone_name)
        matching_events = []
        for event in self.events(league):
            start = event.get("startTime")
            if not start or parse_utc(start).astimezone(timezone).date() != game_date:
                continue
            matching_events.append(event)

        # The league endpoint is a summary/general feed: for NBA/WNBA/NFL/NCAAF it
        # now carries only the game-winner market (2026-10-21: 118 NBA events, 118
        # winner markets, while each event's detail page lists ~14 spreads and ~15
        # totals), so spread/total lanes saw "no_matched_markets". Hydrate only the
        # already date-filtered games of leagues with spread/total models, so MLB
        # innings and these lines are captured without turning every sport into a
        # large detail crawl. A failed detail fetch retains the general event; no
        # missing contract is ever invented.
        if league.upper() in HYDRATED_LEAGUES and matching_events:

            def _hydrate(event: dict[str, Any]) -> dict[str, Any]:
                slug = str(event.get("slug") or "")
                if not slug:
                    return event
                try:
                    return self.event(slug)
                except (httpx.HTTPError, KeyError, TypeError):
                    logger.warning(
                        "Polymarket %s event-detail hydration failed for %s", league, slug, exc_info=True
                    )
                    return event

            with ThreadPoolExecutor(max_workers=min(4, len(matching_events))) as pool:
                matching_events = list(pool.map(_hydrate, matching_events))

        slate = [self._normalize_event(event, league.upper(), timezone_name) for event in matching_events]
        return slate

    def sport_slate(
        self,
        sport: str,
        game_date: date,
        timezone_name: str = "America/New_York",
    ) -> SportSlateResult:
        """Slates for every gateway league in a sport, keyed by league.

        A league whose fetch fails still gets an empty list in ``.events``
        (so callers that only care about event lists keep working
        unmodified), but the failure is also recorded in ``.errors`` so it
        stays distinguishable from a genuinely empty day.
        """
        sport_key = "ncaaf" if sport.lower() == "cfb" else sport.lower()
        leagues = POLYMARKET_SPORT_LEAGUES.get(sport_key)
        if leagues is None:
            raise ValueError(f"unknown sport: {sport}; expected one of {sorted(POLYMARKET_SPORT_LEAGUES)}")
        events: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}

        def _fetch_league(league: str) -> tuple[list[dict[str, Any]], str | None]:
            try:
                return self.slate(league, game_date, timezone_name), None
            except httpx.HTTPError as exc:
                return [], str(exc)[:200]

        # Each league is one independent event-list HTTP call (soccer alone
        # is ~30 leagues) -- this used to be fully sequential and, across
        # every sport daily calls sport_slate for, was the single largest
        # contributor to daily's wall-clock time (measured: ~6 minutes on
        # its own, far more than the BBO-capture step downstream of it).
        with ThreadPoolExecutor(max_workers=min(8, len(leagues))) as pool:
            for league, (league_events, error) in zip(leagues, pool.map(_fetch_league, leagues), strict=True):
                events[league] = league_events
                if error is not None:
                    errors[league] = error
        return SportSlateResult(events=events, errors=errors)

    def market(self, slug: str) -> dict[str, Any]:
        return self._get(f"/v1/market/slug/{slug}")["market"]

    def event(self, slug: str) -> dict[str, Any]:
        """Fetch the complete market inventory used by the game-detail page."""
        return self._get(f"/v1/events/slug/{slug}")["event"]

    def book(self, slug: str) -> dict[str, Any]:
        return self._get(f"/v1/markets/{slug}/book")["marketData"]

    def _book_with_bbo_fallback(self, slug: str) -> dict[str, Any]:
        """Recover an exact, two-sided top-of-book quote if full depth is throttled."""
        if not self.prefer_bbo:
            try:
                return self.book(slug)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429:
                    raise
                # The documented IP-wide 429 contract requires a pause before
                # trying the lighter endpoint; an immediate request also 429s.
                time.sleep(max(1.0, _retry_delay_seconds(exc.response, 0)))
        else:
            # One request per second from this client avoids a book+BBO burst
            # across the esports slate. The gateway limit is shared by IP.
            remaining = 1.0 - (time.monotonic() - self._last_bbo_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_bbo_request = time.monotonic()
        bbo = self._get(f"/v1/markets/{slug}/bbo")["marketData"]
        bid = _amount(bbo.get("bestBid"))
        ask = _amount(bbo.get("bestAsk"))
        if (
            bbo.get("marketSlug") != slug
            or bbo.get("state") != "MARKET_STATE_OPEN"
            or bid is None
            or ask is None
            or not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid < ask < 1)
        ):
            raise ValueError(f"invalid_executable_bbo_for_{slug}")
        return {**bbo, "_response_source": "bbo"}

    def snapshot(self, slug: str, observed_at: datetime | None = None) -> dict[str, Any]:
        market = self.market(slug)
        book = self._book_with_bbo_fallback(slug)
        return self._build_snapshot(market, book, slug, observed_at)

    def snapshot_from_slate(
        self, event: dict[str, Any], market: dict[str, Any], observed_at: datetime | None = None
    ) -> dict[str, Any]:
        """Snapshot a slate market with one request (the live book).

        `snapshot` also calls `market(slug)`, but that response only supplies the
        market id, sides, line, start time and team, all of which the normalized
        slate market already holds. The book is still fetched live.
        """
        slug = market["market_slug"]
        sides = [
            {
                "description": side["description"],
                "long": bool(side.get("is_long")),
                "team": {"name": side["team"]} if side.get("team") else None,
            }
            for side in market["sides"]
        ]
        market_like = {
            "id": market["market_id"],
            "gameStartTime": event.get("event_start_utc"),
            "line": market.get("line"),
            "marketSides": sides,
        }
        return self._build_snapshot(market_like, self._book_with_bbo_fallback(slug), slug, observed_at)

    @staticmethod
    def _build_snapshot(
        market: dict[str, Any], book: dict[str, Any], slug: str, observed_at: datetime | None
    ) -> dict[str, Any]:
        long_side = next(side for side in market["marketSides"] if side.get("long"))
        short_side = next(side for side in market["marketSides"] if not side.get("long"))
        best_bid, bid_size = _best_level(book, "bids", best=max)
        best_ask, ask_size = _best_level(book, "offers", best=min)
        # Older gateway payloads exposed bestBid/bestAsk directly; keep as fallback.
        if best_ask is None:
            best_ask = _amount(book.get("bestAsk"))
        if best_bid is None:
            best_bid = _amount(book.get("bestBid"))
        midpoint = (
            round((best_bid + best_ask) / 2, 6) if best_bid is not None and best_ask is not None else None
        )
        short_bid = round(1 - best_ask, 6) if best_ask is not None else None
        short_ask = round(1 - best_bid, 6) if best_bid is not None else None
        short_midpoint = None if midpoint is None else round(1 - midpoint, 6)
        # Same extraction _normalize_event uses for the stored slate: the
        # market's own `line` is always expressed relative to whichever team
        # `team` names (e.g. a spread market's `line=-1.5` with `team="Red
        # Sox"` means the long side is "Red Sox -1.5"; the short side is
        # always the negation, "Red Sox +1.5" == the opponent's own +1.5).
        # None for market types with no team anchor (total, btts).
        team = next(
            ((side.get("team") or {}).get("name") for side in market["marketSides"] if side.get("team")),
            None,
        )
        return {
            "provider": "polymarket_us",
            # This object is built directly from authenticated live gateway
            # market/book responses in this call; it is not reconstructed
            # from historical prices or postgame data.
            "reconstructed": False,
            "reconstruction_status_source": (
                "live_gateway_market_and_bbo_response"
                if book.get("_response_source") == "bbo"
                else "live_gateway_market_and_book_response"
            ),
            "market_id": str(market["id"]),
            "market_slug": slug,
            "event_start_utc": market.get("gameStartTime"),
            "observed_at_utc": iso_utc(observed_at or utc_now()),
            "market_state": book.get("state"),
            "transact_time_utc": book.get("transactTime"),
            "line": market.get("line"),
            "team": team,
            "long": {
                "description": long_side["description"],
                "bid": best_bid,
                "ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "midpoint": midpoint,
                "price": best_ask,
            },
            "short": {
                "description": short_side["description"],
                "bid": short_bid,
                "ask": short_ask,
                "bid_size": ask_size,
                "ask_size": bid_size,
                "midpoint": short_midpoint,
                "price": short_ask,
            },
        }

    @staticmethod
    def _normalize_event(event: dict[str, Any], league: str, timezone_name: str) -> dict[str, Any]:
        start = parse_utc(event["startTime"])
        markets = []
        for market in event.get("markets", []):
            raw_type_v2 = market.get("sportsMarketTypeV2")
            raw_type_legacy = market.get("sportsMarketType")
            market_type = MARKET_TYPES.get(raw_type_v2) or MARKET_TYPES.get(raw_type_legacy)
            if market_type is None:
                _log_unknown_market_type(raw_type_v2 or raw_type_legacy, league)
                continue
            if (
                league in FULL_GAME_LINE_LEAGUES
                and market_type in {"spread", "total"}
                and raw_type_legacy is not None
                and raw_type_legacy not in FULL_GAME_LINE_TYPES
            ):
                continue
            sides = []
            for side in market.get("marketSides", []):
                price = _amount(side.get("quote"))
                if price is None:
                    try:
                        price = float(side["price"])
                    except (KeyError, TypeError, ValueError):
                        continue
                # The gateway can briefly expose boundary quotes (0 or 1) on
                # inactive or transitioning sides. They are not executable
                # probabilities and cannot be converted into finite odds.
                # Drop the entire binary market below unless both sides remain
                # valid, rather than aborting the daily slate.
                if not 0 < price < 1:
                    continue
                team = side.get("team") or {}
                selection = side["description"].lower()
                line = market.get("line")
                if market_type in {"moneyline", "spread"}:
                    selection = team.get("ordering") or team.get("name") or selection
                if market_type == "spread":
                    with suppress(ValueError):
                        line = float(side["description"])
                sides.append(
                    {
                        "side_id": str(side["id"]),
                        "description": side["description"],
                        "selection": selection.lower(),
                        "team": team.get("name"),
                        "line": line,
                        "price_probability": price,
                        "decimal_odds": round(1 / price, 6),
                        "american_odds": probability_to_american(price),
                        "is_long": bool(side.get("long")),
                    }
                )
            if len(sides) != 2:
                continue
            markets.append(
                {
                    "market_id": str(market["id"]),
                    "market_slug": market["slug"],
                    "market_type": market_type,
                    "question": market.get("question"),
                    "line": market.get("line"),
                    "sides": sides,
                }
            )
        return {
            "provider": "polymarket_us",
            "league": league,
            "event_id": str(event["id"]),
            "event_slug": event["slug"],
            "title": event.get("title"),
            "event_start_utc": iso_utc(start),
            "event_start_local": start.astimezone(ZoneInfo(timezone_name)).isoformat(),
            "markets": markets,
        }


def _slug_prefilter(slug: str) -> bool:
    """True when ``slug in raw_line`` is a safe necessary condition for a match.

    A line whose market_slug equals ``slug`` contains ``slug`` verbatim unless
    JSON (ASCII-escaping, as append() writes) altered any character in it, so skip the raw-substring shortcut then.
    """
    return bool(slug) and json.dumps(slug)[1:-1] == slug


class PolymarketSnapshotStore:
    """Append-only BBO snapshot store.

    ``for_sport_date`` gives the canonical per-sport location
    ``data/odds/{sport}/{date}/polymarket_snapshots.jsonl``; the flat
    constructor remains for the legacy single-file store.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_sport_date(cls, data_root: str | Path, sport: str, game_date: str) -> PolymarketSnapshotStore:
        return cls(Path(data_root) / "odds" / sport.lower() / game_date / "polymarket_snapshots.jsonl")

    def append(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")

    def latest(self, slug: str) -> dict[str, Any] | None:
        """Return the newest stored snapshot for one exact contract."""
        if not self.path.exists():
            return None
        latest: dict[str, Any] | None = None
        prefilter = _slug_prefilter(slug)
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if prefilter and slug not in line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("market_slug") != slug:
                    continue
                if latest is None or str(item.get("observed_at_utc") or "") >= str(
                    latest.get("observed_at_utc") or ""
                ):
                    latest = item
        return latest

    def closing_snapshot(self, slug: str, event_start_utc: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        event_start = parse_utc(event_start_utc)
        candidates = []
        prefilter = _slug_prefilter(slug)
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if prefilter and slug not in line:
                    continue
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if item.get("market_slug") != slug:
                    continue
                raw_observed = item.get("observed_at_utc")
                if not raw_observed:
                    continue
                try:
                    observed = parse_utc(raw_observed)
                except (ValueError, TypeError):
                    continue
                if observed <= event_start:
                    candidates.append((observed, item))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _is_preseason_event(event: dict[str, Any], league: str) -> bool:
    """Return True for preseason/exhibition events that should be skipped.

    NFL preseason runs in August; regular season starts September.
    Polymarket does not tag preseason explicitly, so we use the date.
    """
    if league.upper() != "NFL":
        return False
    start_str = str(event.get("startTime") or event.get("event_start_utc") or "")
    if not start_str:
        return False
    try:
        start_month = int(start_str[5:7])
    except (ValueError, IndexError):
        return False
    # NFL regular season begins in September. Preseason runs July–August.
    return start_month in (7, 8)


# Shared pacing for prospective BBO capture. The gateway's /book limit is IP-wide
# and well under one request a second sustained: an unpaced 4-worker burst drew
# a 429 within about a minute on every daily run of 2026-11-14, and the old
# abandon-on-first-429 rule then skipped 1,750-2,050 queued contracts per run
# (every NCAAF contract among them). Requests are spaced instead, and a 429
# pauses every worker and doubles the spacing rather than ending the capture.
CAPTURE_MIN_REQUEST_INTERVAL_SECONDS = 0.5
CAPTURE_MAX_REQUEST_INTERVAL_SECONDS = 4.0
CAPTURE_RATE_LIMIT_BASE_PAUSE_SECONDS = 5.0
CAPTURE_RATE_LIMIT_MAX_PAUSE_SECONDS = 60.0
# Consecutive 429 episodes (no unthrottled response in between) before the
# capture gives up on the rest of its queue.
CAPTURE_RATE_LIMIT_BUDGET = 5
# Attempts per contract when its own request is throttled.
CAPTURE_CONTRACT_429_ATTEMPTS = 3
# Unthrottled responses needed before a widened spacing eases back by 20%.
_CAPTURE_INTERVAL_RECOVERY_SUCCESSES = 20

_SKIP_RATE_LIMITED = "source_rate_limited_after_http_429"
_SKIP_TIME_BUDGET = "capture_time_budget_exhausted"

_CaptureEntry = tuple[str, str, dict[str, Any], dict[str, Any]]


class _CapturePacer:
    """Spaces capture requests across workers and backs off together on a 429.

    ``acquire`` hands each request a start slot ``interval`` after the previous
    one. A 429 that survives the client's own retry opens a pause episode: every
    worker waits it out and the spacing doubles. Requests already dispatched when
    the episode opened carry the old generation, so their 429s join that episode
    instead of each counting toward the abandon budget.
    """

    def __init__(self, min_interval: float, rate_limit_budget: int) -> None:
        self._lock = Lock()
        self.min_interval = max(0.0, float(min_interval))
        self.interval = self.min_interval
        self.max_interval = max(CAPTURE_MAX_REQUEST_INTERVAL_SECONDS, self.min_interval)
        self.rate_limit_budget = max(1, int(rate_limit_budget))
        self.consecutive_rate_limits = 0
        self.pauses = 0
        self._generation = 0
        self._next_slot = 0.0
        self._resume_at = 0.0
        self._successes = 0

    def acquire(self) -> int:
        """Block until this request's slot; return the pause generation it was issued under."""
        while True:
            with self._lock:
                slot = max(time.monotonic(), self._next_slot, self._resume_at)
                self._next_slot = slot + self.interval
                generation = self._generation
            delay = slot - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            with self._lock:
                if generation == self._generation:
                    return generation
            # A pause episode opened while this worker waited: take a slot after it.

    def record_response(self) -> None:
        """The gateway answered without throttling."""
        with self._lock:
            self.consecutive_rate_limits = 0
            self._successes += 1
            if self.interval > self.min_interval and self._successes >= _CAPTURE_INTERVAL_RECOVERY_SUCCESSES:
                self.interval = max(self.min_interval, self.interval * 0.8)
                self._successes = 0

    def record_rate_limit(self, generation: int, retry_after: float | None) -> bool:
        """Open (or join) a pause episode; True once the consecutive-429 budget is spent."""
        with self._lock:
            if generation != self._generation:
                return self.consecutive_rate_limits >= self.rate_limit_budget
            self._generation += 1
            self.consecutive_rate_limits += 1
            self.pauses += 1
            self._successes = 0
            self.interval = min(
                self.max_interval, max(2 * self.interval, CAPTURE_MIN_REQUEST_INTERVAL_SECONDS)
            )
            pause = (
                retry_after
                if retry_after is not None
                else CAPTURE_RATE_LIMIT_BASE_PAUSE_SECONDS * 2 ** (self.consecutive_rate_limits - 1)
            )
            self._resume_at = max(
                self._resume_at, time.monotonic() + min(pause, CAPTURE_RATE_LIMIT_MAX_PAUSE_SECONDS)
            )
            return self.consecutive_rate_limits >= self.rate_limit_budget


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After", "").strip()
    return float(retry_after) if retry_after.isdigit() else None


def _has_started(event: dict[str, Any], now: datetime) -> bool:
    """True when the event has a parseable start at or before ``now``."""
    try:
        return parse_utc(str(event["event_start_utc"])) <= now
    except (KeyError, TypeError, ValueError):
        return False


def _round_robin_by_kickoff(entries: list[_CaptureEntry], now: datetime) -> list[_CaptureEntry]:
    """Interleave leagues one contract at a time, each league in kickoff order.

    One kickoff-ordered queue across leagues let early soccer, tennis and esports
    games use up the budget before a later-kicking sport got anything (NCAAF on
    2026-11-14). Upcoming games go before started ones within a league; the sort
    is stable, so an event's markets keep their slate order.
    """
    by_league: dict[str, list[_CaptureEntry]] = {}
    for entry in entries:
        by_league.setdefault(entry[0].upper(), []).append(entry)
    queues = [
        sorted(
            by_league[league],
            key=lambda entry: (
                _has_started(entry[2], now),
                str(entry[2].get("event_start_utc") or ""),
                str(entry[2].get("event_id") or ""),
            ),
        )
        for league in sorted(by_league)
    ]
    return [entry for group in zip_longest(*queues) for entry in group if entry is not None]


def _capture_tiers(
    entries: list[_CaptureEntry],
    *,
    priority_leagues: frozenset[str] | None,
    keep_slugs: frozenset[str],
    now: datetime,
) -> tuple[list[_CaptureEntry], list[_CaptureEntry]]:
    """Split the capture queue into (primary, secondary), each round-robin by league.

    Primary: upcoming open-pick contracts (their CLV history) first, then
    upcoming contracts of ``priority_leagues`` (every league when None).
    Secondary: out-of-scope leagues and games already under way, whose quotes
    can no longer serve a pregame decision or a closing line.
    """
    keep_first: list[_CaptureEntry] = []
    primary: list[_CaptureEntry] = []
    secondary: list[_CaptureEntry] = []
    for entry in entries:
        league, _sport, event, market = entry
        if _has_started(event, now):
            secondary.append(entry)
        elif market.get("market_slug") in keep_slugs:
            keep_first.append(entry)
        elif priority_leagues is None or league.upper() in priority_leagues:
            primary.append(entry)
        else:
            secondary.append(entry)
    return (
        _round_robin_by_kickoff(keep_first, now) + _round_robin_by_kickoff(primary, now),
        _round_robin_by_kickoff(secondary, now),
    )


def _starts_after(event: dict[str, Any], cutoff: datetime) -> bool:
    """True when the event has a parseable start later than ``cutoff``."""
    try:
        return parse_utc(str(event["event_start_utc"])) > cutoff
    except (KeyError, TypeError, ValueError):
        return False


def _long_side_distance(market: dict[str, Any]) -> float:
    """Distance of the slate's cached long-side price from a coin flip (inf when unknown)."""
    for side in market.get("sides") or []:
        if side.get("is_long") and side.get("price_probability") is not None:
            return abs(float(side["price_probability"]) - 0.5)
    return float("inf")


def _prune_line_ladders(
    entries: list[tuple[str, str, dict[str, Any], dict[str, Any]]],
    keep: int,
    keep_slugs: frozenset[str] = frozenset(),
) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """Keep the ``keep`` most balanced spread/total lines per (event, market type).

    The downstream main-line pickers choose the quote whose live ask is closest
    to 0.50, but they need a book for every alternate to do it, and the gateway
    rate-limits /book to well under one request a second. The slate's cached
    quote is stale (it cannot be used as a price) yet orders a ladder well
    enough to shortlist the lines worth a live book; the true main line is
    still decided from live asks among the survivors. Contracts without a
    cached price are always kept, as are ``keep_slugs`` (open picks): a pick's
    closing-line value is read from its own slug's snapshot history, so a line
    that drops out of a later run's shortlist would leave CLV measured against
    a stale price.
    """
    groups: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for index, (_league, _sport, event, market) in enumerate(entries):
        if market.get("market_type") in {"spread", "total"} and market.get("market_slug") not in keep_slugs:
            key = (str(event.get("event_id", "")), str(market["market_type"]))
            groups.setdefault(key, []).append((_long_side_distance(market), index))
    dropped: set[int] = set()
    for members in groups.values():
        ranked = sorted(members)
        dropped.update(index for distance, index in ranked[keep:] if distance != float("inf"))
    return [entry for index, entry in enumerate(entries) if index not in dropped]


def capture_slate_snapshots(
    client: PolymarketUSClient,
    events_by_league: dict[str, list[dict[str, Any]]],
    data_root: str | Path,
    game_date: str,
    *,
    horizon_hours: float | None = None,
    now: datetime | None = None,
    ladder_keep: int | Mapping[str, int] | None = None,
    ladder_leagues: frozenset[str] = frozenset({"NCAAF"}),
    ladder_keep_slugs: frozenset[str] = frozenset(),
    priority_leagues: frozenset[str] | None = None,
    max_workers: int = 4,
    min_request_interval: float | None = None,
    rate_limit_budget: int | None = None,
    max_capture_seconds: float | None = None,
    secondary_max_seconds: float | None = None,
) -> dict[str, Any]:
    """Persist a prospective executable BBO for every discovered contract.

    With ``horizon_hours`` set, contracts whose event starts later than that
    many hours from now are skipped (each run only prices games about to start;
    games already started are still captured, after everything else).
    ``None`` keeps every contract.

    With ``ladder_keep`` set, leagues in ``ladder_leagues`` only fetch books for
    that many spread/total alternates per event (see ``_prune_line_ladders``);
    a mapping gives each league its own count. ``ladder_keep_slugs`` are
    fetched regardless, and first.

    Requests are paced ``min_request_interval`` seconds apart across all
    workers. A 429 pauses every worker and widens the spacing; the rest of the
    queue is abandoned only after ``rate_limit_budget`` consecutive 429
    episodes. ``priority_leagues`` (default: every league) are fetched before
    the secondary tier (other leagues, started games), which stops after
    ``secondary_max_seconds``; ``max_capture_seconds`` caps the whole capture.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    started_at = time.monotonic()
    pacer = _CapturePacer(
        CAPTURE_MIN_REQUEST_INTERVAL_SECONDS if min_request_interval is None else min_request_interval,
        CAPTURE_RATE_LIMIT_BUDGET if rate_limit_budget is None else rate_limit_budget,
    )
    capture_now = now or utc_now()
    priority = None if priority_leagues is None else frozenset(league.upper() for league in priority_leagues)
    league_to_sport = {
        league: sport for sport, leagues in POLYMARKET_SPORT_LEAGUES.items() for league in leagues
    }
    # Broad groups such as basketball also contain NBA/WNBA/NCAAB. They
    # must not overwrite a dedicated sport's canonical archive directory.
    league_to_sport.update(
        {
            sport.upper(): sport
            for sport, leagues in POLYMARKET_SPORT_LEAGUES.items()
            if sport.upper() in leagues
        }
    )
    qualification_sports = BBO_CAPTURE_SPORTS
    captured = missing_bbo = failures = skipped_nonqualification_contracts = 0
    skipped_summer_league = timestamp_invalid = skipped_beyond_horizon = skipped_ladder = 0
    horizon_cutoff = None if horizon_hours is None else capture_now + timedelta(hours=horizon_hours)
    failure_details: list[dict[str, str]] = []
    source_rate_limited = Event()

    # Skip summer league / preseason events — they offer only moneyline
    # markets and pollute the spread/total readiness counts.
    _SUMMER_LEAGUE_PATTERNS = (
        "nbasl",  # NBA Summer League
    )

    # Build one flat contract list across every qualifying league instead of
    # fetching league-by-league with a fresh 8-worker pool per league (which
    # serialized the ~18 leagues against each other). Every contract is an
    # independent HTTP fetch, so pooling them all together lets one shared
    # pool of workers stay saturated across the whole slate at once --
    # each snapshot is two HTTP round-trips; ~200 serial contracts took
    # minutes before this and per-league-only pooling still wastes it
    # whenever any one league has fewer contracts than there are workers.
    all_contracts: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for league, events in events_by_league.items():
        sport = league_to_sport.get(league.upper())
        if sport is None:
            failures += 1
            failure_details.append({"league": league, "reason": "league has no canonical sport mapping"})
            continue
        if sport not in qualification_sports:
            skipped_nonqualification_contracts += sum(len(event.get("markets", [])) for event in events)
            continue
        league_contracts = [
            (league, sport, event, market)
            for event in events
            for market in event.get("markets", [])
            if market.get("market_slug")
        ]
        kept = [
            entry
            for entry in league_contracts
            if not (
                any(
                    pattern in str(entry[3].get("market_slug", "")).casefold()
                    for pattern in _SUMMER_LEAGUE_PATTERNS
                )
                or _is_preseason_event(entry[2], league)
            )
        ]
        skipped_summer_league += len(league_contracts) - len(kept)
        if horizon_cutoff is not None:
            within = [entry for entry in kept if not _starts_after(entry[2], horizon_cutoff)]
            skipped_beyond_horizon += len(kept) - len(within)
            kept = within
        # One integer used to apply to every ladder league, and the CLI picked
        # it first-match (NBA present => 3), so NCAAB and NCAAF priced 3 rungs
        # each instead of 1 and 2 and the queue grew past the rate limit.
        league_keep = ladder_keep.get(league.upper()) if isinstance(ladder_keep, Mapping) else ladder_keep
        if league_keep and league.upper() in ladder_leagues:
            pruned = _prune_line_ladders(kept, league_keep, ladder_keep_slugs)
            skipped_ladder += len(kept) - len(pruned)
            kept = pruned
        all_contracts.extend(kept)

    primary_contracts, secondary_contracts = _capture_tiers(
        all_contracts, priority_leagues=priority, keep_slugs=ladder_keep_slugs, now=capture_now
    )

    def _fetch(entry, deadline):
        league, sport, event, market = entry
        slug = str(market.get("market_slug") or "")
        rate_limited_attempts = 0
        while True:
            if source_rate_limited.is_set():
                return league, sport, slug, None, _SKIP_RATE_LIMITED
            if deadline is not None and time.monotonic() >= deadline:
                return league, sport, slug, None, _SKIP_TIME_BUDGET
            generation = pacer.acquire()
            # The slot may have come after a long pause; re-check before spending it.
            if source_rate_limited.is_set():
                return league, sport, slug, None, _SKIP_RATE_LIMITED
            if deadline is not None and time.monotonic() >= deadline:
                return league, sport, slug, None, _SKIP_TIME_BUDGET
            try:
                return _fetch_once(league, sport, event, market, slug)
            except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    rate_limited_attempts += 1
                    if pacer.record_rate_limit(generation, _retry_after_seconds(exc.response)):
                        source_rate_limited.set()
                        return league, sport, slug, None, str(exc)[:200]
                    if rate_limited_attempts < CAPTURE_CONTRACT_429_ATTEMPTS:
                        continue
                else:
                    pacer.record_response()
                return league, sport, slug, None, str(exc)[:200]

    def _fetch_once(league, sport, event, market, slug):
        if hasattr(client, "snapshot_from_slate") and market.get("sides") and market.get("market_id"):
            snapshot = client.snapshot_from_slate(event, market)
        else:
            snapshot = client.snapshot(slug)
        pacer.record_response()
        observed = parse_utc(snapshot["observed_at_utc"])
        event_start = parse_utc(snapshot["event_start_utc"])
        snapshot.update(
            {
                "league": league.upper(),
                "event_id": str(event.get("event_id", "")),
                "event_slug": str(event.get("event_slug", "")),
                "event_title": str(event.get("title", "")),
                "market_type": market.get("market_type"),
                "line": market.get("line"),
                # Which team this specific market concerns, e.g. for
                # team_win markets (soccer's "Will X win?" Yes/No,
                # one market per team, unlike moneyline's single
                # combined market) -- None where not team-specific.
                "team": next(
                    (side.get("team") for side in market.get("sides", []) if side.get("team")),
                    None,
                ),
                "timestamp_valid": observed <= event_start,
                "reconstructed": snapshot.get("reconstructed"),
                "reconstruction_status_source": snapshot.get("reconstruction_status_source"),
                "usage": "prospective_executable_bbo",
            }
        )
        return league, sport, slug, snapshot, None

    # The secondary tier (out-of-scope leagues, started games) runs only after
    # the primary tier and on its own short clock, so research capture never
    # holds the daily run hostage; max_capture_seconds bounds both.
    overall_deadline = None if max_capture_seconds is None else started_at + max_capture_seconds
    results: list[tuple[str, str, str, dict[str, Any] | None, str | None]] = []
    for tier_contracts, tier_seconds in (
        (primary_contracts, None),
        (secondary_contracts, secondary_max_seconds),
    ):
        if not tier_contracts:
            continue
        deadline = overall_deadline
        if tier_seconds is not None:
            tier_deadline = time.monotonic() + tier_seconds
            deadline = tier_deadline if deadline is None else min(deadline, tier_deadline)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tier_contracts))) as pool:
            results.extend(
                pool.map(lambda entry, _deadline=deadline: _fetch(entry, _deadline), tier_contracts)
            )

    stores: dict[str, PolymarketSnapshotStore] = {}
    nba_captured: dict[str, set[str]] = {}
    captured_by_league: dict[str, int] = {}
    failures_by_league: dict[str, int] = {}
    for league, sport, slug, snapshot, fetch_error in results:
        if snapshot is None:
            failures += 1
            failures_by_league[league.upper()] = failures_by_league.get(league.upper(), 0) + 1
            failure_details.append(
                {
                    "league": league,
                    "market_slug": slug,
                    "reason": fetch_error or "",
                }
            )
            continue
        store = stores.get(sport)
        if store is None:
            store = PolymarketSnapshotStore.for_sport_date(data_root, sport, game_date)
            stores[sport] = store
        store.append(snapshot)
        if (
            league.upper() == "NBA"
            and snapshot.get("timestamp_valid")
            and snapshot["long"].get("ask") is not None
            and snapshot["short"].get("ask") is not None
        ):
            nba_captured.setdefault(str(snapshot.get("event_id")), set()).add(
                str(snapshot.get("market_type"))
            )
        captured += 1
        captured_by_league[league.upper()] = captured_by_league.get(league.upper(), 0) + 1
        if snapshot["long"].get("ask") is None or snapshot["short"].get("ask") is None:
            missing_bbo += 1
        if not snapshot.get("timestamp_valid", True):
            timestamp_invalid += 1
    nba_events = {
        str(event.get("event_id"))
        for event in events_by_league.get("NBA", [])
        if event.get("event_id") is not None
    }
    nba_missing_by_market = {
        market: sorted(event_id for event_id in nba_events if market not in nba_captured.get(event_id, set()))
        for market in ("moneyline", "spread", "total")
    }
    return {
        "status": ("ok" if failures == 0 and missing_bbo == 0 and timestamp_invalid == 0 else "partial"),
        "captured": captured,
        "missing_executable_ask": missing_bbo,
        "failures": failures,
        "source_rate_limited": source_rate_limited.is_set(),
        "skipped_after_rate_limit": sum(detail["reason"] == _SKIP_RATE_LIMITED for detail in failure_details),
        "skipped_after_time_budget": sum(detail["reason"] == _SKIP_TIME_BUDGET for detail in failure_details),
        "rate_limit_pauses": pacer.pauses,
        "min_request_interval": pacer.min_interval,
        "final_request_interval": round(pacer.interval, 3),
        "elapsed_seconds": round(time.monotonic() - started_at, 1),
        "priority_leagues": None if priority is None else sorted(priority),
        "primary_contracts": len(primary_contracts),
        "secondary_contracts": len(secondary_contracts),
        "captured_by_league": dict(sorted(captured_by_league.items())),
        "failures_by_league": dict(sorted(failures_by_league.items())),
        "skipped_nonqualification_contracts": skipped_nonqualification_contracts,
        "skipped_summer_league": skipped_summer_league,
        "skipped_beyond_horizon": skipped_beyond_horizon,
        "horizon_hours": horizon_hours,
        "skipped_ladder_alternates": skipped_ladder,
        "ladder_keep": dict(ladder_keep) if isinstance(ladder_keep, Mapping) else ladder_keep,
        "failure_details": failure_details,
        "nba_missing_quote_event_ids": nba_missing_by_market,
        "timestamp_valid": timestamp_invalid == 0,
        "timestamp_invalid_count": timestamp_invalid,
        "sports_scope": sorted(qualification_sports),
        "storage": f"data/odds/<sport>/{game_date}/polymarket_snapshots.jsonl",
    }


def refresh_contract_snapshots(
    client: PolymarketUSClient,
    contracts: list[dict[str, str]],
    data_root: str | Path,
    game_date: str,
) -> dict[str, Any]:
    """Refresh only explicitly selected, previously mapped ledger contracts.

    Contract metadata is copied from the last saved discovery snapshot. This
    keeps the refresh path bounded and prevents it from querying a whole sport
    merely to rediscover contracts already attached to ledger picks.
    """
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for contract in contracts:
        sport = str(contract.get("sport") or "").strip().lower()
        slug = str(contract.get("market_slug") or "").strip()
        contract_day = str(contract.get("game_date") or game_date).strip()
        if sport and slug and contract_day:
            unique[(sport, contract_day, slug)] = {
                "sport": sport,
                "game_date": contract_day,
                "market_slug": slug,
            }

    refreshed = missing_bbo = failures = 0
    failure_details: list[dict[str, str]] = []
    refreshed_contracts: list[dict[str, str]] = []
    for (sport, contract_day, slug), contract in sorted(unique.items()):
        store = PolymarketSnapshotStore.for_sport_date(data_root, sport, contract_day)
        previous = store.latest(slug)
        if previous is None:
            failures += 1
            failure_details.append(
                {
                    **contract,
                    "reason": "no prior exact contract mapping; broad discovery was not attempted",
                }
            )
            continue
        try:
            snapshot = client.snapshot(slug)
            observed = parse_utc(snapshot["observed_at_utc"])
            event_start = parse_utc(snapshot["event_start_utc"])
            snapshot.update({key: previous.get(key) for key in ("league", "event_id", "market_type", "line")})
            snapshot.update(
                {
                    "timestamp_valid": observed <= event_start,
                    "reconstructed": snapshot.get("reconstructed"),
                    "reconstruction_status_source": snapshot.get("reconstruction_status_source"),
                    "usage": "prospective_executable_bbo",
                }
            )
            store.append(snapshot)
        except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
            failures += 1
            failure_details.append({**contract, "reason": str(error)[:200]})
            continue
        refreshed += 1
        refreshed_contracts.append(contract)
        if snapshot["long"].get("ask") is None or snapshot["short"].get("ask") is None:
            missing_bbo += 1

    return {
        "status": "ok" if failures == 0 else "partial",
        "requested_contracts": len(contracts),
        "unique_contracts": len(unique),
        "refreshed": refreshed,
        "missing_executable_ask": missing_bbo,
        "failures": failures,
        "failure_details": failure_details,
        "refreshed_contracts": refreshed_contracts,
        "scope": "all_open_visible_ledger_contracts_only",
        "broad_discovery_attempted": False,
        "storage": "data/odds/<sport>/<game_date>/polymarket_snapshots.jsonl",
    }


def _best_level(book: dict[str, Any], side: str, best: Any) -> tuple[float | None, float | None]:
    """Best price and its resting size from a depth array of {px, qty} levels."""
    levels = []
    for level in book.get(side) or []:
        price = _amount(level.get("px"))
        if price is None:
            continue
        try:
            size = float(level.get("qty"))
        except (TypeError, ValueError):
            size = None
        levels.append((price, size))
    if not levels:
        return None, None
    return best(levels, key=lambda item: item[0])


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
