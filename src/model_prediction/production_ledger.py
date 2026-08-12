"""Append-only SQLite production prediction ledger.

Stored at ``<runtime_root>/production/predictions.db``.  Every production
prediction, health check snapshot, and run record is stored here — append-only,
with idempotency keys so re-running the same production cycle with identical
inputs is a no-op.

Design contract (same as ``ShadowLedger``):

- ``predictions`` and ``health_checks`` are append-only.  No UPDATE or DELETE.
  A correction is a new INSERT whose ``supersedes_id`` points at the row it
  replaces.
- Every row carries ``created_at``, ``run_id``, ``schema_version``.
- ``predictions`` enforce a UNIQUE index on ``(run_id, event_id, sport,
  market, model_id) WHERE supersedes_id IS NULL`` — rerunning the same
  job with identical inputs returns the existing row; an explicit correction
  (``supersedes_id`` set) bypasses the index and always appends.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ProductionLedger:
    """Central append-only ledger for the production prediction service.

    Tables:

    - ``runs`` — one row per production cycle, with git SHA and status.
    - ``predictions`` — per-event, per-market, per-model prediction records.
    - ``health_checks`` — per-sport, per-market health check snapshots.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    # ── schema ──────────────────────────────────────────────────────────

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                git_sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running', 'completed', 'failed')),
                started_at_utc TEXT NOT NULL,
                completed_at_utc TEXT,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES predictions(id),

                -- event identity
                prediction_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                sport TEXT NOT NULL,
                market TEXT NOT NULL,
                event_start_utc TEXT,

                -- prediction metadata
                prediction_time_utc TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_artifact_hash TEXT,
                feature_schema_hash TEXT,

                -- prediction output
                predicted_side TEXT,
                probabilities_json TEXT NOT NULL,
                rationale TEXT,

                -- data provenance
                data_timestamp TEXT,
                data_age_seconds REAL,
                git_sha TEXT,

                -- lifecycle
                status TEXT NOT NULL DEFAULT 'predicted'
                    CHECK(status IN (
                        'predicted', 'settled', 'voided',
                        'superseded', 'error'
                    ))
            );

            -- Idempotency: same (run_id, event_id, sport, market, model_id)
            -- with no supersedes_id is a duplicate insert that returns the
            -- existing row rather than creating a new one.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_idempotent
                ON predictions(run_id, event_id, sport, market, model_id)
                WHERE supersedes_id IS NULL;

            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES health_checks(id),

                sport TEXT NOT NULL,
                market TEXT,
                model_id TEXT,
                status TEXT NOT NULL
                    CHECK(status IN ('HEALTHY', 'DEGRADED', 'DOWN')),
                reason TEXT,
                details_json TEXT,
                checked_at_utc TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_health_checks_sport
                ON health_checks(sport, market, checked_at_utc);
        """)
        self.conn.commit()

    # ── runs ────────────────────────────────────────────────────────────

    def start_run(self, run_id: str | None = None,
                  git_sha: str = "unknown") -> str:
        """Create a new run row and return the run_id."""
        if run_id is None:
            run_id = uuid.uuid4().hex[:12]
        now = utc_now()
        self.conn.execute(
            """INSERT OR IGNORE INTO runs(run_id, created_at, git_sha,
               started_at_utc, status)
               VALUES(?, ?, ?, ?, 'running')""",
            (run_id, now, git_sha, now),
        )
        self.conn.commit()
        return run_id

    def complete_run(self, run_id: str, status: str = "completed",
                     note: str | None = None) -> None:
        """Mark a run as completed or failed."""
        self.conn.execute(
            """UPDATE runs SET status=?, completed_at_utc=?, note=?
               WHERE run_id=?""",
            (status, utc_now(), note, run_id),
        )
        self.conn.commit()

    # ── predictions ─────────────────────────────────────────────────────

    def record_prediction(
        self,
        run_id: str,
        prediction_id: str,
        event_id: str,
        sport: str,
        market: str,
        model_id: str,
        probabilities: dict[str, float],
        *,
        event_start_utc: str | None = None,
        prediction_time_utc: str | None = None,
        model_artifact_hash: str | None = None,
        feature_schema_hash: str | None = None,
        predicted_side: str | None = None,
        rationale: str | None = None,
        data_timestamp: str | None = None,
        data_age_seconds: float | None = None,
        git_sha: str | None = None,
        supersedes_id: int | None = None,
    ) -> int:
        """Record a production prediction.

        Idempotent: if an un-superseded row for the same
        ``(run_id, event_id, sport, market, model_id)`` already exists,
        returns its ``id`` without inserting a duplicate.

        Returns the row ``id``.
        """
        now = utc_now()
        probabilities_json = json.dumps(probabilities, sort_keys=True)

        try:
            cursor = self.conn.execute(
                """INSERT INTO predictions(
                    created_at, run_id, schema_version, supersedes_id,
                    prediction_id, event_id, sport, market, event_start_utc,
                    prediction_time_utc, model_id, model_artifact_hash,
                    feature_schema_hash, predicted_side, probabilities_json,
                    rationale, data_timestamp, data_age_seconds, git_sha, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'predicted')
                ON CONFLICT(run_id, event_id, sport, market, model_id)
                WHERE supersedes_id IS NULL
                DO UPDATE SET id=id
                RETURNING id""",
                (
                    now, run_id, SCHEMA_VERSION,
                    supersedes_id,
                    prediction_id, event_id, sport, market,
                    event_start_utc,
                    prediction_time_utc or now, model_id,
                    model_artifact_hash,
                    feature_schema_hash, predicted_side,
                    probabilities_json,
                    rationale, data_timestamp, data_age_seconds,
                    git_sha,
                ),
            )
            row = cursor.fetchone()
            self.conn.commit()
            return row["id"]
        except Exception:
            self.conn.rollback()
            raise

    def get_predictions(
        self, run_id: str | None = None, sport: str | None = None,
        market: str | None = None, model_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query predictions, optionally filtered."""
        conditions = ["supersedes_id IS NULL"]
        params: list[Any] = []
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if sport is not None:
            conditions.append("sport = ?")
            params.append(sport)
        if market is not None:
            conditions.append("market = ?")
            params.append(market)
        if model_id is not None:
            conditions.append("model_id = ?")
            params.append(model_id)
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT * FROM predictions WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [_prediction_row(r) for r in rows]

    # ── health checks ───────────────────────────────────────────────────

    def record_health_check(
        self,
        run_id: str,
        sport: str,
        status: str,
        *,
        market: str | None = None,
        model_id: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        checked_at_utc: str | None = None,
    ) -> int:
        """Record a health check snapshot."""
        now = utc_now()
        cursor = self.conn.execute(
            """INSERT INTO health_checks(
                created_at, run_id, schema_version,
                sport, market, model_id, status, reason, details_json,
                checked_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id""",
            (
                now, run_id, SCHEMA_VERSION,
                sport, market, model_id, status, reason,
                json.dumps(details, sort_keys=True) if details else None,
                checked_at_utc or now,
            ),
        )
        row = cursor.fetchone()
        self.conn.commit()
        return row["id"]

    def latest_health(
        self, sport: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the most recent health check for each sport/market.

        Optionally filtered to a single sport.
        """
        where = "WHERE supersedes_id IS NULL"
        params: list[Any] = []
        if sport is not None:
            where += " AND sport = ?"
            params.append(sport)
        rows = self.conn.execute(
            f"""SELECT h.* FROM health_checks h
                INNER JOIN (
                    SELECT sport, market, MAX(created_at) AS max_created
                    FROM health_checks {where}
                    GROUP BY sport, market
                ) latest
                ON h.sport = latest.sport
                AND (h.market = latest.market OR (h.market IS NULL AND latest.market IS NULL))
                AND h.created_at = latest.max_created
                ORDER BY h.sport, h.market""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # ── lifecycle ───────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ProductionLedger:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _prediction_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    prob = d.get("probabilities_json")
    if isinstance(prob, str):
        try:
            d["probabilities"] = json.loads(prob)
        except json.JSONDecodeError:
            d["probabilities"] = {}
    return d
