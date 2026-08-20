"""Recover official WNBA injury PDFs for a bounded historical game window.

The files retain their embedded publication timestamps but are downloaded
retrospectively. They are suitable for diagnostic reconstruction, never for a
claim that the project observed the report before tipoff.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from model_prediction.data_sources.wnba_injuries import parse_report_pdf
from model_prediction.domain import parse_utc

BASE_URL = "https://ak-static.cms.nba.com/referee/wnba_injury"
EASTERN = ZoneInfo("America/New_York")
REPORT_OFFSETS_MINUTES = (30, 60)


def _scoreboard_events(data_root: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted((data_root / "raw/wnba").glob("*/scores_wnba.json")):
        sports_date = path.parent.name
        if not start_date <= sports_date <= end_date:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        events.extend(payload.get("events", []))
    return events


def _candidate_names(events: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for event in events:
        start = parse_utc(str(event["date"])).astimezone(EASTERN)
        for offset in REPORT_OFFSETS_MINUTES:
            report_at = start - timedelta(minutes=offset)
            names.add(report_at.strftime("Injury-Report_%Y-%m-%d_%I_%M%p.pdf"))
    return sorted(names)


def _download(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(url, headers={"User-Agent": "model-prediction-research/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = str(response.headers.get("Content-Type", ""))
                payload = response.read()
            if "pdf" not in content_type.casefold() and not payload.startswith(b"%PDF"):
                raise ValueError(f"unexpected response type {content_type!r}")
            return payload
        except urllib.error.HTTPError:
            raise
        except (http.client.IncompleteRead, TimeoutError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise OSError(f"download failed after 3 attempts: {last_error}")


def _recover_report(output_dir: Path, name: str, request_delay: float) -> dict[str, Any]:
    path = output_dir / name
    url = f"{BASE_URL}/{name}"
    try:
        existed = path.exists()
        payload = path.read_bytes() if existed else _download(url)
        parsed = parse_report_pdf(payload)
        if not existed:
            path.write_bytes(payload)
        record = {
            "name": name,
            "url": url,
            "status": "cached" if existed else "downloaded",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "report_at_utc": parsed.report_at_utc,
            "entries": len(parsed.entries),
        }
    except urllib.error.HTTPError as error:
        record = {"name": name, "url": url, "status": f"http_{error.code}"}
    except (OSError, ValueError) as error:
        record = {"name": name, "url": url, "status": "error", "error": str(error)}
    time.sleep(max(0.0, request_delay))
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", default="data/availability/wnba/expanded_reports")
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    output_dir = root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _scoreboard_events(root / "data", args.start_date, args.end_date)
    names = _candidate_names(events)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        records = list(
            pool.map(
                lambda name: _recover_report(output_dir, name, args.request_delay),
                names,
            )
        )

    manifest = {
        "schema_version": "1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "provenance_class": "retrospectively_retrieved_official_publication_timestamp",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "events": len(events),
        "candidate_urls": len(names),
        "reports_recovered": sum(record["status"] in {"downloaded", "cached"} for record in records),
        "records": records,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: manifest[key] for key in ("events", "candidate_urls", "reports_recovered")}, indent=2
        )
    )
    print(manifest_path)


if __name__ == "__main__":
    main()
