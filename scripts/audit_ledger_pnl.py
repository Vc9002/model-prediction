"""Audit and narrowly repair settlement/P&L drift across active ledgers.

SQLite is the production authority. XLSX rows are used only as settlement
evidence when the same canonical identity already exists, is still open in
SQLite, and has a matching append-only settlement audit event. Membership is
never reconciled from XLSX: doing that after the SQLite cutover could tombstone
valid canonical rows.

The global per-model workbooks are evidence views, not bankroll ledgers. Their
stranded open rows may be settled from an unambiguous, exact event/market/line/
model/selection match in an active tier workbook. Conflicting economics are
reported and left untouched. MLB v9 is included as a source and is never
disabled or removed.

Dry-run is the default. Apply either repair class explicitly::

    MODEL_PREDICTION_RUNTIME_ROOT=/... python scripts/audit_ledger_pnl.py
    MODEL_PREDICTION_RUNTIME_ROOT=/... python scripts/audit_ledger_pnl.py \
        --apply-sqlite --apply-model-ledgers --report tmp/ledger-pnl-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.model_ledger import ModelLedger, _event_settlement_key
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.xlsx_ledger import read_xlsx_rows

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACTIVE_TIERS = ("main", "flat", "flat_v9", "research", "gated_research")
SETTLEMENT_EVENTS = {"pick_settled", "pick_resettled_corrected", "pick_voided"}


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_number(left: object, right: object, tolerance: float = 1e-6) -> bool:
    left_number, right_number = _number(left), _number(right)
    if left_number is None or right_number is None:
        return left_number is right_number
    return abs(left_number - right_number) <= tolerance


def _tier_ledgers() -> list[tuple[str, str, Path, Path]]:
    rows: list[tuple[str, str, Path, Path]] = []
    for tier in ACTIVE_TIERS:
        tier_root = DATA / tier
        if not tier_root.exists():
            continue
        audit = DATA / "events.jsonl" if tier in {"main", "flat"} else tier_root / "events.jsonl"
        for path in sorted(tier_root.glob("*.xlsx")):
            rows.append((tier, path.stem, path, audit))
    return rows


def _load_settlement_events(paths: set[Path]) -> dict[str, list[dict[str, Any]]]:
    by_pick: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(paths):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if event.get("event_type") in SETTLEMENT_EVENTS:
                    by_pick[str(event.get("subject_id") or "")].append(event)
    return by_pick


def _matching_audit_event(row: dict[str, str], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("event_type") == "pick_voided"
            and row.get("result") == "push"
            and _number(row.get("pnl_units")) == 0.0
        ):
            return event
        if payload.get("result") != row.get("result"):
            continue
        if not _same_number(payload.get("pnl_units"), row.get("pnl_units"), tolerance=1e-4):
            continue
        return event
    return None


def _load_source_rows() -> tuple[dict[tuple[str, str, str], tuple[dict[str, str], Path]], dict[str, Any]]:
    source: dict[tuple[str, str, str], tuple[dict[str, str], Path]] = {}
    workbook_stats: dict[str, Any] = {}
    for tier, sport, path, _audit in _tier_ledgers():
        _, rows = read_xlsx_rows(path)
        anomalies: list[str] = []
        settled = 0
        for row in rows:
            source[(tier, sport, row["pick_id"])] = (row, path)
            status, result = row.get("status"), row.get("result")
            pnl, units = _number(row.get("pnl_units")), _number(row.get("units")) or 0.0
            if status == "settled":
                settled += 1
            if status == "open" and pnl not in (None, 0.0):
                anomalies.append(f"{row['pick_id']}:open_nonzero_pnl")
            if result == "win" and units > 0 and pnl is not None and pnl <= 0:
                anomalies.append(f"{row['pick_id']}:win_nonpositive_pnl")
            if result == "loss" and pnl is not None and abs(pnl + units) > 1e-4:
                anomalies.append(f"{row['pick_id']}:loss_pnl_not_negative_units")
        workbook_stats[str(path.relative_to(ROOT))] = {
            "rows": len(rows),
            "settled": settled,
            "open": len(rows) - settled,
            "pnl_anomalies": anomalies,
        }
    return source, workbook_stats


def _parse_json_object(value: object) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else None


def _mutation_from_repair(
    current: dict[str, Any], source_row: dict[str, str], operation_id: str
) -> LedgerMutation:
    return LedgerMutation(
        pick_id=current["pick_id"],
        operation_id=operation_id,
        ledger_tier=current["ledger_tier"],
        sport=current["sport"],
        event_type="settle",
        created_at_utc=current["created_at_utc"],
        event_id=current["event_id"],
        canonical_event_id=current["canonical_event_id"],
        event_start_utc=current["event_start_utc"],
        market_type=current["market_type"],
        selection=current["selection"],
        line=current["line"],
        model_id=current["model_id"],
        model_artifact_hash=current["model_artifact_hash"],
        market_snapshot_hash=current["market_snapshot_hash"],
        market_snapshot_archive_path=current["market_snapshot_archive_path"],
        market_snapshot_record_id=current["market_snapshot_record_id"],
        feature_schema_version=current["feature_schema_version"],
        model_probability=current["model_probability"],
        market_probability=current["market_probability"],
        edge=current["edge"],
        confidence=current["confidence"],
        units=current["units"],
        decision=current["decision"],
        reason_code=current["reason_code"],
        status="settled",
        result=source_row["result"],
        pnl_units=_number(source_row.get("pnl_units")),
        settled_at_utc=source_row.get("settled_at_utc") or None,
        decision_payload=dict(source_row),
        feature_payload=_parse_json_object(current["feature_payload_json"]),
        note="evidence-backed XLSX settlement sync after SQLite authority cutover",
    )


def _operation_id(current: dict[str, Any], source_row: dict[str, str]) -> str:
    material = "|".join(
        [
            current["ledger_tier"],
            current["sport"],
            current["pick_id"],
            source_row.get("settled_at_utc", ""),
            source_row.get("result", ""),
            source_row.get("pnl_units", ""),
        ]
    )
    return "op-pnl-sync-" + hashlib.sha256(material.encode()).hexdigest()[:24]


def _plan_sqlite_repairs(
    store: RuntimeLedgerStore,
    source: dict[tuple[str, str, str], tuple[dict[str, str], Path]],
    audit_events: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for current in store.records():
        source_match = source.get((current["ledger_tier"], current["sport"], current["pick_id"]))
        if source_match is None:
            continue
        row, path = source_match
        if current["status"] == "settled" or row.get("status") != "settled":
            continue
        audit = _matching_audit_event(row, audit_events.get(current["pick_id"], []))
        item = {
            "pick_id": current["pick_id"],
            "tier": current["ledger_tier"],
            "sport": current["sport"],
            "result": row.get("result"),
            "pnl_units": _number(row.get("pnl_units")),
            "source": str(path.relative_to(ROOT)),
        }
        if audit is None:
            rejected.append({**item, "reason": "no matching append-only settlement event"})
            continue
        repairs.append(
            {
                **item,
                "audit_event_id": audit.get("event_id"),
                "current": current,
                "source_row": row,
            }
        )
    return repairs, rejected


def _economic_signature(row: dict[str, str]) -> tuple[object, ...]:
    def normalized(value: object) -> object:
        number = _number(value)
        return None if number is None else round(number, 6)

    # Compare stake-normalized economics: pnl per unit staked. Loss rows
    # carry pnl = -units by invariant, and tiers size the same pick
    # differently by design (flat 1.0U paper vs gated quarter-Kelly), so
    # raw pnl_units would flag benign sizing variance as a settlement
    # conflict. pnl/units leaves the economic content (quote for wins,
    # -1.0 for losses) and folds sizing out.
    pnl = _number(row.get("pnl_units"))
    units = _number(row.get("units"))
    stake_normalized_pnl = None
    if pnl is not None and units:
        # Ledger rows write pnl at 4 decimals; normalizing at higher
        # precision manufactures false conflicts from display rounding
        # (0.8197 vs 1.0246/1.25 at 6dp). 4dp matches the written format.
        stake_normalized_pnl = round(pnl / abs(units), 4)

    return (
        row.get("result"),
        stake_normalized_pnl,
        normalized(row.get("closing_raw_implied_probability") or row.get("closing_implied_probability")),
        normalized(row.get("probability_clv")),
    )


def _model_sources() -> tuple[dict[tuple[str, str, str, str, str], dict[str, str]], list[dict[str, Any]]]:
    candidates: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for _tier, _sport, path, _audit in _tier_ledgers():
        _, rows = read_xlsx_rows(path)
        for row in rows:
            if row.get("status") == "settled" and row.get("result"):
                candidates[_event_settlement_key(row)].append(row)
    unambiguous: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    conflicts: list[dict[str, Any]] = []
    for key, rows in candidates.items():
        signatures = {_economic_signature(row) for row in rows}
        if len(signatures) == 1:
            unambiguous[key] = max(rows, key=lambda row: row.get("settled_at_utc") or "")
        else:
            conflicts.append(
                {
                    "key": list(key),
                    "signatures": [list(signature) for signature in sorted(signatures, key=str)],
                }
            )
    return unambiguous, conflicts


def _plan_model_repairs(
    sources: dict[tuple[str, str, str, str, str], dict[str, str]],
) -> dict[Path, list[tuple[str, dict[str, str]]]]:
    planned: dict[Path, list[tuple[str, dict[str, str]]]] = defaultdict(list)
    for path in sorted((DATA / "model_ledgers").glob("*.xlsx")):
        if ".bak-" in path.name or ".backup" in path.name:
            continue
        for row in ModelLedger(path).rows():
            if row.get("status") != "open":
                continue
            source = sources.get(_event_settlement_key(row))
            if source is not None:
                planned[path].append((row["prediction_id"], source))
    return planned


def _backup_sqlite(db_path: Path, stamp: str) -> Path:
    backup = db_path.with_name(f"{db_path.stem}.bak-pnl-sync-{stamp}{db_path.suffix}")
    source = sqlite3.connect(db_path, timeout=10)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def _apply_sqlite_repairs(
    store: RuntimeLedgerStore, repairs: list[dict[str, Any]], stamp: str
) -> tuple[Path | None, int]:
    if not repairs:
        return None, 0
    backup = _backup_sqlite(store.paths.ledgers_db, stamp)
    applied = 0
    for repair in repairs:
        current, source_row = repair["current"], repair["source_row"]
        mutation = _mutation_from_repair(current, source_row, _operation_id(current, source_row))
        applied += int(store.apply(mutation))
    return backup, applied


def _apply_model_repairs(
    planned: dict[Path, list[tuple[str, dict[str, str]]]], stamp: str
) -> tuple[list[Path], int]:
    backups: list[Path] = []
    applied = 0
    for path, changes in planned.items():
        backup = path.with_name(f"{path.stem}.bak-pnl-sync-{stamp}{path.suffix}")
        shutil.copy2(path, backup)
        backups.append(backup)
        by_id = {prediction_id: source for prediction_id, source in changes}
        ledger = ModelLedger(path)
        with ledger._lock():
            rows = ledger._read_unlocked()
            for row in rows:
                source = by_id.get(row["prediction_id"])
                if source is None or row.get("status") != "open":
                    continue
                row["status"] = "settled"
                row["result"] = source.get("result", "")
                closing = source.get("closing_raw_implied_probability") or source.get(
                    "closing_implied_probability", ""
                )
                row["closing_price"] = closing
                row["pnl_units"] = source.get("pnl_units", "")
                row["probability_clv"] = source.get("probability_clv", "")
                row["settled_at_utc"] = source.get("settled_at_utc", "")
                applied += 1
            ledger._write_unlocked(rows)
    return backups, applied


def _public_sqlite_repair(repair: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in repair.items() if key not in {"current", "source_row"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-sqlite", action="store_true")
    parser.add_argument("--apply-model-ledgers", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    paths = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True)
    source, workbook_stats = _load_source_rows()
    audit_events = _load_settlement_events({item[3] for item in _tier_ledgers()})
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with RuntimeLedgerStore(paths) as store:
        sqlite_repairs, sqlite_rejected = _plan_sqlite_repairs(store, source, audit_events)
        model_sources, conflicts = _model_sources()
        model_repairs = _plan_model_repairs(model_sources)
        sqlite_backup = None
        sqlite_applied = 0
        if args.apply_sqlite:
            sqlite_backup, sqlite_applied = _apply_sqlite_repairs(store, sqlite_repairs, stamp)
        integrity_ok, integrity_problems = store.verify_integrity()

    model_backups: list[Path] = []
    model_applied = 0
    if args.apply_model_ledgers:
        model_backups, model_applied = _apply_model_repairs(model_repairs, stamp)

    report = {
        "mode": "apply" if args.apply_sqlite or args.apply_model_ledgers else "dry_run",
        "runtime_db": str(paths.ledgers_db),
        "xlsx_workbooks": workbook_stats,
        "xlsx_pnl_anomaly_count": sum(len(item["pnl_anomalies"]) for item in workbook_stats.values()),
        "sqlite_repairs_planned": [_public_sqlite_repair(item) for item in sqlite_repairs],
        "sqlite_repairs_rejected": sqlite_rejected,
        "sqlite_repairs_applied": sqlite_applied,
        "sqlite_backup": str(sqlite_backup) if sqlite_backup else None,
        "sqlite_integrity": {"ok": integrity_ok, "problems": integrity_problems},
        "model_repairs_planned": {
            str(path.relative_to(ROOT)): len(changes) for path, changes in model_repairs.items()
        },
        "model_repairs_planned_total": sum(len(changes) for changes in model_repairs.values()),
        "model_settlement_conflicts": conflicts,
        "model_repairs_applied": model_applied,
        "model_backups": [str(path) for path in model_backups],
        "v9_source_preserved": str(DATA / "flat_v9" / "mlb.xlsx"),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if integrity_ok and not sqlite_rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
