"""Point-in-time starting-quarterback availability for NCAAF.

The v4 feature extractor has carried ``qb_starter_prob_*`` since its first version, but
production never populated it (review 2026-11-11 #13): every game ran with 1.0 and the
-4.5 point backup discount could not fire. This module joins the two sources the system
already captures with provenance -- the daily ESPN CFB injuries snapshot
(``data_sources/espn_cfb_injuries.py``) and the ESPN scoreboard's per-team passing
leader -- into one starter probability per side.

Fail-closed semantics: the output is a *penalty* channel. With no usable report the
starter probability stays at 1.0 (no discount) and the reason says why, so the ledger
row records "no evidence", never a guess. Only a report observed at or before the
decision time counts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import parse_utc

# Mirrors features/nfl_qb_oline.py::check_nfl_qb_injury_status, expressed as the
# probability the listed starter plays rather than as a point penalty: Out/IR take the
# full -4.5 discount (prob 0), Doubtful -2.5 of 4.5, Questionable -1.0 of 4.5.
STATUS_STARTER_PROBABILITY: dict[str, float] = {
    "Out": 0.0,
    "IR": 0.0,
    "Doubtful": round(1.0 - 2.5 / 4.5, 4),
    "Questionable": round(1.0 - 1.0 / 4.5, 4),
    "Probable": 1.0,
}
# An injury report older than this cannot describe Saturday's availability.
MAX_REPORT_AGE_HOURS = 48.0
SNAPSHOT_DIR = Path("availability") / "ncaaf" / "espn_injury_snapshots"


@dataclass(frozen=True)
class CFBQBAvailability:
    starter_prob: float
    status: str
    reason: str
    source: str
    observed_at_utc: str | None
    starter_name: str | None
    starter_athlete_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _no_evidence(reason: str, starter_name: str | None, starter_athlete_id: str | None) -> CFBQBAvailability:
    return CFBQBAvailability(
        starter_prob=1.0,
        status="no_report",
        reason=reason,
        source="unavailable",
        observed_at_utc=None,
        starter_name=starter_name,
        starter_athlete_id=starter_athlete_id,
    )


def load_latest_injury_snapshot(data_root: str | Path, observed_at: datetime) -> dict[str, Any] | None:
    """Newest captured ESPN CFB injuries snapshot observed at or before ``observed_at``.

    Snapshots are small daily files; a full scan keyed on each file's own
    ``observed_at_utc`` (never the filename or mtime) keeps this point-in-time safe.
    """
    root = Path(data_root) / SNAPSHOT_DIR
    if not root.is_dir():
        return None
    best: tuple[datetime, dict[str, Any]] | None = None
    for path in root.glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            captured = parse_utc(str(payload["observed_at_utc"]))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        if payload.get("source") != "espn_cfb_injuries" or captured > observed_at:
            continue
        if best is None or captured > best[0]:
            best = (captured, payload)
    return best[1] if best else None


def passing_leader_from_competitor(competitor: dict[str, Any]) -> tuple[str | None, str | None]:
    """(athlete_id, display_name) of the ESPN scoreboard passing leader, else (None, None).

    The season passing leader is the de facto starter; ESPN offers no college depth
    chart. A missing leader (week 0, FCS opponent) leaves the channel without evidence.
    """
    for group in competitor.get("leaders") or []:
        if str(group.get("name") or "") not in ("passingLeader", "passingYards"):
            continue
        for entry in group.get("leaders") or []:
            athlete = entry.get("athlete") or {}
            position = str((athlete.get("position") or {}).get("abbreviation") or "QB").upper()
            if position != "QB":
                continue
            athlete_id = str(athlete.get("id") or "") or None
            name = str(athlete.get("displayName") or "") or None
            if athlete_id or name:
                return athlete_id, name
    return None, None


def _same_team(entry_team: str, team: str) -> bool:
    from ..data_sources.cfb_data import resolve_team

    left = resolve_team(entry_team)
    right = resolve_team(team)
    if left is not None and right is not None:
        return left.canonical_name == right.canonical_name
    a = entry_team.casefold().strip()
    b = team.casefold().strip()
    return bool(a and b and (a in b or b in a))


def resolve_cfb_qb_availability(
    snapshot: dict[str, Any] | None,
    *,
    team: str,
    starter_athlete_id: str | None,
    starter_name: str | None,
    observed_at: datetime,
    max_report_age_hours: float = MAX_REPORT_AGE_HOURS,
) -> CFBQBAvailability:
    """Starter probability for ``team`` from a point-in-time injuries snapshot."""
    if snapshot is None:
        return _no_evidence("no_injury_snapshot_before_decision", starter_name, starter_athlete_id)
    try:
        captured = parse_utc(str(snapshot["observed_at_utc"]))
    except (KeyError, TypeError, ValueError):
        return _no_evidence("snapshot_missing_observed_at", starter_name, starter_athlete_id)
    if captured > observed_at:
        return _no_evidence("snapshot_after_decision_time", starter_name, starter_athlete_id)
    if observed_at - captured > timedelta(hours=max_report_age_hours):
        return _no_evidence("snapshot_stale", starter_name, starter_athlete_id)
    if not starter_athlete_id and not starter_name:
        return _no_evidence("starter_unresolved", None, None)

    name_norm = (starter_name or "").casefold().strip()
    for entry in snapshot.get("entries") or []:
        if str(entry.get("position") or "QB").upper() != "QB":
            continue
        if not _same_team(str(entry.get("team") or ""), team):
            continue
        id_match = bool(starter_athlete_id) and str(entry.get("athlete_id") or "") == starter_athlete_id
        entry_name = str(entry.get("player_name") or "").casefold().strip()
        name_match = bool(name_norm) and bool(entry_name) and (entry_name == name_norm)
        if not (id_match or name_match):
            continue
        status = str(entry.get("status") or "")
        probability = STATUS_STARTER_PROBABILITY.get(status)
        if probability is None:
            # An unmapped designation (e.g. "Active") is a report of availability, not
            # of absence; record it without discounting.
            probability = 1.0
        return CFBQBAvailability(
            starter_prob=probability,
            status=status or "unknown",
            reason="starter_listed_on_report",
            source=str(snapshot.get("source") or "espn_cfb_injuries"),
            observed_at_utc=captured.isoformat(),
            starter_name=starter_name,
            starter_athlete_id=starter_athlete_id,
        )
    return CFBQBAvailability(
        starter_prob=1.0,
        status="not_listed",
        reason="starter_absent_from_report",
        source=str(snapshot.get("source") or "espn_cfb_injuries"),
        observed_at_utc=captured.isoformat(),
        starter_name=starter_name,
        starter_athlete_id=starter_athlete_id,
    )
