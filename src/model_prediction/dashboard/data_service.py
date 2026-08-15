"""Read-only SQL-backed data service for the dashboard (consolidation C).

The dashboard asks SQL-backed endpoints for only the rows it needs,
server-side paginated and aggregated — no Excel parsing in hot requests,
no loading every historical row into Python, and no mutations: every
connection here opens with ``mode=ro`` (WAL-compatible), so serving the
dashboard can never create, migrate, or write a database.

Routes (mounted by dashboard_server at ``/api/data/*``):
  /api/data/predictions?sport=&market_type=&status=&limit=&cursor=
  /api/data/predictions/counts
  /api/data/runs?limit=
  /api/data/promotions
  /api/data/health
  /api/data/versions   (cheap change fingerprint for the frontend)
  /api/data/ledger?tier=&sport=&status=&limit=&cursor=   (item 11: ledger_records)
  /api/data/ledger/counts?tier=&sport=
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..runtime_paths import RuntimePaths
from ..system_health import system_health

_PREDICTION_COLUMNS = (
    "id, prediction_id, event_id, sport, market_type, model_id, horizon, "
    "decision_time_utc, probabilities_json, predicted_side, status, "
    "resolved_outcome, settled_at_utc, note"
)


def _ro_conn(db_path: Path) -> sqlite3.Connection | None:
    """Read-only connection; None when the database doesn't exist yet."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _paths() -> RuntimePaths:
    return RuntimePaths.resolve(repo_root=PROJECT_ROOT, require_external_runtime=True)


def _predictions(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ro_conn(_paths().production_db)
    if conn is None:
        return {"predictions": [], "next_cursor": None, "note": "no production database yet"}
    try:
        sport = query.get("sport", [None])[0]
        market_type = query.get("market_type", [None])[0]
        status = query.get("status", [None])[0]
        limit = min(int(query.get("limit", ["100"])[0]), 500)
        cursor = query.get("cursor", [None])[0]

        clauses: list[str] = []
        params: list[Any] = []
        if sport:
            clauses.append("sport = ?")
            params.append(sport)
        if market_type:
            clauses.append("market_type = ?")
            params.append(market_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if cursor and cursor.isdigit():
            clauses.append("id < ?")
            params.append(int(cursor))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_PREDICTION_COLUMNS} FROM predictions{where} "
            "ORDER BY id DESC LIMIT ?"
        )
        params.append(limit + 1)
        rows = conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "predictions": _rows_as_dicts_rows(rows),
            "next_cursor": rows[-1]["id"] if has_more and rows else None,
        }
    finally:
        conn.close()


def _rows_as_dicts_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        if data.get("probabilities_json"):
            try:
                data["probabilities"] = json.loads(data["probabilities_json"])
            except json.JSONDecodeError:
                pass
            data.pop("probabilities_json", None)
        out.append(data)
    return out


def _counts(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ro_conn(_paths().production_db)
    if conn is None:
        return {"counts": {}, "note": "no production database yet"}
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM predictions GROUP BY status"
        ).fetchall()
        return {"counts": {str(r["status"]): int(r["n"]) for r in rows}}
    finally:
        conn.close()


