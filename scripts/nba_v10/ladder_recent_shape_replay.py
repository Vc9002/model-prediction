"""Ladder model with the RECENT-era outcome shape, contested rungs only, every settled NBA game.

  python scripts/nba_v10/ladder_recent_shape_replay.py [--runtime E:/model-prediction-runtime]

Round 8 priced rungs with the empirical closing-residual shape of 2007-08..2025-26, whose ATS SD
(12.43) is dragged down by the low-scoring early seasons; 2021-22 onward runs 13.2-13.7 (found in
round 15). Round 7's normal(13.79) bets were lottery tickets on tail rungs. This version fixes both:
the shape is the empirical residual of the 2021-22..2025-26 regular seasons only, and only contested
rungs are tradable.

Pinned (quick 261122-rcs), before any outcome was read:

- Shape: empirical survival of (margin - closing home-margin line) and (total - closing total),
  regular seasons 2022..2026 (season = ending year) from the 2007-2026 closing-line file.
- Mean: least squares of that survival to every two-sided rung mid (spread + moneyline; totals).
- Tradable: rungs whose long mid is in [0.25, 0.75]; per game and market the side with the largest
  fair-minus-ask gap, if >= 3.0 points. One bet per game and market.
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from ladder_empirical_replay import HIST, Survival, fit_mean, ladder_rungs
from settled_common import attach, decision_book, load_games, load_snapshots
from tb_settled_replay import DEFAULT_RUNTIME, ET, ROOT, cluster_ci, fee, finite, grade, pnl

FIRST_SEASON = 2022
BAND = (0.25, 0.75)
GAP = 0.030


def recent_shapes() -> dict[str, Survival]:
    ats, tot = [], []
    with HIST.open(encoding="utf-8") as handle:
        for raw in handle:
            r = json.loads(raw)
            if not r.get("regular") or int(r["season"]) < FIRST_SEASON:
                continue
            hs, as_, sp, tl = (
                finite(r.get(k)) for k in ("home_score", "away_score", "home_spread", "total_line")
            )
            if hs is None or as_ is None:
                continue
            if sp is not None:
                ats.append((hs - as_) + sp)
            if tl is not None:
                tot.append(hs + as_ - tl)
    return {"spread": Survival(np.array(ats)), "total": Survival(np.array(tot))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/LADDER_RECENT_SHAPE_REPLAY.md"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    shapes = recent_shapes()
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    attach(games, snaps)
    cov: dict[str, int] = defaultdict(int)
    rows = []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        cov["games"] += 1
        if not g["slug"]:
            cov["no_event"] += 1
            continue
        book = decision_book(snaps[g["slug"]], g["tip"])
        if not book:
            cov["no_book"] += 1
            continue
        cov["scored"] += 1
        tip_et = g["tip"].astimezone(ET).date()
        for market in ("spread", "total"):
            surv = shapes[market]
            rungs = ladder_rungs(book, g, market)
            mu = fit_mean(rungs, surv)
            if mu is None:
                continue
            best = None
            for r in rungs:
                if not r["trade"] or r["mid"] is None or not BAND[0] <= r["mid"] <= BAND[1]:
                    continue
                p_hi = surv(r["k"] - mu)
                for fair, (team, line, price) in ((p_hi, r["hi"]), (1.0 - p_hi, r["lo"])):
                    if price is not None and (best is None or fair - price > best["gap"]):
                        best = {"gap": fair - price, "fair": fair, "ask": price, "team": team, "line": line}
            if best is None or best["gap"] < GAP:
                continue
            result = grade(g, market, {"team": best["team"], "line": best["line"]})
            rows.append({"event_id": g["event_id"], "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                         "market": market, "side": f"{best['team']} {best['line']}", "ask": best["ask"],
                         "gap": round(best["gap"], 4), "result": result, "stake": 1.0,
                         "pnl_gross": pnl(result, best["ask"]), "pnl_net": pnl(result, best["ask"] + fee(best["ask"]))})  # fmt: skip
    df = pd.DataFrame(rows)
    sd = {m: round(float(np.sqrt(np.trapezoid(2 * np.abs(s.grid) * np.minimum(s.surv, 1 - s.surv), s.grid))), 2)
          for m, s in shapes.items()}  # fmt: skip
    out = [
        "# Ladder model, recent-era shape, contested rungs",
        "",
        "```",
        json.dumps(dict(cov), indent=1),
        "```",
        "",
    ]
    out.append(f"Approximate shape SDs: {sd}.")
    if not df.empty:
        ci = cluster_ci(df, "pnl_net", "stake")
        w, lo_ = int((df.result == "win").sum()), int((df.result == "loss").sum())
        head = (
            f"Picks: {len(df)}, {w}-{lo_}, gross {df.pnl_gross.sum():+.2f}, net {df.pnl_net.sum():+.2f} "
            f"(net CI {ci[0]:+.1%}..{ci[1]:+.1%}); spread {int((df.market == 'spread').sum())}, "
            f"total {int((df.market == 'total').sum())}"
        )
        out += ["", head, "", "| week | n | gross | net |", "|---|---|---|---|"]
        for week, wk in df.groupby("week"):
            out.append(f"| {week} | {len(wk)} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
