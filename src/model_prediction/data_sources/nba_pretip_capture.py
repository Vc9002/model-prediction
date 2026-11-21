"""Forward, point-in-time NBA pre-tip capture: ESPN sportsbook odds plus Polymarket quotes near tip.

Why this exists (quick-261120-npc, NBA v10 plan Phase 0.1 and track T-B): NBA closing-line value
could not be measured. Polymarket quotes were captured only at forecast runs (the last one a median
65 minutes before tip) and no sportsbook consensus was captured live at all. The v10 main gate is CLV
against the devigged sportsbook consensus at about T-10 minutes; track T-B compares that consensus
with the Polymarket ask. This module is run every 10 minutes (worker ``nba-pretip-capture``) and
captures every NBA game that tips inside the next ``window_minutes``, so each game gets a capture
roughly 20 and 10 minutes before tip.

What one run writes
- ESPN: one record per in-window game to ``<runtime root>/odds/nba/<ET game date>/espn_odds.jsonl``
  (the NFL file name, beside that date's Polymarket snapshots). Every provider in the scoreboard's
  ``odds`` list is kept. Sportsbook prices are converted to implied probabilities as they are read;
  the quoted prices themselves are never stored. Each market also carries the overround and the
  fair (devigged) probabilities, plus a cross-book consensus (``devig.py`` documents the method).
  The record links the matched Polymarket event (slug and id), or states why there is none.
- Polymarket: the in-window events go through the existing ``capture_slate_snapshots`` unchanged,
  so the quotes land in the canonical ``polymarket_snapshots.jsonl`` with its schema (bid, ask,
  midpoint, sizes, ``observed_at_utc``, ``event_start_utc``, ``reconstruction_status_source``). The
  existing closing-snapshot and CLV readers see the pre-tip quotes with no new reader.

Point in time: ``observed_at_utc`` is stamped after the response returns. A game whose tip is at or
before that stamp is never written as an ESPN record. Readers must still filter on
``observed_at_utc <= decision time``.

Fail closed per game: no odds on ESPN means no ESPN record (``NO_ESPN_ODDS``); a Polymarket event is
linked only when exactly one event matches the tip time and both team nicknames. A failure on one
game never stops the others, and ``capture_nba_pretip`` never raises.

Requests per run: one ESPN scoreboard per Eastern date the window touches. Only when a game is in
the window: one Polymarket event list, one event-detail request per game for the market inventory,
then ``capture_slate_snapshots`` (one more event-detail request per game, CDN-cached for 30 s, and
paced /book requests for the most balanced spread and total rung, the moneyline, and the
rungs at the ESPN lines), all behind the existing 429 pacing and budget.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..devig import DEFAULT_DEVIG_METHOD, devig, overround
from ..domain import EASTERN, parse_utc
from ..pricing import implied_probability
from ..runtime_paths import rerouted_data_root

_logger = logging.getLogger(__name__)

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}&limit=100"
)
ESPN_ODDS_FILENAME = "espn_odds.jsonl"
SCHEMA_VERSION = "1"
USER_AGENT = "Mozilla/5.0"
DEFAULT_WINDOW_MINUTES = 25.0
# Sized /book quotes per spread/total ladder: the most balanced rung (plus the rungs at the ESPN lines and
# open picks, kept regardless). The event-detail pass already quotes bid/ask on every rung, so /book only
# adds depth. The daily capture keeps 3, but 3 cost 55 /book calls and 152 s with one 429 pause for the
# 6-game 2026-11-21 slate (hand run 2026-11-20); a T-10 slot must finish well inside its 10 minutes.
NBA_LADDER_KEEP = 1
DEFAULT_POLYMARKET_MAX_SECONDS = 180.0
# Polymarket and ESPN both list the scheduled tip; a few minutes of slack covers a rounding mismatch.
MATCH_START_TOLERANCE = timedelta(minutes=5)

HttpGet = Callable[[str], tuple[int, bytes]]
Clock = Callable[[], datetime]


# --------------------------------------------------------------------------- parsing
def price_to_probability(value: Any) -> float | None:
    """Implied probability of one ESPN price field ('-650', '+474', -110.0, 'EVEN'); None if unusable.

    The price is converted here, on read; only the probability ever leaves this function.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().upper()
    if text in ("EVEN", "EV"):
        return 0.5
    try:
        number = float(text.replace("+", ""))
    except ValueError:
        return None
    if not math.isfinite(number) or abs(number) < 100 or number != int(number):
        return None
    try:
        return implied_probability(int(number))
    except ValueError:
        return None


