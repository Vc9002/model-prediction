"""Pre-tip close capture for open ledger rows, run from the 15-minute buyer worker.

Settlement's ``PolymarketSnapshotStore.closing_snapshot`` takes the last snapshot strictly
before tip (and, for NCAAB, strictly after the decision). NCAAB snapshots were only written by
the Daily runs, so the surviving row's "close" was its own decision quote: 339 of 339 settled
NCAAB rows recorded a fake 0 CLV (2026-11-20). This snapshots, shortly before tip, the exact
contract every open row of the sport was priced on, so the close is 0-15 minutes before tip and
after every decision.

Market data only, never an order. Best effort: the function never raises, because accounting
must never fail or delay the buyer cycle beyond its bounded time budget.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .domain import parse_utc, utc_now

_logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
DEFAULT_WINDOW_MINUTES = 25.0  # one 15-minute cycle plus slack, so every game is seen once
MIN_LEAD_SECONDS = 60.0  # a game tipping inside a minute would be captured after tip
DEFAULT_MAX_CAPTURE_SECONDS = 120.0  # the worker holds daily.lock; keep inside its 300 s budget
MIN_USEFUL_CAPTURE_SECONDS = 5.0
# Tighter than the Daily's client (30 s x 6 attempts), so one in-flight request can overrun the
# deadline by at most about timeout x attempts plus a short backoff.
CLIENT_TIMEOUT_SECONDS = 10.0
CLIENT_MAX_ATTEMPTS = 2
CAPTURE_WORKERS = 2  # a few dozen contracts at most; the capture's pacer spaces the requests

# Same slug sources, in the same order, as settlement's ``cli.settle._extract_market_slug``:
# an explicit market_slug, the decision snapshot's research_quote_join slug, then rationale text.
# Each JSON level is guarded with json_valid inside nested CASEs (CASE is evaluated lazily), so
# one malformed payload cannot abort the whole query.
_OPEN_ROWS_SQL = """
SELECT event_start_utc,
       CASE WHEN json_valid(decision_payload_json)
            THEN json_extract(decision_payload_json, '$.market_slug') END AS explicit_slug,
       CASE WHEN json_valid(decision_payload_json)
            THEN CASE WHEN json_valid(json_extract(decision_payload_json, '$.model_input_snapshot_json'))
                      THEN json_extract(
                          json_extract(decision_payload_json, '$.model_input_snapshot_json'),
                          '$.features."research_quote_join.market_slug"')
                 END
       END AS snapshot_slug,
       CASE WHEN json_valid(decision_payload_json)
            THEN json_extract(decision_payload_json, '$.rationale') END AS rationale
