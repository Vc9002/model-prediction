#!/usr/bin/env python3
"""Archive superseded ledger observations and correlated tennis line ladders.

Dry-run is the default. Apply requires exact expected counts, the global daily
writer lock, SQLite authority, a SQLite backup, and a complete JSON archive.
All mutations use PickLedger's audited remove/archive APIs; this script never
deletes canonical rows directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.daily_lock import acquire_lock
from model_prediction.main_ledgers import flat_ledger, main_ledger
from model_prediction.research_ledgers import research_ledger
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths


def _text(value: object) -> str:
    return str(value or "").strip().casefold()


def _line(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return _text(value)


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _observed(row: dict[str, Any]) -> datetime:
    return _time(row.get("observed_at_utc") or row.get("created_at_utc"))


def _contract_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        _text(row.get("ledger_tier")),
        _text(row.get("sport") or row.get("league")),
        _text(row.get("event_id")),
        _text(row.get("market_type")),
        _text(row.get("selection")),
        _line(row.get("line")),
        _text(row.get("period") or row.get("horizon")),
        _text(row.get("sportsbook")),
        _text(row.get("status")),
    )


def _expected_return(row: dict[str, Any]) -> float:
    probability = float(row.get("model_probability") or 0.0)
    price = float(row.get("market_probability_at_decision") or row.get("market_implied_probability") or 0.0)
    return probability / price - 1.0 if 0.0 < price < 1.0 else float("-inf")


def build_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return exact survivors/removals without using any settlement result."""
    active = [row for row in rows if _text(row.get("status")) in {"open", "settled"}]
    by_contract: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        by_contract[_contract_key(row)].append(row)

    retired: dict[str, dict[str, Any]] = {}
    survivors: list[dict[str, Any]] = []
    refresh_groups = 0
    for members in by_contract.values():
        if len(members) == 1:
            survivors.extend(members)
            continue
        refresh_groups += 1
        for row in members:
            if _observed(row) >= _time(row.get("event_start_utc")):
                raise ValueError(f"duplicate group contains non-pregame row {row.get('pick_id')}")
        results = {_text(row.get("result")) for row in members}
        scores = {(_text(row.get("away_score")), _text(row.get("home_score"))) for row in members}
        if len(results) != 1 or len(scores) != 1:
            raise ValueError(f"duplicate group has conflicting outcomes: {_contract_key(members[0])}")
        survivor = max(members, key=lambda row: (_observed(row), _text(row.get("created_at_utc"))))
        survivors.append(survivor)
        for row in members:
            if row is not survivor:
                retired[str(row["pick_id"])] = {
                    "row": row,
                    "reason": "superseded_refresh_observation",
                    "survivor_pick_id": str(survivor["pick_id"]),
                }

    tennis_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in survivors:
        if _text(row.get("sport") or row.get("league")) != "tennis":
            continue
        market_type = _text(row.get("market_type"))
        if market_type not in {"spread", "total"}:
            continue
        tennis_groups[
            (
                _text(row.get("ledger_tier")),
                _text(row.get("sport") or row.get("league")),
                _text(row.get("event_id")),
                market_type,
                _text(row.get("status")),
            )
        ].append(row)

    ladder_groups = 0
    for members in tennis_groups.values():
        if len(members) <= 1:
            continue
        ladder_groups += 1
        survivor = max(
            members,
            key=lambda row: (
                _expected_return(row),
                float(row.get("edge") or 0.0),
                _observed(row),
                str(row.get("pick_id") or ""),
            ),
        )
        for row in members:
            if row is not survivor:
                retired[str(row["pick_id"])] = {
                    "row": row,
                    "reason": "correlated_tennis_line_superseded",
                    "survivor_pick_id": str(survivor["pick_id"]),
                }

    entries = list(retired.values())
    archive_ids = sorted(
        str(entry["row"]["pick_id"]) for entry in entries if _text(entry["row"].get("status")) == "settled"
    )
    remove_ids = sorted(
        str(entry["row"]["pick_id"]) for entry in entries if _text(entry["row"].get("status")) == "open"
    )
    return {
        "refresh_groups": refresh_groups,
        "tennis_ladder_groups": ladder_groups,
        "archive_ids": archive_ids,
        "remove_ids": remove_ids,
        "entries": entries,
    }


