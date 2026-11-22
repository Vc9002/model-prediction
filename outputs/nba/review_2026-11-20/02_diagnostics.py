"""EXPLORATORY diagnostics on the walk-forward replay (rows.jsonl, ml_rows.jsonl).

Split: 60/20/20 on complete ET dates over the replayed rows (same rule as
validation.chronological_split). Every number here is train or validation ONLY; locked-holdout
rows are dropped on load and never scored. Fits (sigma, OLS, blend lambda) use train only and
are reported on validation; lambda grid-searched on validation is labelled as such.
Prints markdown to stdout.
"""

from __future__ import annotations

import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
rows_all = [json.loads(x) for x in (HERE / "rows.jsonl").open(encoding="utf-8")]
ml_rows = {r["event_id"]: r for r in (json.loads(x) for x in (HERE / "ml_rows.jsonl").open(encoding="utf-8"))}

dates = sorted({r["date"] for r in rows_all})
n_tr = math.floor(len(dates) * 0.60)
n_va = math.floor(len(dates) * 0.20)
val_start, hold_start = (
    dates[n_tr],
    min(dates[n_tr + n_va], "2026-02-23"),
)  # v5 registry holdout starts 2026-02-23
for r in rows_all:
    r["split"] = "train" if r["date"] < val_start else ("val" if r["date"] < hold_start else "HOLD")
rows = [r for r in rows_all if r["split"] != "HOLD"]
n_hold = sum(1 for r in rows_all if r["split"] == "HOLD")
for r in rows:
    r["margin"] = r["home_score"] - r["away_score"]
    r["total"] = r["home_score"] + r["away_score"]
    r["cs_min"] = min(r["cs_games_home"], r["cs_games_away"])
    r["rest_diff"] = max(-5.0, min(5.0, r["h_rest"] - r["a_rest"]))
    r["season"] = f"{r['season_year'] - 1}-{str(r['season_year'])[2:]}"
    r["reg"] = r["season_type"] == "regular-season"

print("# raw tables (exploratory)\n")
print(
    f"Split on {len(dates)} ET dates: train {dates[0]}..{dates[n_tr - 1]}, "
    f"val {val_start}..<{hold_start}, holdout from {hold_start} "
    f"({n_hold} rows DROPPED, not scored)."
)
for s in ("train", "val"):
    sub = [r for r in rows if r["split"] == s]
    print(f"- {s}: n={len(sub)}, with closing line n={sum(1 for r in sub if r['line_margin'] is not None)}")


def mean(xs):
    xs = list(xs)
    return st.mean(xs) if xs else float("nan")


def rmse(xs):
    xs = list(xs)
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else float("nan")


def corr(a, b):
    a, b = list(a), list(b)
    return st.correlation(a, b) if len(a) > 3 else float("nan")


def table(header, body):
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in body:
        print("| " + " | ".join(f"{v:.2f}" if isinstance(v, float) else str(v) for v in row) + " |")
    print()


# ------------------------------------------------------------------ PIT season-to-date ppg
games = [json.loads(x) for x in Path("e:/MP/data/processed/nba/games.jsonl").open(encoding="utf-8")]
games = [g for g in games if g.get("season_type") == "regular-season"]
games.sort(key=lambda g: g["event_start_utc"])
# 1) Totals ---------------------------------------------------------------------
print("## 1. Totals by season (regular season, train+val)\n")
body = []
for season in sorted({r["season"] for r in rows}):
    sub = [r for r in rows if r["season"] == season and r["reg"]]
    wl = [r for r in sub if r["line_total"] is not None]
    body.append(
        [
            season,
            len(sub),
            mean(r["total"] for r in sub),
            mean(r["exp_total"] for r in sub),
            mean(r["total"] - r["exp_total"] for r in sub),
            len(wl),
            mean(r["line_total"] for r in wl),
            mean(r["total"] - r["line_total"] for r in wl),
            mean(r["line_total"] - r["exp_total"] for r in wl),
            2 * mean(r["league_baseline"] for r in sub),
            mean(r["home_pts_trend"] + r["away_pts_trend"] for r in sub),
        ]
    )
table(
    [
        "season",
        "n",
        "actual",
        "model",
        "act-model",
        "n_line",
        "line",
        "act-line",
        "line-model",
        "2x league_baseline",
        "trend pts sum",
    ],
    body,
)

