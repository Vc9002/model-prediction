"""Polymarket momentum replayed over every settled NBA game (read-only; ledger games, PIT quotes only).

  python scripts/nba_v10/pm_momentum_settled_replay.py [--runtime E:/model-prediction-runtime]

Hypothesis (from quick 261122-tbr): the DraftKings close beat Polymarket quotes taken 15-60 min
before tip, so Polymarket lags the books. If it under-reacts, a Polymarket move in progress keeps
going, and buying in the direction of its own recent move earns money. Every input is a Polymarket
snapshot observed before the decision, so the replay is point-in-time clean.

Pinned before any outcome was read (quick 261122-pmm):

- Decision quote T2: as in tb_settled_replay, the last snapshot per market in
  [tip - 120 min, tip - 5 min]; rungs older than 15 min before the newest rung are dropped.
- Earlier quote T1: per market, the last snapshot observed at or before T2 - 3 h (and after
  T2 - 24 h), same rung filters.
- Ladder mean: the home-margin (spread + moneyline) or total mean of a normal with the anchor
  artifact's sigma, least squares on the two-sided mids (width <= 10 points, mid in [0.05, 0.95]).
  Moneyline enters the spread ladder as P(home margin > 0).
- Signal: a move of at least MOVE_PROB (3.0 probability points) at the 50% rung, i.e.
  |mean(T2) - mean(T1)| >= MOVE_PROB * sqrt(2 pi) * sigma. Moneyline: |P_home(T2) - P_home(T1)|
  >= MOVE_PROB on the moneyline book itself.
- Bet: the direction of the move, on the T2 rung whose long mid is nearest 50% (spread: the side
  the move favours; total: over if the mean rose). One pick per game and market.
- PnL: 1 unit at the T2 ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist

import pandas as pd
from scipy.optimize import minimize_scalar
from tb_settled_replay import (
    ANCHOR_ARTIFACT,
    DEFAULT_RUNTIME,
    ET,
    QUOTE_MAX_AGE,
    QUOTE_MIN_LEAD,
    ROOT,
    ask,
    cluster_ci,
    fee,
    finite,
    grade,
    load_games,
    load_snapshots,
    pnl,
)

MOVE_PROB = 0.030
T1_MIN_GAP = timedelta(hours=3)
T1_MAX_GAP = timedelta(hours=24)
RUNG_MAX_SPREAD = timedelta(minutes=15)
MID_MAX_WIDTH = 0.10
PHI = NormalDist()


def mid(side: dict | None) -> float | None:
    bid, a = finite((side or {}).get("bid")), finite((side or {}).get("ask"))
    if bid is None or a is None or not 0.0 <= a - bid <= MID_MAX_WIDTH + 1e-9:
        return None
    value = (bid + a) / 2.0
    return value if 0.05 <= value <= 0.95 else None


def book_at(snaps: list[dict], lo, hi) -> list[dict]:
    """Last snapshot per market in [lo, hi], keeping only rungs close in time to the newest one."""
    best: dict[str, dict] = {}
    for rec in snaps:
        if not lo <= rec["_obs"] <= hi or rec.get("market_state") not in (None, "MARKET_STATE_OPEN"):
            continue
        key = str(rec.get("market_slug") or rec.get("market_id"))
        if key not in best or rec["_obs"] > best[key]["_obs"]:
            best[key] = rec
    if not best:
        return []
    newest = max(r["_obs"] for r in best.values())
    return [r for r in best.values() if newest - r["_obs"] <= RUNG_MAX_SPREAD]


def ladder_points(book: list[dict], game: dict, market: str) -> list[tuple[float, float]]:
    """(k, P(X > k)) pairs: X is the home margin (spread + moneyline) or the game total."""
    home, away = game["home"], game["away"]
    pts = []
    for rec in book:
        m = mid(rec.get("long"))
        if m is None:
            continue
        mt = rec["market_type"]
        if market == "total" and mt == "total" and finite(rec.get("line")) is not None:
            pts.append((float(rec["line"]), m))
        elif market == "spread" and mt == "spread" and rec.get("team") in (home, away):
            line = finite(rec.get("line"))
            if line is None:
                continue
            # long = team T at L: home at L covers iff M > -L; away at L covers iff M < L.
            pts.append((-line, m) if rec["team"] == home else (line, 1.0 - m))
        elif market == "spread" and mt == "moneyline" and rec.get("team") in (home, away):
            pts.append((0.0, m if rec["team"] == home else 1.0 - m))
    return pts


def fit_mean(points: list[tuple[float, float]], sigma: float) -> float | None:
    if len(points) < 2:
        return None

    def loss(mu: float) -> float:
        return sum((1.0 - PHI.cdf((k - mu) / sigma) - p) ** 2 for k, p in points)

    centre = sum(k for k, _ in points) / len(points)
    res = minimize_scalar(loss, bounds=(centre - 60, centre + 60), method="bounded")
    return float(res.x) if res.success else None


def ml_home_prob(book: list[dict], game: dict) -> float | None:
    for rec in book:
        if rec["market_type"] == "moneyline" and rec.get("team") in (game["home"], game["away"]):
            m = mid(rec.get("long"))
            if m is not None:
                return m if rec["team"] == game["home"] else 1.0 - m
    return None


def pick_spread(book: list[dict], game: dict, home_up: bool) -> tuple[dict, dict] | None:
    rungs = [
        r
        for r in book
        if r["market_type"] == "spread"
        and r.get("team") in (game["home"], game["away"])
        and ask(r.get("long")) is not None
        and ask(r.get("short")) is not None
        and finite(r.get("line")) is not None
    ]
    if not rungs:
        return None
    rec = min(rungs, key=lambda r: abs((mid(r.get("long")) or ask(r["long"])) - 0.5))
    want = game["home"] if home_up else game["away"]
    line = float(rec["line"])
    if rec["team"] == want:
        return rec, {"label": f"{want} {line:+g}", "team": want, "line": line, "ask": ask(rec["long"])}
    return rec, {"label": f"{want} {-line:+g}", "team": want, "line": -line, "ask": ask(rec["short"])}


def pick_total(book: list[dict], over: bool) -> tuple[dict, dict] | None:
    rungs = [
        r
        for r in book
        if r["market_type"] == "total"
        and finite(r.get("line")) is not None
        and ask(r.get("long")) is not None
        and ask(r.get("short")) is not None
    ]
    if not rungs:
        return None
    rec = min(rungs, key=lambda r: abs((mid(r.get("long")) or ask(r["long"])) - 0.5))
    line = float(rec["line"])
    side = "over" if over else "under"
    return rec, {
        "label": f"{side} {line:g}",
        "team": side,
        "line": line,
        "ask": ask(rec["long"] if over else rec["short"]),
    }


def pick_ml(book: list[dict], game: dict, home_up: bool) -> tuple[dict, dict] | None:
    for rec in book:
        if rec["market_type"] != "moneyline" or rec.get("team") not in (game["home"], game["away"]):
            continue
        want = game["home"] if home_up else game["away"]
        side = rec["long"] if rec["team"] == want else rec["short"]
        if ask(side) is None:
            return None
        return rec, {"label": want, "team": want, "line": None, "ask": ask(side)}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/PM_MOMENTUM_SETTLED_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/pm_momentum_settled_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    sigmas = {m: float(v["sigma"]) for m, v in json.loads(ANCHOR_ARTIFACT.read_text())["markets"].items()}
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    by_tip: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        by_tip[recs[0]["event_start_utc"][:16]].append(slug)
    for game in games.values():
        if game["slug"] in snaps:
            continue
        for slug in by_tip.get(game["tip"].strftime("%Y-%m-%dT%H:%M"), []):
            if {game["home"], game["away"]} & {r.get("team") for r in snaps[slug]}:
                game["slug"] = slug

    coverage: dict[str, int] = defaultdict(int)
    rows = []
    for game in sorted(games.values(), key=lambda g: g["tip"]):
        coverage["games"] += 1
        recs = snaps.get(game["slug"] or "")
        if not recs:
            coverage["no_polymarket_event"] += 1
            continue
        book2 = book_at(recs, game["tip"] - QUOTE_MAX_AGE, game["tip"] - QUOTE_MIN_LEAD)
        if not book2:
            coverage["no_decision_quote"] += 1
            continue
        t2 = max(r["_obs"] for r in book2)
        book1 = book_at(recs, t2 - T1_MAX_GAP, t2 - T1_MIN_GAP)
        if not book1:
            coverage["no_earlier_quote"] += 1
            continue
        coverage["evaluated"] += 1
        signals = []
        for market in ("spread", "total"):
            sigma = sigmas[market]
            m1 = fit_mean(ladder_points(book1, game, market), sigma)
            m2 = fit_mean(ladder_points(book2, game, market), sigma)
            if m1 is None or m2 is None:
                continue
            move = m2 - m1
            move_prob = move / (math.sqrt(2 * math.pi) * sigma)  # prob change at the 50% rung
            signals.append((market, move_prob, m1, m2))
        p1, p2 = ml_home_prob(book1, game), ml_home_prob(book2, game)
        if p1 is not None and p2 is not None:
            signals.append(("moneyline", p2 - p1, p1, p2))
        for market, move_prob, before, after in signals:
            if market == "spread":
                chosen = pick_spread(book2, game, move_prob > 0)
            elif market == "total":
                chosen = pick_total(book2, move_prob > 0)
            else:
                chosen = pick_ml(book2, game, move_prob > 0)
            if chosen is None:
                continue
            rec, side = chosen
            price = side["ask"]
            result = grade(game, market, side)
            tip_et = game["tip"].astimezone(ET).date()
            rows.append(
                {
                    "event_id": game["event_id"],
                    "tip_utc": game["tip"].isoformat(),
                    "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                    "matchup": f"{game['away']} @ {game['home']}",
                    "market": market,
                    "before": round(before, 4),
                    "after": round(after, 4),
                    "move_prob": round(move_prob, 4),
                    "signal": abs(move_prob) >= MOVE_PROB,
                    "side": side["label"],
                    "ask": price,
                    "t1_hours_before_t2": round(
                        (t2 - max(r["_obs"] for r in book1)).total_seconds() / 3600, 2
                    ),
                    "quote_minutes_to_tip": round((game["tip"] - rec["_obs"]).total_seconds() / 60, 1),
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, price),
                    "pnl_net": pnl(result, price + fee(price)),
                }
            )
    df = pd.DataFrame(rows)
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.csv, index=False)

    def line(name: str, part: pd.DataFrame) -> str:
        if part.empty:
            return f"| {name} | 0 | - | - | - | - | - |"
        w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
        ci = cluster_ci(part, "pnl_gross", "stake")
        n = len(part)
        return (
            f"| {name} | {n} | {w}-{lo_} | {part.pnl_gross.sum():+.2f} ({part.pnl_gross.sum() / n:+.1%}) | "
            f"{part.pnl_net.sum():+.2f} ({part.pnl_net.sum() / n:+.1%}) | {ci[0]:+.1%} .. {ci[1]:+.1%} |"
        )

    out = ["# Polymarket momentum replayed over every settled NBA game", ""]
    out += ["```", json.dumps(dict(coverage), indent=1), "```", ""]
    head = [
        "| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | 95% CI ROI (games) |",
        "|---|---|---|---|---|---|",
    ]
    if not df.empty:
        sig = df[df.signal]
        out += ["## Momentum picks (move >= 3.0 points)", "", *head, line("ALL signal picks", sig)]
        for market in ("moneyline", "spread", "total"):
            out.append(line(market, sig[sig.market == market]))
        out += [
            "",
            "### By week (ET, Monday start)",
            "",
            "| week | picks | W-L | PnL gross | PnL net |",
            "|---|---|---|---|---|",
        ]
        for week, wk in sig.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            out.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
        out += ["", "## Context only: every evaluated market, bet with the move however small", "", *head]
        out.append(line("all markets", df))
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