def _ledger(data_root: Path, tier: str, sport: str):
    if tier == "main":
        return main_ledger(data_root, sport)
    if tier == "flat":
        return flat_ledger(data_root, sport)
    if tier == "research":
        return research_ledger(data_root, sport)
    if tier == "gated_research":
        return research_ledger(data_root, sport, gated=True)
    raise ValueError(f"unsupported tier: {tier}")


def _group_ids(plan: dict[str, Any], status: str) -> dict[tuple[str, str], list[str]]:
    wanted = set(plan["archive_ids"] if status == "settled" else plan["remove_ids"])
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in plan["entries"]:
        row = entry["row"]
        pick_id = str(row["pick_id"])
        if pick_id in wanted:
            grouped[(_text(row.get("ledger_tier")), _text(row.get("sport")))].append(pick_id)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-archive", type=int)
    parser.add_argument("--expect-remove", type=int)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    data_root = repo_root / "data"
    paths = RuntimePaths.resolve(repo_root=repo_root, require_external_runtime=True)
    store = RuntimeLedgerStore(paths)
    try:
        plan = build_plan(store.pick_rows())
    finally:
        store.close()
    summary = {
        "refresh_groups": plan["refresh_groups"],
        "tennis_ladder_groups": plan["tennis_ladder_groups"],
        "archive_count": len(plan["archive_ids"]),
        "remove_count": len(plan["remove_ids"]),
    }
    if not args.apply:
        print(json.dumps({"status": "dry_run", **summary}, indent=2))
        return 0
    if os.environ.get("MODEL_PREDICTION_LEDGER_AUTHORITY") != "sqlite":
        raise RuntimeError("apply requires MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite")
    if args.expect_archive != len(plan["archive_ids"]) or args.expect_remove != len(plan["remove_ids"]):
        raise RuntimeError(f"expected counts do not match reviewed plan: {summary}")

    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        raise RuntimeError("daily writer lock is busy; refusing repair")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = paths.ledgers_root / f"ledgers.db.pre-dedupe-{stamp}.bak"
    archive_path = paths.ledgers_root / f"duplicate-exposure-archive-{stamp}.json"
    try:
        source = sqlite3.connect(paths.ledgers_db)
        backup = sqlite3.connect(backup_path)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        archive_path.write_text(
            json.dumps(
                {
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "policy": "latest pregame refresh; max pregame expected return per tennis derivative family",
                    "summary": summary,
                    "entries": plan["entries"],
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        removed: list[str] = []
        archived: list[str] = []
        for (tier, sport), pick_ids in _group_ids(plan, "open").items():
            removed.extend(
                _ledger(data_root, tier, sport).remove_open_rows(
                    pick_ids,
                    reason=f"duplicate exposure repair {stamp}",
                    allow_staked_removal=True,
                )
            )
        for (tier, sport), pick_ids in _group_ids(plan, "settled").items():
            archived.extend(
                str(row["pick_id"])
                for row in _ledger(data_root, tier, sport).archive_settled_rows(
                    pick_ids,
                    reason=f"duplicate exposure repair {stamp}",
                    archive_reference=str(archive_path),
                )
            )
        if set(removed) != set(plan["remove_ids"]) or set(archived) != set(plan["archive_ids"]):
            raise RuntimeError("audited mutations did not match the exact reviewed ID sets")
        print(
            json.dumps(
                {
                    "status": "applied",
                    **summary,
                    "backup": str(backup_path),
                    "archive": str(archive_path),
                },
                indent=2,
            )
        )
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
