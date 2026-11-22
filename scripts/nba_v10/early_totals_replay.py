"""Plan track T-C1, early-season totals from current-season scoring, on every settled NBA game.

  python scripts/nba_v10/early_totals_replay.py [--runtime E:/model-prediction-runtime]

Plan section 9 pinned T-C1 before this loop: games where either team has played fewer than 15
current-season games, a rolling current-season total vs the line. Early in a season the market
leans on last season's scoring while the new season's scoring environment (rule changes, pace) is
already visible in the box scores; v9's all-history baseline ran totals about 3 points low.

Pinned (quick 261122-ect), before any outcome was read:

- Inputs (PIT): final scores of every earlier 2026-27 game (scoreboard index plus settled ledger
  games), never the game itself or later games.
- Prediction: team points-for and points-against per game, each shrunk toward the current-season
  league per-team average with a prior of K=5 games; predicted total = (home_for + away_against)/2
  + (away_for + home_against)/2. League average needs >= 30 earlier games.
- Market: the Polymarket total ladder mean at the last pre-tip book (round 2's fit_mean, sigma
  17.615); fair over probability at the main total rung (long mid nearest 50c) from
  N(prediction, 17.615).
- Bet: over or under at the main rung if fair minus ask >= 3.0 points, only when either team has
  played fewer than 15 earlier games (T-C1). One bet per game; 1u at the ask; gross and net of the
  0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist

import pandas as pd
from pm_momentum_settled_replay import fit_mean, ladder_points, mid
from settled_common import attach, decision_book, load_games, load_snapshots
from tb_settled_replay import DEFAULT_RUNTIME, ET, ROOT, ask, cluster_ci, fee, finite, grade, parse_utc, pnl

SIGMA = 17.615
K_PRIOR = 5.0
MIN_LEAGUE_GAMES = 30
EARLY_GAMES = 15
GAP = 0.030
SCOREBOARD = ROOT / "data/research/nba_v10/games/scoreboard_index.jsonl"
NAME_FIX = {"LA Clippers": "Los Angeles Clippers"}


def season_results(games: dict[str, dict]) -> list[dict]:
    """Every completed 2026-27 regular-season game: scoreboard index plus settled ledger games."""
    out: dict[str, dict] = {}
    with SCOREBOARD.open(encoding="utf-8") as handle:
        for raw in handle:
            r = json.loads(raw)
            if r.get("season_year") != 2027 or r.get("season_type") != "regular-season":
                continue
            hs, as_ = finite(r.get("home_score")), finite(r.get("away_score"))
            if hs is None or as_ is None or r.get("status") != "completed":
                continue
            out[str(r["event_id"])] = {"tip": parse_utc(r["event_start_utc"]), "home": NAME_FIX.get(r["home_team"], r["home_team"]),
                                       "away": NAME_FIX.get(r["away_team"], r["away_team"]), "hs": hs, "as": as_}  # fmt: skip
    for g in games.values():
        out.setdefault(g["event_id"], {"tip": g["tip"], "home": g["home"], "away": g["away"],
                                       "hs": g["home_score"], "as": g["away_score"]})  # fmt: skip
    return sorted(out.values(), key=lambda r: r["tip"])


def predict(results: list[dict], game: dict) -> tuple[float | None, int]:
    prior = [r for r in results if r["tip"] < game["tip"] - timedelta(hours=3)]
    if len(prior) < MIN_LEAGUE_GAMES:
        return None, 0
    league = sum(r["hs"] + r["as"] for r in prior) / (2 * len(prior))
    pf, pa, n = defaultdict(float), defaultdict(float), defaultdict(int)
    for r in prior:
        pf[r["home"]] += r["hs"]
        pa[r["home"]] += r["as"]
        pf[r["away"]] += r["as"]
        pa[r["away"]] += r["hs"]
        n[r["home"]] += 1
        n[r["away"]] += 1

    def shrunk(total: float, games_n: int) -> float:
        return (total + K_PRIOR * league) / (games_n + K_PRIOR)

    h, a = game["home"], game["away"]
    h_for, h_against = shrunk(pf[h], n[h]), shrunk(pa[h], n[h])
    a_for, a_against = shrunk(pf[a], n[a]), shrunk(pa[a], n[a])
    return (h_for + a_against) / 2 + (a_for + h_against) / 2, min(n[h], n[a])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/EARLY_TOTALS_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/early_totals_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    attach(games, snaps)
    results = season_results(games)
    phi = NormalDist()
    cov: dict[str, int] = defaultdict(int)
    rows = []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        cov["games"] += 1
        pred, min_n = predict(results, g)
        if pred is None:
            cov["league_too_young"] += 1
            continue
        if min_n >= EARLY_GAMES:
            cov["not_early"] += 1
            continue
        if not g["slug"]:
            cov["no_event"] += 1
            continue
        book = decision_book(snaps[g["slug"]], g["tip"])
        rungs = [r for r in book if r["market_type"] == "total" and finite(r.get("line")) is not None
                 and mid(r.get("long")) is not None and ask(r["long"]) and ask(r["short"])]  # fmt: skip
        mean = fit_mean(ladder_points(book, g, "total"), SIGMA)
        if not rungs or mean is None:
            cov["no_total_ladder"] += 1
            continue
        cov["evaluated"] += 1
        rec = min(rungs, key=lambda r: abs(mid(r["long"]) - 0.5))
        line = float(rec["line"])
        p_over = 1.0 - phi.cdf((line - pred) / SIGMA)
        sides = [("over", p_over, ask(rec["long"])), ("under", 1.0 - p_over, ask(rec["short"]))]
        side, fair, price = max(sides, key=lambda s: s[1] - s[2])
        result = grade(g, "total", {"team": side, "line": line})
        tip_et = g["tip"].astimezone(ET).date()
        rows.append({"event_id": g["event_id"], "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                     "matchup": f"{g['away']} @ {g['home']}", "min_games": min_n, "pred_total": round(pred, 1),
                     "ladder_mean": round(mean, 1), "line": line, "side": side, "ask": price, "fair": round(fair, 4),
                     "gap": round(fair - price, 4), "signal": fair - price >= GAP, "result": result, "stake": 1.0,
                     "actual": g["home_score"] + g["away_score"],
                     "pnl_gross": pnl(result, price), "pnl_net": pnl(result, price + fee(price))})  # fmt: skip
    df = pd.DataFrame(rows)
    df.to_csv(args.csv, index=False)
    out = [
        "# T-C1 early-season totals on settled NBA games",
        "",
        "```",
        json.dumps(dict(cov), indent=1),
        "```",
        "",
    ]
    if not df.empty:
        err_model = (df.actual - df.pred_total).abs().mean()
        err_mkt = (df.actual - df.ladder_mean).abs().mean()
        out.append(f"MAE vs actual: model {err_model:.2f}, Polymarket ladder {err_mkt:.2f} (n {len(df)}); "
                   f"mean model - ladder {(df.pred_total - df.ladder_mean).mean():+.2f}.")  # fmt: skip
        sig = df[df.signal]
        if not sig.empty:
            ci = cluster_ci(sig, "pnl_net", "stake")
            w, lo_ = int((sig.result == "win").sum()), int((sig.result == "loss").sum())
            head = (
                f"Signals: {len(sig)}, {w}-{lo_}, gross {sig.pnl_gross.sum():+.2f}, net {sig.pnl_net.sum():+.2f} "
                f"(net CI {ci[0]:+.1%}..{ci[1]:+.1%}); overs {int((sig.side == 'over').sum())}"
            )
            out += ["", head, "", "| week | n | gross | net |", "|---|---|---|---|"]
            for week, wk in sig.groupby("week"):
                out.append(f"| {week} | {len(wk)} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