_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _line(value: Any) -> float | None:
    """A point line from a number or an ESPN display string ('-11.5', '+11.5', 'o229.5', 'u229.5')."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    match = _NUM.search(str(value))
    return float(match.group(0)) if match else None


def _leg(block: Any, side: str, phase: str, key: str) -> Any:
    if not isinstance(block, dict):
        return None
    node = (block.get(side) or {}).get(phase) if isinstance(block.get(side), dict) else None
    return node.get(key) if isinstance(node, dict) else None


def _two_way(raw_a: float | None, raw_b: float | None, method: str) -> dict[str, float | None]:
    fair = devig((raw_a, raw_b), method)
    return {
        "overround": None if raw_a is None or raw_b is None else round(overround((raw_a, raw_b)), 6),
        "fair_a": None if fair is None else round(fair[0], 6),
        "fair_b": None if fair is None else round(fair[1], 6),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def parse_provider_odds(item: dict[str, Any], method: str = DEFAULT_DEVIG_METHOD) -> dict[str, Any]:
    """One scoreboard odds item as probabilities: moneyline, spread and total, current and open.

    ESPN's ``close`` block is the live pregame line until tip (it becomes the closing line after), so it
    is stored as ``current``. Spread lines are home-anchored (negative = home favoured).
    """
    provider = item.get("provider") or {}
    name = str(provider.get("name") or provider.get("displayName") or "")
    moneyline = item.get("moneyline") or {}
    spread = item.get("pointSpread") or {}
    total = item.get("total") or {}

    ml_home = price_to_probability(_leg(moneyline, "home", "close", "odds"))
    ml_away = price_to_probability(_leg(moneyline, "away", "close", "odds"))
    ml_fair = _two_way(ml_home, ml_away, method)

    home_line = _line(_leg(spread, "home", "close", "line"))
    if home_line is None:
        home_line = _line(item.get("spread"))
    away_line = _line(_leg(spread, "away", "close", "line"))
    sp_home = price_to_probability(_leg(spread, "home", "close", "odds"))
    sp_away = price_to_probability(_leg(spread, "away", "close", "odds"))
    # Both sides must quote the same number with opposite signs, or the pair is not one market.
    spread_consistent = home_line is not None and (away_line is None or math.isclose(away_line, -home_line))
    sp_fair = _two_way(sp_home, sp_away, method) if spread_consistent else _two_way(None, None, method)

    total_line = _line(_leg(total, "over", "close", "line"))
    if total_line is None:
        total_line = _line(item.get("overUnder"))
    under_line = _line(_leg(total, "under", "close", "line"))
    over = price_to_probability(_leg(total, "over", "close", "odds"))
    under = price_to_probability(_leg(total, "under", "close", "odds"))
    total_consistent = total_line is not None and (under_line is None or math.isclose(under_line, total_line))
    tot_fair = _two_way(over, under, method) if total_consistent else _two_way(None, None, method)

    return {
        "provider_id": None if provider.get("id") is None else str(provider.get("id")),
        "provider_name": name,
        "is_live": "live" in name.lower() or bool(item.get("live")),
        "details": item.get("details"),
        "moneyline": {
            "home_prob_raw": _round(ml_home),
            "away_prob_raw": _round(ml_away),
            "overround": ml_fair["overround"],
            "home_fair": ml_fair["fair_a"],
            "away_fair": ml_fair["fair_b"],
            "home_prob_raw_open": _round(price_to_probability(_leg(moneyline, "home", "open", "odds"))),
            "away_prob_raw_open": _round(price_to_probability(_leg(moneyline, "away", "open", "odds"))),
        },
        "spread": {
            "home_line": home_line,
            "away_line": away_line if away_line is not None else (None if home_line is None else -home_line),
            "lines_consistent": spread_consistent,
            "home_prob_raw": _round(sp_home),
            "away_prob_raw": _round(sp_away),
            "overround": sp_fair["overround"],
            "home_fair": sp_fair["fair_a"],
            "away_fair": sp_fair["fair_b"],
            "home_line_open": _line(_leg(spread, "home", "open", "line")),
            "home_prob_raw_open": _round(price_to_probability(_leg(spread, "home", "open", "odds"))),
            "away_prob_raw_open": _round(price_to_probability(_leg(spread, "away", "open", "odds"))),
        },
        "total": {
            "line": total_line,
            "lines_consistent": total_consistent,
            "over_prob_raw": _round(over),
            "under_prob_raw": _round(under),
            "overround": tot_fair["overround"],
            "over_fair": tot_fair["fair_a"],
            "under_fair": tot_fair["fair_b"],
            "line_open": _line(_leg(total, "over", "open", "line")),
            "over_prob_raw_open": _round(price_to_probability(_leg(total, "over", "open", "odds"))),
            "under_prob_raw_open": _round(price_to_probability(_leg(total, "under", "open", "odds"))),
        },
    }


def _modal(values: Iterable[float]) -> float | None:
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    # Most books first; ties go to the smaller line so the choice is deterministic.
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def consensus(providers: list[dict[str, Any]], method: str = DEFAULT_DEVIG_METHOD) -> dict[str, Any]:
    """Cross-book consensus: the mean fair probability per side over pregame books at the modal line.

    A book contributes to a market only when both of its sides are priced (fail closed). Lines differ
    between books, so a spread or total consensus is taken only over books quoting the modal line and
    states that line; ``n_books`` says how many books it rests on.
    """
    pregame = [p for p in providers if not p.get("is_live")]

    def _mean(values: list[float]) -> float | None:
        return round(statistics.fmean(values), 6) if values else None

    ml = [p["moneyline"] for p in pregame if p["moneyline"]["home_fair"] is not None]
    spreads = [p["spread"] for p in pregame if p["spread"]["home_fair"] is not None]
    spread_line = _modal(s["home_line"] for s in spreads)
    spreads = [s for s in spreads if s["home_line"] == spread_line]
    totals = [p["total"] for p in pregame if p["total"]["over_fair"] is not None]
    total_line = _modal(t["line"] for t in totals)
    totals = [t for t in totals if t["line"] == total_line]
    return {
        "method": method,
        "moneyline": {
            "home": _mean([m["home_fair"] for m in ml]),
            "away": _mean([m["away_fair"] for m in ml]),
            "n_books": len(ml),
        },
        "spread": {
            "home_line": spread_line,
            "home": _mean([s["home_fair"] for s in spreads]),
            "away": _mean([s["away_fair"] for s in spreads]),
            "n_books": len(spreads),
        },
        "total": {
            "line": total_line,
            "over": _mean([t["over_fair"] for t in totals]),
            "under": _mean([t["under_fair"] for t in totals]),
            "n_books": len(totals),
        },
        "providers_live_excluded": len(providers) - len(pregame),
    }


def parse_scoreboard_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Identity, tip time, status and raw odds items of one scoreboard event; None without an identity."""
    try:
        competition = event["competitions"][0]
        home = next(c for c in competition["competitors"] if c["homeAway"] == "home")
        away = next(c for c in competition["competitors"] if c["homeAway"] == "away")
        event_id = str(event["id"])
        start = str(event["date"])
    except (KeyError, IndexError, StopIteration, TypeError):
        return None
    status = ((event.get("status") or {}).get("type") or {}).get("name") or ""
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    return {
        "event_id": event_id,
        "event_start_utc": start,
        "status": str(status),
        "home_team": home_team.get("displayName"),
        "away_team": away_team.get("displayName"),
        "home_nickname": home_team.get("name"),
        "away_nickname": away_team.get("name"),
        "home_abbr": home_team.get("abbreviation"),
        "away_abbr": away_team.get("abbreviation"),
        "odds_items": [item for item in competition.get("odds") or [] if isinstance(item, dict)],
    }


