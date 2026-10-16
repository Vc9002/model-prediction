"""Timestamped real-time NFL player injury snapshots from ESPN's public injury feed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..domain import parse_utc, utc_now

ESPN_NFL_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

STATUS_MAP = {
    "Out": "Out",
    "Doubtful": "Doubtful",
    "Questionable": "Questionable",
    "Day-To-Day": "Questionable",
    "Injured Reserve": "IR",
    "IR": "IR",
    "Probable": "Probable",
}


def normalize_espn_nfl_injuries(raw_payload: Mapping[str, Any], *, observed_at: datetime) -> dict[str, Any]:
    import re

    observed = parse_utc(observed_at.isoformat())
    entries: list[dict[str, Any]] = []

    for team_group in raw_payload.get("injuries", []):
        tg_team = team_group.get("team", {})
        tg_name = str(team_group.get("displayName") or tg_team.get("displayName") or "")
        tg_abbr = str(tg_team.get("abbreviation") or team_group.get("abbreviation") or "")

        for item in team_group.get("injuries", []):
            athlete = item.get("athlete", {})
            player_name = str(athlete.get("displayName", ""))
            athlete_id = str(athlete.get("id") or "")
            if not athlete_id:
                for lk in athlete.get("links", []):
                    m = re.search(r"/id/(\d+)", lk.get("href", ""))
                    if m:
                        athlete_id = m.group(1)
                        break

            position = str(athlete.get("position", {}).get("abbreviation") or "")
            t_obj = athlete.get("team", {})
            team_name = str(t_obj.get("displayName") or tg_name)
            team_abbr = str(t_obj.get("abbreviation") or tg_abbr)

            type_obj = item.get("type", {})
            raw_type = str(type_obj.get("description") or type_obj.get("name") or "")
            type_abbr = str(type_obj.get("abbreviation") or "")

            status_obj = item.get("status")
            if isinstance(status_obj, dict):
                raw_status = str(status_obj.get("name") or status_obj.get("type") or raw_type)
            else:
                raw_status = str(status_obj or raw_type)

            status = STATUS_MAP.get(raw_status, STATUS_MAP.get(raw_type, raw_status))
            if type_abbr == "IR":
                status = "IR"
            elif type_abbr == "O":
                status = "Out"
            elif type_abbr == "D":
                status = "Doubtful"
            elif type_abbr == "Q":
                status = "Questionable"

            details = item.get("details", {})
            injury_type = str(details.get("type") or "Undisclosed")
            detail_note = str(details.get("detail") or "")
            return_date = details.get("returnDate")

            notes = athlete.get("notes", {}).get("items", [])
            latest_headline = notes[0].get("headline") if notes else None

            entries.append(
                {
                    "team": team_name,
                    "team_abbreviation": team_abbr,
                    "player_name": player_name,
                    "athlete_id": athlete_id,
                    "position": position,
                    "status": status,
                    "raw_status": raw_status,
                    "status_abbreviation": type_abbr,
                    "injury_type": injury_type,
                    "detail": detail_note,
                    "return_date": return_date,
                    "latest_headline": latest_headline,
                }
            )

    return {
        "schema_version": "1",
        "sport": "nfl",
        "source": "espn_nfl_injuries",
        "observed_at_utc": observed.isoformat(),
        "total_injuries": len(entries),
        "entries": entries,
    }


def capture_espn_nfl_injuries(
    data_root: str | Path,
    *,
    client: httpx.Client | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed = parse_utc((observed_at or utc_now()).isoformat())
    http_client = client or httpx.Client(timeout=30)
    response = http_client.get(ESPN_NFL_INJURIES_URL)
    response.raise_for_status()

    raw_data = response.json()
    payload = normalize_espn_nfl_injuries(raw_data, observed_at=observed)

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()

    stamp = observed.strftime("%Y%m%dT%H%M%S%z")
    path = (
        Path(data_root)
        / "availability/nfl/espn_injury_snapshots"
        / observed.date().isoformat()
        / f"nfl-injuries-{stamp}-{payload['snapshot_sha256'][:12]}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "status": "ok",
        "snapshot_path": str(path),
        "total_injuries": payload["total_injuries"],
        "observed_at_utc": payload["observed_at_utc"],
    }