print("## 1b. Totals by month (all game types, train+val)\n")
body = []
for mo in sorted({r["date"][:7] for r in rows}):
    sub = [r for r in rows if r["date"][:7] == mo]
    wl = [r for r in sub if r["line_total"] is not None]
    body.append(
        [
            mo,
            rows and sub[0]["split"],
            len(sub),
            mean(r["total"] for r in sub),
            mean(r["total"] - r["exp_total"] for r in sub),
            mean(r["total"] - r["line_total"] for r in wl),
            mean(r["line_total"] - r["exp_total"] for r in wl),
            mean(r["league_baseline"] for r in sub),
        ]
    )
table(["month", "split", "n", "actual", "act-model", "act-line", "line-model", "league_baseline/team"], body)

# season-to-date ppg vs baseline the engine shrinks toward
print("## 1c. Engine league_baseline vs PIT season-to-date points/team (regular season rows)\n")
by_season_games = defaultdict(list)
for g in games:
    by_season_games[g["season_year"]].append(g)
body = []
for r in rows:
    pass
samples = defaultdict(list)
for r in rows:
    if not r["reg"]:
        continue
    prior = [g for g in by_season_games[r["season_year"]] if g["event_start_utc"][:10] < r["date"]]
    if len(prior) < 30:
        continue
    std_ppg = mean((g["home_score"] + g["away_score"]) / 2 for g in prior)
    samples[r["season"]].append((std_ppg, r["league_baseline"]))
for season, xs in sorted(samples.items()):
    body.append([season, len(xs), mean(a for a, _ in xs), mean(b for _, b in xs), mean(a - b for a, b in xs)])
table(["season", "n", "season-to-date ppg", "engine baseline ppg", "gap"], body)

# 2) Margins / HCA ---------------------------------------------------------------
print("## 2. Home margin by season (regular season, train+val)\n")
body = []
for season in sorted({r["season"] for r in rows}):
    sub = [r for r in rows if r["season"] == season and r["reg"]]
    wl = [r for r in sub if r["line_margin"] is not None]
    body.append(
        [
            season,
            len(sub),
            mean(r["margin"] for r in sub),
            mean(r["exp_margin"] for r in sub),
            mean(r["margin"] - r["exp_margin"] for r in sub),
            mean(r["line_margin"] for r in wl),
            mean(r["margin"] - r["line_margin"] for r in wl),
            mean(1.0 if r["margin"] > 0 else 0.0 for r in sub),
        ]
    )
table(
    [
        "season",
        "n",
        "actual home margin",
        "model margin",
        "act-model",
        "line margin",
        "act-line",
        "home win%",
    ],
    body,
)

# HCA via OLS margin ~ const + (elo-based rating diff without HCA)
print("## 2b. Implied HCA by regression (train+val, with lines)\n")
wl = [r for r in rows if r["line_margin"] is not None]
for label, fn in (("actual margin ~ a + b*(line)", lambda r: r["line_margin"]),):
    pass
X = np.array([[1.0, r["elo_margin"] - 38.0 / 28.0] for r in rows])
y = np.array([r["margin"] for r in rows])
beta = np.linalg.lstsq(X, y, rcond=None)[0]
print(
    f"- OLS actual margin = a + b*(Elo diff in pts, no HCA): a(HCA)={beta[0]:.2f}, b={beta[1]:.2f}, n={len(rows)}"
)
print("- Served model HCA = 0.70*(38 Elo/28) + 0.30*1.75 = 1.48 pts (before rest term)")
print()

# 3) Early season --------------------------------------------------------------
print(
    "## 3. Error by current-season games played (min of two teams), REGULAR SEASON 2024-25+, rows with lines\n"
)
buckets = [(0, 5), (6, 10), (11, 20), (21, 40), (41, 99)]
body = []
for lo, hi in buckets:
    sub = [r for r in wl if lo <= r["cs_min"] <= hi and r["reg"] and r["season_year"] >= 2025]
    if not sub:
        continue
    body.append(
        [
            f"{lo}-{hi}",
            len(sub),
            rmse(r["margin"] - r["exp_margin"] for r in sub),
            rmse(r["margin"] - r["line_margin"] for r in sub),
            mean(abs(r["exp_margin"] - r["line_margin"]) for r in sub),
            corr(
                (r["exp_margin"] - r["line_margin"] for r in sub),
                (r["margin"] - r["line_margin"] for r in sub),
            ),
            rmse(r["total"] - r["exp_total"] for r in sub),
            rmse(r["total"] - r["line_total"] for r in sub),
            mean(r["total"] - r["exp_total"] for r in sub),
        ]
    )
