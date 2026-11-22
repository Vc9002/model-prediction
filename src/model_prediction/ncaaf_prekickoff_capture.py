"""NCAAF pre-kickoff close capture: the full Polymarket book of every game kicking off soon.

NCAAF settlement takes as a row's close the latest snapshot of the row's own contract that was
observed strictly after the decision quote and strictly before kickoff, with an open book and,
for a spread or total, at the row's own line (``cli.settle._closing_probability_for_moneyline_pick``).
NCAAF snapshots were written only by the Daily runs and the day-ahead capture, so most rows had no
quote between their decision and kickoff and settled with no close and no CLV (2026-11-21).

Run every 10 minutes by the supervisor worker ``ncaaf-prekickoff-capture``. Each run captures the
games kicking off in (now + 60 s, now + ``window_minutes``], so with the default 25-minute window
every game is quoted two or three times in its last 25 minutes. Per game: the contract of every
open NCAAF ledger row first (soonest kickoff first), then the moneyline and the most balanced
spread and total rung. Rows go through ``capture_slate_snapshots`` into the canonical
``<runtime root>/odds/ncaaf/<ET game date>/polymarket_snapshots.jsonl``, the file settlement reads.

Requests: none when neither the ledger nor the snapshot archive has a game in the window (an
off-day run is free). Otherwise one event list, one event-detail GET per in-window game, and one
paced /book request per contract (NCAAF is /book-only, see ``EVENT_DETAIL_EXCLUDED_LEAGUES``),
behind the capture's shared 429 pacer and one wall-clock deadline. A held contract that discovery
did not produce (event list or detail failed, rung delisted) is still captured from its archived
record, as the NCAAB pre-tip capture does (``pretip_close_capture``).

Market data only, never an order. Never raises.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .domain import EASTERN, parse_utc
from .pretip_close_capture import _OPEN_ROWS_SQL, _contracts_from_archive, _latest_archived, _row_slug

_logger = logging.getLogger(__name__)

SPORT = "ncaaf"
LEAGUE = "NCAAF"
DEFAULT_WINDOW_MINUTES = 25.0  # two or three 10-minute runs see every game
MIN_LEAD_SECONDS = 60.0  # a game kicking off inside a minute would be quoted after kickoff
DEFAULT_MAX_CAPTURE_SECONDS = 240.0  # supervisor timeout is 360 s; rows are written only at the end
MIN_USEFUL_CAPTURE_SECONDS = 5.0
# Moneyline plus the most balanced spread and total rung per game; open-row rungs are added on top.
# The Daily keeps 2, but a noon cluster is 12+ games and one /book costs about a second at the limit.
LADDER_KEEP = 1
CLIENT_TIMEOUT_SECONDS = 10.0
CLIENT_MAX_ATTEMPTS = 2

Clock = Callable[[], datetime]

_EVENT_START = re.compile(r'"event_start_utc":\s*"([^"]+)"')
# NCAAF moneyline rows record "<contract slug>-<selection>" (models/college_football.py and v6.2
# append the side), a slug no Polymarket contract has: 0 of 159 settled NCAAF moneyline rows
# since 2026-11-01 had a close. The contract is the part before the side.
_ML_SIDE_SUFFIX = re.compile(r"^(aec-cfb-[a-z0-9-]+-\d{4}-\d{2}-\d{2})-(?:home|away)$")


def ncaaf_contract_slug(slug: str) -> str:
    """The Polymarket contract slug of an NCAAF ledger slug (drops a moneyline row's side suffix)."""
    match = _ML_SIDE_SUFFIX.match(slug)
    return match.group(1) if match else slug


_CONTRACT_PREFIXES = ("atc", "tsc", "asc", "mkt", "aec")


def ncaaf_settlement_slug(slug: str, rationale: str) -> str:
    """The contract an NCAAF row's close is looked up on, from settlement's extracted slug.

    The shared ``cli.settle._extract_market_slug`` takes the first hyphenated parenthesized token,
    and the v6.2 rationale reads "challenger (anchor-blended): ... (aec-cfb-...)", so all 336 v6.2
    rows extracted "anchor-blended" and never had a close (2026-11-21). A contract-prefixed token
    from the rationale replaces a slug that is not one. Kept local to NCAAF settlement: the shared
    extractor also feeds the real-money ticket gate, which is the operator's call to change.
    """
    if not slug.startswith(_CONTRACT_PREFIXES):
        better = _row_slug(None, None, rationale)
        if better is not None and better.startswith(_CONTRACT_PREFIXES):
            slug = better
    return ncaaf_contract_slug(slug)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _in_window(start: datetime, observed: datetime, window_minutes: float) -> bool:
    return (
        observed + timedelta(seconds=MIN_LEAD_SECONDS) < start <= observed + timedelta(minutes=window_minutes)
    )


def _held_rows(
    ledgers_db: Path, now: datetime, window_minutes: float
) -> tuple[frozenset[str], dict[str, str], dict[str, int]]:
    """(every open NCAAF slug, in-window slug -> ledger kickoff, counts). Read-only.

    Every open slug is a keep-slug: the ledger's kickoff and the contract's can differ by minutes,
    so a rung in a discovered in-window game is captured first whatever the ledger says.
    """
    counts = {"open_rows": 0, "in_window_rows": 0, "rows_without_slug": 0}
    conn = sqlite3.connect(f"{ledgers_db.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(_OPEN_ROWS_SQL, (SPORT,)).fetchall()
    finally:
        conn.close()
    every: set[str] = set()
    in_window: dict[str, str] = {}
    for start_raw, explicit, snapshot, rationale in rows:
        counts["open_rows"] += 1
        slug = _row_slug(explicit, snapshot, rationale)
        if slug is None:
            counts["rows_without_slug"] += 1
            continue
        slug = ncaaf_contract_slug(slug)
        every.add(slug)
        try:
            start = parse_utc(str(start_raw or ""))
        except (TypeError, ValueError):
            continue
        if _in_window(start, now, window_minutes):
            counts["in_window_rows"] += 1
            in_window[slug] = str(start_raw)
    return frozenset(every), in_window, counts


def _snapshot_path(runtime_root: Path, game_date: str) -> Path:
    from .data_sources.polymarket_us import PolymarketSnapshotStore

    return PolymarketSnapshotStore.for_sport_date(runtime_root, SPORT, game_date).path


def _window_dates(now: datetime, window_minutes: float) -> list[str]:
    end = now + timedelta(minutes=window_minutes)
    return sorted({now.astimezone(EASTERN).date().isoformat(), end.astimezone(EASTERN).date().isoformat()})


def _archive_has_game_in_window(runtime_root: Path, now: datetime, window_minutes: float) -> bool:
    """True when the Daily's archive lists a contract kicking off in the window (no request made)."""
    for game_date in _window_dates(now, window_minutes):
        path = _snapshot_path(runtime_root, game_date)
        if not path.exists():
            continue
        seen: set[str] = set()
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = _EVENT_START.search(line)
                if match is None or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                try:
                    if _in_window(parse_utc(match.group(1)), now, window_minutes):
                        return True
                except (TypeError, ValueError):
                    continue
    return False


def _discover(
    client: Any, clock: Clock, window_minutes: float, deadline: float, status: dict[str, Any]
) -> dict[str, list[dict[str, Any]]] | None:
    """In-window NCAAF events, normalized and keyed by ET game date; None when the list failed."""
    from .data_sources.polymarket_us import PolymarketUSClient

    try:
        listed = client.events(LEAGUE)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        status["errors"].append({"kind": "polymarket_events", "error": f"{type(exc).__name__}: {exc}"[:160]})
        return None
    observed = clock()  # after the response: a game that kicked off during the call is out
    status["events_listed"] = len(listed)
    wanted: list[tuple[datetime, str]] = []
    for event in listed:
        try:
            start = parse_utc(str(event.get("startTime") or ""))
        except (TypeError, ValueError):
            continue
        slug = str(event.get("slug") or "")
        if slug and _in_window(start, observed, window_minutes):
            wanted.append((start, slug))
    by_date: dict[str, list[dict[str, Any]]] = {}
    for start, slug in sorted(wanted):
        if deadline - time.monotonic() < MIN_USEFUL_CAPTURE_SECONDS:
            status["errors"].append({"kind": "polymarket_event_detail", "slug": slug, "error": "time budget"})
            continue
        try:
            normalized = PolymarketUSClient._normalize_event(client.event(slug), LEAGUE, "America/New_York")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            status["errors"].append(
                {"kind": "polymarket_event_detail", "slug": slug, "error": type(exc).__name__}
            )
            continue
        if not normalized.get("markets"):
            continue
        by_date.setdefault(start.astimezone(EASTERN).date().isoformat(), []).append(normalized)
    status["games_discovered"] = sum(len(events) for events in by_date.values())
    return by_date


def _fallback_events(
    runtime_root: Path,
    held_in_window: dict[str, str],
    discovered_slugs: set[str],
    status: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Held in-window contracts discovery did not produce, rebuilt from their archived records.

    Each slug's newest archived record supplies its market type, line and anchor team (never
    guessed: a slug with no archived record gets no close). Filed under the row's ET game date.
    """
    missing = {slug: start for slug, start in held_in_window.items() if slug not in discovered_slugs}
    if not missing:
        return {}
    by_date: dict[str, dict[str, str]] = {}
    for slug, start_raw in missing.items():
        by_date.setdefault(parse_utc(start_raw).astimezone(EASTERN).date().isoformat(), {})[slug] = start_raw
    out: dict[str, list[dict[str, Any]]] = {}
    unmapped: list[str] = []
    for game_date, row_starts in by_date.items():
        archived = _latest_archived(_snapshot_path(runtime_root, game_date), set(row_starts))
        unmapped.extend(sorted(set(row_starts) - set(archived)))
        if archived:
            events = _contracts_from_archive(archived, row_starts, SPORT).get(LEAGUE, [])
            out.setdefault(game_date, []).extend(events)
    status["fallback_contracts"] = sum(len(e["markets"]) for events in out.values() for e in events)
    if unmapped:
        status["held_without_archived_record"] = unmapped[:20]
    return out


def capture_ncaaf_prekickoff(
    *,
    now: Clock | None = None,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    ledgers_db: Path | None = None,
    runtime_root: Path | None = None,
    client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
    max_capture_seconds: float = DEFAULT_MAX_CAPTURE_SECONDS,
) -> dict[str, Any]:
    """Quote every NCAAF game kicking off within ``window_minutes``; see the module docstring.

    ``now`` is a clock callable, read at the start and again after the event list returns.
    ``client`` and ``capture_fn`` are injectable for tests. ``max_capture_seconds`` is one
    wall-clock deadline over discovery and capture. Returns a status dict; never raises.
    """
    clock: Clock = now or (lambda: datetime.now(UTC))
    started = time.monotonic()
    deadline = started + max(0.0, max_capture_seconds)
    status: dict[str, Any] = {"status": "ok", "sport": SPORT, "captured": 0, "errors": []}
    own_http: list[httpx.Client] = []
    try:
        if ledgers_db is None or runtime_root is None:
            from .runtime_paths import RuntimePaths

            paths = RuntimePaths.resolve()
            ledgers_db = ledgers_db or paths.ledgers_db
            runtime_root = runtime_root or paths.runtime_root
        root = Path(runtime_root)
        moment = clock()
        status["observed_at_utc"] = _iso(moment)
        keep_slugs: frozenset[str] = frozenset()
        held_in_window: dict[str, str] = {}
        if Path(ledgers_db).exists():
            keep_slugs, held_in_window, counts = _held_rows(Path(ledgers_db), moment, window_minutes)
            status.update(counts)
        else:
            status["ledger"] = "no_ledger_db"
        if not held_in_window and not _archive_has_game_in_window(root, moment, window_minutes):
            return {**status, "status": "idle", "elapsed_seconds": round(time.monotonic() - started, 1)}

        from .data_sources.polymarket_us import PolymarketUSClient, capture_slate_snapshots

        if client is None:
            http = httpx.Client(timeout=CLIENT_TIMEOUT_SECONDS)
            own_http.append(http)
            client = PolymarketUSClient(client=http, max_attempts=CLIENT_MAX_ATTEMPTS)
        discovered = _discover(client, clock, window_minutes, deadline, status)
        events_by_date: dict[str, list[dict[str, Any]]] = dict(discovered or {})
        discovered_slugs = {
            str(market.get("market_slug"))
            for events in events_by_date.values()
            for event in events
            for market in event.get("markets", [])
        }
        for game_date, events in _fallback_events(root, held_in_window, discovered_slugs, status).items():
            events_by_date.setdefault(game_date, []).extend(events)
        status["held_in_window_slugs"] = len(held_in_window)
        status["held_in_window_discovered"] = len(set(held_in_window) & discovered_slugs)

        capture = capture_fn or capture_slate_snapshots
        per_date: dict[str, Any] = {}
        status["dates"] = per_date
        for game_date, events in sorted(events_by_date.items()):
            remaining = deadline - time.monotonic()
            if remaining < MIN_USEFUL_CAPTURE_SECONDS:
                per_date[game_date] = {"status": "skipped", "reason": "time_budget_exhausted"}
                continue
            try:
                result = capture(
                    client,
                    {LEAGUE: events},
                    root,
                    game_date,
                    horizon_hours=None,
                    ladder_keep={LEAGUE: LADDER_KEEP},
                    ladder_leagues=frozenset({LEAGUE}),
                    ladder_keep_slugs=keep_slugs,
                    priority_leagues=frozenset({LEAGUE}),
                    max_capture_seconds=remaining,
                )
            except Exception as exc:  # noqa: BLE001 -- one date's failure must not hide the other's
                status["errors"].append(
                    {
                        "kind": "polymarket_capture",
                        "date": game_date,
                        "error": f"{type(exc).__name__}: {exc}"[:160],
                    }
                )
                per_date[game_date] = {"status": "error"}
                continue
            summary = result if isinstance(result, dict) else {}
            captured = int(summary.get("captured") or 0)
            status["captured"] += captured
            per_date[game_date] = {
                "games": len(events),
                "listed_contracts": sum(len(event.get("markets", [])) for event in events),
                "queued_contracts": summary.get("primary_contracts"),
                "captured": captured,
                "failures": summary.get("failures"),
                "skipped_ladder_alternates": summary.get("skipped_ladder_alternates"),
                "skipped_after_time_budget": summary.get("skipped_after_time_budget"),
                "source_rate_limited": summary.get("source_rate_limited"),
                "rate_limit_pauses": summary.get("rate_limit_pauses"),
                "elapsed_seconds": summary.get("elapsed_seconds"),
            }
        if discovered is None and status["captured"] == 0:
            status["status"] = "error"  # the exchange was unreachable and nothing was saved
        elif status["errors"] or any(row.get("status") in ("error", "skipped") for row in per_date.values()):
            status["status"] = "partial"
        status["elapsed_seconds"] = round(time.monotonic() - started, 1)
        status["errors"] = status["errors"][:20]
        return status
    except Exception as exc:  # a capture must never take the worker down with a traceback
        _logger.warning("NCAAF pre-kickoff capture failed", exc_info=True)
        return {
            **status,
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}"[:200],
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
    finally:
        for http in own_http:
            http.close()


__all__ = ["DEFAULT_WINDOW_MINUTES", "capture_ncaaf_prekickoff"]
