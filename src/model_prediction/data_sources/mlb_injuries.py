"""Point-in-time capture of MLB official roster status and IL transaction
history, from the free, keyless MLB Stats API.

Two endpoints, two different point-in-time properties:

- ``/v1/teams/{teamId}/roster?rosterType=40Man`` is a LIVE-ONLY read: it
  reports each player's *current* status (``Active``, ``Injured 15-Day``,
  etc.) as of whenever it's called. It cannot be queried for a past date --
  there is no way to ask "what did this roster look like on 2026-06-01."
  Useful only as a same-day freshness cross-check at live forecast time.

- ``/v1/transactions?teamId=&startDate=&endDate=`` is genuinely
  point-in-time queryable: it returns the full dated history of IL
  placements/activations for any date range, past or present, including
  real injury descriptions. This is the primary source this module relies
  on, since the SAME retrospective query works identically whether it's
  being used to build live forecast features today or to reconstruct
  historical training data for a game from weeks ago.

CRITICAL: every transaction row has both a ``date`` (when MLB reported the
move) and an ``effectiveDate`` (sometimes retroactively backdated -- e.g. a
15-day IL placement "retroactive to" a date a week earlier). Only ``date``
may ever be used to decide what a decision made at a given time could have
known. ``effective_date`` is captured for reference only and must never
feed a point-in-time filter -- using it would let a retroactively-announced
move leak future knowledge into a prediction made before the move was
actually reported.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..domain import parse_utc, utc_now

ROSTER_URL_TEMPLATE = "https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
TRANSACTIONS_URL = "https://statsapi.mlb.com/api/v1/transactions"
SNAPSHOT_SCHEMA_VERSION = "1"
EASTERN = ZoneInfo("America/New_York")

# Real MLB Stats API roster/transaction status descriptions, observed live
# 2026-08-01 against the 40Man roster endpoint and a real season's worth of
# transactions. Anything not in this map fails closed rather than guessing.
STATUS_ACTIVE_PROBABILITIES: dict[str, float] = {
    "Active": 1.0,
    "Bereavement List": 0.0,
    "Paternity List": 0.0,
    "Restricted List": 0.0,
    "Reassigned to Minors": 0.0,
    "Injured 7-Day": 0.0,
    "Injured 10-Day": 0.0,
    "Injured 15-Day": 0.0,
    "Injured 60-Day": 0.0,
    "Suspended List": 0.0,
}
# Subset of the above that reflects genuine injury/administrative-list
# unavailability -- deliberately EXCLUDES "Reassigned to Minors". A named
# probable starter reassigned to AAA is definitely not starting today's
# game (STATUS_ACTIVE_PROBABILITIES is correct for that per-player check),
# but for an AGGREGATE pitching-staff-health signal, routine 40-man-vs-
# 26-man roster depth movement is not a meaningful health signal -- every
# team has pitchers optioned to AAA on any given day regardless of injury
# status, so including it would swamp the real signal (actual injuries)
# with structural noise present for every team, every day.
INJURY_OR_ADMIN_LIST_STATUSES = frozenset(
    status for status in STATUS_ACTIVE_PROBABILITIES if status not in ("Active", "Reassigned to Minors")
)
# Real MLB Stats API transactions almost never carry a discriminative
# typeDesc for IL moves -- both a placement and an activation are typically
# typeDesc "Status Change". The actual detail lives in the free-text
# `description` field (e.g. "placed RF Aaron Judge on the 10-day injured
# list", "activated LHP ... from the 15-day injured list"; live-confirmed
# 2026-08-01), so these markers are matched against `description`, not
# `type_desc`.
UNAVAILABLE_TRANSACTION_MARKERS = (
    "injured list",
    "bereavement list",
    "paternity list",
    "restricted list",
    "suspended",
    # "sent RHP X on a rehab assignment to Y" -- real, common (291 distinct
    # instances live-confirmed 2026-08-01), and means the player is still
    # recovering, not yet activated. Without this marker, a player whose
    # original IL placement falls outside the transactions capture window
    # (see cli.py's 60-day lookback) but who has a recent, in-window rehab-
    # assignment transaction would silently default to Active -- the exact
    # failure mode this feature exists to catch. "Activated" is still
    # checked first in _starter_status, so a genuine return from rehab
    # (typically its own "activated from the X-day injured list"
    # transaction) still correctly overrides this.
    "rehab assignment",
)
# Matched first: an activation description also mentions the list it's
# activating the player FROM (e.g. "from the 10-day injured list"), which
# would otherwise also match an unavailable marker above.
AVAILABLE_TRANSACTION_MARKERS = ("activated",)

# Static id <-> full team name map (statsapi.mlb.com/api/v1/teams?sportId=1,
# live-confirmed 2026-08-01). MLB's 30 franchises and numeric ids are stable
# for the length of a season (unlike WNBA's roster, this needs no prospective
# build), and these names match the "City Name" strings already used
# elsewhere in this project (ESPN displayName, e.g. "New York Yankees").
MLB_TEAM_IDS: dict[str, int] = {
    "Los Angeles Angels": 108,
    "Arizona Diamondbacks": 109,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Dodgers": 119,
    "Washington Nationals": 120,
    "New York Mets": 121,
    "Athletics": 133,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "Seattle Mariners": 136,
    "San Francisco Giants": 137,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Minnesota Twins": 142,
    "Philadelphia Phillies": 143,
    "Atlanta Braves": 144,
    "Chicago White Sox": 145,
    "Miami Marlins": 146,
    "New York Yankees": 147,
    "Milwaukee Brewers": 158,
}


def _team_identity(value: str) -> str:
    return " ".join(value.replace("-", " ").casefold().split())


_TEAM_ID_BY_IDENTITY: dict[str, int] = {_team_identity(name): team_id for name, team_id in MLB_TEAM_IDS.items()}


def team_id_for_name(team_name: str) -> int:
    """Resolve a "City Name" team string (ESPN displayName convention) to
    its MLB Stats API numeric team id, or raise if unrecognized."""
    team_id = _TEAM_ID_BY_IDENTITY.get(_team_identity(team_name))
    if team_id is None:
        raise ValueError(f"NO_CALL_MLB_AVAILABILITY_UNKNOWN_TEAM: {team_name!r}")
    return team_id


def _stamp_and_digest(observed: datetime, entries: list[dict[str, Any]]) -> tuple[str, str, str]:
    day = observed.astimezone(EASTERN).date().isoformat()
    stamp = observed.astimezone(EASTERN).strftime("%Y%m%dT%H%M%S%z")
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return day, stamp, digest


def _write_snapshot(data_root: str | Path, name_prefix: str, day: str, stamp: str, digest: str,
                     payload: dict[str, Any]) -> tuple[Path, Path]:
    root = Path(data_root) / "availability" / "mlb"
    raw_path = root / "raw" / day / f"{name_prefix}-{stamp}-{digest[:12]}.json"
    snapshot_path = root / "snapshots" / day / f"{name_prefix}-{stamp}-{digest[:12]}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raw_path.write_text(text, encoding="utf-8")
    snapshot_path.write_text(text, encoding="utf-8")
    return raw_path, snapshot_path


def capture_roster_snapshot(
    data_root: str | Path,
    team_ids: list[int],
    *,
    observed_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch and persist a same-day 40-man-roster status snapshot.

    Live-only by construction (see module docstring) -- useful as a
    same-day freshness cross-check, not as historical training data.
    """
    observed = parse_utc((observed_at or utc_now()).isoformat())
    http = client or httpx.Client(timeout=15)
    entries: list[dict[str, Any]] = []
    for team_id in team_ids:
        response = http.get(
            ROSTER_URL_TEMPLATE.format(team_id=team_id), params={"rosterType": "40Man"}
        )
        response.raise_for_status()
        payload = response.json()
        for row in payload.get("roster", []):
            person = row.get("person", {})
            entries.append(
                {
                    "team_id": team_id,
                    "player_id": person.get("id"),
                    "player_name": person.get("fullName"),
                    "current_status": str((row.get("status") or {}).get("description", "")),
                    "position_type": str((row.get("position") or {}).get("type", "")),
                }
            )
    day, stamp, digest = _stamp_and_digest(observed, entries)
    payload_out = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "sport": "mlb",
        "source": "mlb_statsapi_roster_40man",
        "source_url_template": ROSTER_URL_TEMPLATE,
        "observed_at_utc": observed.isoformat(),
        "report_at_utc": observed.isoformat(),
        "team_ids": team_ids,
        "entries": entries,
    }
    _write_snapshot(data_root, "roster", day, stamp, digest, payload_out)
    return payload_out


