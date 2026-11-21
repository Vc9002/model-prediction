"""Live capture of the official NBA injury report PDFs (NBA v10 forward track, T-C2).

The league publishes a report every 15 minutes (``Injury-Report_<YYYY-MM-DD>_<hh>_<mm><AM|PM>.pdf``,
the slot in Eastern time; 15-minute names since 2025-12-22). Each run of the pre-tip worker probes
the slots of the last ``LOOKBACK`` that are not stored yet, newest first, at most ``MAX_PROBES``
requests, and stores every new report: the raw PDF, one ``reports.jsonl`` row and its parsed rows
in ``rows.jsonl``, under ``<runtime root>/injury_reports/nba/<ET report date>/``.

Two times per report, both stored:
- ``published_at_utc``: the later of the filename slot and the PDF's own header time (the content
  is as of the header time; the backfill parser uses the same rule). It dates an event.
- ``fetched_at_utc``: stamped after our response returned. A decision may use a report only from
  this time on; it is when WE knew it.

A missing slot answers HTTP 403 (also "not published yet"), so an unstored slot inside the
lookback is probed again on the next run. Fail-soft: every error is counted in the summary, and
nothing here raises out of ``capture_injury_reports``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import EASTERN
from .nba_injury_report import parse_report, row_is_consistent

_logger = logging.getLogger(__name__)

BASE_URL = "https://ak-static.cms.nba.com/referee/injury/"
SLOT = timedelta(minutes=15)
LOOKBACK = timedelta(hours=2)
MAX_PROBES = 8
REQUEST_TIMEOUT_SECONDS = 10.0
REPORTS_FILENAME = "reports.jsonl"
ROWS_FILENAME = "rows.jsonl"

HttpGet = Callable[[str], tuple[int, bytes]]
Clock = Callable[[], datetime]


def injury_store_root(runtime_root: str | Path) -> Path:
    return Path(runtime_root) / "injury_reports" / "nba"


def slot_filename(slot_et: datetime) -> str:
    hour12 = slot_et.hour % 12 or 12
    meridiem = "AM" if slot_et.hour < 12 else "PM"
    return f"Injury-Report_{slot_et.date().isoformat()}_{hour12:02d}_{slot_et.minute:02d}{meridiem}.pdf"


def floor_slot(moment: datetime) -> datetime:
    """The latest 15-minute Eastern slot at or before ``moment`` (aware, Eastern)."""
    local = moment.astimezone(EASTERN)
    return local.replace(minute=local.minute - local.minute % 15, second=0, microsecond=0)


def header_time_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        local = datetime.strptime(text, "%m/%d/%y %I:%M %p").replace(tzinfo=EASTERN)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _default_http_get(url: str) -> tuple[int, bytes]:
    import httpx

    response = httpx.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
    )
    return response.status_code, response.content


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
    return out


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def load_reports(store_root: Path, dates: list[str]) -> list[dict[str, Any]]:
    """Stored report rows of the given ET report dates (ingest-ordered: callers max-scan)."""
    out: list[dict[str, Any]] = []
    for day in dates:
        out.extend(_read_jsonl(store_root / day / REPORTS_FILENAME))
    return out


def load_rows(store_root: Path, dates: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in dates:
        out.extend(_read_jsonl(store_root / day / ROWS_FILENAME))
    return out


def capture_injury_reports(
    store_root: str | Path,
    *,
    now: Clock | None = None,
    http_get: HttpGet | None = None,
    deadline: float | None = None,
    max_probes: int = MAX_PROBES,
) -> dict[str, Any]:
    """Fetch and store every unstored report slot of the last ``LOOKBACK``. Never raises.

    ``deadline`` is a ``time.monotonic()`` value; no new request starts after it.
    """
    try:
        return _capture(
            Path(store_root),
            clock=now or (lambda: datetime.now(UTC)),
            http_get=http_get or _default_http_get,
            deadline=deadline,
            max_probes=max_probes,
        )
    except Exception as exc:  # a research capture must never break its caller
        _logger.warning("NBA injury report capture failed", exc_info=True)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200], "stored": 0}


def _capture(
    store_root: Path, *, clock: Clock, http_get: HttpGet, deadline: float | None, max_probes: int
) -> dict[str, Any]:
    started = clock()
    newest = floor_slot(started)
    oldest = floor_slot(started - LOOKBACK)
    dates = sorted({newest.date().isoformat(), oldest.date().isoformat()})
    stored_names = {str(r.get("filename")) for r in load_reports(store_root, dates)}
    known_sha = {
        str(r.get("sha256")): str(r.get("stored_path"))
        for r in load_reports(store_root, dates)
        if r.get("sha256") and r.get("stored_path")
    }
    slots = []
    slot = newest
    while slot >= oldest:
        if slot_filename(slot) not in stored_names:
            slots.append(slot)
        slot = (slot.astimezone(UTC) - SLOT).astimezone(EASTERN)
    summary: dict[str, Any] = {
        "status": "ok",
        "newest_slot_et": newest.isoformat(),
        "unstored_slots": len(slots),
        "probes": 0,
        "not_published": 0,
        "stored": 0,
        "reports": [],
        "errors": [],
    }
    for slot in slots[:max_probes]:
        if deadline is not None and time.monotonic() >= deadline:
            summary["status"] = "partial"
            summary["errors"].append({"kind": "deadline", "slot": slot.isoformat()})
            break
        name = slot_filename(slot)
        summary["probes"] += 1
        try:
            status, body = http_get(BASE_URL + name)
        except Exception as exc:  # noqa: BLE001 -- one slot's network error must not stop the rest
            summary["errors"].append({"kind": "http", "slot": name, "error": f"{type(exc).__name__}"[:80]})
            continue
        fetched = clock()  # stamped after the response returned: when we knew it
        if status in (403, 404):
            summary["not_published"] += 1
            continue
        if status != 200 or body[:4] != b"%PDF":
            summary["errors"].append({"kind": "http", "slot": name, "error": f"HTTP {status}"})
            continue
        try:
            row = _store_report(store_root, slot, name, body, fetched, known_sha)
        except OSError as exc:
            summary["errors"].append({"kind": "write", "slot": name, "error": f"{type(exc).__name__}"[:80]})
            continue
        known_sha.setdefault(row["sha256"], row["stored_path"])
        summary["stored"] += 1
        summary["reports"].append(
            {
                "filename": name,
                "published_at_utc": row["published_at_utc"],
                "fetched_at_utc": row["fetched_at_utc"],
                "n_rows": row["n_rows"],
                "parse_error": row["parse_error"],
            }
        )
    if summary["errors"] and summary["status"] == "ok":
        summary["status"] = "partial"
    summary["errors"] = summary["errors"][:10]
    return summary


def _store_report(
    store_root: Path,
    slot: datetime,
    name: str,
    body: bytes,
    fetched: datetime,
    known_sha: dict[str, str],
) -> dict[str, Any]:
    day = slot.date().isoformat()
    day_dir = store_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(body).hexdigest()
    stored_path = known_sha.get(sha)
    duplicate = stored_path is not None
    if not duplicate:
        stored_path = f"{day}/{name}"
        (store_root / stored_path).write_bytes(body)
    filename_time = slot.astimezone(UTC)
    header_text: str | None = None
    parse_error: str | None = None
    rows: list[dict[str, Any]] = []
    n_unplaced = 0
    n_pages = 0
    try:
        parsed = parse_report(body)
        header_text = parsed.header_time_text
        rows = parsed.rows
        n_unplaced = len(parsed.unplaced)
        n_pages = parsed.n_pages
    except Exception as exc:  # noqa: BLE001 -- a malformed PDF is recorded, not fatal
        parse_error = f"{type(exc).__name__}: {exc}"[:200]
    header = header_time_utc(header_text)
    published = max(filename_time, header) if header is not None else filename_time
    report = {
        "schema_version": "1",
        "filename": name,
        "url": BASE_URL + name,
        "report_date": day,
        "slot_et": slot.strftime("%H:%M"),
        "filename_time_utc": _iso(filename_time),
        "header_time_et": header_text,
        "header_time_utc": None if header is None else _iso(header),
        "published_at_utc": _iso(published),
        "fetched_at_utc": _iso(fetched),
        "sha256": sha,
        "bytes": len(body),
        "stored_path": stored_path,
        "duplicate_of_stored": duplicate,
        "n_pages": n_pages,
        "n_rows": len(rows),
        "n_unplaced": n_unplaced,
        "n_inconsistent": sum(1 for r in rows if not row_is_consistent(r)),
        "parse_error": parse_error,
    }
    # Rows first, then the report row: a reader keys on reports.jsonl, so a run killed in between
    # leaves orphan rows (ignored) rather than a report without rows.
    _append_jsonl(
        day_dir / ROWS_FILENAME,
        [
            {
                **r,
                "report_filename": name,
                "report_sha256": sha,
                "published_at_utc": report["published_at_utc"],
                "fetched_at_utc": report["fetched_at_utc"],
                "consistent": row_is_consistent(r),
            }
            for r in rows
        ],
    )
    _append_jsonl(day_dir / REPORTS_FILENAME, [report])
    return report


__all__ = [
    "BASE_URL",
    "capture_injury_reports",
    "floor_slot",
    "header_time_utc",
    "injury_store_root",
    "load_reports",
    "load_rows",
    "slot_filename",
]
