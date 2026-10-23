"""Prospective, exact-identity BO3 schedule context; never historical backfill."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .domain import parse_utc, utc_now

MAX_PAGES = 4
PAGE_SIZE = 100
TIME_BUDGET_SECONDS = 20


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def capture_schedule(
    data_root: str | Path, title: str, earliest_start: datetime, client: Any = None
) -> dict[str, Any]:
    """Capture at most four descending pages, stopping once target coverage is proven.

    Existing client parameters only. Every completed response is archived with
    its actual fetch-completion timestamp; no upstream publication time is known.
    An incomplete bounded scan cannot establish unique event identity.
    """
    from .esports import TITLE_SPECS, Bo3EsportsClient

    discipline = int(TITLE_SPECS[title]["discipline_id"])
    schedule: dict[str, Any] = {
        "schema": "esports-prospective-schedule-v1",
        "title": title,
        "discipline_id": discipline,
        "rows": [],
        "pages": [],
        "status": "pagination_incomplete",
        "historical_availability_verified": False,
    }
    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    transport = None
    if client is None:
        transport = httpx.Client(timeout=4, headers={"User-Agent": "model-prediction-research/1.0"})
        client = Bo3EsportsClient(client=transport)
    previous_start = None
    try:
        for page in range(MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            query = {
                "page[limit]": PAGE_SIZE,
                "page[offset]": page * PAGE_SIZE,
                "filter[matches.discipline_id][eq]": discipline,
                "sort": "-start_date",
            }
            try:
                raw = client._get("/matches", query)
                observed_at = utc_now()
                capture = {
                    "schema": schedule["schema"],
                    "title": title,
                    "path": "/matches",
                    "query": query,
                    "observed_at_utc": observed_at.isoformat(),
                    "response": raw,
                    "historical_availability_verified": False,
                }
                digest = _digest(capture)
                archive = (
                    Path(data_root)
                    / "esports"
                    / "context"
                    / title
                    / observed_at.date().isoformat()
                    / f"{digest}.json"
                )
                archive.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation: never overwrite an observation or history.
                try:
                    with archive.open("x", encoding="utf-8") as handle:
                        json.dump(capture, handle, sort_keys=True, allow_nan=False)
                except FileExistsError:
                    if _digest(json.loads(archive.read_text(encoding="utf-8"))) != digest:
                        raise ValueError("context_archive_hash_collision") from None
                provenance = {
                    "capture_path": str(archive),
                    "capture_sha256": digest,
                    "query_sha256": _digest(query),
                    "response_sha256": _digest(raw),
                    "observed_at_utc": observed_at.isoformat(),
                }
                schedule["pages"].append(provenance)
                rows = raw["results"]
                if not isinstance(rows, list):
                    raise TypeError("invalid_schedule_rows")
                for row in rows:
                    start = parse_utc(str(row["start_date"]))
                    if previous_start is not None and start > previous_start:
                        raise ValueError("schedule_not_descending")
                    previous_start = start
                    schedule["rows"].append({"row": row, "provenance": provenance})
                if time.monotonic() >= deadline:
                    break
                if len(rows) < PAGE_SIZE or (previous_start is not None and previous_start < earliest_start):
                    schedule["status"] = "complete"
                    break
            except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as error:
                schedule["status"] = "source_unavailable"
                schedule["detail"] = f"{type(error).__name__}: {error}"[:200]
                break
    finally:
        if transport is not None:
            transport.close()
    return schedule


def resolve_format(
    schedule: dict[str, Any], team_ids: list[str], event_start: datetime, decision_time: datetime
) -> dict[str, Any]:
    """Bind a unique upcoming row by exact source IDs and exact UTC start only."""
    result: dict[str, Any] = {
        "status": schedule["status"],
        "best_of": None,
        "historical_availability_verified": False,
    }
    if schedule["status"] != "complete":
        return result
    if any(parse_utc(page["observed_at_utc"]) > decision_time for page in schedule["pages"]):
        return result | {"status": "source_observed_after_decision"}
    discipline = schedule["discipline_id"]
    if (
        len(team_ids) != 2
        or len(set(team_ids)) != 2
        or any(re.fullmatch(rf"bo3:{discipline}:[1-9][0-9]*", value) is None for value in team_ids)
    ):
        return result | {"status": "source_identity_unresolved"}
    expected = set(team_ids)
    candidates = []
    for item in schedule["rows"]:
        row = item["row"]
        try:
            if parse_utc(str(row["start_date"])) != event_start:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        ids = {f"bo3:{discipline}:{row.get('team1_id')}", f"bo3:{discipline}:{row.get('team2_id')}"}
        if ids == expected:
            candidates.append(item)
    if len(candidates) != 1:
        return result | {"status": "source_match_missing" if not candidates else "source_match_ambiguous"}
    item = candidates[0]
    row = item["row"]
    provenance = item["provenance"]
    result |= {
        "source_match_id": row.get("id"),
        "source_team_ids": sorted(expected),
        "event_start_utc": event_start.isoformat(),
        "provenance": provenance,
        "source_record_sha256": _digest(row),
    }
    if parse_utc(provenance["observed_at_utc"]) > decision_time:
        return result | {"status": "source_observed_after_decision"}
    if event_start <= decision_time or row.get("status") != "upcoming" or row.get("end_date") is not None:
        return result | {"status": "source_not_upcoming"}
    if type(row.get("id")) is not int or row["id"] <= 0:
        return result | {"status": "source_match_identity_missing"}
    bo = row.get("bo_type")
    if type(bo) is not int or bo not in {1, 3, 5}:
        return result | {"status": "source_format_unsupported"}
    return result | {"status": "matched", "best_of": bo}
