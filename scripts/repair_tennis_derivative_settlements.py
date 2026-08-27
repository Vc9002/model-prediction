"""Repair tennis derivatives graded from binary match-winner scores.

Dry-run is the default.  ``--apply`` requires an exact expected candidate
count, acquires the daily-writer lock, creates a SQLite backup, resolves each
candidate by its exact ESPN event/competition identity, and then either:

* regrades a normally completed match from summed per-set game scores; or
* voids a derivative whose result is book-specific (retirement, walkover,
  missing/misaligned linescores).

The candidate predicate is the bug's exact stored signature, never a time
window: a settled tennis spread/total with a win/loss result whose persisted
away/home scores are binary and sum to one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.cli.settle import _find_tennis_result
from model_prediction.daily_lock import acquire_lock
from model_prediction.data_sources.espn import ESPNClient
from model_prediction.domain import EASTERN, parse_utc
from model_prediction.main_ledgers import flat_ledger, main_ledger
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

CORRECTION_REASON = (
    "2026-08-23 tennis derivative settlement used binary match-winner scores "
    "instead of actual per-set game scores"
)
VOID_REASON = "TENNIS_DERIVATIVE_RESULT_UNGRADEABLE_FROM_OFFICIAL_SCORE"


@dataclass(frozen=True)
class RepairAction:
    pick_id: str
    ledger_tier: str
    event_id: str
    market_type: str
    selection: str
    line: str
    prior_result: str
    prior_pnl_units: str
    action: str
    away_games: int | None
    home_games: int | None
    source_result_id: str
    reason: str | None = None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def has_binary_derivative_signature(row: dict[str, Any]) -> bool:
    """Return whether a row proves the exact winner-only grading defect."""
    away_score = _integer(row.get("away_score"))
    home_score = _integer(row.get("home_score"))
    sport = str(row.get("sport") or row.get("league") or "").casefold()
    return (
        sport == "tennis"
        and str(row.get("ledger_tier") or "") in {"main", "flat"}
        and str(row.get("market_type") or "").casefold() in {"spread", "total"}
        and str(row.get("status") or "").casefold() == "settled"
        and str(row.get("result") or "").casefold() in {"win", "loss"}
        and away_score in {0, 1}
        and home_score in {0, 1}
        and away_score + home_score == 1
    )


def candidate_rows(store: RuntimeLedgerStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tier in ("main", "flat"):
        rows.extend(store.pick_rows(tier=tier, sport="tennis"))
    return sorted(
        (row for row in rows if has_binary_derivative_signature(row)),
        key=lambda row: (str(row["ledger_tier"]), str(row["pick_id"])),
    )


ResultFinder = Callable[[ESPNClient, str, dict[str, Any]], dict[str, Any] | None]


def plan_repairs(
    rows: list[dict[str, Any]],
    espn: ESPNClient,
    *,
    result_finder: ResultFinder = _find_tennis_result,
) -> list[RepairAction]:
    """Resolve every candidate before allowing the first mutation."""
    actions: list[RepairAction] = []
    for row in rows:
        game_day = parse_utc(str(row["event_start_utc"])).astimezone(EASTERN).date().isoformat()
        result = result_finder(espn, game_day, row)
        if result is None or not result.get("completed"):
            raise RuntimeError(f"no completed result for {row['pick_id']} ({row['event_id']})")
        source_result_id = str(result.get("source_result_id") or "")
        if source_result_id != str(row["event_id"]):
            raise RuntimeError(
                f"exact ESPN result identity mismatch for {row['pick_id']}: "
                f"ledger={row['event_id']} source={source_result_id or 'missing'}"
            )

        common = {
            "pick_id": str(row["pick_id"]),
            "ledger_tier": str(row["ledger_tier"]),
            "event_id": str(row["event_id"]),
            "market_type": str(row["market_type"]),
            "selection": str(row["selection"]),
            "line": str(row.get("decision_line") or row.get("line") or ""),
            "prior_result": str(row["result"]),
            "prior_pnl_units": str(row.get("pnl_units") or ""),
            "source_result_id": source_result_id,
        }
        ungradeable = result.get("derivative_ungradeable_reason")
        if ungradeable:
            actions.append(
                RepairAction(
                    **common,
                    action="void",
                    away_games=None,
                    home_games=None,
                    reason=str(ungradeable),
                )
            )
            continue
        if "away_games" not in result or "home_games" not in result:
            raise RuntimeError(f"actual game totals missing for {row['pick_id']}")
        actions.append(
            RepairAction(
                **common,
                action="regrade",
                away_games=int(result["away_games"]),
                home_games=int(result["home_games"]),
            )
        )
    return actions


def _backup_database(paths: RuntimePaths) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = paths.runtime_root / "backups" / f"ledgers.pre-tennis-repair.{stamp}.db"
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


def apply_repairs(actions: list[RepairAction], paths: RuntimePaths) -> dict[str, Any]:
    ledgers = {
        "main": main_ledger(paths.repo_root / "data", "tennis"),
        "flat": flat_ledger(paths.repo_root / "data", "tennis"),
    }
    counts = {"regraded": 0, "voided": 0}
    result_counts: dict[str, int] = {}
    for action in actions:
        ledger = ledgers[action.ledger_tier]
        if action.action == "void":
            repaired = ledger.void(
                action.pick_id,
                VOID_REASON,
                correction_reason=f"{CORRECTION_REASON}; {action.reason}",
            )
            counts["voided"] += 1
        else:
            assert action.away_games is not None and action.home_games is not None
            repaired = ledger.settle(
                action.pick_id,
                action.away_games,
                action.home_games,
                correction_reason=CORRECTION_REASON,
            )
            counts["regraded"] += 1
        result = str(repaired["result"])
        result_counts[result] = result_counts.get(result, 0) + 1
    return {**counts, "results": dict(sorted(result_counts.items()))}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--apply", action="store_true", help="apply the preflighted repair")
    root.add_argument(
        "--expect-count",
        type=int,
        help="required with --apply; must exactly equal the identity-scoped candidate count",
    )
    root.add_argument("--manifest", type=Path, help="optional path for the enumerated repair plan")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = RuntimePaths.resolve(require_external_runtime=True)
    lock = acquire_lock(paths.lock_root / "daily.lock")
    if lock is None:
        raise RuntimeError("daily writer is active; refusing tennis ledger repair")
    try:
        store = RuntimeLedgerStore(paths)
        try:
            rows = candidate_rows(store)
        finally:
            store.close()
        actions = plan_repairs(rows, ESPNClient())
        manifest = {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "correction_reason": CORRECTION_REASON,
            "candidate_count": len(actions),
            "actions": [asdict(action) for action in actions],
        }
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2, sort_keys=True))
        if not args.apply:
            return 0
        if args.expect_count is None:
            raise ValueError("--apply requires --expect-count")
        if len(actions) != args.expect_count:
            raise ValueError(
                f"candidate count changed: expected {args.expect_count}, found {len(actions)}; refusing repair"
            )
        backup = _backup_database(paths)
        applied = apply_repairs(actions, paths)
        verifier = RuntimeLedgerStore(paths)
        try:
            chain_ok, problems = verifier.verify_integrity()
            remaining = len(candidate_rows(verifier))
        finally:
            verifier.close()
        if not chain_ok or remaining:
            raise RuntimeError(
                f"post-repair verification failed: chain_ok={chain_ok} "
                f"remaining={remaining} problems={problems[:1]}"
            )
        print(
            json.dumps(
                {
                    "status": "repaired",
                    "backup": str(backup),
                    "candidate_count": len(actions),
                    "remaining_bad_signature": remaining,
                    "event_chain_intact": chain_ok,
                    **applied,
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
