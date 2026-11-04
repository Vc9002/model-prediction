"""Read-only reconciliation of NCAAF Main and Flat graded ledger history."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/research/ncaaf_main_flat_reconciliation_2026-11-04.json"


def _key(row: sqlite3.Row) -> tuple:
    return row["event_id"], row["market_type"], row["selection"], row["line"]


def _summary(rows: list[sqlite3.Row]) -> dict:
    return {
        "calls": len(rows),
        "events": len({r["event_id"] for r in rows}),
        "units": round(sum(r["units"] or 0 for r in rows), 4),
        "pnl_units": round(sum(r["pnl_units"] or 0 for r in rows), 4),
    }


def audit() -> dict:
    path = RuntimePaths.resolve(repo_root=ROOT).ledgers_db
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    flat = conn.execute("SELECT * FROM ledger_records WHERE sport='ncaaf' AND ledger_tier='flat'").fetchall()
    main = conn.execute("SELECT * FROM ledger_records WHERE sport='ncaaf' AND ledger_tier='main'").fetchall()
    flat_settled = [r for r in flat if r["status"] == "settled"]
    main_settled = [r for r in main if r["status"] == "settled"]
    flat_superseded_graded = [
        r for r in flat if r["status"] == "superseded" and r["result"] in ("win", "loss", "push")
    ]
    flat_by_contract: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for row in flat_settled + flat_superseded_graded:
        flat_by_contract[_key(row)].append(row)
    main_exact = [r for r in main_settled if _key(r) in flat_by_contract]
    main_line_different = [r for r in main_settled if _key(r) not in flat_by_contract]
    superseded_match = []
    for row in flat_superseded_graded:
        matches = [m for m in main_settled if _key(m) == _key(row)]
        if (
            len(matches) == 1
            and matches[0]["units"] == row["units"]
            and matches[0]["pnl_units"] == row["pnl_units"]
        ):
            superseded_match.append(row)
    versions = {}
    for model in sorted({r["model_id"] for r in main_settled}):
        subset = [r for r in main_settled if r["model_id"] == model]
        versions[model] = _summary(subset)
    reasons = {}
    for reason in sorted({r["reason_code"] for r in main_settled}):
        reasons[str(reason)] = _summary([r for r in main_settled if r["reason_code"] == reason])
    months = {}
    for month in sorted({r["event_start_utc"][:7] for r in main_settled}):
        months[month] = _summary([r for r in main_settled if r["event_start_utc"].startswith(month)])
    result = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(path),
        "flat_status_settled": _summary(flat_settled),
        "flat_status_superseded_but_graded": _summary(flat_superseded_graded),
        "flat_all_graded": _summary(flat_settled + flat_superseded_graded),
        "main_status_settled": _summary(main_settled),
        "main_exact_flat_graded": _summary(main_exact),
        "main_different_line": _summary(main_line_different),
        "superseded_flat_exact_main_units_and_pnl": len(superseded_match),
        "versions_main_settled": versions,
        "reasons_main_settled": reasons,
        "months_main_settled": months,
        "note": "Flat status=settled is a current-view subset. Superseded rows with final results are retained as historical graded evidence. Main and Flat overlap; never sum their units.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
