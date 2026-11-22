"""Structural residual on top of the Polymarket ladder, scored on every settled NBA game.

  python scripts/nba_v10/structural_residual_replay.py [--runtime E:/model-prediction-runtime]

The market is the anchor; the model only adds a residual. A ridge regression learns, from
2007-08..2022-23 closing lines, how far the result lands from the closing line as a function of
schedule features the market is often said to underweight (rest, back-to-backs, early season,
big spreads, home dogs). It is validated on 2023-24..2025-26 closes. On the settled 2026-27 games
the market mean is the Polymarket ladder's own implied mean at the decision quote (PIT), the fair
mean is that plus the predicted residual, and a pick fires when fair probability minus ask clears
the T-B threshold.

Pinned before any validation or settled outcome was read (quick 261122-srr):

- Features (home perspective): rest days home/away (capped at 5; season opener = 5), back-to-back
  flags, 3-games-in-4-nights flags, games played this season home/away (capped at 20), early
  season flag (either side < 10 games), the closing home spread, |spread| >= 10 flag, home-dog flag;
  totals add the closing total. Regular-season games only.
- Targets: ATS residual = home margin + home spread; total residual = total points - total line.
- Fit: ridge (alpha 10) on standardised features, seasons <= 2023. Validation seasons 2024-2026:
  correlation of prediction with residual and its 95% bootstrap CI. VALIDATION PASS needs the CI
  above 0; a market that fails ships nothing (reported, not re-tuned).
- Settled games: ladder mean from all two-sided Polymarket mids (round 2's fit_mean) at the last
  quote 5-120 min before tip; fair = ladder mean + prediction (spread residual adds to the home
  margin); the rung nearest 50%; pick if fair minus ask >= 3.0 points; one pick per game/market.
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee, weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
from pm_momentum_settled_replay import book_at, fit_mean, ladder_points, mid
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

DATA = ROOT / "data/research/nba_v10"
TRAIN_LAST_SEASON = 2023
VAL_SEASONS = (2024, 2025, 2026)
ALPHA = 10.0
GAP = 0.030
SEED = 20261122
NAME_FIX = {"LA Clippers": "Los Angeles Clippers"}
SPREAD_FEATURES = [
    "rest_h", "rest_a", "b2b_h", "b2b_a", "three_in_four_h", "three_in_four_a",
    "gp_h", "gp_a", "early", "spread", "big_spread", "home_dog",
]  # fmt: skip
TOTAL_FEATURES = [*SPREAD_FEATURES, "total_line"]


def schedule_features(games: pd.DataFrame) -> pd.DataFrame:
    """Rest/b2b/3-in-4/games-played per game from the full schedule (sorted by date)."""
    games = games.sort_values("date").reset_index(drop=True)
    last: dict[tuple[int, str], list[date]] = defaultdict(list)
    cols = defaultdict(list)
    for row in games.itertuples():
        for side, team in (("h", row.home), ("a", row.away)):
            hist = last[(row.season, team)]
            prev = hist[-1] if hist else None
            rest = 5 if prev is None else min(5, (row.date - prev).days)
            cols[f"rest_{side}"].append(rest)
            cols[f"b2b_{side}"].append(int(prev is not None and (row.date - prev).days == 1))
            recent = [d for d in hist if (row.date - d).days <= 3]
            cols[f"three_in_four_{side}"].append(int(len(recent) >= 2))
            cols[f"gp_{side}"].append(min(20, len(hist)))
        for team in (row.home, row.away):
            last[(row.season, team)].append(row.date)
    for key, values in cols.items():
        games[key] = values
    games["early"] = ((games.gp_h < 10) | (games.gp_a < 10)).astype(int)
    return games


def add_line_features(df: pd.DataFrame) -> pd.DataFrame:
    df["big_spread"] = (df.spread.abs() >= 10).astype(int)
    df["home_dog"] = (df.spread > 0).astype(int)
    return df


def load_history() -> pd.DataFrame:
    rows = []
    with (DATA / "closing_lines/nba_closing_lines_2007_2026.jsonl").open(encoding="utf-8") as handle:
        for raw in handle:
            r = json.loads(raw)
            rows.append(
                {
                    "season": int(r["season"]),
                    "date": date.fromisoformat(r.get("date_et") or r["date"]),
                    "home": NAME_FIX.get(r["home_team"], r["home_team"]),
                    "away": NAME_FIX.get(r["away_team"], r["away_team"]),
                    "regular": bool(r.get("regular")),
                    "home_score": finite(r.get("home_score")),
                    "away_score": finite(r.get("away_score")),
                    "spread": finite(r.get("home_spread")),
                    "total_line": finite(r.get("total_line")),
                }
            )
    df = schedule_features(pd.DataFrame(rows))
    df = df[df.regular & df.spread.notna() & df.total_line.notna() & df.home_score.notna()].copy()
    df = add_line_features(df)
    df["ats"] = df.home_score - df.away_score + df.spread
    df["tot"] = df.home_score + df.away_score - df.total_line
    return df


def ridge_fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd[sd == 0] = 1.0
    z = (x - mu) / sd
    intercept = float(y.mean())
    beta = np.linalg.solve(z.T @ z + ALPHA * np.eye(z.shape[1]), z.T @ (y - intercept))
    return beta, mu, sd, intercept


def ridge_predict(model: tuple, x: np.ndarray) -> np.ndarray:
    beta, mu, sd, intercept = model
    return intercept + ((x - mu) / sd) @ beta


def boot_corr(pred: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    rng = np.random.default_rng(SEED)
    n = len(y)
    draws = []
    for _ in range(2000):
        idx = rng.integers(0, n, n)
        draws.append(np.corrcoef(pred[idx], y[idx])[0, 1])
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(np.corrcoef(pred, y)[0, 1]), float(lo), float(hi)


def current_season_schedule(runtime: Path, games: dict[str, dict]) -> pd.DataFrame:
    """2026-27 schedule: research scoreboard index (regular season) plus every settled ledger game."""
    rows = {}
    with (DATA / "games/scoreboard_index.jsonl").open(encoding="utf-8") as handle:
        for raw in handle:
            r = json.loads(raw)
            if r.get("season_year") != 2027 or r.get("season_type") != "regular-season":
                continue
            tip = pd.Timestamp(r["event_start_utc"]).tz_convert(ET)
            rows[str(r["event_id"])] = {
                "event_id": str(r["event_id"]),
                "season": 2027,
                "date": tip.date(),
                "home": NAME_FIX.get(r["home_team"], r["home_team"]),
                "away": NAME_FIX.get(r["away_team"], r["away_team"]),
            }
    for g in games.values():
        rows.setdefault(
            g["event_id"],
            {
                "event_id": g["event_id"],
                "season": 2027,
                "date": g["tip"].astimezone(ET).date(),
                "home": NAME_FIX.get(g["home"], g["home"]),
                "away": NAME_FIX.get(g["away"], g["away"]),
            },
        )
    return schedule_features(pd.DataFrame(rows.values()))


def rung_pick(book: list[dict], game: dict, market: str, fair_mean: float, sigma: float) -> dict | None:
    """Fair probability at the rung nearest 50% and the side with the larger fair-minus-ask gap."""
    home, away = game["home"], game["away"]
    if market == "spread":
        rungs = [r for r in book if r["market_type"] == "spread" and r.get("team") in (home, away)]
    else:
        rungs = [r for r in book if r["market_type"] == "total"]
    rungs = [
        r
        for r in rungs
        if finite(r.get("line")) is not None
        and ask(r.get("long")) is not None
        and ask(r.get("short")) is not None
    ]
    if not rungs:
        return None
    rec = min(rungs, key=lambda r: abs((mid(r.get("long")) or ask(r["long"])) - 0.5))
    line = float(rec["line"])
    phi = NormalDist()
    if market == "spread":
        # long = team T at L: home covers iff M > -L; away at L covers iff M < L.
        if rec["team"] == home:
            p_long = 1.0 - phi.cdf((-line - fair_mean) / sigma)
        else:
            p_long = phi.cdf((line - fair_mean) / sigma)
        other = away if rec["team"] == home else home
        sides = [
            {"label": f"{rec['team']} {line:+g}", "team": rec["team"], "line": line, "ask": ask(rec["long"]),
             "fair": p_long},
            {"label": f"{other} {-line:+g}", "team": other, "line": -line, "ask": ask(rec["short"]),
             "fair": 1.0 - p_long},
        ]  # fmt: skip
    else:
        p_over = 1.0 - phi.cdf((line - fair_mean) / sigma)
        sides = [
            {"label": f"over {line:g}", "team": "over", "line": line, "ask": ask(rec["long"]), "fair": p_over},
            {"label": f"under {line:g}", "team": "under", "line": line, "ask": ask(rec["short"]),
             "fair": 1.0 - p_over},
        ]  # fmt: skip
    side = max(sides, key=lambda s: s["fair"] - s["ask"])
    return {**side, "gap": side["fair"] - side["ask"], "rec": rec}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/STRUCTURAL_RESIDUAL_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/structural_residual_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    sigmas = {m: float(v["sigma"]) for m, v in json.loads(ANCHOR_ARTIFACT.read_text())["markets"].items()}

    hist = load_history()
    train = hist[hist.season <= TRAIN_LAST_SEASON]
    val = hist[hist.season.isin(VAL_SEASONS)]
    models, validation = {}, {}
    for market, feats, target in (("spread", SPREAD_FEATURES, "ats"), ("total", TOTAL_FEATURES, "tot")):
        model = ridge_fit(train[feats].to_numpy(float), train[target].to_numpy(float))
        pred = ridge_predict(model, val[feats].to_numpy(float))
        corr, lo, hi = boot_corr(pred, val[target].to_numpy(float))
        validation[market] = {
            "train_n": len(train),
            "val_n": len(val),
            "val_corr": round(corr, 4),
            "val_ci": [round(lo, 4), round(hi, 4)],
            "val_pred_sd": round(float(pred.std()), 3),
            "pass": lo > 0,
            "coef_std": dict(zip(feats, np.round(model[0], 3).tolist(), strict=True)),
        }
        models[market] = (model, feats)

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
    sched = current_season_schedule(runtime, games).set_index("event_id")

    coverage: dict[str, int] = defaultdict(int)
    rows = []
    for game in sorted(games.values(), key=lambda g: g["tip"]):
        coverage["games"] += 1
        recs = snaps.get(game["slug"] or "")
        if not recs or game["event_id"] not in sched.index:
            coverage["no_polymarket_or_schedule"] += 1
            continue
        book = book_at(recs, game["tip"] - QUOTE_MAX_AGE, game["tip"] - QUOTE_MIN_LEAD)
        if not book:
            coverage["no_quote_in_window"] += 1
            continue
        coverage["evaluated"] += 1
        feats_row = sched.loc[game["event_id"]]
        tip_et = game["tip"].astimezone(ET).date()
        means = {m: fit_mean(ladder_points(book, game, m), sigmas[m]) for m in ("spread", "total")}
        if means["spread"] is None or means["total"] is None:
            coverage["no_ladder_mean"] += 1
            continue
        # The model's line features are the market's own: home spread = -(ladder home-margin mean).
        x = feats_row.to_dict()
        x["spread"] = -means["spread"]
        x["total_line"] = means["total"]
        x["big_spread"] = int(abs(x["spread"]) >= 10)
        x["home_dog"] = int(x["spread"] > 0)
        for market in ("spread", "total"):
            sigma = sigmas[market]
            ladder_mean = means[market]
            model, feat_names = models[market]
            residual = float(ridge_predict(model, np.array([[float(x[f]) for f in feat_names]]))[0])
            fair_mean = ladder_mean + residual
            pick = rung_pick(book, game, market, fair_mean, sigma)
            if pick is None:
                continue
            result = grade(game, market, pick)
            price = pick["ask"]
            rows.append(
                {
                    "event_id": game["event_id"],
                    "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                    "matchup": f"{game['away']} @ {game['home']}",
                    "market": market,
                    "ladder_mean": round(ladder_mean, 2),
                    "residual": round(residual, 3),
                    "side": pick["label"],
                    "ask": price,
                    "fair": round(pick["fair"], 4),
                    "gap": round(pick["gap"], 4),
                    "signal": pick["gap"] >= GAP and validation[market]["pass"],
                    "signal_ignoring_validation": pick["gap"] >= GAP,
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
            return f"| {name} | 0 | - | - | - | - |"
        w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
        ci = cluster_ci(part, "pnl_gross", "stake")
        n = len(part)
        return (
            f"| {name} | {n} | {w}-{lo_} | {part.pnl_gross.sum():+.2f} ({part.pnl_gross.sum() / n:+.1%}) | "
            f"{part.pnl_net.sum():+.2f} ({part.pnl_net.sum() / n:+.1%}) | {ci[0]:+.1%} .. {ci[1]:+.1%} |"
        )

    head = [
        "| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | 95% CI ROI (games) |",
        "|---|---|---|---|---|---|",
    ]
    out = [
        "# Structural residual on the Polymarket ladder, every settled NBA game",
        "",
        "## Validation (history)",
        "",
    ]
    out += [
        "```",
        json.dumps(validation, indent=1),
        "```",
        "",
        "```",
        json.dumps(dict(coverage), indent=1),
        "```",
        "",
    ]
    if not df.empty:
        out += ["## Picks (gap >= 3.0 points, validated markets only)", "", *head, line("ALL", df[df.signal])]
        for mt in ("spread", "total"):
            out.append(line(mt, df[df.signal & (df.market == mt)]))
        out += ["", "### Ignoring the validation gate (context)", "", *head]
        for mt in ("spread", "total"):
            part = df[df.signal_ignoring_validation & (df.market == mt)]
            out.append(line(mt, part))
        out += ["", "### By week, picks ignoring the validation gate", "", "| week | picks | PnL gross | PnL net |",
                "|---|---|---|---|"]  # fmt: skip
        for week, wk in df[df.signal_ignoring_validation].groupby("week"):
            out.append(f"| {week} | {len(wk)} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
