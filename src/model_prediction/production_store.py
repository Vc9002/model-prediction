"""ProductionPredictionStore — the narrow persistence API for production.

Consolidation item 4: business logic never contains hand-written SQL or
workbook operations. This store owns ``production.db`` under the runtime
root (see RuntimePaths) and exposes a small surface — runs, predictions
with a unique identity, operator decisions, market snapshots — plus xlsx
as an explicit ``export`` operation.

Prediction identity: ``(event_id, model_id, market_type, horizon,
decision_time_utc)``. Re-running a job with identical inputs is a no-op
(append returns None on duplicate), never a duplicate row. Superseding a
prediction writes a NEW row with a new decision_time, so the unique index
is partial (``WHERE status = 'predicted'``) exactly like the legacy
ProductionLedger's.

The legacy repo-local files (``data/production/predictions.db``,
``data/production_state.json``) are carried into the runtime root exactly
once by ``runtime_paths.migrate_legacy_state``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .runtime_paths import RuntimePaths, migrate_legacy_state

_SCHEMA = """
-- Legacy-compatible shape: a migrated database (the canary's historical
-- predictions.db carried over by migrate_legacy_state) already has these
-- tables with NOT NULLs and CHECK constraints, and CREATE TABLE IF NOT
-- EXISTS leaves them untouched. The store therefore matches the legacy
-- schema exactly and ADDS its own columns via _migrate_columns below.
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    git_sha        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'failed')),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    note           TEXT,
    counters       TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at           TEXT NOT NULL,
    run_id               TEXT NOT NULL REFERENCES runs(run_id),
    schema_version       TEXT NOT NULL,
    supersedes_id        INTEGER,

    prediction_id        TEXT NOT NULL,
    event_id             TEXT NOT NULL,
    canonical_event_id   TEXT,
    sport                TEXT NOT NULL,
    market               TEXT NOT NULL,
    market_type          TEXT NOT NULL DEFAULT '',
    event_start_utc      TEXT,
    horizon              TEXT NOT NULL DEFAULT 'game',
    decision_time_utc    TEXT NOT NULL DEFAULT '',

    prediction_time_utc  TEXT NOT NULL,
    model_id             TEXT NOT NULL,
    model_artifact_hash  TEXT,
    feature_schema_hash  TEXT,

    predicted_side       TEXT,
    probabilities_json   TEXT NOT NULL,
    rationale            TEXT,

    note                 TEXT,
    data_timestamp       TEXT,
    git_sha              TEXT,

    status               TEXT NOT NULL DEFAULT 'predicted'
        CHECK(status IN ('predicted', 'settled', 'voided',
                         'superseded', 'error')),
    resolved_outcome     TEXT
        CHECK(resolved_outcome IN ('won', 'lost', 'void')),
    settled_at_utc       TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id      TEXT NOT NULL,
    decision_time_utc  TEXT NOT NULL,
    operator           TEXT NOT NULL,
    action             TEXT NOT NULL,
    note               TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT NOT NULL,
    sport            TEXT NOT NULL,
    market           TEXT NOT NULL,
    captured_at_utc  TEXT NOT NULL,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_event
    ON market_snapshots (event_id, market, captured_at_utc);
"""

_IDENTITY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_identity
    ON predictions (event_id, model_id, market_type, horizon, decision_time_utc)
    WHERE status = 'predicted';
"""

_RUN_STATUSES = ("running", "completed", "failed")
_PREDICTION_STATUSES = ("predicted", "settled", "voided", "superseded", "error")