FROM ledger_records
WHERE lower(sport) = ? AND status = 'open'
"""


def _row_slug(explicit: Any, snapshot: Any, rationale: Any) -> str | None:
    for candidate in (explicit, snapshot):
        if isinstance(candidate, str) and "-" in candidate.strip():
            return candidate.strip()
    text = str(rationale or "")
    match = re.search(r"market_slug=([a-z0-9_\-]+)", text)
    if match:
        return match.group(1)
    candidates = re.findall(r"\(([a-z0-9_\-]+)\)", text)
    # Contract-prefixed tokens first, as settlement does ("(anchor-blended)" in NCAAF v6.2 text).
    for found in candidates:
        if found.startswith(("atc", "tsc", "asc", "mkt", "aec")):
            return found
    for found in candidates:
        if "-" in found:
            return found
    return None


def _wanted_slugs_by_date(
    ledgers_db: Path, sport: str, now: datetime, window_minutes: float
) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    """Slugs of open rows tipping in (now + 60 s, now + window], keyed by ET game date.

    Each slug maps to its row's ``event_start_utc`` (a fallback when the archived record has none).
    """
    counts = {"open_rows": 0, "in_window_rows": 0, "rows_without_slug": 0}
    earliest = now + timedelta(seconds=MIN_LEAD_SECONDS)
    latest = now + timedelta(minutes=window_minutes)
    conn = sqlite3.connect(f"{ledgers_db.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(_OPEN_ROWS_SQL, (sport.lower(),)).fetchall()
    finally:
        conn.close()
    by_date: dict[str, dict[str, str]] = {}
    for start_raw, explicit, snapshot, rationale in rows:
        counts["open_rows"] += 1
        try:
            start = parse_utc(str(start_raw or ""))
        except (TypeError, ValueError):
            continue
        if not earliest < start <= latest:
            continue
        counts["in_window_rows"] += 1
        slug = _row_slug(explicit, snapshot, rationale)
        if slug is None:
            counts["rows_without_slug"] += 1
            continue
        by_date.setdefault(start.astimezone(EASTERN).date().isoformat(), {})[slug] = str(start_raw)
    return by_date, counts


def _latest_archived(path: Path, slugs: set[str]) -> dict[str, dict[str, Any]]:
    """Newest archived record per wanted slug (a max-scan: the archive is ingest-ordered)."""
    found: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return found
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not any(slug in line for slug in slugs):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line the Daily is still appending
            slug = item.get("market_slug")
            if slug not in slugs:
                continue
            previous = found.get(slug)
            if previous is None or str(item.get("observed_at_utc") or "") >= str(
                previous.get("observed_at_utc") or ""
            ):
                found[slug] = item
    return found


def _contracts_from_archive(
    archived: dict[str, dict[str, Any]], row_starts: dict[str, str], sport: str
) -> dict[str, list[dict[str, Any]]]:
    """Minimal slate-shaped events for ``capture_slate_snapshots``, from each slug's archived record.

    The capture stamps every written row's market_type, line and team from the market dict, and
    its event_id/event_slug/title from the event dict, so all of them are copied from the Daily's
    own record: settlement resolves a spread close's side through ``team`` and matches the rung on
    ``line``. No ``market_id`` is given, so each contract is fetched with ``client.snapshot(slug)``.
    """
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for slug, record in sorted(archived.items()):
        league = str(record.get("league") or sport.upper())
        event_id = str(record.get("event_id") or "")
        event = events.setdefault(
            (league, event_id),
            {
                "event_id": event_id,
                "event_slug": str(record.get("event_slug") or ""),
                "title": str(record.get("event_title") or ""),
                "event_start_utc": record.get("event_start_utc") or row_starts.get(slug),
                "markets": [],
            },
        )
        event["markets"].append(
            {
                "market_slug": slug,
                "market_type": record.get("market_type"),
                "line": record.get("line"),
                "sides": [{"team": record["team"]}] if record.get("team") else [],
            }
        )
    wanted: dict[str, list[dict[str, Any]]] = {}
    for (league, _event_id), event in events.items():
        wanted.setdefault(league, []).append(event)
    return wanted


class _SnapshotOnlyClient:
    """Exposes only ``snapshot``, so the capture skips its event-detail pass (one more GET per event)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def snapshot(self, slug: str) -> dict[str, Any]:
        return self._client.snapshot(slug)


