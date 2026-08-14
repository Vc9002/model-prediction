"""Runtime ledger store (consolidation G1-G3).

The SQLite side of the ledger dual-write. Two design rules from the
migration plan:

1. **One canonical mutation object** — business logic builds a
   :class:`LedgerMutation` once (with ``pick_id`` + ``operation_id``
   generated at the mutation boundary, G3) and hands the SAME object to
   the legacy XLSX writer and this store. Backends never generate IDs —
   interrupted retries reconcile by deterministic operation ids.
2. **The audit chain lives in the same database** — every mutation and
   its ``ledger_events`` row commit in ONE transaction (G2): the pick
   mutation and its audit event either both exist or neither does. The
   event chain is hash-linked (``previous_hash``), mirroring the legacy
   audit chain's integrity property but without the cross-file
   atomicity gap.

Tables:
  ledger_records  — canonical, highly-queried fields in real columns;
                    long-tail snapshots as versioned JSON
  ledger_events   — hash-linked audit chain, one row per mutation
  ledger_runs     — dual-write cycle bookkeeping (parity markers)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Self

from .runtime_paths import RuntimePaths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_records (
    pick_id                 TEXT NOT NULL,
    operation_id            TEXT NOT NULL,
    ledger_tier             TEXT NOT NULL,
    sport                   TEXT NOT NULL,

    event_id                TEXT,
    canonical_event_id      TEXT,
    event_start_utc         TEXT,

    market_type             TEXT,
    selection               TEXT,
    line                    REAL,

    model_id                TEXT,
    model_artifact_hash     TEXT,
    feature_schema_version  TEXT,

    model_probability       REAL,
    market_probability      REAL,
    edge                    REAL,
    confidence              REAL,
    units                   REAL,

    decision                TEXT,
    reason_code             TEXT,

    status                  TEXT NOT NULL DEFAULT 'open',
    result                  TEXT,
    pnl_units               REAL,

    created_at_utc          TEXT NOT NULL,
    settled_at_utc          TEXT,

    decision_payload_json   TEXT,
    feature_payload_json    TEXT,

    PRIMARY KEY (pick_id, ledger_tier)
);
CREATE INDEX IF NOT EXISTS idx_ledger_records_tier_sport
    ON ledger_records (ledger_tier, sport, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_ledger_records_event
    ON ledger_records (event_id, ledger_tier);
CREATE INDEX IF NOT EXISTS idx_ledger_records_operation
    ON ledger_records (operation_id);

CREATE TABLE IF NOT EXISTS ledger_events (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL,
    pick_id        TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    previous_hash  TEXT,
    event_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_events_pick
    ON ledger_events (pick_id, sequence);

CREATE TABLE IF NOT EXISTS ledger_runs (
    run_id         TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    tier           TEXT,
    mutations      INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'running'
);
"""

_EVENT_TYPES = ("append", "settle", "void", "update", "archive", "remove")
# v2: the primary key became (pick_id, ledger_tier) — main and flat
# legitimately share pick_ids (one decision, two tiers), so a single
# pick_id key wrongly collapsed them (found live on flat/tennis).
_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class LedgerMutation:
    """One canonical ledger mutation — identical for both backends (G3)."""

    pick_id: str
    operation_id: str
    ledger_tier: str
    sport: str
    event_type: str
    created_at_utc: str
    # identity
    event_id: str | None = None
    canonical_event_id: str | None = None
    event_start_utc: str | None = None
    # market
    market_type: str | None = None
    selection: str | None = None
    line: float | None = None
    # model lineage
    model_id: str | None = None
    model_artifact_hash: str | None = None
    feature_schema_version: str | None = None
    # numbers
    model_probability: float | None = None
    market_probability: float | None = None
    edge: float | None = None
    confidence: float | None = None
    units: float | None = None
    # decisions/outcome
    decision: str | None = None
    reason_code: str | None = None
    status: str = "open"
    result: str | None = None
    pnl_units: float | None = None
    settled_at_utc: str | None = None
    # long-tail snapshots (versioned JSON, not 80 columns)
    decision_payload: dict[str, Any] | None = None
    feature_payload: dict[str, Any] | None = None
    note: str | None = field(default=None, kw_only=True)