table(
    [
        "cs games",
        "n",
        "RMSE margin model",
        "RMSE margin line",
        "|model-line|",
        "corr(m-l, a-l)",
        "RMSE total model",
        "RMSE total line",
        "total bias act-model",
    ],
    body,
)

# 4) Rest -----------------------------------------------------------------------
print("## 4. Rest / back-to-back (REGULAR SEASON rows with lines)\n")
wlr = [r for r in wl if r["reg"]]


def se(xs):
    xs = list(xs)
    return st.pstdev(xs) / math.sqrt(len(xs))


def rest_bucket(r):
    d = r["rest_diff"]
    if d <= -1.5:
        return "a: home rest <= away-1.5d"
    if d < -0.5:
        return "b: home -1d"
    if d <= 0.5:
        return "c: equal"
    if d < 1.5:
        return "d: home +1d"
    return "e: home >= away+1.5d"


body = []
for b in sorted({rest_bucket(r) for r in wlr}):
    sub = [r for r in wlr if rest_bucket(r) == b]
    body.append(
        [
            b,
            len(sub),
            mean(r["margin"] - r["line_margin"] for r in sub),
            mean(r["exp_margin"] - r["line_margin"] for r in sub),
            mean(r["margin"] - r["exp_margin"] for r in sub),
            se(r["margin"] - r["line_margin"] for r in sub),
        ]
    )
table(["rest diff", "n", "act-line (market err)", "model-line", "act-model", "SE"], body)


def b2b(r):
    h, a = r["h_rest"] <= 1.25, r["a_rest"] <= 1.25
    return {
        (False, False): "neither",
        (True, False): "home b2b",
        (False, True): "away b2b",
        (True, True): "both b2b",
    }[(h, a)]


body = []
for b in ("neither", "home b2b", "away b2b", "both b2b"):
    sub = [r for r in wlr if b2b(r) == b]
    body.append(
        [
            b,
            len(sub),
            mean(r["margin"] - r["line_margin"] for r in sub),
            mean(r["exp_margin"] - r["line_margin"] for r in sub),
            mean(r["margin"] - r["exp_margin"] for r in sub),
            mean(r["total"] - r["line_total"] for r in sub),
            mean(r["exp_total"] - r["line_total"] for r in sub),
            se(r["margin"] - r["line_margin"] for r in sub),
            se(r["total"] - r["line_total"] for r in sub),
            mean(r["line_total"] for r in sub),
            mean(r["total"] for r in sub),
            mean(r["exp_total"] for r in sub),
        ]
    )
table(
    [
        "b2b",
        "n",
        "margin act-line",
        "margin model-line",
        "margin act-model",
        "total act-line",
        "total model-line",
        "SE margin",
        "SE total",
        "line total",
        "actual total",
        "model total",
    ],
    body,
)

# 5) SD + calibration ------------------------------------------------------------
print("## 5. Residual SD vs served sigma (rows with lines)\n")
body = []
for s in ("train", "val"):
    sub = [r for r in wl if r["split"] == s]
    body.append(
        [
            s,
            len(sub),
            st.pstdev([r["margin"] - r["exp_margin"] for r in sub]),
            st.pstdev([r["margin"] - r["line_margin"] for r in sub]),
            st.pstdev([r["total"] - r["exp_total"] for r in sub]),
            st.pstdev([r["total"] - r["line_total"] for r in sub]),
            st.pstdev([r["exp_margin"] - r["line_margin"] for r in sub]),
            st.pstdev([r["exp_total"] - r["line_total"] for r in sub]),
        ]
    )
table(
    [
        "split",
        "n",
        "SD margin resid (model)",
        "SD margin resid (line)",
        "SD total resid (model)",
        "SD total resid (line)",
        "SD(model-line) margin",
        "SD(model-line) total",
    ],
    body,
)
print("Served: margin_sd=14.0, total_sd=18.0\n")