def capture_transactions_snapshot(
    data_root: str | Path,
    team_ids: list[int],
    start_date: str,
    end_date: str,
    *,
    observed_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch and persist the dated IL-transaction history for the given
    teams and date range.

    Stores both ``reported_date`` (from the API's ``date`` field -- the
    authoritative point-in-time timestamp) and ``effective_date`` (from
    ``effectiveDate`` -- reference only). See module docstring: only
    ``reported_date`` may ever be used to filter what was knowable as of a
    given decision time.
    """
    observed = parse_utc((observed_at or utc_now()).isoformat())
    http = client or httpx.Client(timeout=15)
    entries: list[dict[str, Any]] = []
    for team_id in team_ids:
        response = http.get(
            TRANSACTIONS_URL,
            params={"teamId": team_id, "startDate": start_date, "endDate": end_date},
        )
        response.raise_for_status()
        payload = response.json()
        for row in payload.get("transactions", []):
            person = row.get("person", {})
            entries.append(
                {
                    "team_id": team_id,
                    "player_id": person.get("id"),
                    "player_name": person.get("fullName"),
                    "reported_date": row.get("date"),
                    "effective_date": row.get("effectiveDate"),
                    "type_desc": row.get("typeDesc"),
                    "description": row.get("description"),
                }
            )
    day, stamp, digest = _stamp_and_digest(observed, entries)
    payload_out = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "sport": "mlb",
        "source": "mlb_statsapi_transactions",
        "source_url": TRANSACTIONS_URL,
        "observed_at_utc": observed.isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "team_ids": team_ids,
        "entries": entries,
    }
    _write_snapshot(data_root, "txn", day, stamp, digest, payload_out)
    return payload_out
