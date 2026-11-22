"""Model-agreement gate on the actual settled NBA Flat ledger: spread and moneyline models agree.

  python scripts/nba_v10/model_agreement_settled.py [--ledger E:/model-prediction-runtime/ledgers/ledgers.db]

The Flat ledger calls every game with whichever spread model and moneyline model was serving that
day. The two are separate models (margin engine vs Elo/trend logistic). A gate that keeps a spread
pick only when the moneyline model backs the same team uses no outcome information and no tuning;
PnL is the ledger's own settled PnL (no replay, no reconstruction).

Pinned (quick 261122-agr), before any outcome was read in this form:

- Spread pick: the Flat spread row per game (latest created row per game if several), any model.
- Moneyline pick: the Flat moneyline row per game, any model.
- PRIMARY: spread rows whose team equals the moneyline row's team. Secondary: moneyline rows
  whose team equals the spread row's team. Both scored with stored units and pnl_units, and also
  per 1u (pnl / units). Weekly by ET Monday; game-cluster CI.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd
from tb_settled_replay import ET, ROOT, cluster_ci, parse_utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", default="E:/model-prediction-runtime/ledgers/ledgers.db")
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/MODEL_AGREEMENT_SETTLED.md"))
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.ledger}?mode=ro", uri=True)
    df = pd.read_sql(
        """select event_id, market_type, selection, model_id, units, pnl_units, result, created_at_utc,
                  event_start_utc
           from ledger_records
           where sport='nba' and status='settled' and ledger_tier='flat' and units > 0
             and market_type in ('spread', 'moneyline') and model_id != 'nba-anchor-v10'""",
        conn,
    )
    conn.close()
    df = df.sort_values("created_at_utc").drop_duplicates(["event_id", "market_type"], keep="last")
    sp = df[df.market_type == "spread"].set_index("event_id")
    ml = df[df.market_type == "moneyline"].set_index("event_id")
    both = sp.index.intersection(ml.index)
    rows = []
    for ev in both:
        agree = sp.loc[ev, "selection"] == ml.loc[ev, "selection"]
        tip = parse_utc(sp.loc[ev, "event_start_utc"]).astimezone(ET).date()
        week = (tip - timedelta(days=tip.weekday())).isoformat()
        for market, frame in (("spread", sp), ("moneyline", ml)):
            r = frame.loc[ev]
            rows.append({"event_id": ev, "week": week, "market": market, "agree": agree, "model": r.model_id,
                         "units": float(r.units), "pnl": float(r.pnl_units), "result": r.result,
                         "pnl_1u": float(r.pnl_units) / float(r.units), "stake": 1.0})  # fmt: skip
    d = pd.DataFrame(rows)
    out = ["# Model-agreement gate on the settled NBA Flat ledger", "",
           f"Games with both a spread and a moneyline Flat row: {len(both)}; agree on {int(d[d.market == 'spread'].agree.sum())}.", ""]  # fmt: skip
    out += [
        "| slice | n | W-L | PnL stored units | PnL per 1u | ROI 1u | 95% CI |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, part in (
        ("PRIMARY spread, models agree", d[(d.market == "spread") & d.agree]),
        ("spread, models disagree", d[(d.market == "spread") & ~d.agree]),
        ("moneyline, models agree", d[(d.market == "moneyline") & d.agree]),
        ("moneyline, models disagree", d[(d.market == "moneyline") & ~d.agree]),
    ):
        if part.empty:
            continue
        ci = cluster_ci(part, "pnl_1u", "stake")
        w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
        out.append(f"| {name} | {len(part)} | {w}-{lo_} | {part.pnl.sum():+.2f} | {part.pnl_1u.sum():+.2f} | "
                   f"{part.pnl_1u.mean():+.1%} | {ci[0]:+.1%} .. {ci[1]:+.1%} |")  # fmt: skip
    prim = d[(d.market == "spread") & d.agree]
    out += ["", "## PRIMARY by week", "", "| week | n | PnL stored | PnL 1u |", "|---|---|---|---|"]
    for week, wk in prim.groupby("week"):
        out.append(f"| {week} | {len(wk)} | {wk.pnl.sum():+.2f} | {wk.pnl_1u.sum():+.2f} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
