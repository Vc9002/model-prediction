"""Ladder dispersion model: market mean, historical spread of outcomes, every settled NBA game.

  python scripts/nba_v10/ladder_dispersion_replay.py [--runtime E:/model-prediction-runtime]

The model takes the centre of the distribution from the market and only the dispersion from
history: the Polymarket ladder's own implied mean (round 2's ``fit_mean``, PIT at the decision
quote) with the anchor artifact's sigma, which was fitted on 2024-26 closing-line residuals before
any 2026-27 game settled (13.792 home-margin points, 17.615 total points). If the venue's market
makers quote the ladder with a narrower distribution than games actually produce, the rungs far
from the mean are cheap and the model buys them.

Disclosure: round 6 (quick 261122-lsh) printed a calibration table of all NBA decision-book rungs
(low-priced rungs won more often than their price) before this model was written. Nothing here is
fitted on 2026-27 outcomes, but the idea was prompted by that in-sample table, so the NBA result is
not a clean out-of-sample test. NCAAF is scored with its own historical sigma as a check.

Pinned (quick 261122-ldm):

- Decision book: last snapshot per market in [tip - 120 min, tip - 5 min] (as in round 1).
- Mean: fit_mean over every two-sided rung mid (spread ladder + moneyline; total ladder), sigma as
  above. Fair probability of each rung side from the normal at that mean and sigma.
- Pick: per game and market (spread / total), the rung side with the largest fair-minus-ask gap,
  if it is at least 3.0 points (the T-B threshold, not tuned). Moneyline is not traded (it is the
  ladder's own centre).
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
- Diagnostic: the venue's implied sigma per game (mean and sigma fitted jointly on the mids).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist

import pandas as pd
from longshot_harvest_replay import load_games, load_snapshots, team_side
from pm_momentum_settled_replay import fit_mean, mid
from scipy.optimize import minimize
from tb_settled_replay import (
    ANCHOR_ARTIFACT,
    DEFAULT_RUNTIME,
    ET,
    ROOT,
    ask,
    cluster_ci,
    fee,
    finite,
    grade,
    latest_quotes,
    pnl,
)

GAP = 0.030
PHI = NormalDist()
# NCAAF check: home-margin and total residual SDs of college football closing lines (common
# published values; the project's NCAAF engines use 15.5-16 for margins). Pinned, not fitted here.
NCAAF_SIGMA = {"spread": 15.5, "total": 16.0}


def ladder(book: list[dict], game: dict, market: str) -> list[dict]:
    """Rungs as P(X > k) contracts with both sides' asks: X = home margin or total."""
    out = []
    for rec in book:
        mt = rec["market_type"]
        line = finite(rec.get("line"))
        la, sa = ask(rec.get("long")), ask(rec.get("short"))
        if la is None or sa is None:
            continue
        if market == "total" and mt == "total" and line is not None:
            out.append({"k": line, "hi_ask": la, "lo_ask": sa, "mid_hi": mid(rec["long"]), "rec": rec,
                        "hi": ("over", line), "lo": ("under", line)})  # fmt: skip
        elif market == "spread" and mt in ("spread", "moneyline"):
            side = team_side(rec.get("team"), game)
            if side is None or (mt == "spread" and line is None):
                continue
            other = "away" if side == "home" else "home"
            ln = 0.0 if mt == "moneyline" else line
            m = mid(rec["long"])
            # long = team T at L. Home at L covers iff M > -L; away at L covers iff M < L.
            if side == "home":
                k, hi_ask, lo_ask, mid_hi = -ln, la, sa, m
                hi, lo = (
                    (game["home"], None if mt == "moneyline" else ln),
                    (game["away"], None if mt == "moneyline" else -ln),
                )
            else:
                k, hi_ask, lo_ask, mid_hi = ln, sa, la, None if m is None else 1.0 - m
                hi, lo = (
                    (game["home"], None if mt == "moneyline" else -ln),
                    (game["away"], None if mt == "moneyline" else ln),
                )
            out.append({"k": k, "hi_ask": hi_ask, "lo_ask": lo_ask, "mid_hi": mid_hi, "rec": rec,
                        "hi": hi, "lo": lo, "is_ml": mt == "moneyline", "other": other})  # fmt: skip
    return out


def implied_sigma(points: list[tuple[float, float]], start_mu: float, start_sigma: float) -> float | None:
    if len(points) < 3:
        return None

    def loss(v):
        mu, s = v
        s = abs(s) + 1e-6
        return sum((1.0 - PHI.cdf((k - mu) / s) - p) ** 2 for k, p in points)

    res = minimize(loss, [start_mu, start_sigma], method="Nelder-Mead")
    return abs(float(res.x[1])) if res.success else None


