"""Resolve settlement identity conflicts with SQLite-canonical evidence.

``audit_ledger_pnl.py`` reports settlement keys where two tier workbooks
record different economics for the same pick, and deliberately refuses to
guess. This companion resolves those conflicts only when the canonical
SQLite store supplies stronger evidence than the projections do:

R1  stale-survivor archive — a settled row in the same tier as the
    canonical row with different economics is archived (evidence preserved,
    status only).
R2  latest-settlement authority — among settled rows for one identity that
    agree on the result, the most recently settled row is the audited
    correction (regrade / authenticated-price sync); every other row is
    corrected stake-normalized to its economics.
R3  lineage-backed reference — exactly one row carries market-snapshot
    lineage; it outranks the latest-settlement rule as the reference.
R4  benign sizing variance — same result and stake-normalized economics
    (pnl/units equal); no ledger mutation, projection rebuild only.

Rows that disagree on the RESULT are never auto-resolved — they need an
operator decision.

After SQLite corrections, the affected tier XLSX projections are rebuilt
from canonical rows via ``PickLedger.rebuild_xlsx_projection``.

Dry-run is the default. Apply explicitly::

    MODEL_PREDICTION_RUNTIME_ROOT=/... MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite \
        python scripts/resolve_ledger_conflicts.py \
        --apply --report tmp/ledger-conflicts-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.ledger import PickLedger
from model_prediction.runtime_ledger_store import LedgerMutation, RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from scripts import audit_ledger_pnl  # conflict keys come from the audit's own scan

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPAIR_ERA_START = "2026-08-24T00:00:00Z"


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_pnl(row: dict[str, Any]) -> float | None:
    """Stake-normalized economics: pnl per unit staked.

    Loss rows carry pnl = -units by invariant, so this folds sizing out of
    the comparison and leaves the economic content (quote / result).
    """
    pnl = _number(row.get("pnl_units"))
    units = _number(row.get("units"))
    if pnl is None or not units:
        return None
    return round(pnl / abs(units), 6)


def _settled_sqlite_rows(store: RuntimeLedgerStore, key: list[str]) -> list[dict[str, Any]]:
    event_id, market_type, _line, model_id, selection = key
    rows = []
    for row in store.records():
        if row["status"] != "settled":
            continue
        if str(row.get("event_id") or "") != event_id:
            continue
        if str(row.get("market_type") or "") != market_type:
            continue
        if str(row.get("model_id") or "") != model_id:
            continue
        if str(row.get("selection") or "") != selection:
            continue
        rows.append(row)
    return rows


def _operation_id(pick_id: str, tier: str, resolution: str, values: str) -> str:
    material = f"op-conflict|{tier}|{pick_id}|{resolution}|{values}"
    return f"op-conflict-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _mutation(
    row: dict[str, Any],
    *,
    resolution: str,
    event_type: str = "settle",
    status: str | None = None,
    market_probability: float | None = None,
    edge: float | None = None,
    pnl_units: float | None = None,
    note: str = "",
    payload_updates: dict[str, Any] | None = None,
) -> LedgerMutation:
    # Parse the stored JSON-string payload first — a correction must extend
    # the original record, never replace it with an empty one.
    payload = _parse_payload(row.get("decision_payload_json"))
    if payload_updates:
        payload.update(payload_updates)
    payload["_conflict_resolution"] = {"resolution": resolution, "note": note}
    return LedgerMutation(
        pick_id=row["pick_id"],
        operation_id=_operation_id(
            row["pick_id"],
            row["ledger_tier"],
            resolution,
            f"{market_probability}|{pnl_units}",
        ),
        ledger_tier=row["ledger_tier"],
        sport=row["sport"],
        event_type=event_type,
        created_at_utc=row["created_at_utc"],
        event_id=row["event_id"],
        canonical_event_id=row["canonical_event_id"],
        event_start_utc=row["event_start_utc"],
        market_type=row["market_type"],
        selection=row["selection"],
        line=_number(row.get("line")),
        model_id=row["model_id"],
        model_artifact_hash=row["model_artifact_hash"],
        market_snapshot_hash=row["market_snapshot_hash"],
        market_snapshot_archive_path=row["market_snapshot_archive_path"],
        market_snapshot_record_id=row["market_snapshot_record_id"],
        feature_schema_version=row["feature_schema_version"],
        model_probability=_number(row.get("model_probability")),
        market_probability=(
            market_probability if market_probability is not None else _number(row.get("market_probability"))
        ),
        edge=edge if edge is not None else _number(row.get("edge")),
        confidence=_number(row.get("confidence")),
        units=_number(row.get("units")),
        decision=row["decision"],
        reason_code=row["reason_code"],
        status=status if status is not None else row["status"],
        result=row["result"],
        pnl_units=pnl_units if pnl_units is not None else _number(row.get("pnl_units")),
        settled_at_utc=row["settled_at_utc"],
        decision_payload=payload,
        note=note,
    )


def _backup_sqlite(db_path: Path, stamp: str) -> Path:
    backup = db_path.with_name(f"{db_path.stem}.bak-conflicts-{stamp}{db_path.suffix}")
    source = sqlite3.connect(db_path, timeout=10)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def _rebuild_projection(tier: str, sport: str, store: RuntimeLedgerStore) -> int:
    """Rebuild one tier XLSX projection from canonical SQLite rows."""
    audit = DATA / "events.jsonl" if tier in {"main", "flat"} else DATA / tier / "events.jsonl"
    path = DATA / tier / f"{sport}.xlsx"
    ledger = PickLedger(
        path,
        audit_path=audit,
        model_ledgers_dir=DATA / "model_ledgers",
        tier=tier,
        mirror=store,
        authority="sqlite",
        sport=sport,
    )
    return ledger.rebuild_xlsx_projection()


def _parse_payload(value: object) -> dict[str, Any]:
    """Canonical rows store decision_payload_json as a JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _payload_economics(payload: object) -> tuple[object, object]:
    parsed = _parse_payload(payload)
    if not parsed:
        return (None, None)
    return (
        parsed.get("closing_raw_implied_probability") or parsed.get("closing_implied_probability"),
        parsed.get("probability_clv"),
    )


