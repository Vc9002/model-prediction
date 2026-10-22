"""Backfill settled research / gated-research history into Flat / Main.

The per-sport research (every candidate) and gated-research (CALL subset) ledgers were
merged into Flat and Main on 2026-10-22. New picks are routed there by the daily run;
this script copies the existing settled history across, through the hash-chained
RuntimeLedgerStore so the audit trail stays valid:

    research        -> flat
    gated_research  -> main

Rules:
  * Only settled rows are copied (open rows settle in their legacy ledger first; re-run
    this script afterwards -- it is idempotent). Superseded and removed rows are skipped.
  * One row per pick key (model, event, market, selection, line): the latest by
    created_at_utc, matching what the ledger now does for re-forecasts.
  * A key already present in the destination tier is skipped, never overwritten.
  * Original pick_id, timestamps, result and P&L are preserved; provenance is added to
    decision_payload (merged_from_tier, merged_from_pick_id, merge_tag).
  * Dry run by default. --apply writes. Take a backup of ledgers.db first.

Usage:
    python scripts/merge_research_ledgers.py [--runtime-root PATH] [--apply]
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

from model_prediction.research_ledgers import RESEARCH_LEDGER_SPORTS
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

MERGE_TAG = "research-merge-2026-10-22"
SOURCE_TO_DEST = {"research": "flat", "gated_research": "main"}
KEY_COLUMNS = ("model_id", "event_id", "market_type", "selection", "line")
_SOURCE_COLUMNS = (
    "pick_id, ledger_tier, sport, event_id, canonical_event_id, event_start_utc, market_type, selection, "
    "line, model_id, model_artifact_hash, market_snapshot_hash, market_snapshot_archive_path, "
    "market_snapshot_record_id, feature_schema_version, model_probability, market_probability, edge, "
    "confidence, units, decision, reason_code, status, result, pnl_units, created_at_utc, settled_at_utc, "
    "decision_payload_json, feature_payload_json"
)


def pick_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in KEY_COLUMNS)


def load_source_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    sports = ",".join("?" for _ in RESEARCH_LEDGER_SPORTS)
    tiers = ",".join("?" for _ in SOURCE_TO_DEST)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        f"SELECT {_SOURCE_COLUMNS} FROM ledger_records "
        f"WHERE ledger_tier IN ({tiers}) AND sport IN ({sports}) AND status = 'settled'",
        (*SOURCE_TO_DEST, *RESEARCH_LEDGER_SPORTS),
    )
    return [dict(row) for row in cursor]


def load_destination_keys(conn: sqlite3.Connection) -> dict[str, set[tuple[Any, ...]]]:
    sports = ",".join("?" for _ in RESEARCH_LEDGER_SPORTS)
    tiers = ",".join("?" for _ in set(SOURCE_TO_DEST.values()))
    conn.row_factory = sqlite3.Row
    keys: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    cursor = conn.execute(
        f"SELECT ledger_tier, {', '.join(KEY_COLUMNS)} FROM ledger_records "
        f"WHERE ledger_tier IN ({tiers}) AND sport IN ({sports}) AND status IN ('open', 'settled')",
        (*set(SOURCE_TO_DEST.values()), *RESEARCH_LEDGER_SPORTS),
    )
    for row in cursor:
        record = dict(row)
        keys[record["ledger_tier"]].add(pick_key(record))
    return keys


def plan_rows(
    source_rows: list[dict[str, Any]], destination_keys: dict[str, set[tuple[Any, ...]]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Choose the rows to copy: latest per key, skipping keys already in the destination."""
    groups: dict[tuple[str, tuple[Any, ...]], list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        groups[(row["ledger_tier"], pick_key(row))].append(row)
    chosen: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "source_rows": len(source_rows),
        "duplicate_rows_dropped": 0,
        "already_present": 0,
    }
    for (source_tier, key), group in groups.items():
        group.sort(key=lambda r: (r["created_at_utc"], r["pick_id"]))
        stats["duplicate_rows_dropped"] += len(group) - 1
        if key in destination_keys.get(SOURCE_TO_DEST[source_tier], set()):
            stats["already_present"] += 1
            continue
        chosen.append(group[-1])
    chosen.sort(key=lambda r: (r["created_at_utc"], r["pick_id"]))
    stats["to_copy"] = len(chosen)
    return chosen, stats


def to_mutation(row: dict[str, Any]) -> LedgerMutation:
    decision_payload = json.loads(row["decision_payload_json"]) if row["decision_payload_json"] else {}
    decision_payload.update(
        {
            "merged_from_tier": row["ledger_tier"],
            "merged_from_pick_id": row["pick_id"],
            "merge_tag": MERGE_TAG,
        }
    )
    feature_payload = json.loads(row["feature_payload_json"]) if row["feature_payload_json"] else None
    return LedgerMutation(
        pick_id=row["pick_id"],
        operation_id=f"op-merge-{row['ledger_tier']}-{row['pick_id']}",
        ledger_tier=SOURCE_TO_DEST[row["ledger_tier"]],
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
        note=f"{MERGE_TAG}: copied from {row['ledger_tier']}",
    )


def summarize(rows: list[dict[str, Any]]) -> list[str]:
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[(row["sport"], SOURCE_TO_DEST[row["ledger_tier"]])].append(row)
    lines = [f"{'sport':<12}{'to':<6}{'rows':>6}{'wins':>6}{'loss':>6}{'push':>6}{'units':>10}"]
    for (sport, destination), group in sorted(by_group.items()):
        results = [r["result"] for r in group]
        units = sum(float(r["pnl_units"] or 0) for r in group)
        lines.append(
            f"{sport:<12}{destination:<6}{len(group):>6}{results.count('win'):>6}"
            f"{results.count('loss'):>6}{results.count('push'):>6}{units:>+10.2f}"
        )
    return lines


def run(paths: RuntimePaths, *, apply: bool) -> dict[str, Any]:
    db = Path(paths.ledgers_db)
    if not db.exists():
        raise FileNotFoundError(f"ledger database not found: {db}")
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        source_rows = load_source_rows(conn)
        destination_keys = load_destination_keys(conn)
    chosen, stats = plan_rows(source_rows, destination_keys)
    result: dict[str, Any] = {**stats, "applied": 0, "summary": summarize(chosen), "integrity_ok": None}
    if not apply:
        return result
    with RuntimeLedgerStore(paths) as store:
        for row in chosen:
            if store.apply(to_mutation(row)):
                result["applied"] += 1
        ok, problems = store.verify_integrity()
    result["integrity_ok"] = ok
    result["integrity_problems"] = problems[:5]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runtime-root", default=os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT", ""))
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true", help="write the copies (default is a dry run)")
    args = parser.parse_args(argv)
    if not args.runtime_root:
        print("set --runtime-root or MODEL_PREDICTION_RUNTIME_ROOT", file=sys.stderr)
        return 2
    paths = RuntimePaths(repo_root=Path(args.repo_root), runtime_root=Path(args.runtime_root))
    result = run(paths, apply=args.apply)
    print("\n".join(result["summary"]))
    print(
        f"source settled rows {result['source_rows']}, duplicate rows dropped {result['duplicate_rows_dropped']}, "
        f"already present {result['already_present']}, to copy {result['to_copy']}"
    )
    if args.apply:
        print(f"applied {result['applied']} mutations; hash chain intact: {result['integrity_ok']}")
        if result["integrity_ok"] is False:
            print("integrity problems:", result["integrity_problems"], file=sys.stderr)
            return 1
    else:
        print("dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