def _contains_word(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def match_polymarket_event(
    game: dict[str, Any], polymarket_events: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    """The one Polymarket event with the same tip time and both team nicknames in its title.

    Returns ``(event, "matched")`` or ``(None, reason)``. Nicknames, not cities or abbreviations:
    ESPN says "LA Clippers" where Polymarket's team list says "Los Angeles Clippers", and the slug
    abbreviations differ between the two (ESPN "NY", "SA", "UTAH").
    """
    try:
        start = parse_utc(game["event_start_utc"])
    except (KeyError, TypeError, ValueError):
        return None, "NO_CALL_BAD_ESPN_START"
    home = str(game.get("home_nickname") or "").casefold()
    away = str(game.get("away_nickname") or "").casefold()
    if not home or not away:
        return None, "NO_CALL_MISSING_ESPN_NICKNAME"
    hits = []
    for event in polymarket_events:
        try:
            pm_start = parse_utc(str(event.get("startTime")))
        except (TypeError, ValueError):
            continue
        if abs(pm_start - start) > MATCH_START_TOLERANCE:
            continue
        title = str(event.get("title") or "").casefold()
        if _contains_word(title, home) and _contains_word(title, away):
            hits.append(event)
    if not hits:
        return None, "NO_CALL_NO_POLYMARKET_EVENT"
    if len(hits) > 1:
        return None, "NO_CALL_AMBIGUOUS_POLYMARKET_EVENT"
    return hits[0], "matched"


def espn_line_slugs(record: dict[str, Any], normalized_event: dict[str, Any]) -> set[str]:
    """Polymarket spread/total rungs at the ESPN consensus lines, so each gets a sized /book quote."""
    spread_line = record["consensus"]["spread"]["home_line"]
    total_line = record["consensus"]["total"]["line"]
    slugs: set[str] = set()
    for market in normalized_event.get("markets") or []:
        try:
            line = None if market.get("line") is None else float(market["line"])
        except (TypeError, ValueError):
            continue
        if line is None:
            continue
        kind = market.get("market_type")
        at_spread = kind == "spread" and spread_line is not None and abs(line) == abs(spread_line)
        at_total = kind == "total" and total_line is not None and line == total_line
        if at_spread or at_total:
            slugs.add(str(market.get("market_slug")))
    return slugs


# --------------------------------------------------------------------------- storage
def _record_hash(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def espn_odds_path(data_root: str | Path, game_date_et: str) -> Path:
    """ESPN odds file for one Eastern game date, beside that date's Polymarket snapshots."""
    return rerouted_data_root(data_root) / "odds" / "nba" / game_date_et / ESPN_ODDS_FILENAME


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _default_http_get(url: str) -> tuple[int, bytes]:
    import httpx

    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)
    return response.status_code, response.content


_CAPTURE_SUMMARY_KEYS = (
    "status",
    "captured",
    "captured_by_source",
    "failures",
    "missing_executable_ask",
    "source_rate_limited",
    "rate_limit_pauses",
    "timestamp_invalid_count",
    "event_detail_events",
    "primary_contracts",
    "skipped_ladder_alternates",
    "nba_missing_quote_event_ids",
    "elapsed_seconds",
)


# --------------------------------------------------------------------------- capture
def capture_nba_pretip(
    data_root: str | Path,
    *,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    now: Clock | None = None,
    http_get: HttpGet | None = None,
    polymarket_client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
    keep_slugs: frozenset[str] = frozenset(),
    polymarket_max_seconds: float = DEFAULT_POLYMARKET_MAX_SECONDS,
    method: str = DEFAULT_DEVIG_METHOD,
) -> dict[str, Any]:
    """Capture ESPN odds and Polymarket quotes for every NBA game tipping within ``window_minutes``.

    ``now`` is a clock callable read again after every response. ``http_get`` (ESPN),
    ``polymarket_client`` and ``capture_fn`` are injectable for tests. Never raises.
    """
    try:
        return _capture(
            data_root,
            window_minutes=window_minutes,
            clock=now or (lambda: datetime.now(UTC)),
            http_get=http_get or _default_http_get,
            polymarket_client=polymarket_client,
            capture_fn=capture_fn,
            keep_slugs=keep_slugs,
            polymarket_max_seconds=polymarket_max_seconds,
            method=method,
        )
    except Exception as exc:
        _logger.warning("NBA pre-tip capture failed", exc_info=True)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200], "espn_records_written": 0}


