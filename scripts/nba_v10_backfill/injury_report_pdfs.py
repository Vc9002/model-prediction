"""Archive the official NBA injury report PDFs, 2021-22 onward (quick-261120-nbf).

URL: ak-static.cms.nba.com/referee/injury/Injury-Report_<YYYY-MM-DD>_<slot>.pdf where the
slot is the ET publish time: ``05PM`` (hourly naming, 2021-22 .. at least 2025-12) or
``05_15PM`` (15-minute naming, by 2026-05). The scheme is detected per day. The filename
time is the observed time (PIT), converted to UTC in the index.

A missing slot returns HTTP 403 -- so would a CDN block. After a run of misses on a day that
has reports, a known-good URL is re-fetched; if that also fails the day is left unfinished
(not recorded as a gap) and the run stops.

Writes under ``<root>/injury_reports/``: ``pdfs/<YYYY-MM>/<filename>.pdf`` (byte-identical
repeats of the day's previous PDF are not stored again; the index points at the original),
``pdf_index.jsonl`` and ``pdf_progress.json``. Resumable per date.

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe \
        scripts/nba_v10_backfill/injury_report_pdfs.py [--start 2021-10-01] [--end 2026-11-19]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from model_prediction.data_sources.nba_backfill_common import (
    ET,
    Fetcher,
    append_jsonl,
    read_json,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)

_logger = logging.getLogger("nba_v10.injury_pdfs")
BASE = "https://ak-static.cms.nba.com/referee/injury/"
KNOWN_GOOD = BASE + "Injury-Report_2024-03-23_11PM.pdf"
MISS_RUN_CHECK = 8


def hourly_slots() -> list[tuple[int, int, str]]:
    out = []
    for h24 in range(24):
        h12 = h24 % 12 or 12
        ap = "AM" if h24 < 12 else "PM"
        out.append((h24, 0, f"{h12:02d}{ap}"))
    return out


def quarter_slots() -> list[tuple[int, int, str]]:
    out = []
    for h24 in range(24):
        h12 = h24 % 12 or 12
        ap = "AM" if h24 < 12 else "PM"
        for m in (0, 15, 30, 45):
            out.append((h24, m, f"{h12:02d}_{m:02d}{ap}"))
    return out


def filename(d: date, slot: str) -> str:
    return f"Injury-Report_{d.isoformat()}_{slot}.pdf"


def observed_at_utc(d: date, h24: int, minute: int) -> str:
    local = datetime(d.year, d.month, d.day, h24, minute, tzinfo=ET)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_scheme(fetcher: Fetcher, d: date) -> str | None:
    """'quarter', 'hourly' or None (no reports that day)."""
    for slot in ("05_15PM", "01_15PM", "08_15PM"):
        status, _ = fetcher.get(BASE + filename(d, slot), head=True)
        if status == 200:
            return "quarter"
    for slot in ("05PM", "01PM", "08PM", "11AM"):
        status, _ = fetcher.get(BASE + filename(d, slot), head=True)
        if status == 200:
            return "hourly"
    return None


def cdn_ok(fetcher: Fetcher) -> bool:
    status, _ = fetcher.get(KNOWN_GOOD, head=True)
    return status == 200


def fetch_day(fetcher: Fetcher, root: Path, d: date) -> dict[str, Any] | None:
    scheme = detect_scheme(fetcher, d)
    if scheme is None:
        if not cdn_ok(fetcher):
            return None
        return {"scheme": None, "found": 0, "stored": 0}
    slots = quarter_slots() if scheme == "quarter" else hourly_slots()
    pdf_dir = root / "injury_reports" / "pdfs" / d.strftime("%Y-%m")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    last_sha: str | None = None
    last_path: str | None = None
    misses = 0
    found = stored = 0
    for h24, minute, slot in slots:
        name = filename(d, slot)
        url = BASE + name
        status, response = fetcher.get(url)
        row: dict[str, Any] = {
            "filename": name,
            "url": url,
            "report_date": d.isoformat(),
            "slot_et": f"{h24:02d}:{minute:02d}",
            "observed_at_utc": observed_at_utc(d, h24, minute),
            "scheme": scheme,
            "http_status": status,
            "fetched_at_utc": utc_now_iso(),
        }
        if status == 200 and response is not None and response.content[:4] == b"%PDF":
            body = response.content
            sha = hashlib.sha256(body).hexdigest()
            row.update(bytes=len(body), sha256=sha)
            if sha == last_sha:
                row["stored_path"] = last_path
                row["duplicate_of_previous"] = True
            else:
                path = pdf_dir / name
                path.write_bytes(body)
                last_sha, last_path = sha, str(path.relative_to(root)).replace("\\", "/")
                row["stored_path"] = last_path
                stored += 1
            found += 1
            misses = 0
        else:
            if status is None:
                return None
            misses += 1
            if misses == MISS_RUN_CHECK and not cdn_ok(fetcher):
                return None
        rows.append(row)
    append_jsonl(root / "injury_reports" / "pdf_index.jsonl", rows)
    return {"scheme": scheme, "found": found, "stored": stored}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--start", default="2021-10-01")
    parser.add_argument("--end", default="2026-11-19")
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root = resolve_out_root(args.out_root)
    progress_path = root / "injury_reports" / "pdf_progress.json"
    progress = read_json(progress_path, {"days": {}})
    fetcher = Fetcher(args.interval, max_retries=5)
    cur, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while cur <= end:
        key = cur.isoformat()
        if key not in progress["days"]:
            result = fetch_day(fetcher, root, cur)
            if result is None:
                _logger.error("CDN check failed on %s; stopping (resume later)", key)
                break
            result["fetched_at_utc"] = utc_now_iso()
            progress["days"][key] = result
            progress["updated_at_utc"] = utc_now_iso()
            write_json_atomic(progress_path, progress)
            _logger.info(
                "%s scheme=%s found=%d stored=%d req=%d",
                key,
                result["scheme"],
                result["found"],
                result["stored"],
                fetcher.requests,
            )
        cur += timedelta(days=1)
    _logger.info("stopped at %s; requests=%d retries=%d", cur, fetcher.requests, fetcher.retries)


if __name__ == "__main__":
    main()
