"""Backfill explicit feature availability payloads in the canonical ledger.

Dry-run is the default. ``--apply`` requires the exact candidate count,
holds the daily-writer lock, creates an integrity-checked backup, and appends
one hash-linked update event per row. Values are copied only from the stored
decision payload; absent values remain explicitly unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.daily_lock import acquire_lock
from model_prediction.ledger import PickLedger
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

BACKFILL_NOTE = (
    "historical feature payload backfill from stored decision fields; "
    "missing values remain unavailable and were not synthesized"
)


def missing_feature_records(store: RuntimeLedgerStore) -> list[dict[str, Any]]:
    return sorted(
        (row for row in store.records() if row.get("feature_payload_json") is None),
        key=lambda row: (str(row["ledger_tier"]), str(row["sport"]), str(row["pick_id"])),
    )


def _decoded_payload(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("decision_payload_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid decision payload for {record['pick_id']}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"non-object decision payload for {record['pick_id']}")
    return payload


def feature_payload_for(record: dict[str, Any]) -> dict[str, Any]:
    """Derive availability metadata from persisted decision fields only."""
    return PickLedger._feature_payload(_decoded_payload(record))


def mutation_for(record: dict[str, Any]) -> LedgerMutation:
    decision_payload = _decoded_payload(record)
    feature_payload = PickLedger._feature_payload(decision_payload)
    digest = hashlib.sha256(
        json.dumps(feature_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return LedgerMutation(
        pick_id=str(record["pick_id"]),
        operation_id=(f"op-feature-backfill-v1-{record['ledger_tier']}-{record['pick_id']}-{digest}"),
        ledger_tier=str(record["ledger_tier"]),
        sport=str(record["sport"]),
        event_type="update",
        created_at_utc=str(record["created_at_utc"]),
        event_id=record.get("event_id"),
        canonical_event_id=record.get("canonical_event_id"),
        event_start_utc=record.get("event_start_utc"),
        market_type=record.get("market_type"),
        selection=record.get("selection"),
        line=record.get("line"),
        model_id=record.get("model_id"),
        model_artifact_hash=record.get("model_artifact_hash"),
        market_snapshot_hash=record.get("market_snapshot_hash"),
        market_snapshot_archive_path=record.get("market_snapshot_archive_path"),
        market_snapshot_record_id=record.get("market_snapshot_record_id"),
        feature_schema_version=record.get("feature_schema_version"),
        model_probability=record.get("model_probability"),
        market_probability=record.get("market_probability"),
        edge=record.get("edge"),
        confidence=record.get("confidence"),
        units=record.get("units"),
        decision=record.get("decision"),
        reason_code=record.get("reason_code"),
        status=str(record["status"]),
        result=record.get("result"),
        pnl_units=record.get("pnl_units"),
        settled_at_utc=record.get("settled_at_utc"),
        decision_payload=decision_payload or None,
        feature_payload=feature_payload,
        note=BACKFILL_NOTE,
    )


def _backup_database(paths: RuntimePaths) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = paths.runtime_root / "backups" / f"ledgers.pre-feature-backfill.{stamp}.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(paths.ledgers_db)
    backup = sqlite3.connect(destination)
    try:
        source.backup(backup)
        status = backup.execute("PRAGMA integrity_check").fetchone()
        if status is None or status[0] != "ok":
            raise RuntimeError(f"backup integrity check failed: {status}")
    finally:
        backup.close()
        source.close()
    return destination


def _manifest(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_scope = Counter(f"{row['ledger_tier']}:{row['sport']}" for row in records)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "candidate_count": len(records),
        "scope_counts": dict(sorted(by_scope.items())),
        "pick_ids": [f"{row['ledger_tier']}:{row['pick_id']}" for row in records],
        "note": BACKFILL_NOTE,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--apply", action="store_true")
    root.add_argument("--expect-count", type=int)
    root.add_argument("--manifest", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = RuntimePaths.resolve(require_external_runtime=True)
    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        raise RuntimeError("daily writer is active; refusing feature payload backfill")
    try:
        store = RuntimeLedgerStore(paths)
        try:
            records = missing_feature_records(store)
            manifest = _manifest(records)
            if args.manifest:
                args.manifest.parent.mkdir(parents=True, exist_ok=True)
                args.manifest.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            print(
                json.dumps(
                    {key: value for key, value in manifest.items() if key != "pick_ids"},
                    indent=2,
                    sort_keys=True,
                )
            )
            if not args.apply:
                return 0
            if args.expect_count is None:
                raise ValueError("--apply requires --expect-count")
            if len(records) != args.expect_count:
                raise ValueError(
                    f"candidate count changed: expected {args.expect_count}, "
                    f"found {len(records)}; refusing backfill"
                )
            backup = _backup_database(paths)
            applied = sum(1 for record in records if store.apply(mutation_for(record)))
            chain_ok, problems = store.verify_integrity()
            remaining = len(missing_feature_records(store))
        finally:
            store.close()
        if not chain_ok or remaining:
            raise RuntimeError(
                f"post-backfill verification failed: chain_ok={chain_ok} "
                f"remaining={remaining} problems={problems[:1]}"
            )
        print(
            json.dumps(
                {
                    "status": "backfilled",
                    "backup": str(backup),
                    "candidate_count": len(records),
                    "applied": applied,
                    "remaining_missing_feature_payload": remaining,
                    "event_chain_intact": chain_ok,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