def new_pick_ids() -> tuple[str, str]:
    """Generate (pick_id, operation_id) ONCE at the mutation boundary.

    Both are stable for the lifetime of one logical mutation: retrying an
    interrupted write reuses the same pair, so reconciliation and
    idempotency are by deterministic ids, never by per-backend UUIDs.
    """
    return f"pick-{uuid.uuid4().hex}", f"op-{uuid.uuid4().hex}"


def _hash_event(
    event_type: str, pick_id: str, payload_json: str, previous_hash: str | None
) -> str:
    digest = hashlib.sha256()
    for part in (event_type, pick_id, payload_json, previous_hash or ""):
        digest.update(part.encode())
    return digest.hexdigest()


class RuntimeLedgerStore:
    """SQLite mirror store for the ledger dual-write (phase 1: XLSX stays
    authoritative; this mirror is reconciled by ledger_parity)."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        paths.ledgers_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(paths.ledgers_db, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        schema_row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ledger_records'"
        ).fetchone()
        pk_matches = (
            schema_row is not None
            and "PRIMARY KEY (pick_id, ledger_tier)" in (schema_row[0] or "")
        )
        if schema_row is not None and not pk_matches:
            # Schema change on a mirror: drop + rebuild. This is safe
            # because the mirror is fully reconstructible from the
            # authoritative XLSX via ledger_parity backfill (deterministic
            # operation ids) — never do this to a canonical store. The
            # check is STRUCTURAL (the table's own declared key), not a
            # version pragma — a pragma can be stamped by a half-run but
            # the table can't lie about its shape.
            self._conn.executescript(
                "DROP TABLE IF EXISTS ledger_records; "
                "DROP TABLE IF EXISTS ledger_events;"
            )
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------ mutation

    def apply(self, mutation: LedgerMutation) -> bool:
        """Record one mutation + its audit event in ONE transaction (G2).

        Returns True when the record changed, False when the mutation is
        an exact idempotent retry (same operation_id already applied) —
        reapplying is a safe no-op, never a duplicate event.
        """
        if mutation.event_type not in _EVENT_TYPES:
            raise ValueError(f"event_type must be one of {_EVENT_TYPES}")
        payload = json.dumps(
            _mutation_payload(mutation), sort_keys=True, separators=(",", ":")
        )
        with self._conn:
            existing = self._conn.execute(
                "SELECT operation_id FROM ledger_records "
                "WHERE pick_id = ? AND ledger_tier = ?",
                (mutation.pick_id, mutation.ledger_tier),
            ).fetchone()
            if existing is not None and existing["operation_id"] == mutation.operation_id:
                return False  # idempotent retry of the exact same mutation

            self._conn.execute(
                """INSERT INTO ledger_records (
                    pick_id, operation_id, ledger_tier, sport,
                    event_id, canonical_event_id, event_start_utc,
                    market_type, selection, line,
                    model_id, model_artifact_hash, feature_schema_version,
                    model_probability, market_probability, edge, confidence, units,
                    decision, reason_code, status, result, pnl_units,
                    created_at_utc, settled_at_utc,
                    decision_payload_json, feature_payload_json)
                VALUES (:pick_id, :operation_id, :ledger_tier, :sport,
                    :event_id, :canonical_event_id, :event_start_utc,
                    :market_type, :selection, :line,
                    :model_id, :model_artifact_hash, :feature_schema_version,
                    :model_probability, :market_probability, :edge, :confidence, :units,
                    :decision, :reason_code, :status, :result, :pnl_units,
                    :created_at_utc, :settled_at_utc,
                    :decision_payload, :feature_payload)
                ON CONFLICT (pick_id, ledger_tier) DO UPDATE SET
                    operation_id = excluded.operation_id,
                    ledger_tier = excluded.ledger_tier,
                    sport = excluded.sport,
                    event_id = excluded.event_id,
                    canonical_event_id = excluded.canonical_event_id,
                    event_start_utc = excluded.event_start_utc,
                    market_type = excluded.market_type,
                    selection = excluded.selection,
                    line = excluded.line,
                    model_id = excluded.model_id,
                    model_artifact_hash = excluded.model_artifact_hash,
                    feature_schema_version = excluded.feature_schema_version,
                    model_probability = excluded.model_probability,
                    market_probability = excluded.market_probability,
                    edge = excluded.edge,
                    confidence = excluded.confidence,
                    units = excluded.units,
                    decision = excluded.decision,
                    reason_code = excluded.reason_code,
                    status = excluded.status,
                    result = excluded.result,
                    pnl_units = excluded.pnl_units,
                    settled_at_utc = excluded.settled_at_utc,
                    decision_payload_json = excluded.decision_payload_json,
                    feature_payload_json = excluded.feature_payload_json""",
                _mutation_row(mutation),
            )

            # Hash-linked audit event, SAME transaction as the record.
            previous = self._conn.execute(
                "SELECT event_hash FROM ledger_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["event_hash"] if previous else None
            event_hash = _hash_event(
                mutation.event_type, mutation.pick_id, payload, previous_hash
            )
            self._conn.execute(
                "INSERT INTO ledger_events (event_id, pick_id, event_type, "
                "event_time_utc, payload_json, previous_hash, event_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"evt-{uuid.uuid4().hex}",
                    mutation.pick_id,
                    mutation.event_type,
                    mutation.created_at_utc,
                    payload,
                    previous_hash,
                    event_hash,
                ),
            )
        return True

    # ------------------------------------------------------------- reading

    def records(self, *, tier: str | None = None, sport: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ledger_records"
        clauses: list[str] = []
        params: list[Any] = []
        if tier is not None:
            clauses.append("ledger_tier = ?")
            params.append(tier)
        if sport is not None:
            clauses.append("sport = ?")
            params.append(sport)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at_utc"
        return [dict(r) for r in self._conn.execute(query, params).fetchall()]

    def event_count(self) -> int:
        """Total hash-linked audit events (I2 overlap report)."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM ledger_events"
        ).fetchone()
        return int(row["n"])

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """Replay the hash chain; report the first break (I2 precursor)."""
        rows = self._conn.execute(
            "SELECT * FROM ledger_events ORDER BY sequence"
        ).fetchall()
        problems: list[str] = []
        previous_hash: str | None = None
        for row in rows:
            expected = _hash_event(
                row["event_type"], row["pick_id"], row["payload_json"], previous_hash
            )
            if expected != row["event_hash"]:
                problems.append(f"hash break at sequence {row['sequence']}")
                break
            previous_hash = row["event_hash"]
        return (not problems), problems