def calib(sub, pkey, outcome):
    bins = [(0.5, 0.525), (0.525, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 1.01)]
    out = []
    for lo, hi in bins:
        pts = []
        for r in sub:
            p = r[pkey]
            o = outcome(r)
            if p is None or o is None:
                continue
            side_p, side_o = (p, o) if p >= 0.5 else (1 - p, 1 - o)
            if lo <= side_p < hi:
                pts.append((side_p, side_o))
        if pts:
            out.append(
                [
                    f"{lo:.3f}-{min(hi, 1):.3f}",
                    len(pts),
                    100 * mean(p for p, _ in pts),
                    100 * mean(o for _, o in pts),
                ]
            )
    return out


def away_cover(r):
    d = r["margin"] - r["line_margin"]
    return None if d == 0 else (1 if d < 0 else 0)


def over(r):
    d = r["total"] - r["line_total"]
    return None if d == 0 else (1 if d > 0 else 0)


for s in ("train", "val"):
    sub = [r for r in wl if r["split"] == s]
    print(f"### 5b. Spread cover calibration ({s}) - picked side prob vs hit%\n")
    table(["model side prob", "n", "mean model %", "hit %"], calib(sub, "p_away_cover", away_cover))
    print(f"### 5c. Total over/under calibration ({s})\n")
    table(["model side prob", "n", "mean model %", "hit %"], calib(sub, "p_over", over))
    for key, fn in (("p_away_cover", away_cover), ("p_over", over)):
        pts = [(r[key], fn(r)) for r in sub if fn(r) is not None]
        brier = mean((p - o) ** 2 for p, o in pts)
        print(
            f"- {s} {key}: n={len(pts)}, Brier model={brier:.4f} vs coin-flip 0.2500; "
            f"hit% at model side={100 * mean(1.0 if (p >= 0.5) == (o == 1) else 0.0 for p, o in pts):.1f}"
        )
    print()

# 6) Market information ------------------------------------------------------------
print("## 6. Does the model add information beyond the closing line?\n")
tr = [r for r in wl if r["split"] == "train"]
va = [r for r in wl if r["split"] == "val"]
body = []
for nm, a, m, ln in (
    ("margin", "margin", "exp_margin", "line_margin"),
    ("total", "total", "exp_total", "line_total"),
):
    for s, sub in (("train", tr), ("val", va)):
        body.append(
            [
                nm,
                s,
                len(sub),
                corr((r[m] - r[ln] for r in sub), (r[a] - r[ln] for r in sub)),
                rmse(r[a] - r[m] for r in sub),
                rmse(r[a] - r[ln] for r in sub),
            ]
        )
table(["market", "split", "n", "corr(model-line, actual-line)", "RMSE model", "RMSE line"], body)

print("### 6b. Shrink-to-market: pred = line + lam*(model - line); lam fit on train (OLS), applied to val\n")
body = []
for nm, a, m, ln in (
    ("margin", "margin", "exp_margin", "line_margin"),
    ("total", "total", "exp_total", "line_total"),
):
    x = np.array([r[m] - r[ln] for r in tr])
    yv = np.array([r[a] - r[ln] for r in tr])
    lam = float((x @ yv) / (x @ x))
    # bias-aware: actual-line = c + lam*(model-line)
    Xc = np.column_stack([np.ones_like(x), x])
    c2, lam2 = np.linalg.lstsq(Xc, yv, rcond=None)[0]
    xv = np.array([r[m] - r[ln] for r in va])
    yvv = np.array([r[a] - r[ln] for r in va])
    mse_line = float(np.mean(yvv**2))
    mse_blend = float(np.mean((yvv - lam * xv) ** 2))
    mse_model = float(np.mean((yvv - xv) ** 2))
    grid = np.linspace(-0.5, 1.0, 151)
    lam_val = float(grid[np.argmin([np.mean((yvv - g * xv) ** 2) for g in grid])])
    body.append([nm, lam, c2, lam2, lam_val, math.sqrt(mse_line), math.sqrt(mse_blend), math.sqrt(mse_model)])
table(
    [
        "market",
        "lam (train, no const)",
        "const (train)",
        "lam w/ const (train)",
        "lam argmin on VAL",
        "val RMSE line",
        "val RMSE blend(train lam)",
        "val RMSE model",
    ],
    body,
)

