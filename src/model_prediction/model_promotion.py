"""Atomic model promotion and rollback (consolidation A-3, part 2).

Promotion is one command, not five hand-edited files: validate the
candidate's contract, freeze its hash, preserve the old champion as the
new entry's rollback model, update the registry config atomically, and
write a promotion record::

    python -m model_prediction.model_promotion promote \
        --new mlb-elo-trend-lr-v9 --sport MLB --market moneyline \
        --approved-by operator --evidence <evidence-id>
    python -m model_prediction.model_promotion rollback \
        --sport MLB --market moneyline
    python -m model_prediction.model_promotion history

Promotion records live in a ``promotions`` table in the supervisor's
run-state database; the yaml update is a tmp-file + ``os.replace`` write,
then the on-disk result is re-validated through the registry before the
record is marked active.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import PROJECT_ROOT
from .production_registry import ProductionModelRegistry
from .runtime_paths import RuntimePaths, migrate_legacy_state

_PROMOTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS promotions (
    promotion_id       TEXT PRIMARY KEY,
    sport              TEXT NOT NULL,
    market             TEXT NOT NULL,
    old_model_id       TEXT,
    new_model_id       TEXT NOT NULL,
    old_artifact_hash  TEXT,
    new_artifact_hash  TEXT,
    approved_by        TEXT NOT NULL,
    evidence_id        TEXT,
    market_evidence_id TEXT,
    market_evidence_unavailable_reason TEXT,
    git_sha            TEXT,
    promoted_at_utc    TEXT NOT NULL,
    status             TEXT NOT NULL,
    rolled_back_at_utc TEXT,
    note               TEXT
);
CREATE INDEX IF NOT EXISTS idx_promotions_sport_market
    ON promotions (sport, market, promoted_at_utc DESC);
"""


def _db_path(repo_root: Path) -> Path:
    # Same control-plane store as the run supervisor, resolved through
    # RuntimePaths (runtime root when MODEL_PREDICTION_RUNTIME_ROOT is
    # set, repo data/ otherwise; legacy files migrated once).
    paths = RuntimePaths.resolve(repo_root=repo_root, require_external_runtime=True)
    migrate_legacy_state(paths)
    return paths.runs_db


