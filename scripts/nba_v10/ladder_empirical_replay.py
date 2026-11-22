"""Ladder model with an empirical outcome shape, on every settled NBA game (full coverage).

  python scripts/nba_v10/ladder_empirical_replay.py [--runtime E:/model-prediction-runtime]

Round 7 (quick 261122-ldm) priced every Polymarket rung from the ladder's own mean and a normal
with the historical sigma. Its non-lottery picks were underdogs at +1.5..+4.5, exactly where a
normal misstates NBA margins (no ties, key numbers), so the gaps may be the normal's error, not the
venue's. This version replaces the normal with the empirical distribution of closing-line
residuals over 2007-08..2025-26 regular seasons (margin minus the closing home-margin line; total
minus the closing total), fitted entirely on history, and uses the coverage-complete loaders of
``settled_common`` (any-age last pre-tip book, nickname team matching).

Pinned (quick 261122-emp), identical to round 7 except the shape and coverage:

- Mean: least squares of the empirical survival S(k - mean) to every two-sided rung mid (spread
  ladder + moneyline as P(home margin > 0); total ladder).
- Pick: per game and market (spread / total), the rung side with the largest fair-minus-ask gap,
  if >= 3.0 points. Moneyline not traded.
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday. Every settled
  game is listed in the coverage table with the reason it was or was not scored.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
from pm_momentum_settled_replay import mid
from scipy.optimize import minimize_scalar
from settled_common import attach, decision_book, load_games, load_snapshots
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
    pnl,
)

GAP = 0.030
HIST = ROOT / "data/research/nba_v10/closing_lines/nba_closing_lines_2007_2026.jsonl"


class Survival:
    """Empirical P(residual > x), linearly interpolated on a 0.25-point grid."""

    def __init__(self, residuals: np.ndarray) -> None:
        self.grid = np.arange(-80.0, 80.25, 0.25)
        r = np.sort(residuals)
        self.surv = 1.0 - np.searchsorted(r, self.grid, side="right") / len(r)

    def __call__(self, x: float) -> float:
        return float(np.interp(x, self.grid, self.surv))


def history_shapes() -> dict[str, Survival]:
    ats, tot = [], []
    with HIST.open(encoding="utf-8") as handle:
        for raw in handle:
            r = json.loads(raw)
            hs, as_, sp, tl = (
                finite(r.get(k)) for k in ("home_score", "away_score", "home_spread", "total_line")
            )
            if not r.get("regular") or None in (hs, as_):
                continue
            if sp is not None:
                ats.append((hs - as_) - (-sp))
            if tl is not None:
                tot.append(hs + as_ - tl)
    return {"spread": Survival(np.array(ats)), "total": Survival(np.array(tot))}


def ladder_rungs(book: list[dict], game: dict, market: str) -> list[dict]:
    out = []
    for rec in book:
        mt, line = rec["market_type"], finite(rec.get("line"))
        la, sa = ask(rec.get("long")), ask(rec.get("short"))
        m = mid(rec.get("long"))
        if market == "total" and mt == "total" and line is not None:
            out.append({"k": line, "mid": m, "trade": True,
                        "hi": ("over", line, la), "lo": ("under", line, sa)})  # fmt: skip
        elif (
            market == "spread"
            and mt in ("spread", "moneyline")
            and rec.get("team") in (game["home"], game["away"])
        ):
            if mt == "moneyline":
                p_home = None if m is None else (m if rec["team"] == game["home"] else 1.0 - m)
                out.append({"k": 0.0, "mid": p_home, "trade": False})
                continue
            if line is None:
                continue
            other = game["away"] if rec["team"] == game["home"] else game["home"]
            if rec["team"] == game["home"]:  # home at L covers iff M > -L
                out.append({"k": -line, "mid": m, "trade": True,
                            "hi": (rec["team"], line, la), "lo": (other, -line, sa)})  # fmt: skip
            else:  # away at L covers iff M < L
                out.append({"k": line, "mid": None if m is None else 1.0 - m, "trade": True,
                            "hi": (other, -line, sa), "lo": (rec["team"], line, la)})  # fmt: skip
    return out


def fit_mean(rungs: list[dict], surv: Survival) -> float | None:
    pts = [(r["k"], r["mid"]) for r in rungs if r["mid"] is not None]
    if len(pts) < 2:
        return None
    centre = float(np.mean([k for k, _ in pts]))
    res = minimize_scalar(
        lambda mu: sum((surv(k - mu) - p) ** 2 for k, p in pts),
        bounds=(centre - 60, centre + 60),
        method="bounded",
    )
    return float(res.x) if res.success else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/LADDER_EMPIRICAL_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/ladder_empirical_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    shapes = history_shapes()
    sigmas = {m: float(v["sigma"]) for m, v in json.loads(ANCHOR_ARTIFACT.read_text())["markets"].items()}

    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    mapping = attach(games, snaps)
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows = []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        tip_et = g["tip"].astimezone(ET).date()
        week = (tip_et - timedelta(days=tip_et.weekday())).isoformat()
        coverage[week]["settled"] += 1
        if not g["slug"]:
            coverage[week]["no_polymarket_event"] += 1
            continue
        book = decision_book(snaps[g["slug"]], g["tip"])
        if not book:
            coverage[week]["no_pre_tip_book"] += 1
            continue
        coverage[week]["scored"] += 1
        age = (g["tip"] - max(r["_obs"] for r in book)).total_seconds() / 60
        for market in ("spread", "total"):
            surv = shapes[market]
            rungs = ladder_rungs(book, g, market)
            mu = fit_mean(rungs, surv)
            if mu is None:
                continue
            best = None
            for r in rungs:
                if not r["trade"]:
                    continue
                p_hi = surv(r["k"] - mu)
                for fair, (team, ln, price) in ((p_hi, r["hi"]), (1.0 - p_hi, r["lo"])):
                    if price is None:
                        continue
                    if best is None or fair - price > best["gap"]:
                        best = {"gap": fair - price, "fair": fair, "ask": price, "team": team, "line": ln}
            if best is None or best["gap"] < GAP:
                continue
            result = grade(g, market, {"team": best["team"], "line": best["line"]})
            rows.append(
                {
                    "event_id": g["event_id"],
                    "week": week,
                    "matchup": f"{g['away']} @ {g['home']}",
                    "market": market,
                    "book_age_min": round(age, 1),
                    "mean": round(mu, 2),
                    "side": f"{best['team']} {best['line']:+g}"
                    if market == "spread"
                    else f"{best['team']} {best['line']:g}",
                    "ask": best["ask"],
                    "fair": round(best["fair"], 4),
                    "gap": round(best["gap"], 4),
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, best["ask"]),
                    "pnl_net": pnl(result, best["ask"] + fee(best["ask"])),
                }
            )
    df = pd.DataFrame(rows)
    df.to_csv(args.csv, index=False)

    # Diagnostic: empirical vs normal survival at the offsets round 7 traded.
    diag = ["| offset x | empirical P(resid > x) | normal | diff (pts) |", "|---|---|---|---|"]
    for x in (0.5, 1.5, 2.5, 3.5, 4.5, 6.5, 10.5, 20.5):
        e = shapes["spread"](x)
        n = 1.0 - NormalDist(0, sigmas["spread"]).cdf(x)
        diag.append(f"| {x} | {e:.4f} | {n:.4f} | {100 * (e - n):+.2f} |")

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

    head = [
        "| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | net 95% CI (games) | median ask |",
        "|---|---|---|---|---|---|---|",
    ]
    out = ["# Ladder model, empirical shape (history), every settled NBA game", ""]
    out += ["Event mapping: " + json.dumps(mapping), "", "## Coverage by week", ""]
    out += ["| week | settled | scored | no event | no book |", "|---|---|---|---|---|"]
    for week in sorted(coverage):
        c = coverage[week]
        out.append(
            f"| {week} | {c['settled']} | {c['scored']} | {c['no_polymarket_event']} | {c['no_pre_tip_book']} |"
        )
    out += ["", f"## Spread shape: empirical vs normal(0, {sigmas['spread']:.3f})", "", *diag, ""]
    out += ["## Picks (gap >= 3.0 points)", "", *head, line("ALL", df)]
    if not df.empty:
        for mt in ("spread", "total"):
            out.append(line(mt, df[df.market == mt]))
        out += [
            "",
            "### By week",
            "",
            "| week | picks | W-L | PnL gross | PnL net |",
            "|---|---|---|---|---|",
        ]
        for week, wk in df.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            out.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
