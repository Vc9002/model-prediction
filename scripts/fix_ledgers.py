#!/usr/bin/env python3
"""Fix corrupted picks.xlsx and flat_picks.xlsx ledgers.

Problems fixed:
  1. Duplicate pick_ids → regenerated as proper UUIDs
  2. sportsbook empty → "polymarket_us"
  3. call_type "flat" → "research_observation"
  4. reason_code empty → "RESEARCH_BACKFILL" (for rows without one)
  5. model_uncertainty empty → 0.05 default
  6. RESEARCH_OBSERVATION with units > 0 → units→legacy_units, units=0
  7. probability_clv computed where closing odds available

Run from project root:
  PYTHONPATH=src:. .venv/bin/python scripts/fix_ledgers.py
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Project root
PROJECT = Path(__file__).resolve().parent.parent

from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic
from model_prediction.pricing import american_to_decimal, implied_probability
from model_prediction.domain import PickResult, RecordType, PickStatus, MarketType


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _fix_row(row: dict[str, str]) -> dict[str, str]:
    """Fix a single corrupted row."""
    fixed = dict(row)

    # 1. Regenerate pick_id as proper UUID
    fixed["pick_id"] = uuid.uuid4().hex[:16]

    # 2. Fix sportsbook
    if not fixed.get("sportsbook", "").strip():
        fixed["sportsbook"] = "polymarket_us"
    elif fixed["sportsbook"] == "polymarket":
        fixed["sportsbook"] = "polymarket_us"

    # 3. Fix call_type
    if fixed.get("call_type", "") == "flat":
        fixed["call_type"] = "research_observation"

    # 4. Fix reason_code
    if not fixed.get("reason_code", "").strip():
        fixed["reason_code"] = "RESEARCH_BACKFILL"

    # 5. Fix model_uncertainty
    if not fixed.get("model_uncertainty", "").strip():
        fixed["model_uncertainty"] = "0.050000"

    # 6. Fix RESEARCH_OBSERVATION with non-zero units
    if fixed.get("record_type") == RecordType.RESEARCH_OBSERVATION.value:
        current_units = _safe_float(fixed.get("units", "0"))
        if current_units > 0:
            # Move units to legacy_units
            fixed["legacy_units"] = f"{current_units:.2f}"
            fixed["units"] = "0.00"
            # PnL for research observations comes from research_score_units,
            # not from units. Clear the direct pnl.
            fixed["pnl_units"] = "0.0000"
            # If research_score_units isn't set, move the score there
            if not fixed.get("research_score_units", "").strip():
                fixed["research_score_units"] = f"{current_units:.4f}"
                # Recompute research PnL
                result_str = fixed.get("result", "")
                if result_str in ("win", "loss"):
                    result = PickResult(result_str)
                    dec_odds = _safe_float(
                        fixed.get("decision_decimal_odds", "")
                        or fixed.get("decimal_odds", ""),
                        1.909091,
                    )
                    pnl = current_units * (dec_odds - 1) if result == PickResult.WIN else -current_units
                    fixed["research_pnl_units"] = f"{pnl:.6f}"
                elif not fixed.get("research_pnl_units", "").strip():
                    fixed["research_pnl_units"] = "0.000000"
                if not fixed.get("research_scored_at_utc", "").strip():
                    fixed["research_scored_at_utc"] = fixed.get("settled_at_utc", "")
                if not fixed.get("research_scoring_note", "").strip():
                    fixed["research_scoring_note"] = "backfill migration — unit scoring preserved"

    # 7. Compute probability_clv if closing odds are available and CLV is missing
    if fixed.get("closing_american_odds", "").strip() and not fixed.get("probability_clv", "").strip():
        try:
            closing_am = _safe_int(fixed["closing_american_odds"])
            decision_prob = _safe_float(
                fixed.get("decision_raw_implied_probability", "")
                or fixed.get("market_implied_probability", "")
            )
            closing_prob = implied_probability(closing_am)
            fixed["probability_clv"] = f"{closing_prob - decision_prob:.6f}"
        except (ValueError, ZeroDivisionError):
            pass

    # 8. Validate and fix decimal_odds consistency
    if fixed.get("american_odds", "").strip() and not fixed.get("decimal_odds", "").strip():
        try:
            am = _safe_int(fixed["american_odds"])
            fixed["decimal_odds"] = f"{american_to_decimal(am):.6f}"
        except ValueError:
            pass

    # 9. Validate market_implied_probability
    if fixed.get("american_odds", "").strip() and not fixed.get("market_implied_probability", "").strip():
        try:
            am = _safe_int(fixed["american_odds"])
            fixed["market_implied_probability"] = f"{implied_probability(am):.6f}"
        except (ValueError, ZeroDivisionError):
            pass

    # 10. Ensure decision_ fields mirror the primary fields if empty
    if not fixed.get("decision_american_odds", "").strip():
        fixed["decision_american_odds"] = fixed.get("american_odds", "")
    if not fixed.get("decision_decimal_odds", "").strip():
        fixed["decision_decimal_odds"] = fixed.get("decimal_odds", "")
    if not fixed.get("decision_raw_implied_probability", "").strip():
        fixed["decision_raw_implied_probability"] = fixed.get("market_implied_probability", "")
    if not fixed.get("decision_line", "").strip() and fixed.get("line", "").strip():
        fixed["decision_line"] = fixed["line"]

    return fixed


def fix_ledger(path: Path, backup: bool = True) -> dict:
    """Fix a single ledger file."""
    if not path.exists():
        return {"path": str(path), "status": "not_found", "rows": 0}

    if backup:
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".pre-fix-{timestamp}")
        shutil.copy2(path, backup_path)
        print(f"  Backup: {backup_path.name}")

    headers, rows = read_xlsx_rows(path)
    original_count = len(rows)

    # Fix each row
    fixed_rows = [_fix_row(row) for row in rows]

    # Deduplicate: if any pick_ids still collide (shouldn't happen with UUIDs, but be safe)
    seen_ids = set()
    deduped = []
    dupes_removed = 0
    for row in fixed_rows:
        if row["pick_id"] in seen_ids:
            dupes_removed += 1
            continue
        seen_ids.add(row["pick_id"])
        deduped.append(row)

    # Validate no duplicate pick_ids
    all_ids = [r["pick_id"] for r in deduped]
    assert len(all_ids) == len(set(all_ids)), f"BUG: duplicate pick_ids remain after fix in {path.name}"

    # Validate no empty sportsbook
    empty_sb = sum(1 for r in deduped if not r.get("sportsbook", "").strip())
    if empty_sb:
        print(f"  WARNING: {empty_sb} rows still have empty sportsbook")

    # Validate all call_types are valid
    valid_call_types = {"model_qualified", "research_observation", "no_call", "forced_call"}
    bad_ct = [r for r in deduped if r.get("call_type", "") not in valid_call_types]
    if bad_ct:
        print(f"  WARNING: {len(bad_ct)} rows have invalid call_type")

    # Write back
    write_xlsx_rows_atomic(path, headers, deduped)

    # Verify
    _, verify_rows = read_xlsx_rows(path)
    verify_ids = [r["pick_id"] for r in verify_rows]

    result = {
        "path": str(path),
        "original_rows": original_count,
        "fixed_rows": len(deduped),
        "duplicates_removed": dupes_removed,
        "unique_pick_ids": len(set(verify_ids)),
        "sportsbook_empty": sum(1 for r in verify_rows if not r.get("sportsbook", "").strip()),
        "call_type_flat": sum(1 for r in verify_rows if r.get("call_type") == "flat"),
        "reason_code_empty": sum(1 for r in verify_rows if not r.get("reason_code", "").strip()),
        "model_uncertainty_empty": sum(1 for r in verify_rows if not r.get("model_uncertainty", "").strip()),
        "research_with_units": sum(
            1 for r in verify_rows
            if r.get("record_type") == RecordType.RESEARCH_OBSERVATION.value
            and _safe_float(r.get("units", "0")) > 0
        ),
    }

    return result


def main():
    picks_path = PROJECT / "data" / "picks.xlsx"
    flat_path = PROJECT / "data" / "flat_picks.xlsx"

    print("=== Fixing picks.xlsx ===")
    result_main = fix_ledger(picks_path)
    for key, value in result_main.items():
        print(f"  {key}: {value}")

    print()
    print("=== Fixing flat_picks.xlsx ===")
    result_flat = fix_ledger(flat_path)
    for key, value in result_flat.items():
        print(f"  {key}: {value}")

    print()
    # Final validation
    all_ok = True
    for name, result in [("picks.xlsx", result_main), ("flat_picks.xlsx", result_flat)]:
        issues = []
        if result["sportsbook_empty"] > 0:
            issues.append(f"{result['sportsbook_empty']} empty sportsbook")
        if result["call_type_flat"] > 0:
            issues.append(f"{result['call_type_flat']} 'flat' call_type")
        if result["reason_code_empty"] > 0:
            issues.append(f"{result['reason_code_empty']} empty reason_code")
        if result["model_uncertainty_empty"] > 0:
            issues.append(f"{result['model_uncertainty_empty']} empty model_uncertainty")
        if result["research_with_units"] > 0:
            issues.append(f"{result['research_with_units']} research rows with units>0")

        if issues:
            print(f"❌ {name}: {', '.join(issues)}")
            all_ok = False
        else:
            print(f"✅ {name}: all checks passed ({result['fixed_rows']} rows, {result['unique_pick_ids']} unique)")

    if all_ok:
        print("\n✅ BOTH LEDGERS FIXED SUCCESSFULLY")
    else:
        print("\n❌ Some issues remain — review above")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
