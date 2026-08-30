"""Canonical event identity (consolidation C, item 14).

Every subsystem joins on one ``canonical_event_id``; provider IDs are
mappings, not competing primary keys. A market snapshot, prediction,
settlement, and challenger all reference the same canonical id — which is
what makes exact same-event champion/challenger testing straightforward.

For US sports the ESPN event id IS the canonical id (ESPN is the
scheduling source of truth); other providers' ids map onto it through the
``canonical_events`` table (RuntimePaths-resolved control plane):

    canonicalize("espn", "401690001", "WNBA") -> "401690001"
    canonicalize("polymarket", "<token-id>", "WNBA") -> registers/looks up
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .runtime_paths import RuntimePaths, migrate_legacy_state

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_events (
    canonical_event_id  TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_event_id   TEXT NOT NULL,
    sport               TEXT NOT NULL,
    first_seen_utc      TEXT NOT NULL,
    PRIMARY KEY (provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_canonical_events_by_canonical
    ON canonical_events (canonical_event_id, provider);
"""

_ESPN_PROVIDER = "espn"


def _conn(repo_root: Path | str | None = None) -> sqlite3.Connection:
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    paths = RuntimePaths.resolve(repo_root=root)
    migrate_legacy_state(paths)
    paths.runs_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.runs_db, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def canonicalize(
    provider: str,
    provider_event_id: str,
    sport: str,
    *,
    repo_root: Path | str | None = None,
) -> str:
    """Return the canonical event id for a provider id, registering the
    mapping when first seen. ESPN ids are canonical by definition."""
    if provider.lower() == _ESPN_PROVIDER:
        return provider_event_id
    from datetime import UTC, datetime

    conn = _conn(repo_root)
    try:
        row = conn.execute(
            "SELECT canonical_event_id FROM canonical_events WHERE provider = ? AND provider_event_id = ?",
            (provider.lower(), str(provider_event_id)),
        ).fetchone()
        if row is not None:
            return str(row[0])
        # First sighting: the canonical id is provider-prefixed until a
        # real same-event mapping is registered (keeps joins honest — a
        # prefixed id can never collide with another provider's raw id).
        canonical = f"{provider.lower()}:{provider_event_id}"
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO canonical_events "
                "(canonical_event_id, provider, provider_event_id, sport, "
                "first_seen_utc) VALUES (?, ?, ?, ?, ?)",
                (
                    canonical,
                    provider.lower(),
                    str(provider_event_id),
                    sport,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return canonical
    finally:
        conn.close()


def map_same_event(
    provider: str,
    provider_event_id: str,
    canonical_event_id: str,
    sport: str,
    *,
    repo_root: Path | str | None = None,
) -> None:
    """Declare that a provider id refers to an existing canonical event
    (e.g. a Polymarket token matched to an ESPN game)."""
    from datetime import UTC, datetime

    conn = _conn(repo_root)
    try:
        with conn:
            conn.execute(
                "INSERT INTO canonical_events (canonical_event_id, provider, "
                "provider_event_id, sport, first_seen_utc) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (provider, provider_event_id) DO UPDATE SET "
                "canonical_event_id = excluded.canonical_event_id, "
                "sport = excluded.sport",
                (
                    canonical_event_id,
                    provider.lower(),
                    str(provider_event_id),
                    sport,
                    datetime.now(UTC).isoformat(),
                ),
            )
    finally:
        conn.close()


def mappings_for(canonical_event_id: str, *, repo_root: Path | str | None = None) -> list[dict[str, Any]]:
    """All provider ids known to map to one canonical event."""
    conn = _conn(repo_root)
    try:
        rows = conn.execute(
            "SELECT provider, provider_event_id, sport FROM canonical_events WHERE canonical_event_id = ?",
            (canonical_event_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