class ProductionPredictionStore:
    """SQLite-backed production persistence (WAL, busy_timeout, FK checks)."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        migrate_legacy_state(paths)
        paths.production_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(paths.production_db, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        """Bring a migrated legacy database up to the store's contract.

        ``CREATE TABLE IF NOT EXISTS`` leaves an existing legacy table
        untouched — the canary's historical predictions.db has no
        ``market_type``/``horizon``/``decision_time_utc``/``canonical_event_id``
        columns and no ``runs.counters``. Add them (NOT NULL adds carry a
        DEFAULT, per SQLite's rules), backfill ``decision_time_utc`` from
        the legacy ``prediction_time_utc`` so the new identity index can
        be created without collisions, then create that index.
        """
        for table, column, ddl in (
            ("predictions", "canonical_event_id", "TEXT"),
            ("predictions", "market_type", "TEXT NOT NULL DEFAULT ''"),
            ("predictions", "horizon", "TEXT NOT NULL DEFAULT 'game'"),
            ("predictions", "decision_time_utc", "TEXT NOT NULL DEFAULT ''"),
            ("runs", "counters", "TEXT"),
        ):
            self._ensure_column(table, column, ddl)
        with self._conn:
            self._conn.execute(
                "UPDATE predictions SET decision_time_utc = prediction_time_utc "
                "WHERE decision_time_utc = ''"
            )
        # The query/identity indexes reference the migrated columns, so
        # they can only be created after _ensure_column above (a legacy
        # table lacks market_type when _SCHEMA runs).
        self._conn.execute(_IDENTITY_INDEX)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_query "
            "ON predictions (sport, market_type, status, event_start_utc)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_model "
            "ON predictions (model_id, event_start_utc)"
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            with self._conn:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── runs ───────────────────────────────────────────────────────────────

    def start_run(self, *, git_sha: str | None = None) -> str:
        run_id = f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, created_at, git_sha, status, "
                "started_at_utc) VALUES (?, ?, ?, 'running', ?)",
                (
                    run_id,
                    datetime.now(UTC).isoformat(),
                    git_sha or "unknown",
                    datetime.now(UTC).isoformat(),
                ),
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        note: str | None = None,
        counters: dict[str, Any] | None = None,
    ) -> None:
        if status not in _RUN_STATUSES:
            raise ValueError(f"status must be one of {_RUN_STATUSES}")
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET status = ?, completed_at_utc = ?, note = ?, "
                "counters = ? WHERE run_id = ?",
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    note,
                    json.dumps(counters) if counters else None,
                    run_id,
                ),
            )

    # ── predictions ────────────────────────────────────────────────────────

    def append_prediction(
        self,
        *,
        run_id: str,
        prediction_id: str,
        event_id: str,
        sport: str,
        market: str,
        market_type: str,
        model_id: str,
        probabilities: dict[str, Any],
        decision_time_utc: str,
        horizon: str = "game",
        canonical_event_id: str | None = None,
        event_start_utc: str | None = None,
        prediction_time_utc: str | None = None,
        model_artifact_hash: str | None = None,
        feature_schema_hash: str | None = None,
        predicted_side: str | None = None,
        rationale: str | None = None,
        git_sha: str | None = None,
    ) -> int | None:
        """Insert one prediction; None when the identity already exists.

        Idempotent by ``(event_id, model_id, market_type, horizon,
        decision_time_utc)`` — a re-fired cycle with identical inputs is a
        no-op instead of a duplicate row.
        """
        # The run must exist — absent runs are a caller bug, not a state
        # to normalize (an empty-string run_id would silently orphan the
        # row from its run lineage).
        if self._conn.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone() is None:
            raise ValueError(f"run_id {run_id!r} does not exist in runs")
        prediction_time = prediction_time_utc or decision_time_utc
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO predictions (prediction_id, created_at, run_id, "
                "schema_version, event_id, canonical_event_id, sport, market, "
                "market_type, model_id, horizon, decision_time_utc, "
                "probabilities_json, event_start_utc, prediction_time_utc, "
                "model_artifact_hash, feature_schema_hash, predicted_side, "
                "rationale, git_sha, status) "
                "VALUES (:prediction_id, :created_at, :run_id, :schema_version, "
                ":event_id, :canonical_event_id, :sport, :market, :market_type, "
                ":model_id, :horizon, :decision_time_utc, :probabilities, "
                ":event_start_utc, :prediction_time_utc, :model_artifact_hash, "
                ":feature_schema_hash, :predicted_side, :rationale, :git_sha, "
                "'predicted') "
                "ON CONFLICT (event_id, model_id, market_type, horizon, "
                "decision_time_utc) WHERE status = 'predicted' DO NOTHING",
                {
                    "prediction_id": prediction_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "run_id": run_id,
                    "schema_version": "3",
                    "event_id": event_id,
                    "canonical_event_id": canonical_event_id,
                    "sport": sport,
                    "market": market,
                    "market_type": market_type,
                    "model_id": model_id,
                    "horizon": horizon,
                    "decision_time_utc": decision_time_utc,
                    "probabilities": json.dumps(probabilities),
                    "event_start_utc": event_start_utc,
                    "prediction_time_utc": prediction_time,
                    "model_artifact_hash": model_artifact_hash,
                    "feature_schema_hash": feature_schema_hash,
                    "predicted_side": predicted_side,
                    "rationale": rationale,
                    "git_sha": git_sha,
                },
            )
            return cursor.lastrowid if cursor.rowcount else None

    def _transition(self, row_id: int, status: str, note: str | None) -> dict[str, Any]:
        if status not in _PREDICTION_STATUSES:
            raise ValueError(f"status must be one of {_PREDICTION_STATUSES}")
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE predictions SET status = ?, note = ? WHERE id = ? "
                "AND status = 'predicted'",
                (status, note, row_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"prediction {row_id} does not exist or is not in "
                    "'predicted' state (terminal states are final)"
                )
            return self.get_prediction(row_id)  # type: ignore[return-value]

    def settle_prediction(
        self, row_id: int, outcome: str, *, note: str | None = None
    ) -> dict[str, Any]:
        if outcome not in ("won", "lost", "void"):
            raise ValueError("outcome must be won, lost, or void")
        # ONE transaction: a crash between "outcome recorded" and "status
        # transitioned" must never leave status=predicted with a resolved
        # outcome — settlement is atomic, all fields or none.
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE predictions SET status = 'settled', "
                "resolved_outcome = ?, settled_at_utc = ?, note = ? "
                "WHERE id = ? AND status = 'predicted'",
                (outcome, datetime.now(UTC).isoformat(), note, row_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"prediction {row_id} does not exist or is not in "
                    "'predicted' state (terminal states are final)"
                )
            return self.get_prediction(row_id)  # type: ignore[return-value]

    def void_prediction(self, row_id: int, *, note: str | None = None) -> dict[str, Any]:
        return self._transition(row_id, "voided", note)

    def supersede_prediction(
        self, row_id: int, *, note: str | None = None
    ) -> dict[str, Any]:
        return self._transition(row_id, "superseded", note)

    def mark_prediction_error(
        self, row_id: int, *, note: str | None = None
    ) -> dict[str, Any]:
        return self._transition(row_id, "error", note)

    # ── decisions & snapshots ──────────────────────────────────────────────

    def record_decision(
        self,
        prediction_id: str,
        operator: str,
        action: str,
        *,
        note: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO decisions (prediction_id, decision_time_utc, "
                "operator, action, note) VALUES (?, ?, ?, ?, ?)",
                (prediction_id, datetime.now(UTC).isoformat(), operator, action, note),
            )

    def record_market_snapshot(
        self, event_id: str, sport: str, market: str, payload: dict[str, Any]
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO market_snapshots (event_id, sport, market, "
                "captured_at_utc, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event_id,
                    sport,
                    market,
                    datetime.now(UTC).isoformat(),
                    json.dumps(payload, sort_keys=True),
                ),
            )

    # ── reads ──────────────────────────────────────────────────────────────

    def get_prediction(self, row_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM predictions WHERE id = ?", (row_id,)
        ).fetchone()
        return _decode(row)

    def get_predictions(
        self,
        *,
        sport: str | None = None,
        market_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: int | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Keyset-paginated predictions, newest first (``id`` cursor)."""
        query = "SELECT * FROM predictions"
        clauses: list[str] = []
        params: list[Any] = []
        if sport is not None:
            clauses.append("sport = ?")
            params.append(sport)
        if market_type is not None:
            clauses.append("market_type = ?")
            params.append(market_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if cursor is not None:
            clauses.append("id < ?")
            params.append(cursor)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit) + 1)
        rows = self._conn.execute(query, params).fetchall()
        has_more = len(rows) > int(limit)
        rows = rows[: int(limit)]
        next_cursor = rows[-1]["id"] if has_more and rows else None
        return [_decode(r) for r in rows], next_cursor

    def counts_by(self, *, sport: str | None = None, status: str | None = None) -> dict[str, int]:
        """Aggregated counts via SQL GROUP BY — no full-table Python scans."""
        query = "SELECT status, COUNT(*) AS n FROM predictions"
        clauses: list[str] = []
        params: list[Any] = []
        if sport is not None:
            clauses.append("sport = ?")
            params.append(sport)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY status"
        return {
            str(row["status"]): int(row["n"])
            for row in self._conn.execute(query, params).fetchall()
        }

    def latest_prediction_utc(self) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(prediction_time_utc) AS latest FROM predictions"
        ).fetchone()
        return str(row["latest"]) if row and row["latest"] else None

    # ── export (xlsx is an explicit operation, not the database) ───────────

    def export_xlsx(self, path: Path | str) -> int:
        """Write all predictions to an xlsx workbook for human review."""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "predictions"
        columns = [
            "id", "prediction_id", "event_id", "sport", "market_type",
            "model_id", "horizon", "decision_time_utc", "probabilities",
            "predicted_side", "status", "resolved_outcome", "settled_at_utc",
        ]
        ws.append(columns)
        count = 0
        for row in self._conn.execute(
            "SELECT * FROM predictions ORDER BY id"
        ).fetchall():
            decoded = _decode(row)
            ws.append([_cell_value(decoded.get(c)) for c in columns])
            count += 1
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        return count


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if data.get("probabilities_json"):
        try:
            data["probabilities"] = json.loads(data["probabilities_json"])
        except json.JSONDecodeError:
            pass
        data.pop("probabilities_json", None)
    if data.get("counters"):
        try:
            data["counters"] = json.loads(data["counters"])
        except json.JSONDecodeError:
            pass
    return data