print(
    "### 6c. Moneyline: model win prob vs line-implied win prob (Phi(line/sigma), sigma = train SD of actual-line)\n"
)
sig = st.pstdev([r["margin"] - r["line_margin"] for r in tr])


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


body = []
for s, sub in (("train", tr), ("val", va)):
    pts = [r for r in sub if r["event_id"] in ml_rows and r["margin"] != 0]
    o = [1.0 if r["margin"] > 0 else 0.0 for r in pts]
    p_line = [phi(r["line_margin"] / sig) for r in pts]
    p_v9 = [r["p_home_ml"] for r in pts]
    p_v5 = [ml_rows[r["event_id"]]["p_v5"] for r in pts]
    b = lambda ps: mean((p - y) ** 2 for p, y in zip(ps, o))
    body.append(
        [
            s,
            len(pts),
            b(p_line),
            b(p_v5),
            b(p_v9),
            b([0.5 * a + 0.5 * c for a, c in zip(p_line, p_v5)]),
            corr((a - c for a, c in zip(p_v5, p_line)), (y - c for y, c in zip(o, p_line))),
        ]
    )
table(
    [
        "split",
        "n",
        "Brier line-implied",
        "Brier ML v5 (served)",
        "Brier v9 coherent ML",
        "Brier 50/50 line+v5",
        "corr(v5-line, y-line)",
    ],
    body,
)
print(f"(sigma from train = {sig:.2f})\n")

# logistic blend weight for v5 vs line on val (logit-space), fit on train
from math import log


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return log(p / (1 - p))


def fit_w(sub):
    pts = [r for r in sub if r["event_id"] in ml_rows and r["margin"] != 0]
    best = None
    for w in np.linspace(0, 1, 101):
        loss = 0.0
        for r in pts:
            z = (1 - w) * logit(phi(r["line_margin"] / sig)) + w * logit(ml_rows[r["event_id"]]["p_v5"])
            p = 1 / (1 + math.exp(-z))
            y = 1.0 if r["margin"] > 0 else 0.0
            loss += (p - y) ** 2
        loss /= len(pts)
        if best is None or loss < best[1]:
            best = (float(w), loss)
    return best


print(f"- logit-blend weight on v5 minimizing Brier: train w={fit_w(tr)[0]:.2f}, val w={fit_w(va)[0]:.2f}\n")

# 7) PIT / leak signals ----------------------------------------------------------
print("## 7. Leak / integrity signals\n")
for s, sub in (
    ("train", [r for r in rows if r["split"] == "train"]),
    ("val", [r for r in rows if r["split"] == "val"]),
):
    straight = [r for r in sub if r["margin"] != 0]
    hit = mean(1.0 if (r["exp_margin"] > 0) == (r["margin"] > 0) else 0.0 for r in straight)
    b0 = mean((r["p_home_ml"] - (1.0 if r["margin"] > 0 else 0.0)) ** 2 for r in straight)
    print(
        f"- {s}: straight-up 'spread at line 0' hit {100 * hit:.1f}%, v9 ML Brier {b0:.4f} (n={len(straight)})"
    )
print(
    "- artifact claim: spread holdout_brier_score 0.1948; total 0.2235 (see 5b/5c for real closing-line Brier)"
)
cs_bug = sum(1 for r in rows if r["cs_games_min"] > r["cs_min"] + 5)
print(
    f"- rows where engine current_season_games_min (lifetime games_played) exceeds true current-season "
    f"min by >5: {cs_bug}/{len(rows)}"
)

# 8) Extra: season type, fatigue decomposition, drift-corrected totals ---------------
print("\n## 8a. By season type (train+val, rows with lines)\n")
body = []
for t in ("regular-season", "play-in-season", "post-season"):
    sub = [r for r in wl if r["season_type"] == t]
    if sub:
        body.append(
            [
                t,
                len(sub),
                mean(r["total"] - r["exp_total"] for r in sub),
                mean(r["total"] - r["line_total"] for r in sub),
                mean(r["line_total"] - r["exp_total"] for r in sub),
                rmse(r["margin"] - r["exp_margin"] for r in sub),
                rmse(r["margin"] - r["line_margin"] for r in sub),
            ]
        )
table(
    [
        "type",
        "n",
        "total act-model",
        "total act-line",
        "total line-model",
        "RMSE margin model",
        "RMSE margin line",
    ],
    body,
)