def run(runtime: Path, sport: str, sigmas: dict[str, float]) -> tuple[pd.DataFrame, dict, list[float]]:
    games = load_games(runtime / "ledgers/ledgers.db", sport)
    snaps = load_snapshots(runtime, sport)
    by_tip: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        by_tip[str(recs[0].get("event_start_utc"))[:16]].append(slug)
    cov: dict[str, int] = defaultdict(int)
    rows, implied = [], []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        cov["games"] += 1
        if g["slug"] not in snaps:
            for slug in by_tip.get(g["tip"].strftime("%Y-%m-%dT%H:%M"), []):
                if any(team_side(r.get("team"), g) for r in snaps[slug][:50]):
                    g["slug"] = slug
                    break
        recs = snaps.get(g["slug"] or "")
        if not recs:
            cov["no_polymarket_event"] += 1
            continue
        book = latest_quotes(recs, g["tip"])
        if not book:
            cov["no_quote_in_window"] += 1
            continue
        cov["evaluated"] += 1
        tip_et = g["tip"].astimezone(ET).date()
        for market in ("spread", "total"):
            sigma = sigmas[market]
            rungs = ladder(book, g, market)
            if sport != "nba" and market == "total":
                # Period totals share the full-game slug pattern outside the NBA: keep plausible lines.
                rungs = [r for r in rungs if r["k"] >= 30]
            points = [(r["k"], r["mid_hi"]) for r in rungs if r["mid_hi"] is not None]
            mu = fit_mean(points, sigma)
            if mu is None:
                continue
            s_imp = implied_sigma(points, mu, sigma)
            if s_imp is not None and market == "spread":
                implied.append(s_imp)
            best = None
            for r in rungs:
                if r.get("is_ml"):
                    continue
                p_hi = 1.0 - PHI.cdf((r["k"] - mu) / sigma)
                for fair, price, (team, ln) in (
                    (p_hi, r["hi_ask"], r["hi"]),
                    (1 - p_hi, r["lo_ask"], r["lo"]),
                ):
                    gap = fair - price
                    if best is None or gap > best["gap"]:
                        best = {"gap": gap, "fair": fair, "ask": price, "team": team, "line": ln}
            if best is None or best["gap"] < GAP:
                continue
            result = grade(g, market, {"team": best["team"], "line": best["line"]})
            rows.append(
                {
                    "sport": sport,
                    "event_id": g["event_id"],
                    "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                    "matchup": f"{g['away']} @ {g['home']}",
                    "market": market,
                    "ladder_mean": round(mu, 2),
                    "side": f"{best['team']} {best['line']}",
                    "ask": best["ask"],
                    "fair": round(best["fair"], 4),
                    "gap": round(best["gap"], 4),
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, best["ask"]),
                    "pnl_net": pnl(result, best["ask"] + fee(best["ask"])),
                }
            )
    return pd.DataFrame(rows), dict(cov), implied


def line(name: str, part: pd.DataFrame) -> str:
    if part.empty:
        return f"| {name} | 0 | - | - | - | - | - |"
    w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
    n = len(part)
    ci = cluster_ci(part, "pnl_net", "stake")
    return (
        f"| {name} | {n} | {w}-{lo_} | {part.pnl_gross.sum():+.2f} ({part.pnl_gross.sum() / n:+.1%}) | "
        f"{part.pnl_net.sum():+.2f} ({part.pnl_net.sum() / n:+.1%}) | {ci[0]:+.1%} .. {ci[1]:+.1%} | "
        f"{part.ask.median():.2f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/LADDER_DISPERSION_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/ladder_dispersion_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    nba_sigma = {m: float(v["sigma"]) for m, v in json.loads(ANCHOR_ARTIFACT.read_text())["markets"].items()}
    nba, nba_cov, nba_imp = run(runtime, "nba", nba_sigma)
    cfb, cfb_cov, cfb_imp = run(runtime, "ncaaf", NCAAF_SIGMA)
    pd.concat([nba, cfb], ignore_index=True).to_csv(args.csv, index=False)
    head = [
        "| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | net 95% CI (games) | median ask |",
        "|---|---|---|---|---|---|---|",
    ]
    out = ["# Ladder dispersion model (market mean, historical sigma)", ""]
    out += ["```", json.dumps({"nba": nba_cov, "ncaaf": cfb_cov}, indent=1), "```", ""]
    if nba_imp:
        s = pd.Series(nba_imp)
        out.append(
            f"NBA venue-implied spread sigma: median {s.median():.2f} (p10 {s.quantile(0.1):.2f}, "
            f"p90 {s.quantile(0.9):.2f}) vs historical {nba_sigma['spread']:.2f}."
        )
    if cfb_imp:
        out.append(f"NCAAF venue-implied spread sigma: median {pd.Series(cfb_imp).median():.2f}.")
    out += ["", "## NBA settled games", "", *head, line("NBA all", nba)]
    for mt in ("spread", "total"):
        out.append(line(f"NBA {mt}", nba[nba.market == mt]) if not nba.empty else line(mt, nba))
    out += [
        "",
        "### NBA by week",
        "",
        "| week | picks | W-L | PnL gross | PnL net |",
        "|---|---|---|---|---|",
    ]
    if not nba.empty:
        for week, wk in nba.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            out.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
    out += ["", "## NCAAF check", "", *head, line("NCAAF all", cfb)]
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