def _capture(
    data_root: str | Path,
    *,
    window_minutes: float,
    clock: Clock,
    http_get: HttpGet,
    polymarket_client: Any | None,
    capture_fn: Callable[..., dict[str, Any]] | None,
    keep_slugs: frozenset[str],
    polymarket_max_seconds: float,
    method: str,
) -> dict[str, Any]:
    started = clock()
    window_end = started + timedelta(minutes=window_minutes)
    errors: list[dict[str, str]] = []
    # ESPN files a game under its Eastern date, so a window crossing midnight ET needs both dates.
    dates = sorted({started.astimezone(EASTERN).date(), window_end.astimezone(EASTERN).date()})

    games: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {"skipped_not_scheduled": 0, "skipped_started": 0, "beyond_window": 0}
    scoreboards_ok = 0
    for day in dates:
        url = SCOREBOARD_URL.format(date=day.strftime("%Y%m%d"))
        try:
            status, raw = http_get(url)
            observed = clock()  # stamped after the response returned
            if status != 200:
                errors.append({"kind": "espn_scoreboard", "date": day.isoformat(), "error": f"HTTP {status}"})
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("scoreboard payload is not a JSON object")
        except Exception as exc:  # noqa: BLE001 -- one bad date must not stop the other
            errors.append(
                {
                    "kind": "espn_scoreboard",
                    "date": day.isoformat(),
                    "error": f"{type(exc).__name__}: {exc}"[:160],
                }
            )
            continue
        scoreboards_ok += 1
        sha = hashlib.sha256(raw).hexdigest()
        for raw_event in payload.get("events") or []:
            game = parse_scoreboard_event(raw_event) if isinstance(raw_event, dict) else None
            if game is None or game["event_id"] in seen:
                continue
            seen.add(game["event_id"])
            try:
                tip = parse_utc(game["event_start_utc"])
            except (TypeError, ValueError):
                errors.append({"kind": "espn_event", "error": f"bad start for {game['event_id']}"})
                continue
            if game["status"] != "STATUS_SCHEDULED":
                counts["skipped_not_scheduled"] += 1
            elif tip <= observed:
                counts["skipped_started"] += 1
            elif tip > window_end:
                counts["beyond_window"] += 1
            else:
                games.append({**game, "_tip": tip, "_observed": observed, "_sha": sha})
    games.sort(key=lambda g: (g["_tip"], g["event_id"]))

    if not games:
        return {
            "status": "error" if scoreboards_ok == 0 else ("partial" if errors else "ok"),
            "observed_at_utc": _iso(clock()),
            "window_minutes": window_minutes,
            "dates": [d.isoformat() for d in dates],
            "games_in_window": 0,
            "espn_records_written": 0,
            **counts,
            "games": [],
            "polymarket": {"status": "not_requested", "reason": "no NBA game in window"},
            "errors": errors[:20],
        }

    # ESPN records first (in memory): the Polymarket link is added before they are written.
    records: dict[str, dict[str, Any]] = {}
    game_status: dict[str, dict[str, Any]] = {}
    for game in games:
        status_row: dict[str, Any] = {
            "event_id": game["event_id"],
            "matchup": f"{game['away_abbr']} @ {game['home_abbr']}",
            "event_start_utc": game["event_start_utc"],
            "minutes_to_tip": round((game["_tip"] - game["_observed"]).total_seconds() / 60, 1),
        }
        game_status[game["event_id"]] = status_row
        providers = [parse_provider_odds(item, method) for item in game["odds_items"]]
        if not providers:
            status_row["espn"] = "NO_ESPN_ODDS"
            continue
        status_row["espn"] = "ok"
        records[game["event_id"]] = {
            "schema_version": SCHEMA_VERSION,
            "source": "espn_scoreboard_odds",
            "sport": "nba",
            "event_id": game["event_id"],
            "event_start_utc": game["event_start_utc"],
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "home_abbr": game["home_abbr"],
            "away_abbr": game["away_abbr"],
            "observed_at_utc": _iso(game["_observed"]),
            "minutes_to_tip": status_row["minutes_to_tip"],
            "content_sha256": game["_sha"],
            "devig_method": method,
            "providers": providers,
            "consensus": consensus(providers, method),
            "polymarket": None,
        }

    from .polymarket_us import PolymarketUSClient

    client = polymarket_client or PolymarketUSClient(max_attempts=2)
    matched = _match_polymarket(client, games, records, game_status, errors=errors)

    # ESPN records are written before the paced Polymarket capture, so a run killed at its timeout
    # still keeps them. Every game here tipped after its scoreboard was observed (filtered above).
    written = 0
    for game in games:
        record = records.get(game["event_id"])
        if record is None:
            continue
        record["record_hash"] = _record_hash(record)
        path = espn_odds_path(data_root, game["_tip"].astimezone(EASTERN).date().isoformat())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        except OSError as exc:
            errors.append({"kind": "write", "error": f"{type(exc).__name__}: {exc}"[:160]})
            game_status[game["event_id"]]["espn"] = "WRITE_FAILED"
            continue
        written += 1

    polymarket_summary = _run_polymarket_capture(
        client,
        data_root,
        matched,
        capture_fn=capture_fn,
        keep_slugs=keep_slugs,
        polymarket_max_seconds=polymarket_max_seconds,
        errors=errors,
    )

    partial = bool(errors) or any(
        row.get("espn") != "ok" or row.get("polymarket") != "matched" for row in game_status.values()
    )
    return {
        "status": "partial" if partial else "ok",
        "observed_at_utc": _iso(clock()),
        "window_minutes": window_minutes,
        "dates": [d.isoformat() for d in dates],
        "games_in_window": len(games),
        "espn_records_written": written,
        **counts,
        "games": list(game_status.values()),
        "polymarket": polymarket_summary,
        "errors": errors[:20],
    }


