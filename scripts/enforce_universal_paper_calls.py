"""Enforce CALL + positive paper units across every active ledger.

Dry-run is the default. ``--apply`` acquires the canonical daily writer lock,
backs up each changed workbook and the runtime SQLite database, and records
auditable update events. Archived and removed records are intentionally left
unchanged: they are historical lifecycle tombstones, not active picks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.daily_lock import acquire_lock
from model_prediction.domain import PickResult
from model_prediction.ledger import PickLedger
from model_prediction.model_ledger import ModelLedger
from model_prediction.pricing import american_to_decimal, profit_units
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACTIVE_TIERS = ("main", "flat", "flat_v9", "research", "gated_research")
ACTIVE_STATUSES = {"open", "settled"}


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _paper_reason(reason_code: object) -> str:
    reason = str(reason_code or "PAPER_CALL_UNSPECIFIED")
    return "PAPER_CALL_" + reason.removeprefix("NO_CALL_") if reason.startswith("NO_CALL_") else reason


def _entry_decimal(row: dict[str, Any]) -> float | None:
    decimal = _number(row.get("decision_decimal_odds") or row.get("decimal_odds"))
    if decimal is not None and decimal > 1:
        return decimal
    probability = _number(
        row.get("market_probability_at_decision")
        or row.get("decision_raw_implied_probability")
        or row.get("market_implied_probability")
        or row.get("decision_price")
    )
    if probability is not None and 0 < probability < 1:
        return 1.0 / probability
    american = _number(row.get("decision_american_odds") or row.get("american_odds"))
    if american not in (None, 0):
        return american_to_decimal(int(american))
    return None


def _settled_pnl(result: str, units: float, decimal: float) -> float:
    return profit_units(PickResult(result), units, decimal)


def normalize_pick_row(row: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    corrected = dict(row)
    unresolved: list[str] = []
    if row.get("status") not in ACTIVE_STATUSES:
        return corrected, unresolved
    corrected["decision"] = "CALL"
    corrected["reason_code"] = _paper_reason(row.get("reason_code"))
    corrected["call_type"] = (
        "model_qualified" if row.get("record_type") == "QUALIFIED_SHADOW_CALL" else "paper_call"
    )
    units = _number(row.get("units")) or 0.0
    if units <= 0:
        units = 1.0
    corrected["units"] = f"{units:.2f}"
    if row.get("status") == "open":
        corrected["pnl_units"] = ""
    elif row.get("status") == "settled" and row.get("result"):
        decimal = _entry_decimal(row)
        if decimal is None:
            unresolved.append(f"{row.get('pick_id')}:settled_missing_entry_price")
        else:
            corrected["pnl_units"] = f"{_settled_pnl(row['result'], units, decimal):.4f}"
    return corrected, unresolved


def _model_units(row: dict[str, str]) -> float:
    existing = _number(row.get("operator_units"))
    if existing is not None and existing > 0:
        return existing
    pnl = _number(row.get("pnl_units"))
    result = row.get("result")
    decimal = _entry_decimal(row)
    if result == "loss" and pnl is not None and pnl < 0:
        return abs(pnl)
    if result == "win" and pnl is not None and pnl > 0 and decimal is not None:
        profit_per_unit = decimal - 1.0
        if profit_per_unit > 0:
            return pnl / profit_per_unit
    return 1.0


def normalize_model_row(row: dict[str, str], migration_timestamp: str) -> tuple[dict[str, str], list[str]]:
    corrected = dict(row)
    unresolved: list[str] = []
    if row.get("status") == "failed":
        return corrected, unresolved
    units = _model_units(row)
    corrected["operator_decision"] = "CALL"
    corrected["operator_units"] = f"{units:.4f}"
    corrected["operator_timestamp"] = row.get("operator_timestamp") or migration_timestamp
    corrected["operator_note"] = row.get("operator_note") or "automatic universal paper call"
    if row.get("status") == "open":
        corrected["pnl_units"] = ""
    elif row.get("status") == "settled" and row.get("result") and not row.get("pnl_units"):
        decimal = _entry_decimal(row)
        if decimal is None:
            unresolved.append(f"{row.get('prediction_id')}:settled_missing_entry_price")
        else:
            corrected["pnl_units"] = f"{_settled_pnl(row['result'], units, decimal):.4f}"
    return corrected, unresolved


def _active_pick_ledgers() -> list[tuple[str, str, Path, Path]]:
    output: list[tuple[str, str, Path, Path]] = []
    for tier in ACTIVE_TIERS:
        tier_root = DATA / tier
        if not tier_root.exists():
            continue
        audit_path = DATA / "events.jsonl" if tier in {"main", "flat"} else tier_root / "events.jsonl"
        for path in sorted(tier_root.glob("*.xlsx")):
            output.append((tier, path.stem, path, audit_path))
    return output


def _backup(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-universal-call-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _backup_sqlite(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"{path.stem}.bak-universal-call-{stamp}{path.suffix}")
    source = sqlite3.connect(path, timeout=10)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def _runtime_mutation(current: dict[str, Any], corrected: dict[str, Any]) -> LedgerMutation:
    material = "|".join(
        [
            current["ledger_tier"],
            current["pick_id"],
            str(corrected["decision"]),
            str(corrected["units"]),
            str(corrected.get("pnl_units")),
        ]
    )
    operation_id = "op-universal-call-" + hashlib.sha256(material.encode()).hexdigest()[:24]
    return LedgerMutation(
        pick_id=current["pick_id"],
        operation_id=operation_id,
        ledger_tier=current["ledger_tier"],
        sport=current["sport"],
        event_type="update",
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
        units=corrected["units"],
        decision=corrected["decision"],
        reason_code=corrected["reason_code"],
        status=current["status"],
        result=current["result"],
        pnl_units=corrected.get("pnl_units"),
        settled_at_utc=current["settled_at_utc"],
        decision_payload=corrected["decision_payload"],
        feature_payload=json.loads(current["feature_payload_json"])
        if current.get("feature_payload_json")
        else None,
        note="universal paper CALL migration",
    )


def _plan_runtime(store: RuntimeLedgerStore) -> tuple[list[LedgerMutation], list[str]]:
    planned: list[LedgerMutation] = []
    unresolved: list[str] = []
    for current in store.records():
        if current["status"] not in ACTIVE_STATUSES:
            continue
        payload = json.loads(current["decision_payload_json"] or "{}")
        payload.setdefault("pick_id", current["pick_id"])
        payload.setdefault("status", current["status"])
        payload.setdefault("result", current["result"] or "")
        payload.setdefault("units", current["units"])
        payload.setdefault("pnl_units", current["pnl_units"])
        corrected_payload, row_unresolved = normalize_pick_row(
            {key: "" if value is None else str(value) for key, value in payload.items()}
        )
        unresolved.extend(f"{current['ledger_tier']}:{current['sport']}:{item}" for item in row_unresolved)
        corrected = {
            "decision": corrected_payload["decision"],
            "reason_code": corrected_payload["reason_code"],
            "units": float(corrected_payload["units"]),
            "pnl_units": _number(corrected_payload.get("pnl_units")),
            "decision_payload": corrected_payload,
        }
        if (
            current["decision"] != corrected["decision"]
            or current["reason_code"] != corrected["reason_code"]
            or _number(current["units"]) != corrected["units"]
            or _number(current["pnl_units"]) != corrected["pnl_units"]
        ):
            planned.append(_runtime_mutation(current, corrected))
    return planned, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    timestamp = datetime.now(UTC)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    migration_timestamp = timestamp.isoformat().replace("+00:00", "Z")
    paths = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True)
    store = RuntimeLedgerStore(paths)
    runtime_plan, unresolved = _plan_runtime(store)

    pick_plans: list[tuple[str, str, Path, Path, list[dict[str, str]]]] = []
    for tier, sport, path, audit_path in _active_pick_ledgers():
        ledger = PickLedger(path, audit_path)
        rows = ledger.rows()
        corrected_rows: list[dict[str, str]] = []
        changed = False
        for row in rows:
            corrected, row_unresolved = normalize_pick_row(row)
            unresolved.extend(f"{tier}:{sport}:{item}" for item in row_unresolved)
            corrected_rows.append(corrected)
            changed = changed or corrected != row
        if changed:
            pick_plans.append((tier, sport, path, audit_path, corrected_rows))

    model_plans: list[tuple[Path, list[dict[str, str]]]] = []
    for path in sorted((DATA / "model_ledgers").glob("*.xlsx")):
        if ".bak-" in path.name or ".backup" in path.name:
            continue
        ledger = ModelLedger(path)
        corrected_rows: list[dict[str, str]] = []
        changed = False
        for row in ledger.rows():
            corrected, row_unresolved = normalize_model_row(row, migration_timestamp)
            unresolved.extend(f"model:{path.name}:{item}" for item in row_unresolved)
            corrected_rows.append(corrected)
            changed = changed or corrected != row
        if changed:
            model_plans.append((path, corrected_rows))

    pick_planned_count = sum(
        sum(old != new for old, new in zip(PickLedger(path, audit).rows(), rows, strict=True))
        for _tier, _sport, path, audit, rows in pick_plans
    )
    model_planned_count = sum(
        sum(old != new for old, new in zip(ModelLedger(path).rows(), rows, strict=True))
        for path, rows in model_plans
    )

    backups: list[str] = []
    applied = {"runtime": 0, "pick_rows": 0, "model_rows": 0}
    if args.apply:
        writer_lock = acquire_lock(paths.lock_root / "daily.lock")
        if writer_lock is None:
            raise RuntimeError("daily writer lock is busy; universal CALL migration refused")
        try:
            if runtime_plan:
                backups.append(str(_backup_sqlite(paths.ledgers_db, stamp)))
                for mutation in runtime_plan:
                    applied["runtime"] += int(store.apply(mutation))
            backed_audits: set[Path] = set()
            for tier, sport, path, audit_path, corrected_rows in pick_plans:
                backups.append(str(_backup(path, stamp)))
                if audit_path.exists() and audit_path not in backed_audits:
                    backups.append(str(_backup(audit_path, stamp)))
                    backed_audits.add(audit_path)
                ledger = PickLedger(path, audit_path)
                with ledger._lock():
                    before = ledger._read_unlocked()
                    changed_ids = [
                        old["pick_id"] for old, new in zip(before, corrected_rows, strict=True) if old != new
                    ]
                    ledger.audit.append(
                        "universal_paper_calls_applied",
                        f"{tier}:{sport}",
                        {"rows_changed": len(changed_ids), "pick_ids": changed_ids},
                    )
                    ledger._write_rows(corrected_rows)
                    applied["pick_rows"] += len(changed_ids)
            for path, corrected_rows in model_plans:
                backups.append(str(_backup(path, stamp)))
                ledger = ModelLedger(path)
                with ledger._lock():
                    before = ledger._read_unlocked()
                    applied["model_rows"] += sum(
                        old != new for old, new in zip(before, corrected_rows, strict=True)
                    )
                    ledger._write_unlocked(corrected_rows)
        finally:
            writer_lock.close()
            store.close()
    else:
        store.close()

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "planned": {
            "runtime_rows": len(runtime_plan),
            "pick_workbooks": len(pick_plans),
            "pick_rows": pick_planned_count,
            "model_workbooks": len(model_plans),
            "model_rows": model_planned_count,
        },
        "applied": applied,
        "unresolved": sorted(set(unresolved)),
        "backups": backups,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
