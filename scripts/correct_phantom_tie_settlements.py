#!/usr/bin/env python3
"""Correct KBO/NPB ledger rows settled as phantom 0-0 ties.

2026-08-13 bug: parse_kbo_rows cached not-yet-played games (empty relay
cell, "0 vs 0" play cell) as 0-0 `tie` rows with a fabricated game_id, and
find_international_baseball_result matched them ahead of the same game's
real-scored row. Every pick on those games settled as a scoreless tie.

The parser no longer creates those rows, and the matcher ignores any that
pre-date the fix; this script repairs the ledger rows that were already
settled while the bug was live. Idempotent — safe to re-run any time; it
only touches rows still carrying the phantom signature
(status=settled, result=push, away_score=0, home_score=0).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/correct_phantom_tie_settlements.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.domain import parse_utc
from model_prediction.international_baseball import (
    find_international_baseball_result,
)
from model_prediction.model_ledger import ModelLedger
from model_prediction.research_ledgers import (
    RESEARCH_LEDGER_SPORTS,
    research_ledger,
)

REASON = (
    "2026-08-13 correction: previous settlement used phantom 0-0 scores "
    "cached from an unplayed-game parser bug in parse_kbo_rows (empty "
    "relay cell -> fabricated game_id -> 0-0 tie row). Real scores "
    "corrected from the official league source."
)


def _phantom_signature(row: dict) -> bool:
    return (
        row.get("status") == "settled"
        and row.get("result") == "push"
        and row.get("away_score") in ("0", "0.0", 0)
        and row.get("home_score") in ("0", "0.0", 0)
    )


def main() -> int:
    data_root = Path("data")
    total_corrected = 0
    total_pending = 0

    for league in ("kbo", "npb"):
        if league not in RESEARCH_LEDGER_SPORTS:
            continue
        ledger = research_ledger(data_root, league)
        targets = [r for r in ledger.rows() if _phantom_signature(r)]
        if not targets:
            print(f"{league}: nothing to correct")
            continue

        corrected_ids: list[str] = []
        for row in targets:
            game_date = parse_utc(row["event_start_utc"]).date().isoformat()
            real = find_international_baseball_result(
                data_root, row["league"], game_date, row["home_team"], row["away_team"]
            )
            if real is None:
                print(
                    f"  {league} pending (no real result yet): {row['pick_id']} "
                    f"{game_date} {row['away_team']}@{row['home_team']}"
                )
                total_pending += 1
                continue
            away_score, home_score = real
            tie = away_score == home_score
            try:
                settled_at = parse_utc(row["settled_at_utc"]) if row.get("settled_at_utc") else None
            except ValueError:
                settled_at = None
            out = ledger.settle(
                row["pick_id"],
                away_score,
                home_score,
                None,
                None,
                settled_at=settled_at,
                binary_contract_settlement_value=(0.5 if tie else None),
                correction_reason=REASON,
            )
            corrected_ids.append(row["pick_id"])
            total_corrected += 1
            print(
                f"  {league} corrected: {row['pick_id']} {game_date} -> "
                f"({away_score},{home_score}) {out['result']} pnl={out['pnl_units']}"
            )

        # Mirror the correction into the derived per-model ledger. The
        # mirror's own rows use prediction_id == pick_id, but some events
        # were mirrored twice (dedupe key includes observed_at_utc), so
        # match by event_id as well.
        if corrected_ids:
            fresh = {r["pick_id"]: (r["result"], r["pnl_units"], r["event_id"]) for r in ledger.rows()}
            by_event: dict[str, tuple[str, str]] = {}
            for pid in corrected_ids:
                result, pnl, event_id = fresh[pid]
                by_event[event_id] = (result, pnl)
            from model_prediction.model_ledger import model_id_for

            model_id = model_id_for(league.upper(), "moneyline")
            mirror_path = data_root / "model_ledgers" / f"{model_id}.xlsx"
            if mirror_path.exists():
                mirror = ModelLedger(mirror_path)
                for m in mirror.rows():
                    corrected = None
                    if m["prediction_id"] in fresh:
                        corrected = (fresh[m["prediction_id"]][0], fresh[m["prediction_id"]][1])
                    elif m["event_id"] in by_event:
                        corrected = by_event[m["event_id"]]
                    if corrected is None:
                        continue
                    result, pnl = corrected
                    already_right = (
                        m["status"] == "settled"
                        and m["result"] == result
                        and (pnl is None or m["pnl_units"] == f"{float(pnl):.4f}")
                    )
                    if not already_right:
                        mirror.settle(
                            m["prediction_id"], result=result, pnl_units=float(pnl) if pnl else None
                        )
                        print(f"  {league} mirror corrected: {m['prediction_id']} -> {result}")

    print(f"\ncorrected: {total_corrected}, still pending: {total_pending}")
    return 0 if total_pending == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