def _capture_dates(
    client_factory: Callable[[], Any],
    capture: Callable[..., dict[str, Any]],
    by_date: dict[str, dict[str, str]],
    sport: str,
    runtime_root: Path,
    deadline: float,
    status: dict[str, Any],
) -> None:
    """Per ET game date: map wanted slugs through the archive, then capture them inside the deadline."""
    from .data_sources.polymarket_us import PolymarketSnapshotStore

    per_date: dict[str, Any] = {}
    status["dates"] = per_date
    status["contracts"] = 0
    market_client: Any = None
    for game_date, row_starts in sorted(by_date.items()):
        slugs = set(row_starts)
        path = PolymarketSnapshotStore.for_sport_date(runtime_root, sport.lower(), game_date).path
        archived = _latest_archived(path, slugs)
        entry: dict[str, Any] = {"wanted": len(slugs), "contracts": len(archived)}
        per_date[game_date] = entry
        if len(archived) < len(slugs):
            # Never guess a contract's line or anchor team: an unmapped slug gets no close.
            entry["no_archived_record"] = sorted(slugs - set(archived))
        if not archived:
            entry["status"] = "no_archived_record"
            continue
        if deadline - time.monotonic() < MIN_USEFUL_CAPTURE_SECONDS:
            entry.update(status="skipped", reason="time_budget_exhausted")
            continue
        if market_client is None:
            market_client = _SnapshotOnlyClient(client_factory())
        remaining = deadline - time.monotonic()
        if remaining < MIN_USEFUL_CAPTURE_SECONDS:
            entry.update(status="skipped", reason="time_budget_exhausted")
            continue
        status["contracts"] += len(archived)
        result = capture(
            market_client,
            _contracts_from_archive(archived, row_starts, sport),
            runtime_root,
            game_date,
            max_capture_seconds=remaining,
            max_workers=CAPTURE_WORKERS,
        )
        captured = int((result or {}).get("captured") or 0)
        status["captured"] += captured
        entry["captured"] = captured
        failures = int((result or {}).get("failures") or 0)
        if failures:
            entry["failures"] = failures


def capture_pretip_closes(
    *,
    now: datetime | None = None,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    sport: str = "ncaab",
    ledgers_db: Path | None = None,
    runtime_root: Path | None = None,
    client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
    max_capture_seconds: float = DEFAULT_MAX_CAPTURE_SECONDS,
) -> dict[str, Any]:
    """Snapshot, shortly before tip, every market an open ledger row of ``sport`` was priced on.

    Reads the ledger read-only, maps each wanted slug through its newest record in the runtime
    root's snapshot archive (the Daily's discovery), and captures only those contracts through
    ``capture_slate_snapshots`` into the same archive, which settlement reads: about two requests
    (market + book) per wanted slug, with no slate or event-detail crawl. The client is built only
    when a mapped contract is in the window, so an idle cycle makes no exchange request.
    ``max_capture_seconds`` is one wall-clock deadline over everything, client construction and
    every request included. Returns a status dict; never raises.
    """
    started = time.monotonic()
    deadline = started + max(0.0, max_capture_seconds)
    status: dict[str, Any] = {"status": "ok", "sport": sport.lower(), "captured": 0}
    try:
        if ledgers_db is None or runtime_root is None:
            from .runtime_paths import RuntimePaths

            paths = RuntimePaths.resolve()
            ledgers_db = ledgers_db or paths.ledgers_db
            runtime_root = runtime_root or paths.runtime_root
        if not Path(ledgers_db).exists():
            return {**status, "status": "skipped", "reason": "no_ledger_db"}
        moment = now or utc_now()
        by_date, counts = _wanted_slugs_by_date(Path(ledgers_db), sport, moment, window_minutes)
        status.update(counts)
        status["wanted_slugs"] = sum(len(slugs) for slugs in by_date.values())
        if not by_date:
            return {**status, "status": "idle"}

        from .data_sources.polymarket_us import PolymarketUSClient, capture_slate_snapshots

        own_http: list[httpx.Client] = []

        def _client_factory() -> Any:
            if client is not None:
                return client
            http = httpx.Client(timeout=CLIENT_TIMEOUT_SECONDS)
            own_http.append(http)
            return PolymarketUSClient(client=http, max_attempts=CLIENT_MAX_ATTEMPTS)

        try:
            _capture_dates(
                _client_factory,
                capture_fn or capture_slate_snapshots,
                by_date,
                sport,
                Path(runtime_root),
                deadline,
                status,
            )
        finally:
            for http in own_http:
                http.close()
        status["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return status
    except Exception as exc:  # accounting capture must never fail the buyer cycle
        _logger.warning("pre-tip close capture failed for %s", sport, exc_info=True)
        return {
            **status,
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}"[:200],
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