@dataclass
class _PolymarketMatch:
    events_listed: int = 0
    events_by_date: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    espn_line_slugs: set[str] = field(default_factory=set)
    list_error: bool = False


def _match_polymarket(
    client: Any,
    games: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    game_status: dict[str, dict[str, Any]],
    *,
    errors: list[dict[str, str]],
) -> _PolymarketMatch:
    """Link each in-window game to its Polymarket event and load that event's market inventory."""
    import httpx

    from .polymarket_us import PolymarketUSClient

    out = _PolymarketMatch()

    def _link(event_id: str, reason: str, event: dict[str, Any] | None = None) -> None:
        game_status[event_id]["polymarket"] = reason
        if event_id in records:
            records[event_id]["polymarket"] = {
                "match_status": reason,
                "event_slug": None if event is None else event.get("slug"),
                "event_id": None if event is None else str(event.get("id")),
            }

    try:
        pm_events = client.events("NBA")
    except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        errors.append({"kind": "polymarket_events", "error": f"{type(exc).__name__}: {exc}"[:160]})
        for game in games:
            _link(game["event_id"], "NO_CALL_POLYMARKET_EVENTS_UNAVAILABLE")
        out.list_error = True
        return out

    out.events_listed = len(pm_events)
    for game in games:
        summary_event, reason = match_polymarket_event(game, pm_events)
        if summary_event is None:
            _link(game["event_id"], reason)
            continue
        slug = str(summary_event.get("slug") or "")
        try:
            detail = client.event(slug)
            normalized = PolymarketUSClient._normalize_event(detail, "NBA", "America/New_York")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            errors.append({"kind": "polymarket_event_detail", "slug": slug, "error": f"{type(exc).__name__}"})
            _link(game["event_id"], "NO_CALL_POLYMARKET_DETAIL_UNAVAILABLE", summary_event)
            continue
        if not normalized.get("markets"):
            _link(game["event_id"], "NO_CALL_NO_TWO_SIDED_POLYMARKET_MARKET", summary_event)
            continue
        _link(game["event_id"], "matched", summary_event)
        if game["event_id"] in records:
            out.espn_line_slugs |= espn_line_slugs(records[game["event_id"]], normalized)
        # Snapshots are filed under the game's Eastern date, the layout forecast and settlement read.
        game_date = game["_tip"].astimezone(EASTERN).date().isoformat()
        out.events_by_date.setdefault(game_date, []).append(normalized)
    return out