def _mutation_row(m: LedgerMutation) -> dict[str, Any]:
    return {
        "pick_id": m.pick_id,
        "operation_id": m.operation_id,
        "ledger_tier": m.ledger_tier,
        "sport": m.sport,
        "event_id": m.event_id,
        "canonical_event_id": m.canonical_event_id,
        "event_start_utc": m.event_start_utc,
        "market_type": m.market_type,
        "selection": m.selection,
        "line": m.line,
        "model_id": m.model_id,
        "model_artifact_hash": m.model_artifact_hash,
        "feature_schema_version": m.feature_schema_version,
        "model_probability": m.model_probability,
        "market_probability": m.market_probability,
        "edge": m.edge,
        "confidence": m.confidence,
        "units": m.units,
        "decision": m.decision,
        "reason_code": m.reason_code,
        "status": m.status,
        "result": m.result,
        "pnl_units": m.pnl_units,
        "created_at_utc": m.created_at_utc,
        "settled_at_utc": m.settled_at_utc,
        "decision_payload": (
            json.dumps(m.decision_payload, sort_keys=True)
            if m.decision_payload
            else None
        ),
        "feature_payload": (
            json.dumps(m.feature_payload, sort_keys=True)
            if m.feature_payload
            else None
        ),
    }


def _mutation_payload(m: LedgerMutation) -> dict[str, Any]:
    """The event payload: every field the audit chain hashes."""
    return {
        "pick_id": m.pick_id,
        "operation_id": m.operation_id,
        "ledger_tier": m.ledger_tier,
        "sport": m.sport,
        "event_type": m.event_type,
        "event_id": m.event_id,
        "market_type": m.market_type,
        "selection": m.selection,
        "line": m.line,
        "model_id": m.model_id,
        "model_artifact_hash": m.model_artifact_hash,
        "feature_schema_version": m.feature_schema_version,
        "model_probability": m.model_probability,
        "market_probability": m.market_probability,
        "edge": m.edge,
        "confidence": m.confidence,
        "units": m.units,
        "decision": m.decision,
        "reason_code": m.reason_code,
        "status": m.status,
        "result": m.result,
        "pnl_units": m.pnl_units,
        "settled_at_utc": m.settled_at_utc,
        "note": m.note,
        "decision_payload": m.decision_payload,
        "feature_payload": m.feature_payload,
    }
