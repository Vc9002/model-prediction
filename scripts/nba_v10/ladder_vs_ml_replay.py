"""Spread rungs priced off the moneyline book (reverse of round 13), every settled NBA game.

  python scripts/nba_v10/ladder_vs_ml_replay.py [--runtime E:/model-prediction-runtime]

Round 13 bought the moneyline when the spread ladder said it was cheap. This is the reverse: the
moneyline book (the venue's deepest market) sets the centre, and a spread rung is bought when it is
cheap against that centre. Same empirical history shape, same threshold, same independent leagues.

Pinned (quick 261122-lvm), before any outcome was read:

- Book: last pre-tip book at any age (``settled_common``). Needs a two-sided moneyline mid.
- Centre: the mean mu solving S(-mu) = moneyline mid P(home win), S = empirical closing-residual
  survival (round 8), rescaled for NCAAB (10.7) and WNBA (10.5) as in round 13.
- Bet: per game, the spread rung side (rungs with long mid in [0.20, 0.80], so the bet is a
  contested line, not a tail) with the largest fair-minus-ask gap, if >= 3.0 points. One bet per
  game; 1u at the ask; gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from ladder_empirical_replay import history_shapes, ladder_rungs
from ml_vs_ladder_replay import LEAGUE_SIGMA, NBA_HISTORY_SD, Scaled, section
from pm_momentum_settled_replay import mid
from scipy.optimize import brentq
from settled_common import attach, decision_book, load_sport
from tb_settled_replay import DEFAULT_RUNTIME, ET, ROOT, fee, grade, pnl

GAP = 0.030
BAND = (0.20, 0.80)


def run_league(runtime: Path, sport: str, surv) -> tuple[pd.DataFrame, dict]:
    games, snaps = load_sport(runtime, sport)
    attach(games, snaps)
    cov: dict[str, int] = defaultdict(int)
    rows = []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        cov["games"] += 1
        if not g["slug"]:
            cov["no_event"] += 1
            continue
        book = decision_book(snaps[g["slug"]], g["tip"])
        ml = next(
            (r for r in book if r["market_type"] == "moneyline" and r.get("team") in (g["home"], g["away"])),
            None,
        )
        m = mid(ml.get("long")) if ml else None
        if m is None:
            cov["no_ml_mid"] += 1
            continue
        p_home = m if ml["team"] == g["home"] else 1.0 - m
        try:
            mu = brentq(lambda x, target=p_home: surv(-x) - target, -60.0, 60.0)
        except ValueError:
            cov["no_centre"] += 1
            continue
        best = None
        for r in ladder_rungs(book, g, "spread"):
            if not r["trade"] or r["mid"] is None or not BAND[0] <= r["mid"] <= BAND[1]:
                continue
            p_hi = surv(r["k"] - mu)
            for fair, (team, line, price) in ((p_hi, r["hi"]), (1.0 - p_hi, r["lo"])):
                if price is not None and (best is None or fair - price > best["gap"]):
                    best = {"gap": fair - price, "fair": fair, "ask": price, "team": team, "line": line}
        if best is None:
            cov["no_rung"] += 1
            continue
        cov["evaluated"] += 1
        result = grade(g, "spread", {"team": best["team"], "line": best["line"]})
        tip_et = g["tip"].astimezone(ET).date()
        rows.append(
            {
                "sport": sport,
                "event_id": g["event_id"],
                "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                "matchup": f"{g['away']} @ {g['home']}",
                "centre": round(mu, 2),
                "side": f"{best['team']} {best['line']:+g}",
                "ask": best["ask"],
                "fair": round(best["fair"], 4),
                "gap": round(best["gap"], 4),
                "signal": best["gap"] >= GAP,
                "result": result,
                "stake": 1.0,
                "pnl_gross": pnl(result, best["ask"]),
                "pnl_net": pnl(result, best["ask"] + fee(best["ask"])),
            }
        )
    return pd.DataFrame(rows), dict(cov)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/LADDER_VS_ML_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/ladder_vs_ml_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    surv = history_shapes()["spread"]
    frames, out = [], ["# Spread rungs priced off the moneyline book", ""]
    leagues = [("nba", surv), *((sp, Scaled(surv, NBA_HISTORY_SD / sg)) for sp, sg in LEAGUE_SIGMA.items())]
    for sport, s in leagues:
        df, cov = run_league(runtime, sport, s)
        frames.append(df)
        out += section(sport.upper(), df, cov)
    pd.concat(frames, ignore_index=True).to_csv(args.csv, index=False)
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
