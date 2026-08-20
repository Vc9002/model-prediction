"""Shared raw-immutable-capture contract for secondary data providers.

Generalizes the snapshot-write pattern ``mlb_injuries.py`` already uses
(hash-stamped, day-bucketed, raw + snapshot copies) so a new provider module
(BALLDONTLIE, Football-Data, Open-Meteo, TheSportsDB, ...) doesn't
reimplement it, and so every provider's raw entries carry one common
provenance envelope. This lets a later cross-source reconciliation step ask
"does BALLDONTLIE's injury entry disagree with ESPN's for the same player at
the same effective time?" instead of silently merging two sources under one
schema and losing which one said what.

This module writes raw capture only. It does not resolve entities against
``EntityRegistry``, does not decide feature values, and must never be called
from a live decision path directly -- a provider module's own capture
function (e.g. a future ``balldontlie.py::capture_mlb_injuries_snapshot``)
calls this after fetching, the same way ``mlb_injuries.py``'s
``capture_*_snapshot`` functions do today.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SNAPSHOT_SCHEMA_VERSION = "1"
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ProviderEntry:
    """One normalized fact from a provider, wrapping its raw payload with the
    provenance fields needed to compare it against the same fact from a
    different source later.

    ``effective_at_utc`` is when the fact itself became true (e.g. an
    injury's reported date, a plate appearance's game time) -- the field a
    point-in-time filter must use. ``observed_at_utc`` is when *this system*
    captured it, which may lag ``effective_at_utc`` for a live-only feed with
    no historical query support (see BallDontLieClient.mlb_player_injuries's
    docstring). ``available=False`` with a ``missing_reason`` records a
    genuine "provider had nothing here" distinct from "we never asked."
    """

    source: str
    source_entity_id: str
    effective_at_utc: str
    observed_at_utc: str
    payload: dict[str, Any]
    canonical_entity_id: str | None = None
    source_version: str | None = None
    available: bool = True
    missing_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_entity_id": self.source_entity_id,
            "canonical_entity_id": self.canonical_entity_id,
            "effective_at_utc": self.effective_at_utc,
            "observed_at_utc": self.observed_at_utc,
            "source_version": self.source_version,
            "available": self.available,
            "missing_reason": self.missing_reason,
            "payload": self.payload,
        }


def _stamp_and_digest(observed: datetime, entries: list[dict[str, Any]]) -> tuple[str, str, str]:
    day = observed.astimezone(EASTERN).date().isoformat()
    stamp = observed.astimezone(EASTERN).strftime("%Y%m%dT%H%M%S%z")
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return day, stamp, digest


def write_provider_snapshot(
    data_root: str | Path,
    source: str,
    sport: str,
    entries: list[ProviderEntry],
    *,
    observed_at: datetime,
    source_url: str | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Write a hash-stamped, day-bucketed raw + snapshot copy for one capture
    call, mirroring ``mlb_injuries.py::_write_snapshot``'s on-disk layout but
    generic across providers/sports: ``data/providers/<source>/<sport>/
    {raw,snapshots}/<day>/<stamp>-<digest12>.json``.

    Returns the written payload dict plus the raw and snapshot paths.
    """
    rows = [entry.as_dict() for entry in entries]
    day, stamp, digest = _stamp_and_digest(observed_at, rows)
    root = Path(data_root) / "providers" / source / sport
    raw_path = root / "raw" / day / f"{stamp}-{digest[:12]}.json"
    snapshot_path = root / "snapshots" / day / f"{stamp}-{digest[:12]}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": source,
        "sport": sport,
        "source_url": source_url,
        "observed_at_utc": observed_at.isoformat(),
        "entry_count": len(rows),
        "entries": rows,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raw_path.write_text(text, encoding="utf-8")
    snapshot_path.write_text(text, encoding="utf-8")
    return payload, raw_path, snapshot_path
