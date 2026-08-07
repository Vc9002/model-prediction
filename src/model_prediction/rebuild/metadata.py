"""SQLite metadata database for schemas, model metadata, source health, entity mappings.

Stored at data/rebuild/metadata.db.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MetadataDB:
    """Central metadata store for the rebuild platform.

    Tables:
        sources        — data source registry with health status
        schemas        — table and column definitions per source
        models         — model version registry with artifact hashes
        entities       — canonical identity mappings across sources
        events         — event identity registry
        audit          — rebuild audit trail (independent of legacy audit)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS _meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tier TEXT NOT NULL CHECK(tier IN (
                    'cached_repository', 'official_public', 'versioned_release',
                    'keyless_api', 'throttled_scraper', 'paid_or_keyed'
                )),
                url TEXT,
                requires_key INTEGER NOT NULL DEFAULT 0,
                rate_limit_rps REAL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'degraded', 'down', 'retired')),
                last_checked_utc TEXT,
                last_success_utc TEXT,
                last_error TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS source_sports (
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                sport TEXT NOT NULL,
                data_type TEXT NOT NULL,
                coverage_start TEXT,
                coverage_end TEXT,
                PRIMARY KEY (source_id, sport, data_type)
            );

            CREATE TABLE IF NOT EXISTS schemas (
                schema_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                table_name TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT '1',
                columns_json TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                UNIQUE(source_id, table_name, schema_version)
            );

            CREATE TABLE IF NOT EXISTS models (
                model_id TEXT PRIMARY KEY,
                sport TEXT NOT NULL,
                market_type TEXT NOT NULL,
                family TEXT NOT NULL,
                version TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                artifact_path TEXT,
                qualified INTEGER NOT NULL DEFAULT 0,
                qualification_evidence_json TEXT,
                status TEXT NOT NULL DEFAULT 'research'
                    CHECK(status IN ('research', 'challenger', 'benchmark',
                                     'qualified_predictive', 'qualified_economic',
                                     'shadow', 'retired')),
                created_utc TEXT NOT NULL,
                promoted_utc TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL CHECK(entity_type IN (
                    'event', 'team', 'player', 'roster', 'venue',
                    'league', 'competition', 'market_contract'
                )),
                canonical_name TEXT NOT NULL,
                sport TEXT NOT NULL,
                effective_from_utc TEXT NOT NULL,
                effective_to_utc TEXT,
                attributes_json TEXT
            );

            CREATE TABLE IF NOT EXISTS entity_mappings (
                mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL REFERENCES entities(entity_id),
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                source_entity_id TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                UNIQUE(entity_id, source_id)
            );

            CREATE TABLE IF NOT EXISTS audit (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                details_json TEXT,
                previous_hash TEXT,
                event_hash TEXT NOT NULL,
                created_utc TEXT NOT NULL
            );
        """)
        self.conn.execute(
            "INSERT OR IGNORE INTO _meta(key, value) VALUES(?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        self.conn.commit()

    # ── source registration ────────────────────────────────────────────

    def register_source(
        self,
        source_id: str,
        name: str,
        tier: str,
        url: str | None = None,
        requires_key: bool = False,
        rate_limit_rps: float | None = None,
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO sources(source_id, name, tier, url, requires_key, rate_limit_rps, notes)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (source_id, name, tier, url, int(requires_key), rate_limit_rps, notes),
        )
        self.conn.commit()

    def update_source_health(self, source_id: str, status: str, error: str | None = None) -> None:
        # Real bug fixed here: this was UPDATE-only, and register_source()
        # (the only way to INSERT a row) had zero real callers anywhere in
        # this codebase (verified via grep) -- every collector calls this
        # on every real collection (dozens of real call sites in
        # collectors.py) against a `sources` row that never existed, so
        # source-health tracking has never actually recorded anything,
        # ever, for any real source. Also blocked real identity-mapping
        # work: entity_mappings.source_id has a real, enforced foreign key
        # (PRAGMA foreign_keys=ON) against sources(source_id), so
        # IdentityRegistry.map() would fail closed for a source that was
        # never registered. INSERT OR IGNORE ensures the row exists (with
        # a reasonable default tier a caller can refine later via
        # register_source()) before every real health update.
        self.conn.execute(
            """INSERT OR IGNORE INTO sources(source_id, name, tier, status)
               VALUES(?, ?, 'keyless_api', 'active')""",
            (source_id, source_id),
        )
        now = utc_now()
        if status == "active":
            self.conn.execute(
                "UPDATE sources SET status=?, last_success_utc=?, last_checked_utc=?, last_error=NULL WHERE source_id=?",
                (status, now, now, source_id),
            )
        else:
            self.conn.execute(
                "UPDATE sources SET status=?, last_checked_utc=?, last_error=? WHERE source_id=?",
                (status, now, error, source_id),
            )
        self.conn.commit()

    def register_source_sport(
        self, source_id: str, sport: str, data_type: str,
        coverage_start: str | None = None, coverage_end: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO source_sports(source_id, sport, data_type, coverage_start, coverage_end)
               VALUES(?, ?, ?, ?, ?)""",
            (source_id, sport, data_type, coverage_start, coverage_end),
        )
        self.conn.commit()

    # ── schema registration ────────────────────────────────────────────

    def register_schema(
        self, schema_id: str, source_id: str, table_name: str,
        columns: list[dict[str, str]], schema_version: str = "1",
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO schemas(schema_id, source_id, table_name, schema_version, columns_json, created_utc)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (schema_id, source_id, table_name, schema_version, json.dumps(columns), utc_now()),
        )
        self.conn.commit()

    # ── model registry ─────────────────────────────────────────────────

    def register_model(
        self, model_id: str, sport: str, market_type: str, family: str,
        version: str, artifact_hash: str, artifact_path: str | None = None,
        qualified: bool = False, status: str = "research", notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO models(model_id, sport, market_type, family, version,
               artifact_hash, artifact_path, qualified, status, created_utc, notes)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (model_id, sport, market_type, family, version, artifact_hash,
             artifact_path, int(qualified), status, utc_now(), notes),
        )
        self.conn.commit()

    # ── entity registry ────────────────────────────────────────────────

    def register_entity(
        self, entity_id: str, entity_type: str, canonical_name: str,
        sport: str, effective_from_utc: str, attributes: dict | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO entities(entity_id, entity_type, canonical_name, sport,
               effective_from_utc, attributes_json)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (entity_id, entity_type, canonical_name, sport, effective_from_utc,
             json.dumps(attributes) if attributes else None),
        )
        self.conn.commit()

    def map_entity(self, entity_id: str, source_id: str, source_entity_id: str, confidence: float = 1.0) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO entity_mappings(entity_id, source_id, source_entity_id, confidence)
               VALUES(?, ?, ?, ?)""",
            (entity_id, source_id, source_entity_id, confidence),
        )
        self.conn.commit()

    # ── audit ──────────────────────────────────────────────────────────

    def audit_event(self, event_type: str, details: dict | None = None,
                    entity_type: str | None = None, entity_id: str | None = None) -> str:
        import hashlib
        import uuid
        event_id = uuid.uuid4().hex
        now = utc_now()
        prev = self.conn.execute(
            "SELECT event_hash FROM audit ORDER BY created_utc DESC LIMIT 1"
        ).fetchone()
        previous_hash = prev["event_hash"] if prev else ""
        raw = json.dumps({
            "event_id": event_id, "event_type": event_type,
            "entity_type": entity_type, "entity_id": entity_id,
            "details": details, "previous_hash": previous_hash, "created_utc": now,
        }, sort_keys=True)
        event_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.conn.execute(
            "INSERT INTO audit(event_id, event_type, entity_type, entity_id, details_json, previous_hash, event_hash, created_utc) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, entity_type, entity_id, json.dumps(details) if details else None, previous_hash, event_hash, now),
        )
        self.conn.commit()
        return event_id

    # ── queries ────────────────────────────────────────────────────────

    def source_status(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()]

    def models_by_sport(self, sport: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM models WHERE sport=? ORDER BY version", (sport,)
        ).fetchall()]

    def entity_by_source(self, source_id: str, source_entity_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT e.* FROM entities e
               JOIN entity_mappings m ON e.entity_id = m.entity_id
               WHERE m.source_id=? AND m.source_entity_id=?""",
            (source_id, source_entity_id),
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()
