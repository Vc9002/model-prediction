"""SQLite production prediction ledger.

Stored at ``<runtime_root>/production/predictions.db``.  Every production
prediction, health check snapshot, and run record is stored here, with
idempotency keys so re-running the same production cycle with identical
inputs is a no-op.

Design contract (same as ``ShadowLedger``):

- ``predictions`` and ``health_checks`` are append-only for *events*: no
  DELETE anywhere, and a correction (new forecast) is a new INSERT whose
  ``supersedes_id`` points at the row it replaces.
- Lifecycle is an explicit, guarded state machine, not a raw write path.
  A prediction moves ``predicted`` -> ``settled``/``voided``/``superseded``/
  ``error`` only through the transition methods (``settle_prediction`` &
  friends), which refuse to touch a row that is not still open and record
  when the row reached its terminal state.  A settled row must stay the
  canonical, visible row carrying its outcome, so settlement is an in-place
  status update via the transition API — the append-only rule covers new
  events and corrections, not lifecycle fields on the row itself.
- ``runs`` keeps a mutable lifecycle status (``running`` -> ``completed``/
  ``failed``) updated only through ``start_run``/``complete_run``; a run row
  is an audit entry for a cycle, not a prediction event, so this is the
  documented exception to the append-only rule for event rows.
- Every row carries ``created_at``, ``run_id``, ``schema_version``.
- ``predictions`` enforce a UNIQUE index on ``(run_id, event_id, sport,
  market, model_id) WHERE supersedes_id IS NULL`` — rerunning the same
  job with identical inputs returns the existing row; an explicit correction
  (``supersedes_id`` set) bypasses the index and always appends.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

SCHEMA_VERSION = "2"

# Lifecycle state machine for prediction rows: open until one of the
# terminal transitions below is applied; terminal rows are immutable.
OPEN_PREDICTION_STATUS = "predicted"
TERMINAL_PREDICTION_STATUSES = frozenset({"settled", "voided", "superseded", "error"})
RUN_TERMINAL_STATUSES = frozenset({"completed", "failed"})
RESOLVED_OUTCOMES = frozenset({"won", "lost", "void"})


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

                -- lifecycle annotation (transition note, e.g. why voided)
                note TEXT,

                -- data provenance
                data_timestamp TEXT,
                data_age_seconds REAL,
                git_sha TEXT,

                -- lifecycle
                status TEXT NOT NULL DEFAULT 'predicted'
                    CHECK(status IN (
                        'predicted', 'settled', 'voided',
                        'superseded', 'error'
                    )),
                resolved_outcome TEXT
                    CHECK(resolved_outcome IN ('won', 'lost', 'void')),
                settled_at_utc TEXT
            );

            -- Idempotency: same (run_id, event_id, sport, market, model_id)
            -- for a LIVE (predicted) row is a duplicate insert that returns
            -- the existing row rather than creating a new one. Only
            -- status='predicted' rows hold the slot: a superseded row must
            -- release it so its replacement (inserted with supersedes_id
            -- set, status predicted) becomes the row a re-fired identical
            -- cycle collides with -- before this predicate, the conflict
            -- hit the old superseded row and returned its stale id.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_idempotent
                ON predictions(run_id, event_id, sport, market, model_id)
                WHERE status = 'predicted';

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
        # Additive migration for databases created before the lifecycle
        # columns existed (the live production DB predates them, and
        # CREATE TABLE IF NOT EXISTS will not touch an existing table).
        # A NULL resolved_outcome/settled_at_utc passes the CHECK, so this
        # is safe for existing rows.
        self._ensure_column(
            "predictions", "resolved_outcome",
            "resolved_outcome TEXT CHECK(resolved_outcome IN ('won', 'lost', 'void'))",
        )
        self._ensure_column("predictions", "settled_at_utc", "settled_at_utc TEXT")
        self._ensure_column("predictions", "note", "note TEXT")
        self._ensure_idempotency_index()
        self.conn.commit()

    def _ensure_idempotency_index(self) -> None:
        """Migrate databases whose idempotency index predates the
        status='predicted' predicate (SCHEMA_VERSION 1).

        ``CREATE UNIQUE INDEX IF NOT EXISTS`` never rewrites an existing
        index, so the old ``WHERE supersedes_id IS NULL`` predicate would
        survive in every live database and keep superseded rows occupying
        the idempotency slot. Drop and recreate when the stored SQL doesn't
        already carry the corrected predicate.
        """
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_predictions_idempotent'"
        ).fetchone()
        if row is not None and "status = 'predicted'" not in (row[0] or ""):
            self.conn.execute("DROP INDEX idx_predictions_idempotent")
            self.conn.execute(
                """CREATE UNIQUE INDEX idx_predictions_idempotent
                   ON predictions(run_id, event_id, sport, market, model_id)
                   WHERE status = 'predicted'"""
            )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        """Add *column* to *table* if it is missing (idempotent)."""
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

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
        """Transition a run to its terminal state (``completed`` or ``failed``).

        Explicit run-lifecycle transition — the only UPDATE the runs table
        ever takes.  Runs are audit entries for a whole cycle, so their
        status is a mutable lifecycle field (documented exception to the
        append-only rule for prediction/health *event* rows).  Guarded the
        same way as prediction transitions: only ``running`` -> terminal,
        and an already-terminal run is left untouched (idempotent no-op —
        the scheduler re-fires every cycle and a completed run must not be
        re-stamped).  Raises ``ValueError`` for an unknown run or an
        invalid status value.
        """
        if status not in RUN_TERMINAL_STATUSES:
            raise ValueError(
                f"run status must be one of {sorted(RUN_TERMINAL_STATUSES)}; "
                f"got {status!r}"
            )
        cursor = self.conn.execute(
            """UPDATE runs SET status=?, completed_at_utc=?, note=?
               WHERE run_id=? AND status='running'""",
            (status, utc_now(), note, run_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            row = self.conn.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"no run with run_id={run_id!r}")
            # Already terminal: idempotent no-op, see docstring. A
            # running -> terminal retry (e.g. a cycle that completed then
            # was re-reported) must not re-stamp completed_at_utc.
            return

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
                WHERE status = 'predicted'
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
        """Query the *current* predictions view, optionally filtered.

        The current view is every row that is not itself ``superseded``:
        a correction's replacement row (``supersedes_id`` set) is the
        current record of that prediction and must show up, while the
        superseded original drops out.  Terminal rows (settled/voided/
        error) stay visible — they carry the resolved outcome.
        """
        conditions = ["status != 'superseded'"]
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

    def get_prediction(self, row_id: int) -> dict[str, Any] | None:
        """Return a single prediction row by its integer id (or None)."""
        row = self.conn.execute(
            "SELECT * FROM predictions WHERE id=?", (row_id,)
        ).fetchone()
        return _prediction_row(row) if row is not None else None

    # ── lifecycle transitions ───────────────────────────────────────────

    def transition_prediction(
        self,
        row_id: int,
        to_status: str,
        *,
        outcome: str | None = None,
        note: str | None = None,
        at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Transition an open prediction to a terminal status, atomically.

        Guardrails (point-in-time sane — the row's decision is frozen once
        the cycle reported it, and re-litigating a settled outcome is how
        track records get corrupted):

        - Only ``predicted`` (open) -> one of ``settled``/``voided``/
          ``superseded``/``error``.  A terminal row can never be
          re-transitioned; a second call raises ``ValueError``.
        - ``settled`` requires *outcome* in ``{won, lost, void}``; the
          outcome ``void`` maps to the terminal status ``voided`` (a
          no-action result is a void, not a settlement), so the ``void``
          outcome and the ``voided`` status stay in lockstep.
        - *outcome* is rejected for any status other than ``settled``.
        - The guard is enforced in the UPDATE predicate, so it holds under
          concurrent writers, not just in this process.

        Returns the updated row dict.  ``settled_at_utc`` records when the
        row reached its terminal state (the name is kept because
        settlement is the dominant case; it is set for every transition).
        """
        if to_status not in TERMINAL_PREDICTION_STATUSES:
            raise ValueError(
                f"transition target {to_status!r} is not a terminal status; "
                f"use one of {sorted(TERMINAL_PREDICTION_STATUSES)}"
            )
        if to_status == "settled":
            if outcome not in RESOLVED_OUTCOMES:
                raise ValueError(
                    f"settling requires outcome in {sorted(RESOLVED_OUTCOMES)}; "
                    f"got {outcome!r}"
                )
            terminal_status = "voided" if outcome == "void" else "settled"
        else:
            if outcome is not None:
                raise ValueError(
                    f"outcome is only valid when settling; got {outcome!r} "
                    f"for {to_status!r}"
                )
            terminal_status = to_status
        now = at_utc or utc_now()
        cursor = self.conn.execute(
            """UPDATE predictions
               SET status=?, resolved_outcome=?, settled_at_utc=?, note=?
               WHERE id=? AND status=?""",
            (terminal_status, outcome, now, note, row_id, OPEN_PREDICTION_STATUS),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            row = self.get_prediction(row_id)
            if row is None:
                raise ValueError(f"no prediction row with id={row_id}")
            raise ValueError(
                f"prediction {row_id} is already '{row['status']}'; "
                f"only '{OPEN_PREDICTION_STATUS}' -> terminal transitions "
                f"are allowed"
            )
        return self.get_prediction(row_id)

    def settle_prediction(
        self, row_id: int, outcome: str, *, note: str | None = None,
        at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a prediction with outcome ``won``/``lost``/``void``.

        The ``void`` outcome produces the terminal status ``voided``.
        """
        return self.transition_prediction(
            row_id, "settled", outcome=outcome, note=note, at_utc=at_utc
        )

    def void_prediction(
        self, row_id: int, *, note: str | None = None,
        at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Void an open prediction (no-action result)."""
        return self.transition_prediction(
            row_id, "settled", outcome="void", note=note, at_utc=at_utc
        )

    def supersede_prediction(
        self, row_id: int, *, note: str | None = None,
        at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Mark a prediction superseded; append its replacement separately.

        The correction flow stays append-only: mark the old row
        ``superseded`` here, then call ``record_prediction`` with
        ``supersedes_id=row_id`` — a superseding row bypasses the
        idempotency index and always appends, and ``get_predictions``
        returns only the replacement.
        """
        return self.transition_prediction(
            row_id, "superseded", note=note, at_utc=at_utc
        )

    def mark_prediction_error(
        self, row_id: int, *, note: str | None = None,
        at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Flag an open prediction as errored (e.g. bad data surfaced late).

        *note* should say what went wrong — it is the only record of why
        the row was invalidated.
        """
        return self.transition_prediction(
            row_id, "error", note=note, at_utc=at_utc
        )

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

        One row per sport/market. The previous version joined on exact
        ``created_at = MAX(created_at)``, so two checks recorded in the same
        microsecond (utc_now() returns identical values for rapid back-to-
        back writes) both matched and produced duplicate "latest" rows; a
        deterministic tiebreak on id (later insert wins) fixes that.
        """
        where = "WHERE supersedes_id IS NULL"
        params: list[Any] = []
        if sport is not None:
            where += " AND sport = ?"
            params.append(sport)
        rows = self.conn.execute(
            f"""SELECT * FROM (
                    SELECT h.*, ROW_NUMBER() OVER (
                        PARTITION BY sport, market
                        ORDER BY created_at DESC, id DESC
                    ) AS _rn
                    FROM health_checks h {where}
                ) ranked
                WHERE _rn = 1
                ORDER BY sport, market""",
            params,
        ).fetchall()
        # Drop the ranking helper column from the public result shape.
        return [{k: v for k, v in dict(r).items() if k != "_rn"} for r in rows]

    # ── lifecycle ───────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
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
