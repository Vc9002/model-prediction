from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from ..domain import League, iso_utc, parse_utc, utc_now
from ..entities import EntityRegistry, EntityResolutionError
from ..pricing import implied_probability
from .polymarket_us import PolymarketUSClient, probability_to_american
from .the_odds_api import TheOddsAPIClient


class MarketUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class MarketSideQuote:
    selection: str
    line: float | None
    american_odds: int
    decision_probability: float
    midpoint_probability: float | None = None
    market_slug: str | None = None
    polymarket_side: str | None = None


@dataclass(frozen=True)
class MLBGameOdds:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    provider: str
    observed_at_utc: str
    markets: dict[str, dict[str, MarketSideQuote]]
    raw_response: dict[str, Any]
    snapshot_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_CLOSING_PROVIDERS = {"polymarket_us", "draftkings_via_the_odds_api"}


class MarketOddsSnapshotStore:
    """Append-only raw market snapshots used for decisions and defensible closes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, snapshot: MLBGameOdds | dict[str, Any]) -> None:
        payload = snapshot.as_dict() if isinstance(snapshot, MLBGameOdds) else snapshot
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()

    def closing_quote(
        self,
        event_id: str,
        event_start_utc: str,
        market_type: str,
        selection: str,
    ) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        event_start = parse_utc(event_start_utc)
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("event_id") != event_id or item.get("provider") not in _CLOSING_PROVIDERS:
                    continue
                observed = parse_utc(item["observed_at_utc"])
                if observed > event_start:
                    continue
                quote = item.get("markets", {}).get(market_type, {}).get(selection)
                if quote is not None:
                    candidates.append((observed, quote))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None


class MLBMarketOddsFeed:
    """Polymarket executable-ask feed with a DraftKings/The Odds API fallback."""

    def __init__(
        self,
        registry: EntityRegistry,
        snapshot_store: MarketOddsSnapshotStore,
        polymarket: PolymarketUSClient | None = None,
        odds_api: TheOddsAPIClient | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        self.registry = registry
        self.snapshot_store = snapshot_store
        self.polymarket = polymarket or PolymarketUSClient()
        self.odds_api = odds_api
        self.observed_at = observed_at or utc_now()
        self._polymarket_events: list[dict[str, Any]] | None = None
        self._odds_api_events: list[dict[str, Any]] | None = None
        self._load_errors: list[str] = []

    def load(self, game_date: str) -> None:
        parsed_date = date.fromisoformat(game_date)
        try:
            self._polymarket_events = self.polymarket.slate("MLB", parsed_date)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            self._polymarket_events = []
            self._load_errors.append(f"polymarket_us:{type(error).__name__}")
        if self.odds_api is None:
            self._odds_api_events = []
            self._load_errors.append("the_odds_api:not_configured")
            return
        try:
            self._odds_api_events = self.odds_api.odds("MLB")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            self._odds_api_events = []
            self._load_errors.append(f"the_odds_api:{type(error).__name__}")

    def for_game(
        self,
        event_id: str,
        event_start_utc: str,
        away_team: str,
        home_team: str,
    ) -> MLBGameOdds:
        if self._polymarket_events is None or self._odds_api_events is None:
            raise RuntimeError("market odds feed must be loaded once before resolving games")
        errors = list(self._load_errors)
        polymarket_event = self._match_event(
            self._polymarket_events,
            away_team,
            home_team,
            source="polymarket",
        )
        if polymarket_event is not None:
            try:
                snapshot = self._from_polymarket(
                    event_id,
                    event_start_utc,
                    away_team,
                    home_team,
                    polymarket_event,
                )
                self.snapshot_store.append(snapshot)
                return snapshot
            except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
                errors.append(f"polymarket_us_exact_bbo:{type(error).__name__}")
        draftkings_event = self._match_event(
            self._odds_api_events,
            away_team,
            home_team,
            source="odds_api",
        )
        if draftkings_event is not None:
            try:
                snapshot = self._from_draftkings(
                    event_id,
                    event_start_utc,
                    away_team,
                    home_team,
                    draftkings_event,
                )
                self.snapshot_store.append(snapshot)
                return snapshot
            except (KeyError, StopIteration, TypeError, ValueError) as error:
                errors.append(f"draftkings_exact_market:{type(error).__name__}")
        detail = ",".join(errors) if errors else "no_matching_event"
        raise MarketUnavailableError(f"NO_CALL_MARKET_UNAVAILABLE ({detail})")

    def _match_event(
        self,
        events: list[dict[str, Any]],
        away_team: str,
        home_team: str,
        source: str,
    ) -> dict[str, Any] | None:
        target = self._canonical_pair(away_team, home_team)
        for event in events:
            try:
                if source == "odds_api":
                    pair = self._canonical_pair(event["away_team"], event["home_team"])
                else:
                    pair = self._polymarket_pair(event)
                if pair == target:
                    return event
            except (EntityResolutionError, KeyError, StopIteration, TypeError, ValueError):
                continue
        return None

    def _canonical_pair(self, away_team: str, home_team: str) -> tuple[str, str]:
        return (
            self.registry.resolve(League.MLB, away_team).canonical_team_id,
            self.registry.resolve(League.MLB, home_team).canonical_team_id,
        )

    def _polymarket_pair(self, event: dict[str, Any]) -> tuple[str, str]:
        market = _select_full_game_market(event, "moneyline")
        if market is None:
            raise StopIteration("no full-game moneyline market")
        by_selection = {side["selection"]: side for side in market["sides"]}
        return self._canonical_pair(by_selection["away"]["team"], by_selection["home"]["team"])

    def _from_polymarket(
        self,
        event_id: str,
        event_start_utc: str,
        away_team: str,
        home_team: str,
        event: dict[str, Any],
    ) -> MLBGameOdds:
        markets: dict[str, dict[str, MarketSideQuote]] = {}
        raw_books: dict[str, Any] = {}
        for market_type, expected_sides in (
            ("moneyline", ("away", "home")),
            ("spread", ("away", "home")),
            ("total", ("over", "under")),
        ):
            market = _select_full_game_market(event, market_type)
            if market is None:
                continue  # market type not available for this event — skip gracefully
            snapshot = self.polymarket.snapshot(market["market_slug"], self.observed_at)
            raw_books[market_type] = snapshot
            normalized: dict[str, MarketSideQuote] = {}
            for side in market["sides"]:
                selection = self._polymarket_selection(side, away_team, home_team, market_type)
                if selection not in expected_sides:
                    continue
                book_side = "long" if side["is_long"] else "short"
                # Decision price is the EXECUTABLE ASK for the selected side — never
                # the midpoint. You cannot execute at midpoint; pricing there
                # overstates every edge by ~half the spread. Midpoint is kept as a
                # reference column only.
                ask = snapshot[book_side].get("ask")
                if ask is None:
                    raise MarketUnavailableError("exact Polymarket BBO ask unavailable")
                midpoint = snapshot[book_side].get("midpoint")
                normalized[selection] = MarketSideQuote(
                    selection=selection,
                    line=None if market_type == "moneyline" else float(side["line"]),
                    american_odds=probability_to_american(float(ask)),
                    decision_probability=float(ask),
                    midpoint_probability=None if midpoint is None else float(midpoint),
                    market_slug=market["market_slug"],
                    polymarket_side=book_side,
                )
            if set(normalized) != set(expected_sides):
                raise MarketUnavailableError(f"incomplete Polymarket {market_type} BBO")
            _validate_lines(market_type, normalized)
            markets[market_type] = normalized
        return _snapshot(
            event_id,
            event_start_utc,
            away_team,
            home_team,
            "polymarket_us",
            self.observed_at,
            markets,
            {"event": event, "books": raw_books},
        )

    def _polymarket_selection(
        self,
        side: dict[str, Any],
        away_team: str,
        home_team: str,
        market_type: str,
    ) -> str:
        selection = str(side["selection"]).casefold()
        if market_type == "total":
            return selection
        if selection in {"away", "home"}:
            return selection
        side_team = self.registry.resolve(League.MLB, str(side["team"])).canonical_team_id
        away_id, home_id = self._canonical_pair(away_team, home_team)
        return "away" if side_team == away_id else "home" if side_team == home_id else selection

    def _from_draftkings(
        self,
        event_id: str,
        event_start_utc: str,
        away_team: str,
        home_team: str,
        event: dict[str, Any],
    ) -> MLBGameOdds:
        bookmaker = next(item for item in event["bookmakers"] if item.get("key") == "draftkings")
        source_markets = {item["key"]: item for item in bookmaker["markets"]}
        markets = {
            "moneyline": self._draftkings_sides(source_markets["h2h"], "moneyline", away_team, home_team),
            "spread": self._draftkings_sides(source_markets["spreads"], "spread", away_team, home_team),
            "total": self._draftkings_sides(source_markets["totals"], "total", away_team, home_team),
        }
        for market_type, sides in markets.items():
            _validate_lines(market_type, sides)
        return _snapshot(
            event_id,
            event_start_utc,
            away_team,
            home_team,
            "draftkings_via_the_odds_api",
            self.observed_at,
            markets,
            {"event": event, "bookmaker": bookmaker},
        )

    def _draftkings_sides(
        self,
        market: dict[str, Any],
        market_type: str,
        away_team: str,
        home_team: str,
    ) -> dict[str, MarketSideQuote]:
        away_id, home_id = self._canonical_pair(away_team, home_team)
        output: dict[str, MarketSideQuote] = {}
        for outcome in market["outcomes"]:
            name = str(outcome["name"])
            if market_type == "total":
                selection = name.casefold()
            else:
                team_id = self.registry.resolve(League.MLB, name).canonical_team_id
                selection = "away" if team_id == away_id else "home" if team_id == home_id else name
            american_odds = int(outcome["price"])
            output[selection] = MarketSideQuote(
                selection=selection,
                line=None if market_type == "moneyline" else float(outcome["point"]),
                american_odds=american_odds,
                decision_probability=implied_probability(american_odds),
            )
        return output


# Slug fragments that mark partial-game or derivative contracts. A Polymarket
# MLB event lists MANY totals/spreads (first-5-innings "f5" lines, alternate
# lines); grabbing the first one silently priced an F5 total 2.5 as a
# full-game market. Only full-game main lines may become decision prices.
_PARTIAL_GAME_MARKERS = ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-")


def _is_full_game_market(market: dict[str, Any]) -> bool:
    slug = str(market.get("market_slug") or "").casefold()
    return bool(slug) and not any(marker in slug for marker in _PARTIAL_GAME_MARKERS)


def _market_balance(market: dict[str, Any]) -> float:
    """Distance of the market's de-vigged midpoint from 50/50.

    The exchange's MAIN line is the one set closest to a coin flip; alternate
    lines (total 2.5 on a full game, run line -2.5) price far from 0.5. Uses
    the indicative slate quotes, which exist before any BBO call.
    """
    prices = []
    for side in market.get("sides", []):
        value = side.get("price_probability")
        if value is not None and 0 < float(value) < 1:
            prices.append(float(value))
    if len(prices) < 2:
        return 0.5  # unknown: rank behind any market with two quotes
    midpoint = (prices[0] + (1 - prices[1])) / 2
    return abs(midpoint - 0.5)


def _select_full_game_market(event: dict[str, Any], market_type: str) -> dict[str, Any] | None:
    """The full-game MAIN market of a type: partial-game slugs excluded,
    then the most balanced line wins (moneyline is unique per game)."""
    candidates = [
        market
        for market in event.get("markets", [])
        if market.get("market_type") == market_type and _is_full_game_market(market)
    ]
    if not candidates:
        return None
    if market_type == "moneyline" or len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=_market_balance)


def _validate_lines(market_type: str, sides: dict[str, MarketSideQuote]) -> None:
    expected = {"away", "home"} if market_type != "total" else {"over", "under"}
    if set(sides) != expected:
        raise MarketUnavailableError(f"incomplete {market_type} market")
    if market_type == "spread":
        if sides["away"].line is None or sides["home"].line is None:
            raise MarketUnavailableError("spread lines unavailable")
        if abs(sides["away"].line + sides["home"].line) > 1e-9:
            raise MarketUnavailableError("spread lines are not opposites")
    if market_type == "total" and (
        sides["over"].line is None or sides["under"].line != sides["over"].line
    ):
            raise MarketUnavailableError("total lines are unavailable or incoherent")


def _snapshot(
    event_id: str,
    event_start_utc: str,
    away_team: str,
    home_team: str,
    provider: str,
    observed_at: datetime,
    markets: dict[str, dict[str, MarketSideQuote]],
    raw_response: dict[str, Any],
) -> MLBGameOdds:
    canonical = {
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "away_team": away_team,
        "home_team": home_team,
        "provider": provider,
        "observed_at_utc": iso_utc(observed_at),
        "markets": {
            market: {side: asdict(quote) for side, quote in quotes.items()}
            for market, quotes in markets.items()
        },
        "raw_response": raw_response,
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return MLBGameOdds(
        event_id=event_id,
        event_start_utc=event_start_utc,
        away_team=away_team,
        home_team=home_team,
        provider=provider,
        observed_at_utc=iso_utc(observed_at),
        markets=markets,
        raw_response=raw_response,
        snapshot_hash=digest,
    )
