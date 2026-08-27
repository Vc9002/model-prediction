"""Repair Flat MLB confidence-gated rows whose paper size was zeroed.

Dry-run is the default. Apply requires the canonical SQLite authority and
acquires the same daily-writer lock as the scheduled pipeline. The repair is
scoped to the exact persisted signature ``flat/mlb +
NO_CALL_BELOW_LEARNED_CONFIDENCE + units=0``; unrelated research reasons and
all Main rows are untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.daily_lock import acquire_lock
from model_prediction.ledger_parity import integrity_report
from model_prediction.main_ledgers import flat_ledger
from model_prediction.model_ledger import ModelLedger, _event_settlement_key, model_id_for
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
REASON = "NO_CALL_BELOW_LEARNED_CONFIDENCE"


def _targets(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("record_type") == "RESEARCH_OBSERVATION"
        and row.get("reason_code") == REASON
        and float(row.get("units") or 0) == 0
        and row.get("status") not in {"archived", "removed"}
    ]


def _canonical_corrections(rows: list[dict[str, str]]) -> dict[Path, dict[tuple[str, ...], dict[str, Any]]]:
    grouped: dict[Path, dict[tuple[str, ...], dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        model_id = model_id_for(row.get("league", ""), row.get("market_type", ""))
        if model_id is None:
            continue
        key = _event_settlement_key(row)
        correction = {
            "result": row.get("result"),
            "pnl_units": row.get("pnl_units"),
            "closing_price": row.get("closing_raw_implied_probability")
            or row.get("closing_implied_probability"),
            "probability_clv": row.get("probability_clv"),
            "settled_at_utc": row.get("settled_at_utc"),
        }
        path = ROOT / "data" / "model_ledgers" / f"{model_id}.xlsx"
        prior = grouped[path].get(key)
        if prior is not None and prior != correction:
            raise ValueError(f"conflicting canonical corrections for {key}")
        grouped[path][key] = correction
    return grouped


def _model_matches(
    grouped: dict[Path, dict[tuple[str, ...], dict[str, Any]]],
) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for path, corrections in grouped.items():
        if not path.exists():
            report[str(path.relative_to(ROOT))] = {"matching": 0, "open": 0, "settled": 0}
            continue
        rows = ModelLedger(path).rows()
        matching = [row for row in rows if _event_settlement_key(row) in corrections]
        report[str(path.relative_to(ROOT))] = {
            "matching": len(matching),
            "open": sum(row.get("status") == "open" for row in matching),
            "settled": sum(row.get("status") == "settled" for row in matching),
        }
    return report


def run(*, apply: bool) -> dict[str, Any]:
    if os.getenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "").casefold() != "sqlite":
        raise RuntimeError("repair requires MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite")
    paths = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True)
    ledger = flat_ledger(ROOT / "data", "mlb")
    before_rows = ledger.rows()
    targets = _targets(before_rows)
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "reason_code": REASON,
        "canonical_targets": len(targets),
        "settled_targets": sum(row.get("status") == "settled" for row in targets),
        "open_targets": sum(row.get("status") == "open" for row in targets),
        "pick_ids": [row["pick_id"] for row in targets],
        "backups": [],
    }
    all_reason_settled = [
        row for row in before_rows if row.get("reason_code") == REASON and row.get("status") == "settled"
    ]
    report["model_rows_before"] = _model_matches(_canonical_corrections(all_reason_settled))
    if not apply:
        return report

    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        raise RuntimeError("daily writer lock is busy; repair refused")
    try:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if targets:
            database_backup = paths.ledgers_root / f"ledgers.db.pre-flat-mlb-pnl-{stamp}.bak"
            shutil.copy2(paths.ledgers_db, database_backup)
            report["backups"].append(str(database_backup))
            projection = ROOT / "data" / "flat" / "mlb.xlsx"
            if projection.exists():
                projection_backup = paths.ledgers_root / f"flat-mlb.xlsx.pre-pnl-{stamp}.bak"
                shutil.copy2(projection, projection_backup)
                report["backups"].append(str(projection_backup))

        changed = ledger.recompute_research_sizing(
            reason_codes={REASON},
            pick_ids={row["pick_id"] for row in targets},
        )
        current_rows = ledger.rows()
        repaired_rows = [row for row in current_rows if row.get("pick_id") in report["pick_ids"]]
        unresolved = _targets(repaired_rows)
        if changed != len(targets) or unresolved:
            raise RuntimeError(
                f"canonical repair incomplete: changed={changed}, expected={len(targets)}, "
                f"unresolved={len(unresolved)}"
            )

        settled_rows = [
            row for row in current_rows if row.get("reason_code") == REASON and row.get("status") == "settled"
        ]
        grouped = _canonical_corrections(settled_rows)
        model_repaired = 0
        for path, corrections in grouped.items():
            if not path.exists():
                continue
            backup = paths.ledgers_root / f"{path.stem}.pre-flat-mlb-pnl-{stamp}.xlsx.bak"
            shutil.copy2(path, backup)
            report["backups"].append(str(backup))
            model_repaired += len(
                ModelLedger(path).repair_events_from_canonical(
                    corrections,
                    correction_reason="canonical Flat MLB confidence-size/P&L repair",
                )
            )
        report["model_rows_repaired"] = model_repaired
        report["model_rows_after"] = _model_matches(grouped)
        report["canonical_rows_repaired"] = changed
        report["integrity"] = integrity_report(paths)
        if not report["integrity"]["chain_ok"]:
            raise RuntimeError(f"canonical integrity failed: {report['integrity']['first_problem']}")
        return report
    finally:
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run(apply=args.apply)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
