"""Void the open NCAAF picks that were priced against a fabricated ask.

Every NCAAF row written on 2026-08-29 carried `market_probability` of exactly
0.50: `models/college_football.py` declared the three market-probability
variables and never assigned them, so every call priced against the no-vig
0.50 instead of the real -110 ask, and eight of eight went QUALIFIED on
fictitious edge. Eight of the nine open Main rows also carry a `line` of 54.0
-- the `CFB_BASELINE_TOTAL` constant, not a real posted total. See the
2026-08-30 addendum in `docs/SYSTEM_DEFECTS_AND_GAPS_AUDIT.md`.

Voiding (never deletion) is the sanctioned repair: `PickLedger.void` settles
the row as a PUSH at zero P&L and stamps a `void_reason`, so the record that
these picks were made survives in full. The already-settled rows are left
alone deliberately -- their 1-6 / -7.5U result is the live evidence that
disproved the backtest, and rewriting it would erase the finding.

NCAAF is not in `MAIN_LEDGER_SPORTS`, so `main_ledger()`/`flat_ledger()`
refuse the sport; the PickLedger here is built with exactly the arguments
those factories use. Run with the operational environment set:

    env MODEL_PREDICTION_RUNTIME_ROOT=~/model-prediction-runtime \
        MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite \
        PYTHONPATH=src:. .venv/bin/python scripts/void_ncaaf_fabricated_ask.py [--apply]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from model_prediction.ledger import PickLedger
from model_prediction.main_ledgers import ledger_authority, ledger_mirror

VOID_REASON = (
    "voided 2026-08-30: priced against a fabricated -110/0.50 ask "
    "(market probability never materialised from odds; the totals line fell "
    "through to the CFB_BASELINE_TOTAL 54.0 constant). NCAAF demoted off "
    "production the same day."
)


def _ledger(data_root: Path, tier: str) -> PickLedger:
    return PickLedger(
        data_root / tier / "ncaaf.xlsx",
        audit_path=data_root / "events.jsonl",
        model_ledgers_dir=data_root / "model_ledgers",
        tier=tier,
        mirror=ledger_mirror(data_root),
        authority=ledger_authority(),
        sport="ncaaf",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--tiers", nargs="+", default=["main", "flat"])
    parser.add_argument("--apply", action="store_true", help="without this, only report")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    for tier in args.tiers:
        ledger = _ledger(data_root, tier)
        open_rows = [row for row in ledger.rows() if row.get("status") == "open"]
        print(f"{tier}: {len(open_rows)} open NCAAF rows")
        for row in open_rows:
            label = (
                f"  {row['pick_id']} {row.get('market_type')} {row.get('selection')} "
                f"line={row.get('line')} units={row.get('units')} "
                f"market_prob={row.get('market_probability')}"
            )
            if not args.apply:
                print(f"{label}  [dry run]")
                continue
            voided = ledger.void(row["pick_id"], VOID_REASON)
            print(f"{label} -> {voided['status']}/{voided['result']} pnl={voided['pnl_units']}")
    if not args.apply:
        print("\ndry run -- re-run with --apply to void")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