def _runs(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ro_conn(_paths().runs_db)
    if conn is None:
        return {"runs": [], "note": "no run-state database yet"}
    try:
        limit = min(int(query.get("limit", ["20"])[0]), 200)
        rows = conn.execute(
            "SELECT run_id, worker, status, started_at_utc, finished_at_utc, "
            "exit_code, note FROM runs ORDER BY started_at_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"runs": [dict(r) for r in rows]}
    finally:
        conn.close()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None


def _promotions(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ro_conn(_paths().runs_db)
    if conn is None:
        return {"promotions": [], "note": "no run-state database yet"}
    try:
        if not _has_table(conn, "promotions"):
            return {"promotions": [], "note": "no promotions recorded yet"}
        cols = "promotion_id, sport, market, old_model_id, new_model_id, " \
               "approved_by, evidence_id, git_sha, promoted_at_utc, status, note"
        rows = conn.execute(
            f"SELECT {cols} FROM promotions ORDER BY promoted_at_utc DESC LIMIT 100"
        ).fetchall()
        return {"promotions": [dict(r) for r in rows]}
    finally:
        conn.close()


def _versions(query: dict[str, list[str]]) -> dict[str, Any]:
    """Cheap change fingerprint: has anything the frontend caches changed?"""
    fingerprint: dict[str, Any] = {"generated_at_utc": None, "parts": {}}
    conn = _ro_conn(_paths().production_db)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT MAX(id) AS max_id, COUNT(*) AS n, "
                "MAX(settled_at_utc) AS latest_settlement FROM predictions"
            ).fetchone()
            fingerprint["parts"]["predictions"] = dict(row)
        finally:
            conn.close()
    conn = _ro_conn(_paths().runs_db)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT MAX(started_at_utc) AS latest_run FROM runs"
            ).fetchone()
            fingerprint["parts"]["runs"] = dict(row)
            # The promotions/experiments tables are created lazily by
            # their writers — a read-only fingerprint must tolerate their
            # absence, not error.
            if _has_table(conn, "promotions"):
                promo = conn.execute(
                    "SELECT MAX(promoted_at_utc) AS latest_promotion FROM promotions"
                ).fetchone()
                fingerprint["parts"]["promotions"] = dict(promo)
            if _has_table(conn, "experiments"):
                exp = conn.execute(
                    "SELECT MAX(created_at_utc) AS latest_experiment FROM experiments"
                ).fetchone()
                fingerprint["parts"]["experiments"] = dict(exp)
        finally:
            conn.close()
    return fingerprint


_LEDGER_COLUMNS = (
    "pick_id, operation_id, ledger_tier, sport, event_id, "
    "canonical_event_id, event_start_utc, market_type, selection, line, "
    "model_id, model_artifact_hash, feature_schema_version, "
    "model_probability, market_probability, edge, confidence, units, "
    "decision, reason_code, status, result, pnl_units, created_at_utc, "
    "settled_at_utc"
)


def _ledger_conn() -> sqlite3.Connection | None:
    return _ro_conn(_paths().ledgers_db)


def _ledger(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ledger_conn()
    if conn is None:
        return {"records": [], "next_cursor": None, "note": "no ledger database yet"}
    try:
        if not _has_table(conn, "ledger_records"):
            return {"records": [], "next_cursor": None, "note": "no ledger database yet"}
        tier = query.get("tier", [None])[0]
        sport = query.get("sport", [None])[0]
        status = query.get("status", [None])[0]
        limit = min(int(query.get("limit", ["100"])[0]), 500)
        cursor = query.get("cursor", [None])[0]

        clauses: list[str] = []
        params: list[Any] = []
        if tier:
            clauses.append("ledger_tier = ?")
            params.append(tier)
        if sport:
            clauses.append("sport = ?")
            params.append(sport)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if cursor:
            clauses.append("pick_id < ?")
            params.append(cursor)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # pick_id has no natural chronology across tiers, so order by the
        # row's real timestamp and keyset-page on (created_at_utc, pick_id)
        # would be the general form; created_at_utc DESC with a pick_id
        # tie-break keeps pagination stable without a composite cursor.
        sql = (
            f"SELECT {_LEDGER_COLUMNS} FROM ledger_records{where} "
            "ORDER BY created_at_utc DESC, pick_id DESC LIMIT ?"
        )
        params.append(limit + 1)
        rows = conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "records": [dict(r) for r in rows],
            "next_cursor": rows[-1]["pick_id"] if has_more and rows else None,
        }
    finally:
        conn.close()


def _ledger_counts(query: dict[str, list[str]]) -> dict[str, Any]:
    conn = _ledger_conn()
    if conn is None:
        return {"counts": {}, "note": "no ledger database yet"}
    try:
        if not _has_table(conn, "ledger_records"):
            return {"counts": {}, "note": "no ledger database yet"}
        tier = query.get("tier", [None])[0]
        sport = query.get("sport", [None])[0]
        clauses: list[str] = []
        params: list[Any] = []
        if tier:
            clauses.append("ledger_tier = ?")
            params.append(tier)
        if sport:
            clauses.append("sport = ?")
            params.append(sport)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS n, COALESCE(SUM(pnl_units), 0) AS pnl "
            f"FROM ledger_records{where} GROUP BY status",
            params,
        ).fetchall()
        return {
            "counts": {
                str(r["status"]): {"n": int(r["n"]), "pnl_units": float(r["pnl"])}
                for r in rows
            }
        }
    finally:
        conn.close()


_HANDLERS = {
    "predictions": _predictions,
    "predictions/counts": _counts,
    "runs": _runs,
    "promotions": _promotions,
    "health": lambda query: system_health(
        repo_root=_paths().repo_root, runtime_root=_paths().runtime_root
    ),
    "versions": _versions,
    "ledger": _ledger,
    "ledger/counts": _ledger_counts,
}


def handle(route: str, query: dict[str, list[str]]) -> dict[str, Any]:
    """Dispatch a ``/api/data/<route>`` request (read-only)."""
    handler = _HANDLERS.get(route)
    if handler is None:
        raise KeyError(f"unknown data route: {route}")
    return handler(query)

