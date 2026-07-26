from __future__ import annotations

import json
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..domain import iso_utc, parse_utc, utc_now

# Verified against GET /v2/leagues on the live gateway (2026-07-16).
LEAGUE_SLUGS = {
    "MLB": "mlb",
    "NBA": "nba",
    "WNBA": "wnba",
    "NFL": "nfl",
    "NHL": "nhl",
    "EPL": "epl",
    "LA_LIGA": "lal",
    "BUNDESLIGA": "bun",
    "SERIE_A": "sea",
    "UCL": "ucl",
    "UEFA": "uefa",
    "MLS": "mls",
    "WORLD_CUP": "fwc",
    "WTA": "wta",
    "ITF_MEN": "itfm",
    "ITF_WOMEN": "itfw",
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

# Gateway coverage per sport key. Ligue 1, Eredivisie, Primeira Liga,
# Championship, and ATP have no Polymarket US league as of 2026-07-16.
POLYMARKET_SPORT_LEAGUES: dict[str, tuple[str, ...]] = {
    "mlb": ("MLB",),
    "nba": ("NBA",),
    "wnba": ("WNBA",),
    "nfl": ("NFL",),
    # WORLD_CUP dropped 2026-07: tournament is over, no games left to trade.
    "soccer": ("EPL", "LA_LIGA", "BUNDESLIGA", "SERIE_A", "UCL", "UEFA", "MLS"),
    "tennis": ("WTA", "ITF_MEN", "ITF_WOMEN"),
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
}

# Sports whose prospective BBO capture feeds the daily qualification/promotion
# pipeline (config/model.yaml, models/registry.py). Tennis has Polymarket
# gateway coverage in POLYMARKET_SPORT_LEAGUES above but is not yet wired into
# daily BBO capture, so it stays out of this set on purpose — do not derive
# this from "every league in POLYMARKET_SPORT_LEAGUES" or from
# registry.MODEL_SPECS wholesale.
# Esports and soccer are research-only, but prospective BBO capture must begin
# before profitability can ever be tested; capturing prices does not qualify a
# model or authorize execution.
BBO_CAPTURE_SPORTS = {"mlb", "nba", "wnba", "nfl", "soccer", "esports", "kbo", "npb"}

MARKET_TYPES = {
    "SPORTS_MARKET_TYPE_MONEYLINE": "moneyline",
    "SPORTS_MARKET_TYPE_SPREAD": "spread",
    "SPORTS_MARKET_TYPE_TOTAL": "total",
}


def probability_to_american(probability: float) -> int:
    if not 0 < probability < 1:
        raise ValueError("price probability must be between 0 and 1")
    if probability == 0.5:
        return 100
    if probability > 0.5:
        return -round(100 * probability / (1 - probability))
    return round(100 * (1 - probability) / probability)


class PolymarketUSClient:
    """Read-only client for the unauthenticated Polymarket US public gateway."""

    def __init__(
        self,
        base_url: str = "https://gateway.polymarket.us",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def events(self, league: str, limit: int = 50) -> list[dict[str, Any]]:
        slug = LEAGUE_SLUGS[league.upper()]
        payload = self._get(
            f"/v2/leagues/{slug}/events",
            {"limit": limit, "section": "general", "type": "sport"},
        )
        return payload.get("events", [])

    def slate(
        self,
        league: str,
        game_date: date,
        timezone_name: str = "America/New_York",
    ) -> list[dict[str, Any]]:
        timezone = ZoneInfo(timezone_name)
        slate = []
        for event in self.events(league):
            start = event.get("startTime")
            if not start or parse_utc(start).astimezone(timezone).date() != game_date:
                continue
            slate.append(self._normalize_event(event, league.upper(), timezone_name))
        return slate

    def sport_slate(
        self,
        sport: str,
        game_date: date,
        timezone_name: str = "America/New_York",
    ) -> dict[str, list[dict[str, Any]]]:
        """Slates for every gateway league in a sport, keyed by league."""
        leagues = POLYMARKET_SPORT_LEAGUES.get(sport.lower())
        if leagues is None:
            raise ValueError(
                f"unknown sport: {sport}; expected one of {sorted(POLYMARKET_SPORT_LEAGUES)}"
            )
        output: dict[str, list[dict[str, Any]]] = {}
        for league in leagues:
            try:
                output[league] = self.slate(league, game_date, timezone_name)
            except httpx.HTTPError:
                output[league] = []
        return output

    def market(self, slug: str) -> dict[str, Any]:
        return self._get(f"/v1/market/slug/{slug}")["market"]

    def book(self, slug: str) -> dict[str, Any]:
        return self._get(f"/v1/markets/{slug}/book")["marketData"]

    def snapshot(self, slug: str, observed_at: datetime | None = None) -> dict[str, Any]:
        market = self.market(slug)
        book = self.book(slug)
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
        return {
            "provider": "polymarket_us",
            "market_id": str(market["id"]),
            "market_slug": slug,
            "event_start_utc": market.get("gameStartTime"),
            "observed_at_utc": iso_utc(observed_at or utc_now()),
            "market_state": book.get("state"),
            "transact_time_utc": book.get("transactTime"),
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
            market_type = MARKET_TYPES.get(market.get("sportsMarketTypeV2") or market.get("sportsMarketType"))
            if market_type is None:
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
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
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
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if item.get("market_slug") != slug:
                    continue
                observed = parse_utc(item["observed_at_utc"])
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


def capture_slate_snapshots(
    client: PolymarketUSClient,
    events_by_league: dict[str, list[dict[str, Any]]],
    data_root: str | Path,
    game_date: str,
) -> dict[str, Any]:
    """Persist a prospective executable BBO for every discovered contract."""
    league_to_sport = {
        league: sport
        for sport, leagues in POLYMARKET_SPORT_LEAGUES.items()
        for league in leagues
    }
    qualification_sports = BBO_CAPTURE_SPORTS
    captured = missing_bbo = failures = skipped_nonqualification_contracts = 0
    skipped_summer_league = 0
    failure_details: list[dict[str, str]] = []
    for league, events in events_by_league.items():
        sport = league_to_sport.get(league.upper())
        if sport is None:
            failures += 1
            failure_details.append({"league": league, "reason": "league has no canonical sport mapping"})
            continue
        if sport not in qualification_sports:
            skipped_nonqualification_contracts += sum(
                len(event.get("markets", [])) for event in events
            )
            continue
        store = PolymarketSnapshotStore.for_sport_date(data_root, sport, game_date)
        # Collect every contract for the league, fetch BBOs concurrently
        # (each snapshot is two HTTP round-trips; ~200 serial contracts took
        # minutes), then append serially so the JSONL stays clean.
        #
        # Skip summer league / preseason events — they offer only moneyline
        # markets and pollute the spread/total readiness counts.
        _SUMMER_LEAGUE_PATTERNS = (
            "nbasl",   # NBA Summer League
        )
        all_contracts = [
            (str(market.get("market_slug") or ""), event, market)
            for event in events
            for market in event.get("markets", [])
            if market.get("market_slug")
        ]
        contracts = [
            entry for entry in all_contracts
            if not (
                any(
                    pattern in str(entry[0]).casefold()
                    for pattern in _SUMMER_LEAGUE_PATTERNS
                )
                or _is_preseason_event(entry[1], league)
            )
        ]
        skipped_summer_league += len(all_contracts) - len(contracts)

        def _fetch(entry):
            slug, event, market = entry
            try:
                snapshot = client.snapshot(slug)
                observed = parse_utc(snapshot["observed_at_utc"])
                event_start = parse_utc(snapshot["event_start_utc"])
                snapshot.update(
                    {
                        "league": league.upper(),
                        "event_id": str(event.get("event_id", "")),
                        "market_type": market.get("market_type"),
                        "line": market.get("line"),
                        "timestamp_valid": observed <= event_start,
                        "usage": "prospective_executable_bbo",
                    }
                )
                return slug, snapshot, None
            except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
                return slug, None, str(error)[:200]

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_fetch, contracts))
        for slug, snapshot, error in results:
            if snapshot is None:
                failures += 1
                failure_details.append({"league": league, "market_slug": slug, "reason": error or ""})
                continue
            store.append(snapshot)
            captured += 1
            if snapshot["long"].get("ask") is None or snapshot["short"].get("ask") is None:
                missing_bbo += 1
    return {
        "status": "ok" if failures == 0 else "partial",
        "captured": captured,
        "missing_executable_ask": missing_bbo,
        "failures": failures,
        "skipped_nonqualification_contracts": skipped_nonqualification_contracts,
        "skipped_summer_league": skipped_summer_league,
        "failure_details": failure_details,
        "timestamp_valid": True,
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
            snapshot.update(
                {
                    key: previous.get(key)
                    for key in ("league", "event_id", "market_type", "line")
                }
            )
            snapshot.update(
                {
                    "timestamp_valid": observed <= event_start,
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


def _best_level(
    book: dict[str, Any], side: str, best: Any
) -> tuple[float | None, float | None]:
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
