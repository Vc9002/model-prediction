#!/usr/bin/env python3
"""Correct esports picks wrongly voided on Polymarket's non-binary price bug.

2026-09-14 bug (see `cli/settle.py::_settle_esports_pick`): Polymarket US's
esports moneyline markets do not reliably report a terminal binary [0.0,
1.0] settlement price even once genuinely resolved (confirmed live against
real CS2 markets: `MARKET_STATE_EXPIRED` with pre-close trading prices still
showing). Every settlement hitting that non-binary price before the fix
assumed forfeit/postponement and voided the pick to a push, regardless of
whether the match actually had a real, decisive winner.

This script re-examines every currently-live research/gated_research
esports row still carrying that void signature, looks up the real result
from BO3 (the same free, no-key results source the settlement fix now
checks first), and corrects any it can confirm via `PickLedger.settle(...,
correction_reason=...)`. Rows BO3 also cannot confirm are left voided
untouched -- fail closed, never guessed. Idempotent: a row already corrected
away from `push` no longer carries the signature and is skipped on rerun.

Only touches `research`/`gated_research` esports ledgers -- zero-unit
shadow/track-record tiers, never Main real money (see CLAUDE.md).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/correct_voided_esports_picks.py [--apply]

Without --apply, prints what WOULD be corrected and exits 0 without writing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.esports import resolve_esports_match_result
from model_prediction.model_ledger import ModelLedger, model_id_for
from model_prediction.research_ledgers import existing_research_ledgers

VOID_SIGNATURE = "esports market settled to a non-binary price"
ESPORTS_LEAGUES = ("LOL", "CS2", "DOTA2", "VALORANT", "RAINBOW_SIX")

CORRECTION_REASON = (
    "2026-09-14 correction: original void treated Polymarket's non-binary "
    "settlement price as forfeit/postponement, but that signal is not "
    "reliable for esports moneylines -- BO3's own finished-match result "
    "confirms a real, decisive winner (see esports.resolve_esports_match_result "
    "and cli/settle.py's BO3 fallback)."
)


def _void_signature(row: dict) -> bool:
    return (
        row.get("status") == "settled"
        and row.get("result") == "push"
        and VOID_SIGNATURE in str(row.get("void_reason") or "")
    )


def main() -> int:
    apply = "--apply" in sys.argv
    data_root = Path("data")
    total_corrected = 0
    total_still_void = 0
    total_ambiguous = 0

    for gated in (False, True):
        for ledger in existing_research_ledgers(data_root, gated=gated):
            targets = [r for r in ledger.rows() if _void_signature(r) and r["league"] in ESPORTS_LEAGUES]
            if not targets:
                continue
            tier = "gated_research" if gated else "research"
            print(f"{tier}/{ledger.sport}: {len(targets)} voided-on-non-binary-price row(s)")

            corrected_ids: list[str] = []
            for row in targets:
                try:
                    bo3_result = resolve_esports_match_result(
                        row["league"],
                        str(row["home_team"]),
                        str(row["away_team"]),
                        str(row["event_start_utc"]),
                        data_root,
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
                    print(f"    {row['pick_id']} BO3 lookup failed: {err}")
                    total_ambiguous += 1
                    continue
                if bo3_result is None:
                    print(f"    {row['pick_id']} still unresolvable (BO3 has no confirming result either)")
                    total_still_void += 1
                    continue
                away_score = 1 if bo3_result["away_win"] else 0
                home_score = 1 if bo3_result["home_win"] else 0
                if not apply:
                    print(
                        f"    [dry-run] would correct {row['pick_id']} "
                        f"{row['away_team']}@{row['home_team']} -> "
                        f"away={away_score} home={home_score}"
                    )
                    total_corrected += 1
                    continue
                out = ledger.settle(
                    row["pick_id"],
                    away_score,
                    home_score,
                    None,
                    None,
                    correction_reason=CORRECTION_REASON,
                )
                corrected_ids.append(row["pick_id"])
                total_corrected += 1
                print(f"    corrected {row['pick_id']} -> {out['result']} pnl={out['pnl_units']}")

            if apply and corrected_ids:
                fresh = {r["pick_id"]: (r["result"], r["pnl_units"], r["event_id"]) for r in ledger.rows()}
                by_event = {fresh[pid][2]: (fresh[pid][0], fresh[pid][1]) for pid in corrected_ids}
                model_id = model_id_for(str(ledger.sport).upper(), "moneyline")
                if model_id:
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
                            already_right = m["status"] == "settled" and m["result"] == result
                            if not already_right:
                                mirror.settle(
                                    m["prediction_id"],
                                    result=result,
                                    pnl_units=float(pnl) if pnl else None,
                                )
                                print(f"    mirror corrected: {m['prediction_id']} -> {result}")

    mode = "applied" if apply else "dry-run (pass --apply to write)"
    print(
        f"\n{mode}: corrected={total_corrected}, still unresolvable={total_still_void}, "
        f"lookup errors={total_ambiguous}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
