"""Append-only SQLite shadow ledger (FOUNDATION_COMPLETION.md Phase 12).

Stored at data/rebuild/shadow.db. This is the durable persistence layer that
turns a series of disconnected `mlb_shadow_run.py`-style JSON reports into a
queryable history: every prediction, market observation, evaluated market,
and BET/NO_BET decision this session records, plus paper fills and
settlements, in one place, forever.

Design contract (non-negotiable, per the plan):

- `predictions`, `market_snapshots`, `market_evaluations`, and
  `trade_decisions` are append-only. There is no UPDATE or DELETE path
  anywhere on this class for any table. A "correction" is a new INSERT
  whose `supersedes_id` points at the row it replaces -- the original row
  is never touched.
- Every row carries `created_at`, `run_id`, `sport`, `event_id` (where
  applicable), `schema_version`, `supersedes_id` (where applicable).
- `trade_decisions` (and, so corrections to a forecast don't silently
  duplicate, `predictions`) enforce a real idempotency key so rerunning the
  same job with identical inputs is a no-op, not a duplicate row. This is
  implemented as a genuine SQLite UNIQUE index scoped to
  `WHERE supersedes_id IS NULL` -- non-superseding duplicates collide and
  return the existing row; an explicit correction (caller passes
  `supersedes_id`) is exempt from the index and always appends.

This module has zero import dependency on `decision.py` -- `_as_dict()`
duck-types any dataclass instance (or plain dict) into the columns it needs,
so it can store real `SportsForecast` / `MarketEvaluation` / `BetDecision`
instances directly without reinventing a second, parallel definition of the
same fields.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _as_dict(obj: Any) -> dict[str, Any]:
    """Accept either a dataclass instance (decision.py's SportsForecast /
    MarketEvaluation / BetDecision, or anything shaped like them) or a plain
    dict. Duck-typed on purpose -- see module docstring."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"expected a dataclass instance or dict, got {type(obj).__name__}")