def _cell_value(value: Any) -> Any:
    """openpyxl can't write dict/list cells — serialize them for export."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


# ── read-only health helpers (consolidation item 12) ────────────────────────
# Health must read the CANONICAL database, never the legacy
# production_state.json — one operational truth, one storage.
# Connections open mode=ro so a health check can never migrate or write.


def _ro_health_conn(paths: RuntimePaths) -> sqlite3.Connection | None:
    if not paths.production_db.is_file():
        return None
    conn = sqlite3.connect(f"file:{paths.production_db}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def read_latest_prediction_utc(paths: RuntimePaths) -> str | None:
    """Latest prediction timestamp from production.db, or None when the
    database (or the table) doesn't exist yet."""
    conn = _ro_health_conn(paths)
    if conn is None:
        return None
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='predictions'"
        ).fetchone() is None:
            return None
        row = conn.execute(
            "SELECT MAX(prediction_time_utc) AS latest FROM predictions"
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()


def read_recent_probabilities(paths: RuntimePaths, limit: int = 20) -> list[dict[str, float]]:
    """The most recent stored binary probability pairs, newest first."""
    conn = _ro_health_conn(paths)
    if conn is None:
        return []
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='predictions'"
        ).fetchone() is None:
            return []
        out: list[dict[str, float]] = []
        for row in conn.execute(
            "SELECT probabilities_json FROM predictions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall():
            try:
                probs = json.loads(row[0])
            except json.JSONDecodeError:
                continue
            out.append({str(k): float(v) for k, v in probs.items()})
        return out
    finally:
        conn.close()
