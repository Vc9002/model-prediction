"""Timestamped WNBA player-status snapshots from ESPN event summaries."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..domain import parse_utc, utc_now


STATUS_MAP = {
    "Out": "Out",
    "Doubtful": "Doubtful",
    "Questionable": "Questionable",
    "Day-To-Day": "Questionable",
    "Probable": "Probable",
}


def normalize_espn_event_injuries(
    summary: Mapping[str, Any], *, event_id: str, observed_at: datetime
) -> dict[str, Any]:
    observed = parse_utc(observed_at.isoformat())
    entries: list[dict[str, Any]] = []
    for team_group in summary.get("injuries", []):
        team = str(team_group.get("team", {}).get("displayName", ""))
        for item in team_group.get("injuries", []):
            raw_status = str(item.get("status", ""))
            status = STATUS_MAP.get(raw_status)
            player = str(item.get("athlete", {}).get("displayName", ""))
            status_at_raw = item.get("date")
            if not status or not team or not player or not status_at_raw:
                continue
            status_at = parse_utc(str(status_at_raw))
            if status_at > observed:
                continue
            details = item.get("details", {})
            entries.append(
                {
                    "team": team,
                    "player_name": player,
                    "current_status": status,
                    "raw_status": raw_status,
                    "status_at_utc": status_at.isoformat(),
                    "reason": str(details.get("type") or "Undisclosed"),
                    "return_date": details.get("returnDate"),
                    "athlete_id": str(item.get("athlete", {}).get("id", "")),
                }
            )
    return {
        "schema_version": "1",
        "sport": "wnba",
        "source": "espn_event_injuries",
        "event_id": str(event_id),
        "observed_at_utc": observed.isoformat(),
        "entries": entries,
    }


def capture_espn_event_injuries(
    data_root: str | Path,
    *,
    event_id: str,
    client: Any,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed = parse_utc((observed_at or utc_now()).isoformat())
    summary = client.summary("WNBA", event_id)
    payload = normalize_espn_event_injuries(
        summary, event_id=event_id, observed_at=observed
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
    stamp = observed.strftime("%Y%m%dT%H%M%S%z")
    path = (
        Path(data_root)
        / "availability/wnba/espn_event_snapshots"
        / observed.date().isoformat()
        / f"{event_id}-{stamp}-{payload['snapshot_sha256'][:12]}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "snapshot_path": str(path), "entry_count": len(payload["entries"])}