print(
    "## 8b. Margin decomposition by b2b (mean pts; fatigue+rest = exp_margin - 0.7*elo - 0.3*(trend_raw+1.75))\n"
)
body = []
for b in ("neither", "home b2b", "away b2b", "both b2b"):
    sub = [r for r in wlr if b2b(r) == b]
    elo_part = [0.7 * r["elo_margin"] for r in sub]
    tr_part = [0.3 * (r["trend_margin_raw"] + 1.75) for r in sub]
    fat = [r["exp_margin"] - e - t for r, e, t in zip(sub, elo_part, tr_part)]
    body.append(
        [
            b,
            len(sub),
            mean(elo_part),
            mean(tr_part),
            mean(fat),
            mean(r["line_margin"] for r in sub),
            mean(r["margin"] for r in sub),
        ]
    )
table(["b2b", "n", "0.7*elo", "0.3*trend", "fatigue+rest", "line", "actual"], body)

print(
    "## 8c. Totals with a PIT season-to-date drift correction (exp_total + 2*(stdppg - baseline)), regular season\n"
)
body = []
for s in ("train", "val"):
    pts = []
    for r in wl:
        if r["split"] != s or not r["reg"]:
            continue
        prior = [g for g in by_season_games[r["season_year"]] if g["event_start_utc"][:10] < r["date"]]
        if len(prior) < 30:
            continue
        stdppg = mean((g["home_score"] + g["away_score"]) / 2 for g in prior)
        pts.append((r, r["exp_total"] + 2 * (stdppg - r["league_baseline"])))
    body.append(
        [
            s,
            len(pts),
            mean(r["total"] - r["exp_total"] for r, _ in pts),
            mean(r["total"] - c for r, c in pts),
            rmse(r["total"] - r["exp_total"] for r, _ in pts),
            rmse(r["total"] - c for r, c in pts),
            rmse(r["total"] - r["line_total"] for r, _ in pts),
            corr((c - r["line_total"] for r, c in pts), (r["total"] - r["line_total"] for r, _ in pts)),
        ]
    )
table(
    [
        "split",
        "n",
        "bias raw",
        "bias corrected",
        "RMSE raw",
        "RMSE corrected",
        "RMSE line",
        "corr(corr-line, act-line)",
    ],
    body,
)

print("## 8d. Brier detail (4dp)\n")
for s, sub in (("train", tr), ("val", va)):
    pts = [r for r in sub if r["event_id"] in ml_rows and r["margin"] != 0]
    o = [1.0 if r["margin"] > 0 else 0.0 for r in pts]
    for nm, ps in (
        ("line-implied", [phi(r["line_margin"] / sig) for r in pts]),
        ("ML v5", [ml_rows[r["event_id"]]["p_v5"] for r in pts]),
        ("v9 coherent", [r["p_home_ml"] for r in pts]),
    ):
        print(f"- {s} {nm}: Brier {mean((p - y) ** 2 for p, y in zip(ps, o)):.4f} (n={len(pts)})")
# best constant-sigma for cover probs on train (model mean, model sigma) vs coin flip
for s, sub in (("train", tr), ("val", va)):
    for sd in (14.0, 20.0, 30.0, 60.0):
        pts = [
            (phi((r["line_margin"] - r["exp_margin"]) / sd), away_cover(r))
            for r in sub
            if away_cover(r) is not None
        ]
        print(f"- {s} cover Brier with sigma={sd:.0f}: {mean((p - o) ** 2 for p, o in pts):.4f}")

print(
    "\n## 8e. Parity check: exp_margin - [0.7*elo + 0.3*(trend_raw+1.75) + 0.15*rest_diff], rows with no b2b\n"
)
from collections import Counter

c = Counter()
for r in rows:
    if r["h_rest"] <= 1.25 or r["a_rest"] <= 1.25:
        continue
    resid = r["exp_margin"] - (
        0.7 * r["elo_margin"] + 0.3 * (r["trend_margin_raw"] + 1.75) + 0.15 * r["rest_diff"]
    )
    c[round(resid, 1)] += 1
print(
    "residual (0.1 pt) counts; expected only {0, -0.24 (home 3in4), +0.36 (away 3in4), +0.12 (both)} +/- rounding:"
)
print(sorted(c.items()))
