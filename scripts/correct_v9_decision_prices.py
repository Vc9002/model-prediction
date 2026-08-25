"""Replace MLB v9's fabricated -110 entries with authenticated PIT quotes.

The original benchmark script hard-coded every row to consensus -110 while
the dashboard displayed a separate market close. This made every winning row
show +0.9091U regardless of the real decision-time price. The correction is
strictly identity- and time-scoped: only v9 placeholder or prior no-price rows
are eligible, and each requires an authenticated market snapshot observed no
later than that row's forecast timestamp and before first pitch. The flat
benchmark is explicitly stale-tolerant because every recorded pick is a 1.0U
CALL; the normal 30-minute execution freshness gate does not apply here.

Dry-run is the default. ``--apply`` backs up every changed workbook and the
append-only audit log, records one correction event per primary-ledger row,
and updates both v9 model-ledger views.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.daily_lock import acquire_lock
from model_prediction.data_sources.mlb_market_odds import MarketOddsSnapshotStore
from model_prediction.ledger import PickLedger
from model_prediction.model_ledger import ModelLedger
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_LEDGER = ROOT / "data" / "flat_v9" / "mlb.xlsx"
AUDIT_LOG = ROOT / "data" / "flat_v9" / "events.jsonl"
SNAPSHOTS = ROOT / "data" / "market_odds_snapshots.jsonl"
MODEL_LEDGERS = (
    ROOT / "data" / "flat_v9" / "model_ledgers" / "mlb-moneyline-elo-trend-lr.xlsx",
    ROOT / "data" / "model_ledgers" / "mlb-v9-candidate-1.xlsx",
)
MODEL_VERSION = "mlb-v9-candidate-1"
REASON = (
    "Correct fabricated consensus -110 v9 entry using the latest authenticated "
    "Polymarket US snapshot known at the original forecast timestamp."
)


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_placeholder(row: dict[str, str]) -> bool:
    return (
        row.get("model_version") == MODEL_VERSION
        and str(row.get("sportsbook") or "").casefold() == "consensus"
        and str(row.get("decision_american_odds") or row.get("american_odds")) == "-110"
        and abs((_number(row.get("decision_raw_implied_probability")) or 0.0) - 0.52381) < 1e-5
    )


def _needs_primary_price_repair(row: dict[str, str]) -> bool:
    return _is_placeholder(row) or (
        row.get("model_version") == MODEL_VERSION
        and row.get("decision") == "NO_CALL"
        and row.get("reason_code") == "NO_CALL_MARKET_PRICE_UNAVAILABLE"
    )


def _evidence_for_row(store: MarketOddsSnapshotStore, row: dict[str, str]) -> dict[str, Any] | None:
    observed_at = row.get("observed_at_utc") or row.get("created_at_utc")
    if not observed_at:
        return None
    return store.decision_quote(
        str(row.get("event_id") or ""),
        observed_at,
        str(row.get("market_type") or "moneyline"),
        str(row.get("selection") or ""),
        provider="polymarket_us",
        maximum_age=None,
    )


def _correct_primary_row(row: dict[str, str], evidence: dict[str, Any]) -> dict[str, str]:
    corrected = dict(row)
    snapshot, quote = evidence["snapshot"], evidence["quote"]
    entry = float(quote["decision_probability"])
    decimal = 1.0 / entry
    edge = float(row["model_probability"]) - entry
    corrected.update(
        {
            "sportsbook": "polymarket_us",
            "american_odds": str(int(quote["american_odds"])),
            "decimal_odds": f"{decimal:.6f}",
            "market_implied_probability": f"{entry:.6f}",
            "decision_american_odds": str(int(quote["american_odds"])),
            "decision_decimal_odds": f"{decimal:.6f}",
            "decision_raw_implied_probability": f"{entry:.6f}",
            "market_probability_at_decision": f"{entry:.6f}",
            "edge": f"{edge:.6f}",
            "trade_candidate": str(edge > 0),
            "market_quote_observed_at_utc": evidence["observed_at_utc"],
            "market_quote_timestamp_valid": "True",
            "market_quote_source": "polymarket_us",
            "market_quote_provenance": "decision_time_executable_quote",
            "market_quote_reconstructed": str(
                bool(snapshot.get("raw_response", {}).get("reconstructed", False))
            ),
            "market_snapshot_hash": str(snapshot["snapshot_hash"]),
            "market_snapshot_archive_path": str(snapshot["snapshot_archive_path"]),
            "market_snapshot_record_id": str(snapshot["snapshot_record_id"]),
            "record_type": "QUALIFIED_SHADOW_CALL",
            "decision": "CALL",
            "reason_code": "FLAT_BENCHMARK_TRACK",
            "units": "1.00",
        }
    )
    if row.get("status") == "settled" and row.get("result"):
        units = float(corrected["units"])
        if row["result"] == "win":
            pnl = units * (1.0 / entry - 1.0)
        elif row["result"] == "loss":
            pnl = -units
        else:
            pnl = 0.0
        corrected["pnl_units"] = f"{pnl:.4f}"
    return corrected


def _correct_model_row(row: dict[str, str], evidence: dict[str, Any]) -> dict[str, str]:
    corrected = dict(row)
    entry = float(evidence["quote"]["decision_probability"])
    corrected["decision_price"] = f"{entry:.6f}"
    model_probability = _number(row.get("model_probability"))
    if model_probability is not None:
        corrected["model_market_difference"] = f"{model_probability - entry:.6f}"
    missing_inputs = {
        value.strip() for value in str(row.get("missing_inputs") or "").split(",") if value.strip()
    }
    missing_inputs.discard("decision_price")
    corrected["missing_inputs"] = ",".join(sorted(missing_inputs))
    if row.get("input_availability") == "market_price_unavailable_at_decision":
        corrected["input_availability"] = "available" if not missing_inputs else "partial"
    if row.get("status") == "settled" and row.get("result"):
        if row["result"] == "win":
            corrected["pnl_units"] = f"{1.0 / entry - 1.0:.4f}"
        elif row["result"] == "loss":
            corrected["pnl_units"] = "-1.0000"
        else:
            corrected["pnl_units"] = "0.0000"
    return corrected


def _changed_fields(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        key: {"before": before.get(key, ""), "after": value}
        for key, value in after.items()
        if before.get(key, "") != value
    }


def _backup(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-v9-price-{stamp}")
    shutil.copy2(path, backup)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    store = MarketOddsSnapshotStore(SNAPSHOTS)
    ledger = PickLedger(PRIMARY_LEDGER, AUDIT_LOG)
    primary_plan: dict[str, tuple[dict[str, str], dict[str, str], dict[str, Any] | None]] = {}
    primary_missing: list[str] = []
    for row in ledger.rows():
        if not _needs_primary_price_repair(row):
            continue
        evidence = _evidence_for_row(store, row)
        if evidence is None:
            primary_missing.append(row["pick_id"])
            continue
        corrected = _correct_primary_row(row, evidence)
        if _changed_fields(row, corrected):
            primary_plan[row["pick_id"]] = (row, corrected, evidence)

    model_plans: dict[Path, dict[str, dict[str, str]]] = {}
    model_missing: dict[str, list[str]] = {}
    for path in MODEL_LEDGERS:
        if not path.exists():
            continue
        changes: dict[str, dict[str, str]] = {}
        missing: list[str] = []
        for row in ModelLedger(path).rows():
            if row.get("model_version") != MODEL_VERSION:
                continue
            evidence = _evidence_for_row(store, row)
            if evidence is None:
                missing.append(row["prediction_id"])
                continue
            corrected = _correct_model_row(row, evidence)
            if _changed_fields(row, corrected):
                changes[row["prediction_id"]] = corrected
        model_plans[path] = changes
        model_missing[str(path.relative_to(ROOT))] = missing

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backups: list[str] = []
    primary_applied = model_applied = 0
    if args.apply:
        paths = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True)
        writer_lock = acquire_lock(paths.lock_root / "daily.lock")
        if writer_lock is None:
            raise RuntimeError("daily writer lock is busy; v9 price repair refused")
        try:
            if primary_plan:
                backups.extend(str(_backup(path, stamp)) for path in (PRIMARY_LEDGER, AUDIT_LOG))
                with ledger._lock():
                    rows = ledger._read_unlocked()
                    for index, row in enumerate(rows):
                        planned = primary_plan.get(row["pick_id"])
                        if planned is None:
                            continue
                        before, corrected, evidence = planned
                        ledger.audit.append(
                            "decision_price_corrected",
                            row["pick_id"],
                            {
                                "reason": REASON,
                                "snapshot_record_id": evidence["snapshot"]["snapshot_record_id"],
                                "quote_observed_at_utc": evidence["observed_at_utc"],
                                "changed_fields": _changed_fields(before, corrected),
                            },
                        )
                        rows[index] = corrected
                        primary_applied += 1
                    ledger._write_rows(rows)
            for path, changes in model_plans.items():
                if not changes:
                    continue
                backups.append(str(_backup(path, stamp)))
                model_ledger = ModelLedger(path)
                with model_ledger._lock():
                    rows = model_ledger._read_unlocked()
                    for index, row in enumerate(rows):
                        corrected = changes.get(row["prediction_id"])
                        if corrected is None:
                            continue
                        rows[index] = corrected
                        model_applied += 1
                    model_ledger._write_unlocked(rows)
        finally:
            writer_lock.close()

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "primary_planned": len(primary_plan),
        "primary_applied": primary_applied,
        "primary_missing_evidence": primary_missing,
        "primary_unrepairable_no_price": primary_missing,
        "model_planned": {str(path.relative_to(ROOT)): len(changes) for path, changes in model_plans.items()},
        "model_applied": model_applied,
        "model_missing_evidence": model_missing,
        "backups": backups,
        "corrected_primary_rows": {
            pick_id: {
                "event_id": before["event_id"],
                "selection": before["selection"],
                "entry_probability": after.get("market_probability_at_decision") or None,
                "result": after["result"],
                "pnl_units": after["pnl_units"],
                "snapshot_record_id": (evidence["snapshot"]["snapshot_record_id"] if evidence else None),
                "decision": after["decision"],
                "reason_code": after["reason_code"],
                "units": after["units"],
            }
            for pick_id, (before, after, evidence) in primary_plan.items()
        },
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