def _conn(repo_root: Path) -> sqlite3.Connection:
    db = _db_path(repo_root)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_PROMOTIONS_SCHEMA)
    # Schema migration for pre-2026-08-27 promotion DBs: CREATE IF NOT
    # EXISTS never touches an existing table, so the gate columns are
    # added explicitly (duplicate-column on a fresh DB is expected).
    for column, kind in (
        ("market_evidence_id", "TEXT"),
        ("market_evidence_unavailable_reason", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE promotions ADD COLUMN {column} {kind}")
        except sqlite3.OperationalError:
            pass
    return conn


def _config_path(repo_root: Path) -> Path:
    return repo_root / "config" / "production.yaml"


def _load_yaml_dict(repo_root: Path) -> dict[str, Any]:
    with open(_config_path(repo_root), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}  # type: ignore[no-any-return]


def _atomic_write_yaml(repo_root: Path, config: dict[str, Any]) -> None:
    path = _config_path(repo_root)
    tmp = path.with_suffix(".promoting.tmp")
    tmp.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def _model_entry(config: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    models = (config.get("prediction_service") or {}).get("models") or []
    for entry in models:
        if isinstance(entry, dict) and entry.get("model_id") == model_id:
            return entry
    return None


def _active_record(repo_root: Path, sport: str, market: str) -> dict[str, Any] | None:
    conn = _conn(repo_root)
    try:
        row = conn.execute(
            "SELECT * FROM promotions WHERE sport = ? AND market = ? "
            "AND status = 'active' ORDER BY promoted_at_utc DESC LIMIT 1",
            (sport, market),
        ).fetchone()
        if row is None:
            return None
        columns = [d[0] for d in conn.execute("SELECT * FROM promotions LIMIT 0").description]
        return dict(zip(columns, row))
    finally:
        conn.close()


def promote(
    *,
    sport: str,
    market: str,
    new_model_id: str,
    approved_by: str,
    evidence_id: str | None = None,
    market_evidence_id: str | None = None,
    market_evidence_unavailable_reason: str | None = None,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically promote *new_model_id* to serve (sport, market)."""
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    registry = ProductionModelRegistry.load(root)

    candidate = registry.entries.get(new_model_id)
    if candidate is None:
        raise ValueError(f"unknown model '{new_model_id}' in production registry")
    if not candidate.available:
        raise ValueError(f"model '{new_model_id}' failed contract validation: {candidate.load_error}")
    # The candidate must be registered for the sport/market it will serve.
    if candidate.sport.lower() != sport.lower() or candidate.market.lower() != market.lower():
        raise ValueError(
            f"'{new_model_id}' is registered for {candidate.sport} {candidate.market}, not {sport} {market}"
        )

    current = registry.champion(sport, market)
    if current is not None and current.model_id == new_model_id:
        raise ValueError(f"'{new_model_id}' already serves {sport} {market}")

    # Phase-23 market-relative gate (operator directive, wired 2026-08-27):
    # a promotion must carry market-relative evidence, or the operator must
    # explicitly record why no market evidence exists for this market.
    # Fail-closed: forgetting the evidence is a refusal, not a warning.
    if not market_evidence_id and not market_evidence_unavailable_reason:
        raise ValueError(
            "promotion requires market-relative evidence: pass "
            "market_evidence_id (the market_eval report artifact) or "
            "market_evidence_unavailable_reason (explicit operator statement "
            "why no market exists for this market) — see ROADMAP Phase 23"
        )

    # Mutate the yaml dict in memory, then write atomically.
    config = _load_yaml_dict(root)
    svc = config["prediction_service"]
    champions = svc.setdefault("champions", {})
    champions.setdefault(sport.upper(), {})[market] = new_model_id
    candidate_entry = _model_entry(config, new_model_id)
    if candidate_entry is not None:
        # The old champion becomes the rollback pointer on the new entry.
        candidate_entry["rollback_model"] = current.model_id if current else None

    promotion_id = f"promo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    record = {
        "promotion_id": promotion_id,
        "sport": sport,
        "market": market,
        "old_model_id": current.model_id if current else None,
        "new_model_id": new_model_id,
        "old_artifact_hash": current.artifact_hash if current else None,
        "new_artifact_hash": candidate.artifact_hash,
        "approved_by": approved_by,
        "evidence_id": evidence_id,
        "market_evidence_id": market_evidence_id,
        "market_evidence_unavailable_reason": market_evidence_unavailable_reason,
        "git_sha": _git_sha(root),
        "promoted_at_utc": datetime.now(UTC).isoformat(),
        "status": "recorded",
        "note": None,
    }
    conn = _conn(root)
    try:
        with conn:
            conn.execute(
                "INSERT INTO promotions (promotion_id, sport, market, "
                "old_model_id, new_model_id, old_artifact_hash, "
                "new_artifact_hash, approved_by, evidence_id, "
                "market_evidence_id, market_evidence_unavailable_reason, "
                "git_sha, promoted_at_utc, status) "
                "VALUES (:promotion_id, :sport, :market, :old_model_id, "
                ":new_model_id, :old_artifact_hash, :new_artifact_hash, "
                ":approved_by, :evidence_id, :market_evidence_id, "
                ":market_evidence_unavailable_reason, :git_sha, "
                ":promoted_at_utc, :status)",
                record,
            )
    finally:
        conn.close()

    try:
        _atomic_write_yaml(root, config)
        # Re-validate what we just wrote — a promotion that leaves the
        # registry unloadable must not be marked active.
        ProductionModelRegistry.load(root)
    except Exception as exc:
        conn = _conn(root)
        try:
            with conn:
                conn.execute(
                    "UPDATE promotions SET status = 'failed', note = ? WHERE promotion_id = ?",
                    (f"write/validate failed: {exc}", promotion_id),
                )
        finally:
            conn.close()
        raise
    conn = _conn(root)
    try:
        with conn:
            conn.execute(
                "UPDATE promotions SET status = 'active' WHERE promotion_id = ?",
                (promotion_id,),
            )
    finally:
        conn.close()
    record["status"] = "active"
    return record


def rollback(*, sport: str, market: str, repo_root: Path | str | None = None) -> dict[str, Any]:
    """Roll (sport, market) back to its previous champion in one command."""
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    registry = ProductionModelRegistry.load(root)

    current = registry.champion(sport, market)
    if current is None:
        raise ValueError(f"no champion serving {sport} {market}")
    target_id = current.rollback_model
    if not target_id:
        raise ValueError(f"'{current.model_id}' has no rollback model recorded")
    target = registry.entries.get(target_id)
    if target is None or not target.available:
        raise ValueError(f"rollback target '{target_id}' is not a valid production model")

    config = _load_yaml_dict(root)
    svc = config["prediction_service"]
    champions = svc.setdefault("champions", {})
    champions.setdefault(sport.upper(), {})[market] = target_id
    _atomic_write_yaml(root, config)
    ProductionModelRegistry.load(root)  # re-validate

    conn = _conn(root)
    try:
        now = datetime.now(UTC).isoformat()
        with conn:
            conn.execute(
                "UPDATE promotions SET status = 'rolled_back', "
                "rolled_back_at_utc = ? WHERE sport = ? AND market = ? "
                "AND status = 'active'",
                (now, sport, market),
            )
            conn.execute(
                "INSERT INTO promotions (promotion_id, sport, market, "
                "old_model_id, new_model_id, old_artifact_hash, "
                "new_artifact_hash, approved_by, evidence_id, git_sha, "
                "promoted_at_utc, status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'rollback', NULL, ?, ?, 'active', ?)",
                (
                    f"promo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}",
                    sport,
                    market,
                    current.model_id,
                    target_id,
                    current.artifact_hash,
                    target.artifact_hash,
                    _git_sha(root),
                    now,
                    f"rollback from {current.model_id} to {target_id}",
                ),
            )
    finally:
        conn.close()
    return {
        "sport": sport,
        "market": market,
        "rolled_back_from": current.model_id,
        "rolled_back_to": target_id,
        "rolled_back_at_utc": now,
    }


def history(repo_root: Path | str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    root = Path(repo_root) if repo_root is not None else PROJECT_ROOT
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT * FROM promotions ORDER BY promoted_at_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        columns = [d[0] for d in conn.execute("SELECT * FROM promotions LIMIT 0").description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def _git_sha(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _arg(args: list[str], name: str) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx == len(args) - 1:
            raise ValueError(f"{name} requires a value")
        return args[idx + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            "usage: python -m model_prediction.model_promotion "
            "{promote --new ID --sport S --market M --approved-by WHO "
            "[--evidence ID] | rollback --sport S --market M | history [N]}",
            file=sys.stderr,
        )
        return 2
    try:
        cmd = args[0]
        if cmd == "promote":
            new_id = _arg(args, "--new")
            sport = _arg(args, "--sport")
            market = _arg(args, "--market")
            approved = _arg(args, "--approved-by")
            if not (new_id and sport and market and approved):
                raise ValueError("--new, --sport, --market, --approved-by are required")
            record = promote(
                sport=sport,
                market=market,
                new_model_id=new_id,
                approved_by=approved,
                evidence_id=_arg(args, "--evidence"),
            )
            print(json.dumps(record, indent=2))
            return 0
        if cmd == "rollback":
            sport = _arg(args, "--sport")
            market = _arg(args, "--market")
            if not (sport and market):
                raise ValueError("--sport and --market are required")
            print(json.dumps(rollback(sport=sport, market=market), indent=2))
            return 0
        if cmd == "history":
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            for row in history(limit=limit):
                print(
                    f"{row['promoted_at_utc']}  {row['status']:<11} "
                    f"{row['sport']} {row['market']} "
                    f"{row['old_model_id']} -> {row['new_model_id']} "
                    f"(by {row['approved_by']})"
                )
            return 0
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"PROMOTION ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
