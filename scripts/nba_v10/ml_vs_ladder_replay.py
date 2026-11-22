"""Moneyline book vs the spread ladder on the same venue, every settled NBA game.

  python scripts/nba_v10/ml_vs_ladder_replay.py [--runtime E:/model-prediction-runtime]

Polymarket lists the moneyline and each spread rung as separate order books with separate
liquidity. The spread ladder implies a win probability; if the moneyline book is priced off it by
more than the T-B threshold, one of them is stale or mispriced. Rounds 7-8 never traded the
moneyline (they used it to fit the ladder), so this comparison is untried.

Pinned (quick 261122-mvl):

- Book: last pre-tip book at any age (``settled_common``).
- Ladder mean: least squares of the empirical closing-residual survival (round 8, history
  2007-26) to the spread rungs' two-sided mids only (moneyline excluded); needs >= 2 rungs.
- Ladder-implied P(home win) = S(-mean), the same empirical survival (handles no-ties).
- Bet: the moneyline side whose ladder-implied probability exceeds its moneyline ask by >= 3.0
  points; one bet per game; 1u at the ask; gross and net of the 0.06 p (1 - p) fee; weekly.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from ladder_empirical_replay import fit_mean, history_shapes, ladder_rungs
from settled_common import attach, decision_book, load_sport
from tb_settled_replay import DEFAULT_RUNTIME, ET, ROOT, ask, cluster_ci, fee, grade, pnl

GAP = 0.030


# Independent check (pinned before it was run): the same frozen rule on NCAAB and WNBA, with the
# NBA empirical survival rescaled by each league's project margin sigma over NBA history's 12.43.
LEAGUE_SIGMA = {"ncaab": 10.7, "wnba": 10.5}
NBA_HISTORY_SD = 12.43


class Scaled:
    def __init__(self, base, scale: float) -> None:
        self.base, self.scale = base, scale

    def __call__(self, x: float) -> float:
        return self.base(x * self.scale)


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
        rungs = [r for r in ladder_rungs(book, g, "spread") if r["trade"]]
        mu = fit_mean(rungs, surv)
        if ml is None or mu is None or ask(ml["long"]) is None or ask(ml["short"]) is None:
            cov["no_ml_or_ladder"] += 1
            continue
        cov["evaluated"] += 1
        p_home = surv(-mu)
        long_home = ml["team"] == g["home"]
        asks = {
            g["home"]: ask(ml["long"] if long_home else ml["short"]),
            g["away"]: ask(ml["short"] if long_home else ml["long"]),
        }
        fair = {g["home"]: p_home, g["away"]: 1.0 - p_home}
        team = max(asks, key=lambda t: fair[t] - asks[t])
        gap = fair[team] - asks[team]
        result = grade(g, "moneyline", {"team": team, "line": None})
        tip_et = g["tip"].astimezone(ET).date()
        rows.append(
            {
                "sport": sport,
                "event_id": g["event_id"],
                "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                "matchup": f"{g['away']} @ {g['home']}",
                "ladder_mean": round(mu, 2),
                "team": team,
                "ask": asks[team],
                "fair": round(fair[team], 4),
                "gap": round(gap, 4),
                "signal": gap >= GAP,
                "result": result,
                "stake": 1.0,
                "pnl_gross": pnl(result, asks[team]),
                "pnl_net": pnl(result, asks[team] + fee(asks[team])),
            }
        )
    return pd.DataFrame(rows), dict(cov)


def section(title: str, df: pd.DataFrame, cov: dict) -> list[str]:
    out = [f"## {title}", "", "```", json.dumps(cov, indent=1), "```", ""]
    if df.empty:
        return out
    out.append(
        f"Gap (best side): median {df.gap.median():+.4f}, p90 {df.gap.quantile(0.9):+.4f}, max {df.gap.max():+.4f}."
    )
    sig = df[df.signal]
    if sig.empty:
        return [*out, "", "No signals.", ""]
    ci = cluster_ci(sig, "pnl_net", "stake")
    w, lo_ = int((sig.result == "win").sum()), int((sig.result == "loss").sum())
    head = (
        f"Signals: {len(sig)}, {w}-{lo_}, gross {sig.pnl_gross.sum():+.2f}, net {sig.pnl_net.sum():+.2f} "
        f"(net CI {ci[0]:+.1%}..{ci[1]:+.1%})"
    )
    out += ["", head, "", "| week | n | gross | net |", "|---|---|---|---|"]
    for week, wk in sig.groupby("week"):
        out.append(f"| {week} | {len(wk)} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |")
    return [*out, ""]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/ML_VS_LADDER_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/ml_vs_ladder_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    surv = history_shapes()["spread"]
    frames, out = [], ["# Moneyline book vs spread ladder", ""]
    for sport, s in (
        ("nba", surv),
        *((sp, Scaled(surv, NBA_HISTORY_SD / sg)) for sp, sg in LEAGUE_SIGMA.items()),
    ):
        df, cov = run_league(runtime, sport, s)
        frames.append(df)
        out += section(sport.upper(), df, cov)
    pd.concat(frames, ignore_index=True).to_csv(args.csv, index=False)
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