def _payload_updates_from_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """Closing/CLV fields to copy from the reference row's payload.

    Only fields the reference payload actually holds are copied — a
    correction must never invent values the evidence did not record.
    """
    parsed = _parse_payload(reference.get("decision_payload_json"))
    updates: dict[str, Any] = {}
    for key in ("closing_raw_implied_probability", "closing_implied_probability", "probability_clv"):
        if key in parsed:
            updates[key] = parsed[key]
    return updates


def _resolve(store: RuntimeLedgerStore, key: list[str]) -> dict[str, Any] | None:
    rows = _settled_sqlite_rows(store, key)
    if not rows:
        return {"key": key, "resolution": "unresolved", "reason": "no settled sqlite rows"}

    # Result disagreement is an operator decision, never auto-resolved.
    results = {row["result"] for row in rows}
    if len(results) > 1:
        return {
            "key": key,
            "resolution": "unresolved",
            "reason": "settled rows disagree on result",
            "resolutions": [],
        }

    # R4 — economically identical sizing variance. When the stored payload
    # closing/CLV fields still disagree (a stale settlement snapshot), plan
    # a payload-only sync so the rebuilt projections converge.
    if len({_normalized_pnl(row) for row in rows}) <= 1:
        payload_economics = {_payload_economics(row.get("decision_payload_json")) for row in rows}
        if len(payload_economics) <= 1:
            return {"key": key, "resolution": "benign_or_unresolved", "resolutions": []}
        lineage_rows = [row for row in rows if row["market_snapshot_hash"] is not None]
        if len(lineage_rows) == 1:
            reference = lineage_rows[0]
        else:
            # Prefer a row whose payload closing price matches its own
            # market_probability — the internally consistent snapshot.
            consistent = [
                row
                for row in rows
                if _number(_payload_economics(row.get("decision_payload_json"))[0]) is not None
                and _number(_payload_economics(row.get("decision_payload_json"))[0])
                == _number(row.get("market_probability"))
            ]
            pool = consistent or rows
            reference = max(pool, key=lambda r: r["settled_at_utc"] or "")
        updates = _payload_updates_from_reference(reference)
        resolutions = []
        for row in rows:
            if row["pick_id"] == reference["pick_id"]:
                continue
            if _payload_economics(row.get("decision_payload_json")) == _payload_economics(
                reference.get("decision_payload_json")
            ):
                continue
            resolutions.append(
                {
                    "rule": "R4-payload-sync",
                    "action": "correct",
                    "pick_id": row["pick_id"],
                    "tier": row["ledger_tier"],
                    "event_id": row["event_id"],
                    "reference_pick_id": reference["pick_id"],
                    "reference_tier": reference["ledger_tier"],
                    "result": row["result"],
                    "old_pnl_units": row["pnl_units"],
                    "new_pnl_units": row["pnl_units"],
                    "new_market_probability": row["market_probability"],
                    "new_edge": row["edge"],
                    "payload_updates": updates,
                }
            )
        if not resolutions:
            return {"key": key, "resolution": "benign_or_unresolved", "resolutions": []}
        return {
            "key": key,
            "resolution": "resolved",
            "reference_pick_id": reference["pick_id"],
            "reference_tier": reference["ledger_tier"],
            "resolutions": resolutions,
        }

    # R3 then R2: the canonical row is the lineage-backed one when exactly
    # one row has snapshot lineage; otherwise the most recently settled row
    # (the audited regrade / authenticated-price correction).
    lineage_rows = [row for row in rows if row["market_snapshot_hash"] is not None]
    if len(lineage_rows) == 1:
        reference = lineage_rows[0]
        rule = "R3-lineage"
    else:
        reference = max(rows, key=lambda r: r["settled_at_utc"] or "")
        rule = "R2-latest-settled"

    resolutions: list[dict[str, Any]] = []
    ref_norm = _normalized_pnl(reference)
    ref_market = _number(reference.get("market_probability"))
    ref_payload_updates = _payload_updates_from_reference(reference)
    for row in rows:
        if row["pick_id"] == reference["pick_id"]:
            continue
        if _normalized_pnl(row) == ref_norm:
            continue  # same economics after normalization: nothing to fix
        if row["ledger_tier"] == reference["ledger_tier"]:
            resolutions.append(
                {
                    "rule": "R1",
                    "action": "archive",
                    "pick_id": row["pick_id"],
                    "tier": row["ledger_tier"],
                    "result": row["result"],
                    "pnl_units": row["pnl_units"],
                    "settled_at_utc": row["settled_at_utc"],
                }
            )
            continue
        stale_units = _number(row.get("units"))
        if ref_norm is None or not stale_units:
            continue
        # Ledger pnl_units are written at 4 decimals (0.9615, 1.0684, ...) —
        # round corrections to the same convention so rebuilt projections
        # byte-match their reference rows instead of colliding at the 5th
        # decimal with a differently-formatted sibling.
        corrected_pnl = round(ref_norm * abs(stale_units), 4)
        model_prob = _number(row.get("model_probability"))
        edge = (
            round(model_prob - ref_market, 6) if model_prob is not None and ref_market is not None else None
        )
        resolutions.append(
            {
                "rule": rule,
                "action": "correct",
                "pick_id": row["pick_id"],
                "tier": row["ledger_tier"],
                "event_id": row["event_id"],
                "reference_pick_id": reference["pick_id"],
                "reference_tier": reference["ledger_tier"],
                "result": row["result"],
                "old_pnl_units": row["pnl_units"],
                "new_pnl_units": corrected_pnl,
                "new_market_probability": ref_market,
                "new_edge": edge,
                "payload_updates": ref_payload_updates,
            }
        )
    if not resolutions:
        return {"key": key, "resolution": "benign_or_unresolved", "resolutions": []}
    return {
        "key": key,
        "resolution": "resolved",
        "reference_pick_id": reference["pick_id"],
        "reference_tier": reference["ledger_tier"],
        "resolutions": resolutions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    paths = RuntimePaths.resolve(repo_root=ROOT, require_external_runtime=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    # Conflict keys come from the same workbook-source scan the audit uses.
    with RuntimeLedgerStore(paths) as store:
        _unambiguous, conflicts = audit_ledger_pnl._model_sources()
        plans = [_resolve(store, conflict["key"]) for conflict in conflicts]

        sqlite_backup = None
        applied = 0
        projection_rebuilds: dict[tuple[str, str], int] = {}
        if args.apply:
            for plan in plans:
                if plan.get("resolution") != "resolved":
                    continue
                for resolution in plan["resolutions"]:
                    if resolution["rule"] == "R1":
                        mutation = _mutation(
                            next(
                                row
                                for row in store.records()
                                if row["pick_id"] == resolution["pick_id"]
                                and row["ledger_tier"] == resolution["tier"]
                            ),
                            resolution="R1-stale-survivor",
                            event_type="archive",
                            status="archived",
                            note="conflict resolution: stale pre-correction survivor archived",
                        )
                        applied += int(store.apply(mutation))
                    elif resolution["action"] == "correct":
                        mutation = _mutation(
                            next(
                                row
                                for row in store.records()
                                if row["pick_id"] == resolution["pick_id"]
                                and row["ledger_tier"] == resolution["tier"]
                            ),
                            resolution=resolution["rule"],
                            market_probability=resolution["new_market_probability"],
                            edge=resolution["new_edge"],
                            pnl_units=resolution["new_pnl_units"],
                            note=(
                                f"conflict resolution {resolution['rule']}: economics "
                                f"corrected to reference {resolution['reference_tier']} "
                                f"{resolution['reference_pick_id']}"
                            ),
                            payload_updates=resolution.get("payload_updates"),
                        )
                        applied += int(store.apply(mutation))
            if applied:
                sqlite_backup = _backup_sqlite(paths.ledgers_db, stamp)
            # Rebuild projections AFTER mutations so exports match canonical.
            # Every affected (tier, sport) pair is rebuilt even when this run
            # found nothing left to mutate — a previous partial run may have
            # corrected SQLite without refreshing the exports.
            affected_pairs = {
                (row["ledger_tier"], row["sport"])
                for conflict in conflicts
                for row in _settled_sqlite_rows(store, conflict["key"])
            }
            for tier, sport in sorted(affected_pairs):
                if tier in {"main", "flat", "flat_v9", "research", "gated_research"}:
                    projection_rebuilds[(tier, sport)] = _rebuild_projection(tier, sport, store)
        integrity_ok, integrity_problems = store.verify_integrity()

    report = {
        "mode": "apply" if args.apply else "dry_run",
        "runtime_db": str(paths.ledgers_db),
        "conflicts": plans,
        "sqlite_mutations_applied": applied,
        "sqlite_backup": str(sqlite_backup) if sqlite_backup else None,
        "projection_rebuilds": {
            f"{tier}/{sport}": count for (tier, sport), count in projection_rebuilds.items()
        },
        "sqlite_integrity": {"ok": integrity_ok, "problems": integrity_problems},
    }
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    unresolved = [p for p in plans if p.get("resolution") != "resolved"]
    return 0 if integrity_ok and not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
