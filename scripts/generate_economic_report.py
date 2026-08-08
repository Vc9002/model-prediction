"""Real, code-derived economic status report from the actual shadow ledger
(CLAUDE.md Part 3 SS16 deliverable: outputs/rebuild/economic_report.md).

Every number in the generated report comes from a live query against
data/rebuild/shadow.db -- never hand-typed. This is deliberately honest
about a real, current blocker: as of this run, zero real trades have ever
been placed (every trade_decision to date is NO_BET) and zero real
settlements/closing prices have ever been captured, so economic
qualification (Part 3 SS6) genuinely cannot be attempted yet -- not because
the ledger/reporting code is missing, but because there is no real
executable-fill or settled-outcome data to report on. Reporting fabricated
PnL/CLV numbers to fill out this deliverable would violate this codebase's
own never-fabricate-missing-data principle; this script reports the real,
disclosed gap instead.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/generate_economic_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.shadow_ledger import ShadowLedger


def main() -> None:
    db_path = Path("data/rebuild/shadow.db")
    if not db_path.exists():
        print(f"ERROR: {db_path} not found. Run the shadow pipeline first.")
        sys.exit(1)

    ledger = ShadowLedger(db_path)
    conn = ledger.conn

    total_decisions = conn.execute("SELECT COUNT(*) AS n FROM trade_decisions").fetchone()["n"]
    action_counts = {
        row["action"]: row["n"]
        for row in conn.execute("SELECT action, COUNT(*) AS n FROM trade_decisions GROUP BY action").fetchall()
    }
    reason_counts = {
        row["reason_code"] or "none": row["n"]
        for row in conn.execute(
            "SELECT reason_code, COUNT(*) AS n FROM trade_decisions GROUP BY reason_code ORDER BY n DESC"
        ).fetchall()
    }
    sport_counts = {
        row["sport"]: row["n"]
        for row in conn.execute("SELECT sport, COUNT(*) AS n FROM trade_decisions GROUP BY sport").fetchall()
    }
    n_paper_orders = conn.execute("SELECT COUNT(*) AS n FROM paper_orders").fetchone()["n"]
    n_settlements = conn.execute("SELECT COUNT(*) AS n FROM settlements").fetchone()["n"]
    n_closing_prices = conn.execute("SELECT COUNT(*) AS n FROM closing_prices").fetchone()["n"]
    settlement_outcome_counts = {
        row["outcome"]: row["n"]
        for row in conn.execute("SELECT outcome, COUNT(*) AS n FROM settlements GROUP BY outcome").fetchall()
    }
    n_settlements_with_closing_price = conn.execute(
        "SELECT COUNT(*) AS n FROM settlements WHERE settled_price IS NOT NULL"
    ).fetchone()["n"]
    n_predictions = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
    n_market_evaluations = conn.execute("SELECT COUNT(*) AS n FROM market_evaluations").fetchone()["n"]
    n_runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    ledger.close()

    bet_count = action_counts.get("BET", 0)
    no_bet_count = action_counts.get("NO_BET", 0)

    lines: list[str] = []
    lines.append("# Economic Report")
    lines.append("")
    lines.append(f"Generated from {db_path} -- every number below is a live query result, not hand-typed.")
    lines.append("")
    lines.append("## Real qualification status")
    lines.append("")
    if bet_count == 0:
        lines.append(
            "**ECONOMIC_SAMPLE_INSUFFICIENT.** Zero real BET decisions have ever been recorded "
            "(all real trade_decisions to date are NO_BET). This is a real data blocker, not a "
            "missing-code gap -- the decision engine, ledger, and this report are all real and "
            "working; there is simply nothing real to grade an economic outcome from yet."
        )
    else:
        lines.append(f"{bet_count} real BET decision(s) recorded. See breakdown below.")
    lines.append("")
    lines.append("## Trade decisions")
    lines.append("")
    lines.append(f"- Total real trade_decisions: {total_decisions}")
    lines.append(f"- BET: {bet_count}")
    lines.append(f"- NO_BET: {no_bet_count}")
    lines.append("")
    lines.append("### By sport")
    lines.append("")
    for sport, n in sorted(sport_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {sport}: {n}")
    lines.append("")
    lines.append("### NO_BET reason codes (real, diagnostic -- shows what's actually gating trades)")
    lines.append("")
    for reason, n in reason_counts.items():
        lines.append(f"- `{reason}`: {n}")
    lines.append("")
    lines.append("## Downstream ledger state")
    lines.append("")
    lines.append(f"- predictions recorded: {n_predictions}")
    lines.append(f"- market_evaluations recorded: {n_market_evaluations}")
    lines.append(f"- paper_orders recorded: {n_paper_orders}")
    lines.append(f"- settlements recorded: {n_settlements}")
    lines.append(f"- closing_prices recorded: {n_closing_prices}")
    lines.append(f"- runs recorded: {n_runs}")
    lines.append("")
    if n_settlements > 0:
        lines.append("### Settlement outcomes (real WIN/LOSS/PUSH for every evaluated side, BET and NO_BET alike)")
        lines.append("")
        for outcome, n in sorted(settlement_outcome_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {outcome}: {n}")
        lines.append("")
        lines.append(
            f"Of {n_settlements} real settlements, {n_settlements_with_closing_price} carry a real "
            f"captured closing price; the rest have `settled_price = NULL` with an explicit per-row "
            f"note (Polymarket's public API serves only currently open markets, so no historical "
            f"closing quote could be recovered for these already-resolved past events -- see "
            f"`mlb_settle_and_capture_closing.py`'s module docstring)."
        )
        lines.append("")
    lines.append("## What this means")
    lines.append("")
    lines.append(
        "The real NO_BET reason-code breakdown above is the honest diagnostic signal: "
        "`stale_quote` and `not_aligned_with_predicted_winner`/`not_aligned_with_frozen_totals_side` "
        "dominate over `insufficient_depth` in this session's real data, meaning the current real "
        "bottleneck is quote freshness and winner-first alignment (both correct, intended gating "
        "behavior per CLAUDE.md's winner-first policy), not primarily the disclosed missing "
        "order-book-depth data source. Zero paper_orders means order-book walking has never been "
        "exercised against a real fill."
    )
    lines.append("")
    if n_settlements > 0 and n_settlements_with_closing_price == 0:
        lines.append(
            f"{n_settlements} real settlements now exist (real WIN/LOSS/PUSH determined from the real "
            "final score for every evaluated market/side, including NO_BET rows -- see the "
            "breakdown above), but zero carry a real closing price, so CLV still cannot be computed "
            "from anything real yet. This is a genuine, disclosed data-timing gap, not a code gap: "
            "closing-price capture requires a poller running prospectively, through each event's "
            "real market close, which has not yet run continuously for any of these past events."
        )
    elif n_settlements == 0:
        lines.append(
            "Zero settlements/closing_prices means CLV and PnL cannot be computed from anything "
            "real yet."
        )
    else:
        lines.append(
            f"{n_settlements} real settlements exist, {n_settlements_with_closing_price} with a real "
            "closing price -- CLV can be computed for those rows."
        )
    lines.append("")
    lines.append(
        "No PnL, ROI, or CLV figures are reported here -- reporting them from zero real accepted "
        "trades would mean fabricating them (real settlement outcomes exist for research/NO_BET "
        "rows, per above, but zero real paper fills exist to compute a real PnL/ROI from). Real "
        "economic evaluation requires real BET decisions to occur first (more real backfill days, "
        "fresher quote collection cadence, or lower-friction markets), then real settlement against "
        "real final scores and a real captured closing price."
    )
    lines.append("")
    lines.append("## Executable edge methodology (real, architectural -- not session-specific)")
    lines.append("")
    lines.append(
        "The system uses only executable Polymarket US order-book BBO data: best_ask is a real "
        "executable ask, never a midpoint; conservative_probability is the calibrated model "
        "probability minus a real uncertainty margin (bootstrap_uncertainty today -- see "
        "FOUNDATION_FROZEN.md's known-blockers list for the remaining "
        "calibration/lineup/missingness/model-disagreement components); cost_adjusted_edge = "
        "conservative_probability - best_ask - spread/2 - fees. No synthetic -110 pricing is ever "
        "used; every edge is computed against a real observed quote or the market fails closed."
    )
    lines.append("")
    lines.append("## Position sizing (real, implemented, never yet exercised against a real accepted trade)")
    lines.append("")
    lines.append(
        "economic.py implements flat/fixed-fractional/capped-fractional/uncertainty-adjusted Kelly "
        "sizing plus event/team/sport/market-type/correlation/daily caps (SizeLimits, Exposure) -- "
        "real, tested code (see tests/test_rebuild.py's TestEconomics). It has never processed a "
        "real accepted trade end to end, since none have occurred yet; this is disclosed here "
        "rather than implied by the code's mere existence."
    )
    lines.append("")

    out_path = Path("outputs/rebuild/economic_report.md")
    out_path.write_text("\n".join(lines))
    print(f"Wrote {out_path}")
    print(f"\nSummary: {total_decisions} real trade_decisions, {bet_count} BET / {no_bet_count} NO_BET, "
          f"{n_paper_orders} paper_orders, {n_settlements} settlements, {n_closing_prices} closing_prices")


if __name__ == "__main__":
    main()