class ShadowLedger:
    """Central append-only shadow-execution ledger for the rebuild platform.

    Tables (see FOUNDATION_COMPLETION.md Phase 12 for the required list):

    Fully implemented (insert + read methods, tested):
        runs, predictions, market_snapshots, market_evaluations,
        trade_decisions, paper_orders, settlements, audit_events

    Schema-only (CREATE TABLE exists, no insert/query methods yet -- marked
    with a TODO comment in _init_tables()):
        raw_snapshots, normalized_observations, feature_snapshots,
        dataset_manifests, model_artifacts, calibration_artifacts,
        closing_prices, reviews
    """

    TABLES: tuple[str, ...] = (
        "runs",
        "raw_snapshots",
        "normalized_observations",
        "feature_snapshots",
        "dataset_manifests",
        "model_artifacts",
        "calibration_artifacts",
        "predictions",
        "market_snapshots",
        "market_evaluations",
        "trade_decisions",
        "paper_orders",
        "settlements",
        "closing_prices",
        "reviews",
        "audit_events",
    )

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

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                sport TEXT NOT NULL,
                run_type TEXT NOT NULL,
                horizon TEXT,
                status TEXT NOT NULL DEFAULT 'started',
                params_json TEXT,
                schema_version TEXT NOT NULL
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            -- Fields follow FOUNDATION_COMPLETION.md Phase 2's raw snapshot
            -- record contract.
            CREATE TABLE IF NOT EXISTS raw_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES raw_snapshots(id),
                source TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                observed_at_utc TEXT NOT NULL,
                effective_at_utc TEXT,
                ingested_at_utc TEXT,
                snapshot_hash TEXT NOT NULL,
                path TEXT,
                http_status INTEGER,
                request_params_hash TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS normalized_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES normalized_observations(id),
                table_name TEXT NOT NULL,
                primary_key_json TEXT NOT NULL,
                observed_at_utc TEXT,
                source TEXT,
                raw_snapshot_hash TEXT,
                payload_json TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS feature_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT,
                horizon TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES feature_snapshots(id),
                decision_time_utc TEXT,
                feature_schema_version TEXT,
                dataset_hash TEXT,
                row_count INTEGER,
                payload_path TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS dataset_manifests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES dataset_manifests(id),
                horizon TEXT,
                dataset_hash TEXT NOT NULL,
                split_manifest_json TEXT,
                final_test_start TEXT,
                final_test_end TEXT,
                final_test_consumed INTEGER NOT NULL DEFAULT 0
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS model_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES model_artifacts(id),
                model_name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                market_family TEXT,
                horizon TEXT,
                training_start TEXT,
                training_end TEXT,
                dataset_hash TEXT,
                split_manifest_hash TEXT,
                code_revision TEXT,
                dependency_lock_hash TEXT,
                artifact_hash TEXT NOT NULL,
                artifact_path TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS calibration_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES calibration_artifacts(id),
                model_artifact_hash TEXT NOT NULL,
                calibration_hash TEXT NOT NULL,
                method TEXT,
                fitted_on_hash TEXT
            );

            -- Fully implemented: record_prediction / get_prediction /
            -- predictions_for_event. Mirrors decision.py's SportsForecast.
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                horizon TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES predictions(id),
                decision_time_utc TEXT NOT NULL,
                predicted_winner TEXT,
                raw_probabilities_json TEXT,
                calibrated_probabilities_json TEXT,
                probability_lower_json TEXT,
                probability_upper_json TEXT,
                expected_home_score REAL,
                expected_away_score REAL,
                model_artifact_hash TEXT NOT NULL,
                calibration_artifact_hash TEXT NOT NULL,
                totals_probabilities_json TEXT,
                spread_probabilities_json TEXT
            );

            -- "Ideally" idempotent per the plan: a rerun with identical
            -- inputs and no explicit supersedes_id must not duplicate.
            CREATE UNIQUE INDEX IF NOT EXISTS ux_predictions_idempotency
            ON predictions(
                sport, event_id, horizon, decision_time_utc,
                model_artifact_hash, calibration_artifact_hash
            )
            WHERE supersedes_id IS NULL;

            -- Fully implemented: record_market_snapshot. Append-only, keyed
            -- per FOUNDATION_COMPLETION.md Phase 2's market snapshot key
            -- (market_id, side_id, line, period, observed_at_utc).
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES market_snapshots(id),
                market_id TEXT NOT NULL,
                side_id TEXT NOT NULL,
                line REAL,
                period TEXT NOT NULL,
                observed_at_utc TEXT NOT NULL,
                best_bid REAL,
                best_ask REAL,
                depth_json TEXT,
                market_state TEXT,
                quote_age_seconds REAL,
                order_book_hash TEXT,
                content_hash TEXT NOT NULL
            );

            -- Fully implemented: record_market_evaluation. Mirrors
            -- decision.py's MarketEvaluation field-for-field.
            CREATE TABLE IF NOT EXISTS market_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES market_evaluations(id),
                decision_time_utc TEXT,
                market_id TEXT NOT NULL,
                market_type TEXT NOT NULL,
                team_or_side TEXT NOT NULL,
                line REAL,
                executable_ask REAL,
                depth_adjusted_price REAL,
                quote_age_seconds REAL,
                available_depth REAL
            );

            -- Fully implemented: record_trade_decision. Mirrors decision.py's
            -- BetDecision plus the plan's required idempotency key.
            CREATE TABLE IF NOT EXISTS trade_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                horizon TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES trade_decisions(id),
                decision_time_utc TEXT NOT NULL,
                model_artifact_hash TEXT NOT NULL,
                market_snapshot_hash TEXT NOT NULL,
                decision_policy_version TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('BET','NO_BET')),
                predicted_winner TEXT,
                market_type TEXT,
                units REAL NOT NULL,
                reason_code TEXT,
                cost_adjusted_edge REAL,
                selected_market_evaluation_id INTEGER REFERENCES market_evaluations(id),
                evaluated_market_evaluation_id INTEGER REFERENCES market_evaluations(id)
            );

            -- The required idempotency key (sport, event_id, horizon,
            -- decision_time_utc, model_artifact_hash, market_snapshot_hash,
            -- decision_policy_version), scoped to non-superseding rows so a
            -- genuine correction (supersedes_id set) can still append even
            -- if every other field is identical to what it replaces.
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_decisions_idempotency
            ON trade_decisions(
                sport, event_id, horizon, decision_time_utc,
                model_artifact_hash, market_snapshot_hash, decision_policy_version
            )
            WHERE supersedes_id IS NULL;

            -- Fully implemented: record_paper_order.
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES paper_orders(id),
                trade_decision_id INTEGER NOT NULL REFERENCES trade_decisions(id),
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                requested_units REAL NOT NULL,
                avg_fill_price REAL,
                worst_fill_price REAL,
                filled_units REAL,
                unfilled_units REAL,
                slippage REAL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                placed_at_utc TEXT
            );

            -- Fully implemented: record_settlement.
            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES settlements(id),
                paper_order_id INTEGER REFERENCES paper_orders(id),
                trade_decision_id INTEGER REFERENCES trade_decisions(id),
                outcome TEXT NOT NULL,
                settled_price REAL,
                pnl REAL,
                settled_at_utc TEXT,
                notes TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS closing_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT NOT NULL,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES closing_prices(id),
                market_id TEXT NOT NULL,
                side_id TEXT NOT NULL,
                line REAL,
                closing_price REAL,
                observed_at_utc TEXT
            );

            -- TODO: schema only -- no insert/query methods implemented yet.
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sport TEXT,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES reviews(id),
                subject_table TEXT,
                subject_id INTEGER,
                reviewer TEXT,
                verdict TEXT,
                notes TEXT,
                reviewed_at_utc TEXT
            );

            -- Fully implemented: record_audit_event. Hash-chained like
            -- metadata.py's MetadataDB.audit_event, ordered by the
            -- autoincrement id rather than created_at so the chain stays
            -- deterministic even when two events share a timestamp.
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_uuid TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                run_id TEXT,
                sport TEXT,
                event_id TEXT,
                schema_version TEXT NOT NULL,
                supersedes_id INTEGER REFERENCES audit_events(id),
                event_type TEXT NOT NULL,
                details_json TEXT,
                previous_hash TEXT,
                event_hash TEXT NOT NULL
            );
        """)
        self.conn.execute(
            "INSERT OR IGNORE INTO _meta(key, value) VALUES(?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        self.conn.commit()

    # ── runs ──────────────────────────────────────────────────────────

    def record_run(
        self,
        sport: str,
        run_type: str = "shadow",
        horizon: str | None = None,
        params: dict[str, Any] | None = None,
        run_id: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> str:
        """Create (or return the existing) run. Calling this twice with the
        same explicit `run_id` is a no-op on the second call -- it does not
        overwrite the first row -- matching the pipeline's "rerunning the
        same timestamped job must be idempotent" requirement at the run
        level."""
        run_id = run_id or f"{sport}_{run_type}_{uuid.uuid4().hex[:12]}"
        existing = self.conn.execute(
            "SELECT run_id FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing is not None:
            return str(existing["run_id"])
        self.conn.execute(
            """INSERT INTO runs(run_id, created_at, sport, run_type, horizon, status, params_json, schema_version)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, utc_now(), sport, run_type, horizon, "started",
             json.dumps(params) if params else None, schema_version),
        )
        self.conn.commit()
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    # ── predictions (append-only) ────────────────────────────────────

    def record_prediction(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str,
        horizon: str,
        decision_time_utc: str,
        forecast: Any,
        supersedes_id: int | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> tuple[int, bool]:
        """Insert a prediction from a SportsForecast dataclass instance (or a
        dict shaped like one). Returns (row_id, created) -- created=False
        means an identical-key row already existed and nothing new was
        inserted (idempotent rerun); created=True means a fresh row was
        appended (either the first time, or an explicit correction via
        `supersedes_id`)."""
        f = _as_dict(forecast)
        model_artifact_hash = f["model_artifact_hash"]
        calibration_artifact_hash = f["calibration_artifact_hash"]

        if supersedes_id is None:
            existing = self.conn.execute(
                """SELECT id FROM predictions
                   WHERE sport=? AND event_id=? AND horizon=? AND decision_time_utc=?
                     AND model_artifact_hash=? AND calibration_artifact_hash=?
                     AND supersedes_id IS NULL""",
                (sport, event_id, horizon, decision_time_utc,
                 model_artifact_hash, calibration_artifact_hash),
            ).fetchone()
            if existing is not None:
                return int(existing["id"]), False

        cur = self._insert_prediction_row(
            run_id, sport, event_id, horizon, schema_version, supersedes_id,
            decision_time_utc, f, model_artifact_hash, calibration_artifact_hash,
        )
        return cur, True

    def _insert_prediction_row(
        self, run_id, sport, event_id, horizon, schema_version, supersedes_id,
        decision_time_utc, f, model_artifact_hash, calibration_artifact_hash,
    ) -> int:
        try:
            cur = self.conn.execute(
                """INSERT INTO predictions(
                    created_at, run_id, sport, event_id, horizon, schema_version, supersedes_id,
                    decision_time_utc, predicted_winner, raw_probabilities_json,
                    calibrated_probabilities_json, probability_lower_json, probability_upper_json,
                    expected_home_score, expected_away_score, model_artifact_hash,
                    calibration_artifact_hash, totals_probabilities_json, spread_probabilities_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    utc_now(), run_id, sport, event_id, horizon, schema_version, supersedes_id,
                    decision_time_utc, f.get("predicted_winner"),
                    json.dumps(f.get("raw_probabilities", {})),
                    json.dumps(f.get("calibrated_probabilities", {})),
                    json.dumps(f.get("probability_lower", {})),
                    json.dumps(f.get("probability_upper", {})),
                    f.get("expected_home_score"), f.get("expected_away_score"),
                    model_artifact_hash, calibration_artifact_hash,
                    json.dumps(f.get("totals_probabilities", {})),
                    json.dumps(f.get("spread_probabilities", {})),
                ),
            )
        except sqlite3.IntegrityError:
            # Race-condition fallback: another writer inserted the identical
            # non-superseding key between our pre-check and this INSERT. The
            # UNIQUE index is the real guarantee; the pre-check above is just
            # the fast, common path.
            existing = self.conn.execute(
                """SELECT id FROM predictions
                   WHERE sport=? AND event_id=? AND horizon=? AND decision_time_utc=?
                     AND model_artifact_hash=? AND calibration_artifact_hash=?
                     AND supersedes_id IS NULL""",
                (sport, event_id, horizon, decision_time_utc,
                 model_artifact_hash, calibration_artifact_hash),
            ).fetchone()
            if existing is None:
                raise
            return int(existing["id"])
        self.conn.commit()
        return int(cur.lastrowid)

    def get_prediction(self, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM predictions WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    def predictions_for_event(self, sport: str, event_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM predictions WHERE sport=? AND event_id=? ORDER BY id",
            (sport, event_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── market_snapshots (append-only) ───────────────────────────────

    def record_market_snapshot(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str | None,
        market_id: str,
        side_id: str,
        line: float | None,
        period: str,
        observed_at_utc: str,
        best_bid: float | None = None,
        best_ask: float | None = None,
        depth: Any = None,
        market_state: str | None = None,
        quote_age_seconds: float | None = None,
        order_book_hash: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> tuple[int, bool]:
        """Insert a market observation. Idempotent on
        (market_id, side_id, line, period, observed_at_utc): a byte-identical
        repeated snapshot returns the existing row rather than duplicating
        (Phase 2: "repeated identical snapshots must be idempotent"). A
        *different* payload sharing that same key fails closed with a
        ValueError and an audit event, rather than silently storing two
        conflicting observations under what is supposed to be one immutable
        key (Phase 2: "conflicting rows ... must fail closed")."""
        content_key = json.dumps(
            {"best_bid": best_bid, "best_ask": best_ask, "depth": depth,
             "market_state": market_state, "order_book_hash": order_book_hash},
            sort_keys=True, default=str,
        )
        content_hash = hashlib.sha256(content_key.encode()).hexdigest()

        existing = self.conn.execute(
            """SELECT id, content_hash FROM market_snapshots
               WHERE market_id=? AND side_id=? AND line IS ? AND period=? AND observed_at_utc=?""",
            (market_id, side_id, line, period, observed_at_utc),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return int(existing["id"]), False
            self.record_audit_event(
                "market_snapshot_conflict",
                details={
                    "market_id": market_id, "side_id": side_id, "line": line,
                    "period": period, "observed_at_utc": observed_at_utc,
                },
                run_id=run_id, sport=sport, event_id=event_id,
            )
            raise ValueError(
                f"conflicting market_snapshot content for identical key "
                f"(market_id={market_id!r}, side_id={side_id!r}, line={line!r}, "
                f"period={period!r}, observed_at_utc={observed_at_utc!r}) -- fail closed"
            )

        cur = self.conn.execute(
            """INSERT INTO market_snapshots(
                created_at, run_id, sport, event_id, schema_version, supersedes_id,
                market_id, side_id, line, period, observed_at_utc,
                best_bid, best_ask, depth_json, market_state, quote_age_seconds,
                order_book_hash, content_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), run_id, sport, event_id, schema_version, None,
                market_id, side_id, line, period, observed_at_utc,
                best_bid, best_ask, json.dumps(depth) if depth is not None else None,
                market_state, quote_age_seconds, order_book_hash, content_hash,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def market_snapshots_for_event(self, sport: str, event_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM market_snapshots WHERE sport=? AND event_id=? ORDER BY id",
            (sport, event_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── market_evaluations (append-only) ─────────────────────────────

    def record_market_evaluation(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str,
        evaluation: Any,
        decision_time_utc: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> int:
        """Insert a MarketEvaluation dataclass instance (or dict shaped like
        one). Always appends -- market_evaluations has no idempotency-key
        requirement in the plan, only predictions and trade_decisions do."""
        e = _as_dict(evaluation)
        cur = self.conn.execute(
            """INSERT INTO market_evaluations(
                created_at, run_id, sport, event_id, schema_version, supersedes_id,
                decision_time_utc, market_id, market_type, team_or_side, line,
                executable_ask, depth_adjusted_price, quote_age_seconds, available_depth
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), run_id, sport, event_id, schema_version, None,
                decision_time_utc, e["market_id"], e["market_type"], e["team_or_side"],
                e.get("line"), e.get("executable_ask"), e.get("depth_adjusted_price"),
                e.get("quote_age_seconds"), e.get("available_depth"),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_market_evaluation(self, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM market_evaluations WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    # ── trade_decisions (append-only, idempotent) ────────────────────

    def record_trade_decision(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str,
        horizon: str,
        decision_time_utc: str,
        model_artifact_hash: str,
        market_snapshot_hash: str,
        decision_policy_version: str,
        decision: Any,
        selected_market_evaluation_id: int | None = None,
        evaluated_market_evaluation_id: int | None = None,
        supersedes_id: int | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> tuple[int, bool]:
        """Insert a BetDecision dataclass instance (or dict shaped like one).

        Idempotency key (per the plan, required):
            (sport, event_id, horizon, decision_time_utc, model_artifact_hash,
             market_snapshot_hash, decision_policy_version)

        Rerunning the same job with identical inputs and no explicit
        `supersedes_id` returns the existing row (created=False) instead of
        duplicating it. This is enforced two ways: an explicit
        check-before-insert (the fast path, and what makes the "only one row
        after two inserts" behavior deterministic and easy to test) plus a
        real SQLite UNIQUE index as the actual guarantee against races.
        """
        d = _as_dict(decision)

        if supersedes_id is None:
            existing = self._find_trade_decision(
                sport, event_id, horizon, decision_time_utc,
                model_artifact_hash, market_snapshot_hash, decision_policy_version,
            )
            if existing is not None:
                return existing, False

        try:
            cur = self.conn.execute(
                """INSERT INTO trade_decisions(
                    created_at, run_id, sport, event_id, horizon, schema_version, supersedes_id,
                    decision_time_utc, model_artifact_hash, market_snapshot_hash,
                    decision_policy_version, action, predicted_winner, market_type, units,
                    reason_code, cost_adjusted_edge, selected_market_evaluation_id,
                    evaluated_market_evaluation_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    utc_now(), run_id, sport, event_id, horizon, schema_version, supersedes_id,
                    decision_time_utc, model_artifact_hash, market_snapshot_hash,
                    decision_policy_version, d["action"], d.get("predicted_winner"),
                    d.get("market_type"), d["units"], d.get("reason_code"),
                    d.get("cost_adjusted_edge"), selected_market_evaluation_id,
                    evaluated_market_evaluation_id,
                ),
            )
        except sqlite3.IntegrityError:
            existing = self._find_trade_decision(
                sport, event_id, horizon, decision_time_utc,
                model_artifact_hash, market_snapshot_hash, decision_policy_version,
            )
            if existing is None:
                raise
            return existing, False
        self.conn.commit()
        return int(cur.lastrowid), True

    def _find_trade_decision(
        self, sport, event_id, horizon, decision_time_utc,
        model_artifact_hash, market_snapshot_hash, decision_policy_version,
    ) -> int | None:
        row = self.conn.execute(
            """SELECT id FROM trade_decisions
               WHERE sport=? AND event_id=? AND horizon=? AND decision_time_utc=?
                 AND model_artifact_hash=? AND market_snapshot_hash=? AND decision_policy_version=?
                 AND supersedes_id IS NULL""",
            (sport, event_id, horizon, decision_time_utc,
             model_artifact_hash, market_snapshot_hash, decision_policy_version),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def get_trade_decision(self, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM trade_decisions WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    def trade_decisions_for_event(self, sport: str, event_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM trade_decisions WHERE sport=? AND event_id=? ORDER BY id",
            (sport, event_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── paper_orders (append-only) ───────────────────────────────────

    def record_paper_order(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str,
        trade_decision_id: int,
        market_id: str,
        side: str,
        requested_units: float,
        avg_fill_price: float | None = None,
        worst_fill_price: float | None = None,
        filled_units: float | None = None,
        unfilled_units: float | None = None,
        slippage: float | None = None,
        status: str = "PENDING",
        placed_at_utc: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO paper_orders(
                created_at, run_id, sport, event_id, schema_version, supersedes_id,
                trade_decision_id, market_id, side, requested_units, avg_fill_price,
                worst_fill_price, filled_units, unfilled_units, slippage, status, placed_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), run_id, sport, event_id, schema_version, None,
                trade_decision_id, market_id, side, requested_units, avg_fill_price,
                worst_fill_price, filled_units, unfilled_units, slippage, status, placed_at_utc,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_paper_order(self, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM paper_orders WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    # ── settlements (append-only) ────────────────────────────────────

    def record_settlement(
        self,
        *,
        run_id: str,
        sport: str,
        event_id: str,
        outcome: str,
        paper_order_id: int | None = None,
        trade_decision_id: int | None = None,
        settled_price: float | None = None,
        pnl: float | None = None,
        settled_at_utc: str | None = None,
        notes: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO settlements(
                created_at, run_id, sport, event_id, schema_version, supersedes_id,
                paper_order_id, trade_decision_id, outcome, settled_price, pnl,
                settled_at_utc, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), run_id, sport, event_id, schema_version, None,
                paper_order_id, trade_decision_id, outcome, settled_price, pnl,
                settled_at_utc, notes,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_settlement(self, id_: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM settlements WHERE id=?", (id_,)).fetchone()
        return dict(row) if row else None

    # ── audit_events ──────────────────────────────────────────────────

    def record_audit_event(
        self,
        event_type: str,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
        sport: str | None = None,
        event_id: str | None = None,
        schema_version: str = SCHEMA_VERSION,
    ) -> str:
        """Hash-chained audit trail, same pattern as metadata.py's
        MetadataDB.audit_event. Returns the new entry's audit_uuid."""
        audit_uuid = uuid.uuid4().hex
        now = utc_now()
        prev = self.conn.execute(
            "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous_hash = prev["event_hash"] if prev else ""
        raw = json.dumps({
            "audit_uuid": audit_uuid, "event_type": event_type, "run_id": run_id,
            "sport": sport, "event_id": event_id, "details": details,
            "previous_hash": previous_hash, "created_at": now,
        }, sort_keys=True, default=str)
        event_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.conn.execute(
            """INSERT INTO audit_events(
                audit_uuid, created_at, run_id, sport, event_id, schema_version,
                supersedes_id, event_type, details_json, previous_hash, event_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (audit_uuid, now, run_id, sport, event_id, schema_version, None,
             event_type, json.dumps(details) if details else None, previous_hash, event_hash),
        )
        self.conn.commit()
        return audit_uuid

    def audit_events_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ── lifecycle ─────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()
