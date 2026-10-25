"""Import settled history from the pre-cutover repo ledger into the live runtime ledger.

Until the 2026-08/10 SQLite cutover the ledger lived at <repo>/data/ledgers/ledgers.db. The
live store (<runtime-root>/ledgers/ledgers.db) started later and never received that history,
so thousands of settled Flat / research picks (2026-07-27 .. 2026-10-05) exist only in the old
file. This copies them across through the hash-chained RuntimeLedgerStore, so the audit trail
stays valid.

Rules (same spirit as merge_research_ledgers.py):
  * Only settled rows. Removed / archived / open / superseded legacy rows are skipped.
  * One row per pick key (model, event, market, selection, line): the latest by created_at_utc.
  * A pick_id already in the live store, or a key already live in the SAME tier (any of
    open / settled / superseded), is skipped, never overwritten. This is what keeps re-keyed
    duplicates out.
  * Tier follows the 2026-10-22 merge: research -> flat, gated_research -> main; flat and main
    stay as they are.
  * Original pick_id, timestamps, result and P&L are preserved; provenance goes in
    decision_payload (imported_from, merge_tag). Idempotent: re-running adds nothing.
  * Dry run by default. --apply writes. Take a backup of the live ledgers.db first and run it
    while no scheduled job is writing.

One-time tool, already run on 2026-10-25 (the old file was then retired to
data/backups/ledgers.retired-20261025.db). Kept as the record of how the history was moved.

Usage:
    python scripts/import_legacy_ledger.py [--legacy-db PATH] [--runtime-root PATH] [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from merge_research_ledgers import _SOURCE_COLUMNS, KEY_COLUMNS, SOURCE_TO_DEST, pick_key, rebuild_exports

from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

MERGE_TAG = "legacy-ledger-import-2026-10-25"
_WORKBOOK_TIERS = ("main", "flat")


def destination_tier(source_tier: str) -> str:
    return SOURCE_TO_DEST.get(source_tier, source_tier)


def load_legacy_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"SELECT {_SOURCE_COLUMNS} FROM ledger_records WHERE status = 'settled'")
    return [dict(row) for row in cursor]


def load_live_index(conn: sqlite3.Connection) -> tuple[set[str], dict[str, set[tuple[Any, ...]]]]:
    """Every live pick_id, and the key set per tier over rows that still count as history."""
    conn.row_factory = sqlite3.Row
    pick_ids: set[str] = set()
    keys: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    cursor = conn.execute(
        f"SELECT pick_id, ledger_tier, status, {', '.join(KEY_COLUMNS)} FROM ledger_records"
    )
    for row in cursor:
        record = dict(row)
        pick_ids.add(record["pick_id"])
        if record["status"] in ("open", "settled", "superseded"):
            keys[record["ledger_tier"]].add(pick_key(record))
    return pick_ids, keys


def plan_rows(
    legacy_rows: list[dict[str, Any]],
    live_pick_ids: set[str],
    live_keys: dict[str, set[tuple[Any, ...]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[str, tuple[Any, ...]], list[dict[str, Any]]] = defaultdict(list)
    for row in legacy_rows:
        groups[(destination_tier(row["ledger_tier"]), pick_key(row))].append(row)
    stats = {
        "legacy_settled": len(legacy_rows),
        "duplicate_rows_dropped": 0,
        "pick_id_live": 0,
        "key_live": 0,
    }
    chosen: list[dict[str, Any]] = []
    for (tier, key), group in groups.items():
        group.sort(key=lambda r: (r["created_at_utc"], r["pick_id"]))
        stats["duplicate_rows_dropped"] += len(group) - 1
        latest = group[-1]
        # The live store already holds legacy research history copied into Flat / Main on
        # 2026-10-22, so a live key in the destination tier OR the legacy source tier counts.
        source_tiers = {r["ledger_tier"] for r in group} | {tier}
        if any(r["pick_id"] in live_pick_ids for r in group):
            stats["pick_id_live"] += 1
        elif any(key in live_keys.get(t, set()) for t in source_tiers):
            stats["key_live"] += 1
        else:
            chosen.append(latest)
    chosen.sort(key=lambda r: (r["created_at_utc"], r["pick_id"]))
    stats["to_import"] = len(chosen)
    return chosen, stats


def to_mutation(row: dict[str, Any]) -> LedgerMutation:
    decision_payload = json.loads(row["decision_payload_json"]) if row["decision_payload_json"] else {}
    decision_payload.update({"imported_from": "repo data/ledgers/ledgers.db", "merge_tag": MERGE_TAG})
    feature_payload = json.loads(row["feature_payload_json"]) if row["feature_payload_json"] else None
    return LedgerMutation(
        pick_id=row["pick_id"],
        operation_id=f"op-legacy-import-{row['pick_id']}",
        ledger_tier=destination_tier(row["ledger_tier"]),
        sport=row["sport"],
        event_type="append",
        created_at_utc=row["created_at_utc"],
        event_id=row["event_id"],
        canonical_event_id=row["canonical_event_id"],
        event_start_utc=row["event_start_utc"],
        market_type=row["market_type"],
        selection=row["selection"],
        line=row["line"],
        model_id=row["model_id"],
        model_artifact_hash=row["model_artifact_hash"],
        market_snapshot_hash=row["market_snapshot_hash"],
        market_snapshot_archive_path=row["market_snapshot_archive_path"],
        market_snapshot_record_id=row["market_snapshot_record_id"],
        feature_schema_version=row["feature_schema_version"],
        model_probability=row["model_probability"],
        market_probability=row["market_probability"],
        edge=row["edge"],
        confidence=row["confidence"],
        units=row["units"],
        decision=row["decision"],
        reason_code=row["reason_code"],
        status=row["status"],
        result=row["result"],
        pnl_units=row["pnl_units"],
        settled_at_utc=row["settled_at_utc"],
        decision_payload=decision_payload,
        feature_payload=feature_payload,
        note=f"{MERGE_TAG}: copied from legacy repo ledger ({row['ledger_tier']})",
    )


def summarize(rows: list[dict[str, Any]]) -> list[str]:
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[(destination_tier(row["ledger_tier"]), row["sport"])].append(row)
    lines = [f"{'tier':<16}{'sport':<12}{'rows':>6}{'wins':>6}{'loss':>6}{'push':>6}{'units':>10}"]
    for (tier, sport), group in sorted(by_group.items()):
        results = [r["result"] for r in group]
        units = sum(float(r["pnl_units"] or 0) for r in group)
        lines.append(
            f"{tier:<16}{sport:<12}{len(group):>6}{results.count('win'):>6}"
            f"{results.count('loss'):>6}{results.count('push'):>6}{units:>+10.2f}"
        )
    total = sum(float(r["pnl_units"] or 0) for r in rows)
    lines.append(f"{'TOTAL':<28}{len(rows):>6}{'':>18}{total:>+10.2f}")
    return lines


def run(
    paths: RuntimePaths, legacy_db: Path, *, apply: bool, data_root: Path | None = None
) -> dict[str, Any]:
    live_db = Path(paths.ledgers_db)
    for db in (legacy_db, live_db):
        if not db.exists():
            raise FileNotFoundError(f"ledger database not found: {db}")
    with sqlite3.connect(f"file:{legacy_db.as_posix()}?mode=ro", uri=True) as conn:
        legacy_rows = load_legacy_rows(conn)
    with sqlite3.connect(f"file:{live_db.as_posix()}?mode=ro", uri=True) as conn:
        live_pick_ids, live_keys = load_live_index(conn)
    chosen, stats = plan_rows(legacy_rows, live_pick_ids, live_keys)
    result: dict[str, Any] = {
        **stats,
        "applied": 0,
        "summary": summarize(chosen),
        "integrity_ok": None,
        "exports_rebuilt": 0,
    }
    if not apply:
        return result
    with RuntimeLedgerStore(paths) as store:
        for row in chosen:
            if store.apply(to_mutation(row)):
                result["applied"] += 1
        ok, problems = store.verify_integrity()
        targets = {
            (row["sport"], destination_tier(row["ledger_tier"]))
            for row in chosen
            if destination_tier(row["ledger_tier"]) in _WORKBOOK_TIERS
        }
        result["exports_rebuilt"] = rebuild_exports(store, data_root or paths.repo_root / "data", targets)
    result["integrity_ok"] = ok
    result["integrity_problems"] = problems[:5]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runtime-root", default=os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT", ""))
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--legacy-db", default="", help="default: <repo-root>/data/backups/ledgers.retired-20261025.db"
    )
    parser.add_argument("--data-root", default="", help="workbook directory (default: <repo-root>/data)")
    parser.add_argument("--apply", action="store_true", help="write the copies (default is a dry run)")
    args = parser.parse_args(argv)
    if not args.runtime_root:
        print("set --runtime-root or MODEL_PREDICTION_RUNTIME_ROOT", file=sys.stderr)
        return 2
    repo_root = Path(args.repo_root)
    paths = RuntimePaths(repo_root=repo_root, runtime_root=Path(args.runtime_root))
    legacy_db = (
        Path(args.legacy_db)
        if args.legacy_db
        else repo_root / "data" / "backups" / "ledgers.retired-20261025.db"
    )
    result = run(
        paths, legacy_db, apply=args.apply, data_root=Path(args.data_root) if args.data_root else None
    )
    print("\n".join(result["summary"]))
    print(
        f"legacy settled rows {result['legacy_settled']}, duplicate rows dropped {result['duplicate_rows_dropped']}, "
        f"pick_id already live {result['pick_id_live']}, key already live {result['key_live']}, "
        f"to import {result['to_import']}"
    )
    if args.apply:
        print(
            f"applied {result['applied']} mutations; hash chain intact: {result['integrity_ok']}; "
            f"workbook exports rebuilt: {result['exports_rebuilt']}"
        )
        if result["integrity_ok"] is False:
            print("integrity problems:", result["integrity_problems"], file=sys.stderr)
            return 1
    else:
        print("dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