def _run_polymarket_capture(
    client: Any,
    data_root: str | Path,
    matched: _PolymarketMatch,
    *,
    capture_fn: Callable[..., dict[str, Any]] | None,
    keep_slugs: frozenset[str],
    polymarket_max_seconds: float,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Quote the matched events through the shared, paced snapshot capture (canonical store)."""
    from .polymarket_us import capture_slate_snapshots

    if matched.list_error:
        return {"status": "error", "reason": "event list unavailable"}
    capture = capture_fn or capture_slate_snapshots
    per_date: dict[str, Any] = {}
    for game_date, events in sorted(matched.events_by_date.items()):
        started = time.monotonic()
        try:
            result = capture(
                client,
                {"NBA": events},
                data_root,
                game_date,
                horizon_hours=None,
                ladder_keep={"NBA": NBA_LADDER_KEEP},
                ladder_leagues=frozenset({"NBA"}),
                ladder_keep_slugs=frozenset(keep_slugs | matched.espn_line_slugs),
                priority_leagues=frozenset({"NBA"}),
                max_capture_seconds=polymarket_max_seconds,
            )
        except Exception as exc:  # noqa: BLE001 -- one date's failure must not hide the other's result
            errors.append(
                {
                    "kind": "polymarket_capture",
                    "date": game_date,
                    "error": f"{type(exc).__name__}: {exc}"[:160],
                }
            )
            per_date[game_date] = {"status": "error", "elapsed_seconds": round(time.monotonic() - started, 1)}
            continue
        per_date[game_date] = {key: result.get(key) for key in _CAPTURE_SUMMARY_KEYS if key in result}
    statuses = {row.get("status") for row in per_date.values()}
    return {
        "status": "ok"
        if statuses <= {"ok"} and per_date
        else ("error" if statuses == {"error"} else "partial"),
        "events_listed": matched.events_listed,
        "events_captured": sum(len(v) for v in matched.events_by_date.values()),
        "by_date": per_date,
    }


__all__ = [
    "DEFAULT_WINDOW_MINUTES",
    "ESPN_ODDS_FILENAME",
    "capture_nba_pretip",
    "consensus",
    "espn_line_slugs",
    "espn_odds_path",
    "match_polymarket_event",
    "parse_provider_odds",
    "parse_scoreboard_event",
    "price_to_probability",
]
