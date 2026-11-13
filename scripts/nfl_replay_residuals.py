"""Residual analysis of the NFL v7 replay rows: where does the engine carry information
beyond the closing line, and where does it add noise?

Usage: python scripts/nfl_replay_residuals.py <rows.json>  (rows from nfl_replay_dump_rows.py)
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

rows = json.loads(Path(sys.argv[1]).read_text())
print("rows", len(rows))


def ols(y, X, names):
    X = np.column_stack([np.ones(len(y)), *X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    sigma2 = resid @ resid / (n - k)
    cov = sigma2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    out = []
    for nm, b, s in zip(["const", *names], beta, se, strict=True):
        out.append(f"    {nm:18s} b={b:+.4f} se={s:.4f} t={b / s:+.2f}")
    r2 = 1 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
    return "\n".join(out) + f"\n    n={n} R2={r2:.4f} resid_sd={math.sqrt(sigma2):.3f}"


def brier(p, y):
    p = np.asarray(p)
    y = np.asarray(y)
    return float(np.mean((p - y) ** 2))


def bucket_table(title, keys, hits, extra=None):
    groups = defaultdict(list)
    for k, h in zip(keys, hits, strict=True):
        groups[k].append(h)
    print(f"\n-- {title} --")
    for k in sorted(groups, key=lambda x: (x is None, x)):
        v = groups[k]
        print(f"    {k!s:22s} n={len(v):5d} mean={np.mean(v):+.4f}")


sp = [r for r in rows if r["margin"] + r["line"] != 0]
tot = [r for r in rows if r["total"] != r["total_line"]]
print(f"spread decided {len(sp)}, total decided {len(tot)}")

# ============================================================ SPREAD
print("\n################ SPREAD ################")
line = np.array([r["line"] for r in sp])
resid = np.array([r["margin"] + r["line"] for r in sp], dtype=float)  # actual margin - market margin
home_cover = (resid > 0).astype(int)
p = np.array([r["p_home_cover"] for r in sp])
exp_m = np.array([r["exp_margin"] for r in sp])
struct_m = np.array([r["struct_exp_margin"] for r in sp])
mkt_m = -line
season = np.array([r["season"] for r in sp])
week = np.array([int(r["week"]) if r["week"] is not None else -1 for r in sp])

print(f"\nmean resid (actual - market, home-positive) = {resid.mean():+.3f}  sd={resid.std():.3f}")
print(
    f"home cover rate {home_cover.mean():.4f}; favourite cover rate "
    f"{np.mean([(rm > 0) == (l < 0) for rm, l in zip(resid, line, strict=True) if l != 0]):.4f}"
)
print(
    f"resid sd |line|<8: {resid[np.abs(line) < 8].std():.3f} (engine 13.8); |line|>=8: {resid[np.abs(line) >= 8].std():.3f} (engine 17.25)"
)
print("\nmarket efficiency: resid ~ line")
print(ols(resid, [line], ["line"]))

print("\nengine disagreement with market: resid ~ (exp_margin - market_margin)")
print(ols(resid, [exp_m - mkt_m], ["engine_minus_mkt"]))
print("\noptimal structural weight: resid ~ (struct_margin - market_margin)   [engine uses 0.20]")
print(ols(resid, [struct_m - mkt_m], ["struct_minus_mkt"]))
for lo, hi in ((2021, 2023), (2024, 2026)):
    m = (season >= lo) & (season <= hi)
    print(f"  seasons {lo}-{hi}:")
    print(ols(resid[m], [(struct_m - mkt_m)[m]], ["struct_minus_mkt"]))

comp_names = ["c_pass", "c_rush", "c_pressure", "c_qb", "c_rest", "c_edsr", "c_tol"]
comps = [np.array([r[c] for r in sp], dtype=float) for c in comp_names]
neutral = np.array([1.0 if r["is_neutral"] else 0.0 for r in sp])
print(
    "\ncomponents (points, home-positive) ~ resid.  Engine-implied coef: 0.20 for pass/rush/pressure/qb/rest, 1.0 for edsr/tol"
)
print(ols(resid, [*comps, neutral], [*comp_names, "neutral"]))
print(
    "\ncomponent dispersion (sd, points):",
    {n: round(float(c.std()), 2) for n, c in zip(comp_names, comps, strict=True)},
)
print(
    "struct margin sd:",
    round(float(struct_m.std()), 2),
    " market margin sd:",
    round(float(mkt_m.std()), 2),
    " engine exp_margin sd:",
    round(float(exp_m.std()), 2),
)

# which side does the engine take?
eng_home = p >= 0.5
dog_is_home = line > 0
takes_dog = np.where(line == 0, False, eng_home == dog_is_home)
print(
    f"\nengine favours home in {eng_home.mean():.3f} of games; favours the dog in {takes_dog[line != 0].mean():.3f} of non-pick games"
)
print(
    f"engine side hit rate {np.mean(eng_home == home_cover.astype(bool)):.4f}; always-dog hit rate "
    f"{np.mean([(rm > 0) == (l > 0) for rm, l in zip(resid, line, strict=True) if l != 0]):.4f}"
)
print(f"Brier engine {brier(p, home_cover):.4f}; Brier 0.5 {0.25:.4f}")
clamped = np.sum((p <= 0.35 + 1e-9) | (p >= 0.65 - 1e-9))
print(f"clamp binds in {clamped} games")

# calibration by p bucket
bucket_table(
    "engine p_home_cover bucket -> home cover rate", [round(float(x) * 20) / 20 for x in p], home_cover
)
# edge-size buckets vs outcome from the engine's side
side_p = np.where(eng_home, p, 1 - p)
side_hit = (eng_home == home_cover.astype(bool)).astype(int)
bucket_table(
    "engine side probability -> side hit rate",
    [round(float(x), 2) for x in np.floor(side_p * 50) / 50],
    side_hit,
)

bucket_table("week -> resid mean (market bias by week)", week, resid)
bucket_table("week -> engine side hit", week, side_hit)
pg = np.array([min(r["home_prior_games"], r["away_prior_games"]) for r in sp])
bucket_table("min prior games bucket -> engine side hit", np.minimum(pg // 4 * 4, 24), side_hit)
restd = np.array([r["home_rest"] - r["away_rest"] for r in sp])
bucket_table("rest diff (home - away, clipped) -> resid", np.clip(restd, -7, 7), resid)
bucket_table("qb_source -> engine side hit", [r["qb_source"] for r in sp], side_hit)
bucket_table("qb_penalty>0 -> engine side hit", [r["qb_penalty"] > 0 for r in sp], side_hit)
bucket_table(
    "|line| bucket -> dog cover rate",
    np.minimum(np.abs(line) // 3 * 3, 12),
    [(rm > 0) == (l > 0) if l != 0 else 0.5 for rm, l in zip(resid, line, strict=True)],
)
bucket_table("season -> resid sd proxy (abs resid)", season, np.abs(resid))

# ============================================================ TOTAL
print("\n################ TOTAL ################")
tl = np.array([r["total_line"] for r in tot])
tres = np.array([r["total"] - r["total_line"] for r in tot], dtype=float)
over = (tres > 0).astype(int)
pa = np.array([r["p_over_a"] for r in tot])
pb = np.array([r["p_over_b"] for r in tot])
eta = np.array([r["exp_total_a"] for r in tot])
etb = np.array([r["exp_total_b"] for r in tot])
tseason = np.array([r["season"] for r in tot])
tweek = np.array([int(r["week"]) if r["week"] is not None else -1 for r in tot])
print(
    f"\nmean resid (actual - line) = {tres.mean():+.3f} sd={tres.std():.3f} (engine 13.8); over rate {over.mean():.4f}"
)
print("\nmarket efficiency: resid ~ total_line")
print(ols(tres, [tl], ["total_line"]))
print("\nengine disagreement: resid ~ (exp_total_b - line)")
print(ols(tres, [etb - tl], ["engine_b_minus_line"]))
print("no-weather variant: resid ~ (exp_total_a - line)")
print(ols(tres, [eta - tl], ["engine_a_minus_line"]))
tn = ["t_pass", "t_rush", "t_rz", "t_explosive", "t_pace", "t_qb", "t_dome", "t_weather_b", "t_high_drag"]
tc = [np.array([r[c] for r in tot], dtype=float) for c in tn]
print("\ncomponents (points) ~ resid. Engine-implied coef = 1.0 for every term")
print(ols(tres, tc, tn))
print("component dispersion (sd):", {n: round(float(c.std()), 2) for n, c in zip(tn, tc, strict=True)})
print(f"Brier A {brier(pa, over):.4f}  B {brier(pb, over):.4f}  0.5 -> 0.25")
clampedt = np.sum((pb <= 0.35 + 1e-9) | (pb >= 0.65 - 1e-9))
print(f"clamp binds in {clampedt} games (B)")
bucket_table("engine p_over (B) bucket -> over rate", [round(float(x) * 20) / 20 for x in pb], over)
bucket_table("total_line bucket -> resid", np.minimum(np.maximum(tl // 3 * 3, 36), 54), tres)
wind = np.array([r["wind"] if r["wind"] is not None else -1 for r in tot], dtype=float)
temp = np.array([r["temp"] if r["temp"] is not None else -999 for r in tot], dtype=float)
hasw = wind >= 0
bucket_table("wind bucket (recorded) -> resid", np.where(hasw, np.minimum(wind // 4 * 4, 20), -1), tres)
bucket_table(
    "temp bucket (recorded) -> resid",
    np.where(temp > -999, np.minimum(np.maximum(temp // 10 * 10, 10), 90), -999),
    tres,
)
bucket_table("dome -> resid", [r["is_dome"] for r in tot], tres)
bucket_table("week -> resid", tweek, tres)
pace = np.array([(r["home_pace"] + r["away_pace"]) / 2 for r in tot])
bucket_table("avg pace bucket -> resid", np.round(pace / 2) * 2, tres)
side_over = pb >= 0.5
tside_hit = (side_over == over.astype(bool)).astype(int)
tside_p = np.where(side_over, pb, 1 - pb)
bucket_table(
    "engine side probability (B) -> side hit rate",
    [round(float(x), 2) for x in np.floor(tside_p * 50) / 50],
    tside_hit,
)
bucket_table("engine side -> hit rate", np.where(side_over, "over", "under"), tside_hit)
