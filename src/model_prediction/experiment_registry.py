"""Experiment registry (consolidation B/C, item 9).

Every challenger run persists its identity and evidence, so "was +56.4u
residual trend generated before or after the parity correction?" is a
registry query, not archaeology. Invalidated results are kept with
``status = 'void'`` and a reason — never silently overwritten.

    python -m model_prediction.experiment_registry record --model-id ...
    python -m model_prediction.experiment_registry void <experiment_id> --reason ...
    python -m model_prediction.experiment_registry list [--model-id ...]
    python -m model_prediction.experiment_registry show <experiment_id>

Rows live in the ``experiments`` table of the control-plane runs.db
(RuntimePaths-resolved, same store as supervisor runs and promotions).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .runtime_paths import RuntimePaths, migrate_legacy_state

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id         TEXT PRIMARY KEY,
    git_sha               TEXT,
    model_id              TEXT NOT NULL,
    incumbent_id          TEXT,
    dataset_hash          TEXT,
    feature_schema_hash   TEXT,
    fold_definition       TEXT,
    hyperparameters       TEXT,
    calibrator            TEXT,
    oof_metrics           TEXT,
    artifact_hashes       TEXT,
    verdict               TEXT,
    status                TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'void')),
    void_reason           TEXT,
    created_at_utc        TEXT NOT NULL,
    updated_at_utc        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_model
    ON experiments (model_id, created_at_utc DESC);
"""

_STATUSES = ("running", "completed", "void")


def _conn(repo_root: Path | None = None) -> sqlite3.Connection:
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    paths = RuntimePaths.resolve(repo_root=root)
    migrate_legacy_state(paths)
    paths.runs_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.runs_db, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record(
    *,
    model_id: str,
    incumbent_id: str | None = None,
    dataset_hash: str | None = None,
    feature_schema_hash: str | None = None,
    fold_definition: dict[str, Any] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    calibrator: str | None = None,
    oof_metrics: dict[str, Any] | None = None,
    artifact_hashes: dict[str, str] | None = None,
    verdict: str | None = None,
    git_sha: str | None = None,
    repo_root: Path | str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Record one experiment run and return the row."""
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES}")
    experiment_id = f"exp-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    now = _now()
    conn = _conn(repo_root)
    try:
        with conn:
            conn.execute(
                "INSERT INTO experiments (experiment_id, git_sha, model_id, "
                "incumbent_id, dataset_hash, feature_schema_hash, "
                "fold_definition, hyperparameters, calibrator, oof_metrics, "
                "artifact_hashes, verdict, status, created_at_utc, "
                "updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?)",
                (
                    experiment_id,
                    git_sha,
                    model_id,
                    incumbent_id,
                    dataset_hash,
                    feature_schema_hash,
                    json.dumps(fold_definition, sort_keys=True) if fold_definition else None,
                    json.dumps(hyperparameters, sort_keys=True) if hyperparameters else None,
                    calibrator,
                    json.dumps(oof_metrics, sort_keys=True) if oof_metrics else None,
                    json.dumps(artifact_hashes, sort_keys=True) if artifact_hashes else None,
                    verdict,
                    status,
                    now,
                    now,
                ),
            )
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return _decode(row)
    finally:
        conn.close()


def void(
    experiment_id: str, reason: str, repo_root: Path | str | None = None
) -> dict[str, Any]:
    """Void a recorded experiment — kept in the registry with a reason."""
    conn = _conn(repo_root)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE experiments SET status = 'void', void_reason = ?, "
                "updated_at_utc = ? WHERE experiment_id = ?",
                (reason, _now(), experiment_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"no experiment with id {experiment_id}")
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return _decode(row)
    finally:
        conn.close()


def list_experiments(
    *, model_id: str | None = None, limit: int = 50, repo_root: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = _conn(repo_root)
    try:
        query = "SELECT * FROM experiments"
        params: list[Any] = []
        if model_id is not None:
            query += " WHERE model_id = ?"
            params.append(model_id)
        query += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(int(limit))
        return [_decode(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def show(
    experiment_id: str, repo_root: Path | str | None = None
) -> dict[str, Any] | None:
    conn = _conn(repo_root)
    try:
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return _decode(row)
    finally:
        conn.close()


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for field in ("fold_definition", "hyperparameters", "oof_metrics", "artifact_hashes"):
        if data.get(field):
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError:
                pass
    return data


# ── CLI ──────────────────────────────────────────────────────────────────────


def _arg(args: list[str], name: str) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx == len(args) - 1:
            raise ValueError(f"{name} requires a value")
        return args[idx + 1]
    return None


def _kv_arg(args: list[str], name: str) -> dict[str, str] | None:
    raw = _arg(args, name)
    if raw is None:
        return None
    out: dict[str, str] = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        if key:
            out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ("record", "void", "list", "show"):
        print(
            "usage: python -m model_prediction.experiment_registry "
            "{record --model-id ID [--incumbent ID] [--dataset-hash H] "
            "[--feature-schema-hash H] [--calibrator C] [--verdict V] "
            "[--metrics k=v,...] | void ID --reason TEXT | list [N] | show ID}",
            file=sys.stderr,
        )
        return 2
    try:
        cmd = args[0]
        if cmd == "record":
            model_id = _arg(args, "--model-id")
            if not model_id:
                raise ValueError("--model-id is required")
            row = record(
                model_id=model_id,
                incumbent_id=_arg(args, "--incumbent"),
                dataset_hash=_arg(args, "--dataset-hash"),
                feature_schema_hash=_arg(args, "--feature-schema-hash"),
                calibrator=_arg(args, "--calibrator"),
                verdict=_arg(args, "--verdict"),
                oof_metrics=_kv_arg(args, "--metrics"),
                hyperparameters=_kv_arg(args, "--hyperparameters"),
                artifact_hashes=_kv_arg(args, "--artifact-hashes"),
            )
            print(json.dumps(row, indent=2, default=str))
            return 0
        if cmd == "void":
            if len(args) < 2 or not _arg(args, "--reason"):
                raise ValueError("usage: void <experiment_id> --reason TEXT")
            print(json.dumps(void(args[1], _arg(args, "--reason")), indent=2, default=str))
            return 0
        if cmd == "list":
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
            for row in list_experiments(limit=limit):
                print(
                    f"{row['experiment_id']}  {row['status']:<10} "
                    f"{row['model_id']:<28} {row['created_at_utc']} "
                    + (f"verdict={row['verdict']}" if row.get("verdict") else "")
                )
            return 0
        if cmd == "show":
            if len(args) < 2:
                raise ValueError("usage: show <experiment_id>")
            row = show(args[1])
            if row is None:
                print(f"no experiment with id {args[1]}", file=sys.stderr)
                return 1
            print(json.dumps(row, indent=2, default=str))
            return 0
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"EXPERIMENT ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
