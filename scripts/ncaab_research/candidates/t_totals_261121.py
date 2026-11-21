"""NCAAB totals candidates T1_tempo_eff_total, T2_total_combiner, T3_total_scale (addendum v3, research only).

T1 reuses C1's per-possession machinery (``c_ppp_261120.py``: efficiency ridge, recency and blowout weights,
tier prior, carryover, HCA prior, lam/carry grid) with the three pinned changes:
    (a) the tempo ridge is fit on possessions per 40 minutes, poss40 = poss x 40 / (40 + 5 n_ot), n_ot from
        the CBBD period scores of each HISTORY game (unknown -> 0, counted);
    (b) mu_total = (eff_home + eff_away) / 100 x poss40 x (1 + E[n_ot] / 8), E[n_ot] = c_ot x p_tie,
        p_tie = F_t8((0.5 - m) / s) - F_t8((-0.5 - m) / s) (m = the variant's margin, s = the fold's t8 margin
        scale of the game's bucket), c_ot by least squares without intercept on the training seasons;
    (c) the (lam, carry) variant is chosen per fold by the training seasons' total RMSE.
T2 is a ridge (alpha 10, standardised, intercept free) of [v6, T1, early x v6, early x T1, neutral, PIT league
level] fit on training seasons. T3 keeps v6's location plus the training median residual and fits a normal SD
s0 sqrt(poss / 68) (1 + c1 early) by maximum likelihood.

Point in time: the walk solves day D from games that tipped before Eastern midnight of D (bisect on tip time);
the predicted game's own box, score and periods are never inputs. Fold discipline: every free parameter is fit on
the three training seasons; held-out outcomes are never handed to a fit. The closing line never enters an input
or a target; its presence only defines which training rows are "totals-cohort-eligible" (the pin's wording).

Stages (repo root, OPENBLAS_NUM_THREADS=1):
    .venv/Scripts/python scripts/ncaab_research/candidates/t_totals_261121.py walk-dev      # heavy, outcome-free
    .venv/Scripts/python scripts/ncaab_research/candidates/t_totals_261121.py predict-dev   # T1/T2/T3 files
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[3]
for _p in (
    REPO / "src",
    REPO / "scripts",
    REPO / "scripts" / "ncaab_research",
    Path(__file__).resolve().parent,
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import c_ppp_261120 as cp  # C1 machinery, reused unchanged

OUT = REPO / "outputs" / "ncaab" / "research_261121"
DEV_COMPONENTS = OUT / "t_components_dev.jsonl.gz"
DEV_WALK_META = OUT / "t_walk_dev_meta.json"
GRID_DIRS = {g: OUT / g for g in ("T1_tempo_eff_total", "T2_total_combiner", "T3_total_scale")}
DEV_SEASONS = (2023, 2024, 2025, 2026)
REG_MINUTES, OT_MINUTES = 40.0, 5.0
OT_FRACTION_OF_REGULATION = OT_MINUTES / REG_MINUTES  # 1/8
T2_ALPHA = 10.0
T2_FEATURES = ("v6_total", "t1_total", "early_v6", "early_t1", "neutral", "league_level")
EARLY_GAMES = 8.0
LEAGUE_MIN_GAMES = 50
REF_POSS = 68.0
HISTORY_CUTOFF = "strictly before Eastern midnight of the game day"


# ------------------------------------------------------------------------------------------- pure functions
def poss_per_40(poss: np.ndarray, n_ot: np.ndarray) -> np.ndarray:
    """Possessions per 40 minutes: overtime periods are 5 minutes each."""
    n = np.asarray(n_ot, float)
    return np.asarray(poss, float) * REG_MINUTES / (REG_MINUTES + OT_MINUTES * n)


def p_tie(margin: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """P(regulation margin rounds to 0) under t(8) with location ``margin`` and ``scale``."""
    from scipy import stats

    m, s = np.asarray(margin, float), np.asarray(scale, float)
    return stats.t.cdf((0.5 - m) / s, df=8.0) - stats.t.cdf((-0.5 - m) / s, df=8.0)


def t1_total(sum_eff: np.ndarray, poss40: np.ndarray, ptie: np.ndarray, c_ot: float) -> np.ndarray:
    return (
        np.asarray(sum_eff, float)
        / 100.0
        * np.asarray(poss40, float)
        * (1.0 + c_ot * np.asarray(ptie, float) * OT_FRACTION_OF_REGULATION)
    )


def fit_c_ot(n_ot: np.ndarray, ptie: np.ndarray) -> float:
    """Least squares without intercept of n_ot on p_tie (rows with known periods)."""
    n, p = np.asarray(n_ot, float), np.asarray(ptie, float)
    ok = ~np.isnan(n) & ~np.isnan(p)
    return float(np.sum(n[ok] * p[ok]) / np.sum(p[ok] ** 2))


def early(sample: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, 1.0 - np.asarray(sample, float) / EARLY_GAMES)


def ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float = T2_ALPHA) -> dict[str, Any]:
    """Standardised ridge with a free intercept (alpha on the standardised coefficients)."""
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    z = (x - mu) / sd
    ym = float(y.mean())
    beta = np.linalg.solve(z.T @ z + alpha * np.eye(z.shape[1]), z.T @ (y - ym))
    return {"mu": mu.tolist(), "sd": sd.tolist(), "beta": beta.tolist(), "intercept": ym}


def ridge_predict(fit: Mapping[str, Any], x: np.ndarray) -> np.ndarray:
    z = (x - np.array(fit["mu"])) / np.array(fit["sd"])
    return fit["intercept"] + z @ np.array(fit["beta"])


def fit_scale_t3(resid: np.ndarray, poss: np.ndarray, sample: np.ndarray) -> tuple[float, float]:
    """Gaussian MLE of sd = s0 sqrt(poss / 68) (1 + c1 early), s0 > 0, c1 >= 0."""
    from scipy.optimize import minimize

    r, k, e = np.asarray(resid, float), np.sqrt(np.asarray(poss, float) / REF_POSS), early(sample)

    def nll(th: np.ndarray) -> float:
        sd = np.exp(th[0]) * k * (1.0 + th[1] * e)
        return float(np.sum(np.log(sd) + 0.5 * (r / sd) ** 2))

    res = minimize(
        nll, np.array([np.log(np.std(r)), 0.1]), method="L-BFGS-B", bounds=[(None, None), (0.0, 5.0)]
    )
    return float(np.exp(res.x[0])), float(res.x[1])


def t3_sd(s0: float, c1: float, poss: np.ndarray, sample: np.ndarray) -> np.ndarray:
    return s0 * np.sqrt(np.asarray(poss, float) / REF_POSS) * (1.0 + c1 * early(sample))


def league_levels(
    events: list[dict[str, Any]], days: Iterable[tuple[int, str]]
) -> dict[tuple[int, str], float]:
    """(season, Eastern day) -> mean total of the season's D1-vs-D1 games that tipped before Eastern midnight of
    the day when >= LEAGUE_MIN_GAMES exist, else the previous season's full D1-vs-D1 mean (NaN if none)."""
    by_season: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    for e in events:
        if cp._is_d1_event(e):
            st = datetime.strptime(e["start_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
            by_season[int(e["season_year"])].append((st, float(e["home_score"] + e["away_score"])))
    cum: dict[int, tuple[list[datetime], np.ndarray]] = {}
    full: dict[int, float] = {}
    for s, rows in by_season.items():
        rows.sort()
        cum[s] = ([r[0] for r in rows], np.concatenate([[0.0], np.cumsum([r[1] for r in rows])]))
        full[s] = float(np.mean([r[1] for r in rows]))
    out = {}
    for s, day in days:
        starts, cs = cum.get(s, ([], np.array([0.0])))
        n = bisect.bisect_left(starts, cp.eastern_midnight_utc(day))
        out[(s, day)] = float(cs[n] / n) if n >= LEAGUE_MIN_GAMES else full.get(s - 1, float("nan"))
    return out


# ------------------------------------------------------------------------------------------- ratings walk
@dataclass
class TGames:
    g: cp.Games
    poss40: np.ndarray


def build_tgames(
    events: list[dict[str, Any]],
    boxes: Mapping[str, Mapping[str, Any]],
    n_ot: Mapping[str, int],
    team_index: dict[str, int],
) -> tuple[TGames, dict[str, int]]:
    """C1's fit rows (cp.build_games, identical filters) plus possessions per 40 minutes."""
    g, drops = cp.build_games(events, boxes, team_index)
    by_key: dict[tuple[datetime, int, int], int] = {}
    for e in events:
        if cp._is_d1_event(e):
            st = datetime.strptime(e["start_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
            key = (st, team_index[str(e["home_team_id"])], team_index[str(e["away_team_id"])])
            by_key[key] = n_ot.get(str(e["event_id"]), -1)
    ot = np.array(
        [by_key.get((st, int(h), int(a)), -1) for st, h, a in zip(g.start, g.hidx, g.aidx, strict=True)]
    )
    drops = dict(drops)
    drops["fit_rows_periods_unknown_ot_0"] = int((ot < 0).sum())
    drops["fit_rows_overtime"] = int((ot > 0).sum())
    return TGames(g=g, poss40=poss_per_40(g.poss, np.maximum(ot, 0))), drops


def tempo_system(tg: TGames, sel: np.ndarray, w: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """C1's tempo normal equations on possessions per 40: poss40 = muT + T_h + T_a."""
    hi, ai, p = tg.g.hidx[sel], tg.g.aidx[sel], tg.poss40[sel]
    gt = np.zeros((n + 1, n + 1))
    bt = np.zeros(n + 1)
    cols = (hi, ai, np.full_like(hi, n))
    for c1 in cols:
        np.add.at(bt, c1, w * p)
        for c2 in cols:
            np.add.at(gt, (c1, c2), w)
    return gt, bt


def walk(
    events: list[dict[str, Any]],
    boxes: Mapping[str, Mapping[str, Any]],
    n_ot: Mapping[str, int],
    seasons: Iterable[int],
    emit_seasons: Iterable[int],
    variants: list[tuple[float, float]] = cp.VARIANTS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Day-by-day walk (cp.run_ratings' structure) emitting, per D1-vs-D1 event of ``emit_seasons`` and per
    variant, the margin, eff_home + eff_away and the projected possessions per 40. Outcome-free output."""
    import grader

    emit = set(emit_seasons)
    team_ids = sorted(
        {str(e[f"{s}_team_id"]) for e in events if cp._is_d1_event(e) for s in ("home", "away")}
    )
    team_index = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)
    tg, drops = build_tgames(events, boxes, n_ot, team_index)
    g = tg.g
    tiers = cp.tier_priors(events, team_index)
    h0 = cp.HCA_PRIOR_POINTS * 100.0 / cp.REF_POSS
    slate: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for e in events:
        if cp._is_d1_event(e) and int(e["season_year"]) in emit:
            slate[int(e["season_year"])][grader.nr.et_day(e["start_utc"])].append(e)
    finals: dict[str, dict[str, np.ndarray]] = {}
    out: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"drops": drops, "n_fit_rows": len(g.start), "n_teams": n, "seasons": {}}
    for s in sorted(seasons):
        tier = np.nan_to_num(tiers.get(s, np.full(n, np.nan)), nan=cp.tier_to_per100(np.array([1180.0]))[0])
        priors = {}
        for lam, carry in variants:
            key = cp.variant_key(lam, carry)
            po = pd_ = 0.5 * tier
            pt = np.zeros(n)
            prev = finals.get(key)
            if prev is not None:
                has = prev["played"]
                po = np.where(has, carry * prev["o"] + (1 - carry) * 0.5 * tier, 0.5 * tier)
                pd_ = np.where(has, carry * prev["d"] + (1 - carry) * 0.5 * tier, 0.5 * tier)
                pt = np.where(has, carry * prev["t"], 0.0)
            priors[key] = (po, pd_, pt)
        in_season = np.flatnonzero(g.season == s)
        if not in_season.size:
            continue
        days = sorted(slate[s]) if s in emit else []
        last_day = date.fromordinal(int(g.day_ord[in_season].max()))
        walk_days = [*days, (last_day + timedelta(days=1)).isoformat()]
        t0 = time.time()
        for day in walk_days:
            cut = bisect.bisect_left(g.start, cp.eastern_midnight_utc(day))
            sel = in_season[in_season < cut]
            dord = date.fromisoformat(day).toordinal()
            w = cp.recency_weight(dord - g.day_ord[sel]) * cp.blowout_weight(g.hpts[sel] - g.apts[sel])
            gm, b, _gt_raw, _bt_raw = cp.eff_system(g, sel, w, n)
            gt, bt = tempo_system(tg, sel, w, n)
            played = np.zeros(n, dtype=bool)
            played[g.hidx[sel]] = True
            played[g.aidx[sel]] = True
            sol = {}
            for lam, carry in variants:
                key = cp.variant_key(lam, carry)
                po, pd_, pt = priors[key]
                sol[key] = (cp.solve_eff(gm, b, n, lam, po, pd_, h0), cp.solve_tempo(gt, bt, n, lam, pt))
            if day not in slate[s]:  # season-end solve seeds next season
                for key, (eff, tempo) in sol.items():
                    finals[key] = {"o": eff[:n], "d": eff[n : 2 * n], "t": tempo[:n], "played": played}
                k0 = cp.variant_key(cp.LAM_GRID[1], cp.CARRY_GRID[1])
                meta["seasons"][str(s)] = {
                    "fit_games": int(sel.size),
                    "league_eff": round(float(sol[k0][0][2 * n + 1]), 2),
                    "league_poss40_intercept": round(float(sol[k0][1][n]), 2),
                    "mean_game_poss40": round(float(tg.poss40[sel].mean()), 2),
                    "seconds": round(time.time() - t0, 1),
                }
                continue
            for e in slate[s][day]:
                hi, ai = team_index[str(e["home_team_id"])], team_index[str(e["away_team_id"])]
                row: dict[str, Any] = {"event_id": str(e["event_id"]), "season": s, "game_date": day}
                for key, (eff, tempo) in sol.items():
                    m, _t, p, eh, ea = cp.predict_game(eff, tempo, n, hi, ai, bool(e["neutral"]))
                    row[f"m_{key}"] = round(m, 4)
                    row[f"se_{key}"] = round(eh + ea, 4)
                    row[f"p40_{key}"] = round(p, 3)
                out.append(row)
        print(f"season {s}: {meta['seasons'].get(str(s))}", flush=True)
    return out, meta


# ------------------------------------------------------------------------------------------- data
def sha256_file(path: Path) -> str:
    return cp.sha256_file(path)


def dev_history() -> list[dict[str, Any]]:
    return cp._history_events()


def cmd_walk_dev(_args: argparse.Namespace) -> int:
    import grader_v3

    events = dev_history()
    boxes = cp.load_boxes()
    per = grader_v3.load_periods(e["event_id"] for e in events)
    n_ot = {k: int(v["n_ot"]) for k, v in per.items()}
    seasons = range(cp.HISTORY_FIRST_SEASON, max(DEV_SEASONS) + 1)
    rows, meta = walk(events, boxes, n_ot, seasons, DEV_SEASONS)
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(DEV_COMPONENTS, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    meta["n_rows"] = len(rows)
    meta["history_events_with_periods"] = len(n_ot)
    meta["components_sha256"] = sha256_file(DEV_COMPONENTS)
    DEV_WALK_META.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


# ------------------------------------------------------------------------------------------- fitting
VARIANT_KEYS = [cp.variant_key(lam, c) for lam, c in cp.VARIANTS]


def t1_fold_fit(tr: pd.DataFrame) -> dict[str, Any]:
    """T1 parameters from training rows (columns: margin, total, n_ot, sample, m_*, se_*, p40_*)."""
    res: dict[str, Any] = {"by_variant": {}}
    smp = tr["sample"].to_numpy()
    b = cp.sample_bucket(smp)
    for key in VARIANT_KEYS:
        m = tr[f"m_{key}"].to_numpy(float)
        scales = cp.t8_scales_by_bucket(tr["margin"].to_numpy(float) - m, smp)
        pt = p_tie(m, np.array(scales)[b])
        c_ot = fit_c_ot(tr["n_ot"].to_numpy(float), pt)
        tot = t1_total(tr[f"se_{key}"].to_numpy(float), tr[f"p40_{key}"].to_numpy(float), pt, c_ot)
        rmse = float(np.sqrt(np.mean((tr["total"].to_numpy(float) - tot) ** 2)))
        res["by_variant"][key] = {"margin_scales": scales, "c_ot": c_ot, "train_total_rmse": round(rmse, 4)}
    best = min(res["by_variant"], key=lambda k: (res["by_variant"][k]["train_total_rmse"], k))
    v = res["by_variant"][best]
    tot = t1_apply(tr, best, v["margin_scales"], v["c_ot"])
    res.update(
        variant=best,
        margin_scales=v["margin_scales"],
        c_ot=v["c_ot"],
        total_sd=cp.sd_by_bucket(tr["total"].to_numpy(float) - tot, smp),
    )
    return res


def t1_apply(df: pd.DataFrame, key: str, margin_scales: list[float], c_ot: float) -> np.ndarray:
    m = df[f"m_{key}"].to_numpy(float)
    pt = p_tie(m, np.array(margin_scales)[cp.sample_bucket(df["sample"].to_numpy())])
    return t1_total(df[f"se_{key}"].to_numpy(float), df[f"p40_{key}"].to_numpy(float), pt, c_ot)


def t2_design(df: pd.DataFrame) -> np.ndarray:
    e = early(df["sample"].to_numpy(float))
    v6, t1 = df["v6_total"].to_numpy(float), df["t1_total"].to_numpy(float)
    return np.column_stack(
        [v6, t1, e * v6, e * t1, df["neutral"].astype(float).to_numpy(), df["league_level"].to_numpy(float)]
    )


def fold_fit(df: pd.DataFrame, held_out: int, seasons: Iterable[int]) -> dict[str, Any]:
    """Every free parameter of T1, T2 and T3 from the training seasons (held_out excluded)."""
    train_seasons = [s for s in seasons if s != held_out]
    tr = df[df["season"].isin(train_seasons) & df["margin"].notna() & df["total"].notna()].copy()
    assert held_out not in set(tr["season"]), "held-out season reached a fit"
    t1 = t1_fold_fit(tr)
    tr["t1_total"] = t1_apply(tr, t1["variant"], t1["margin_scales"], t1["c_ot"])
    el = tr[tr["cohort_eligible"] & tr["v6_total"].notna() & tr["league_level"].notna()]
    fit2 = ridge_fit(t2_design(el), el["total"].to_numpy(float))
    r2 = el["total"].to_numpy(float) - ridge_predict(fit2, t2_design(el))
    el3 = tr[tr["cohort_eligible"] & tr["v6_total"].notna()]
    r3 = el3["total"].to_numpy(float) - el3["v6_total"].to_numpy(float)
    m3 = float(np.median(r3))
    s0, c1 = fit_scale_t3(r3 - m3, el3[f"p40_{t1['variant']}"].to_numpy(float), el3["sample"].to_numpy(float))
    return {
        "held_out": held_out,
        "fit_seasons": train_seasons,
        "n_train_t1": len(tr),
        "t1": t1,
        "t2": {
            "fit": fit2,
            "features": list(T2_FEATURES),
            "alpha": T2_ALPHA,
            "n_train": len(el),
            "sd": cp.sd_by_bucket(r2, el["sample"].to_numpy()),
        },
        "t3": {"median_shift": m3, "s0": s0, "c1": c1, "n_train": len(el3)},
    }


def predict_rows(h: pd.DataFrame, f: Mapping[str, Any]) -> dict[str, np.ndarray]:
    t1 = f["t1"]
    b = cp.sample_bucket(h["sample"].to_numpy())
    mu1 = t1_apply(h, t1["variant"], t1["margin_scales"], t1["c_ot"])
    h = h.assign(t1_total=mu1)
    ok2 = h["v6_total"].notna().to_numpy() & h["league_level"].notna().to_numpy()
    mu2 = np.full(len(h), np.nan)
    mu2[ok2] = ridge_predict(f["t2"]["fit"], t2_design(h[ok2]))
    t3 = f["t3"]
    mu3 = h["v6_total"].to_numpy(float) + t3["median_shift"]
    sd3 = t3_sd(t3["s0"], t3["c1"], h[f"p40_{t1['variant']}"].to_numpy(float), h["sample"].to_numpy(float))
    return {
        "T1_tempo_eff_total": (mu1, np.array(t1["total_sd"])[b]),
        "T2_total_combiner": (mu2, np.array(f["t2"]["sd"])[b]),
        "T3_total_scale": (mu3, sd3),
    }


# ------------------------------------------------------------------------------------------- development
def dev_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(working frame, served v7 submission rows). The working frame keeps outcomes ONLY as columns that the
    fold code restricts to training seasons; the line itself is reduced to the cohort-eligibility flag."""
    import grader
    import grader_v3

    a = grader.load_archive(DEV_SEASONS)
    v7 = grader.baseline_predictions(a)["v7_served"]
    comp = pd.read_json(DEV_COMPONENTS, lines=True, compression="gzip", dtype={"event_id": str})
    comp = comp.drop(columns=["season", "game_date"])
    keep = ["event_id", "season", "game_date", "neutral", "d1", "sample", "v6_total", "margin", "total"]
    df = a[keep].copy()
    df["cohort_eligible"] = (a["line"].notna() & a["total_line"].notna()).to_numpy()
    df = df[df["d1"]].merge(comp, on="event_id", how="inner")
    per = grader_v3.load_periods(df["event_id"])
    df["n_ot"] = df["event_id"].map(lambda e: per[e]["n_ot"] if e in per else np.nan)
    levels = league_levels(dev_history(), set(zip(df["season"].astype(int), df["game_date"], strict=True)))
    df["league_level"] = [levels[(int(s), d)] for s, d in zip(df["season"], df["game_date"], strict=True)]
    return df, v7


def cmd_predict_dev(_args: argparse.Namespace) -> int:
    import grader
    import grader_v3

    df, v7 = dev_frame()
    folds: dict[int, dict[str, Any]] = {}
    parts: dict[str, list[pd.DataFrame]] = {g: [] for g in GRID_DIRS}
    v7i = v7.set_index("event_id")
    for s in DEV_SEASONS:
        f = fold_fit(df, s, DEV_SEASONS)
        folds[s] = f
        h = df[df["season"] == s].drop(columns=["margin", "total", "n_ot"])  # held-out outcomes never reach
        h = h[h["event_id"].isin(v7i.index)]
        preds = predict_rows(h, f)
        for grid, (mu, sd) in preds.items():
            base = v7i.loc[h["event_id"]].reset_index()
            base = base.drop(columns=["mu_total", "scale_total", "dist_total"])
            base["mu_total"] = np.round(mu, 4)
            base["scale_total"] = sd
            base["dist_total"] = "normal"
            parts[grid].append(base[np.isfinite(base["mu_total"]) & (base["scale_total"] > 0)])
        print(
            s,
            "T1 variant",
            f["t1"]["variant"],
            "c_ot",
            round(f["t1"]["c_ot"], 3),
            "T2 beta",
            np.round(f["t2"]["fit"]["beta"], 3).tolist(),
            "T3",
            round(f["t3"]["median_shift"], 3),
            round(f["t3"]["s0"], 3),
            round(f["t3"]["c1"], 3),
            flush=True,
        )
    code = {
        "scripts/ncaab_research/candidates/t_totals_261121.py": sha256_file(Path(__file__)),
        "scripts/ncaab_research/candidates/c_ppp_261120.py": sha256_file(Path(cp.__file__)),
    }
    sources = {
        str(DEV_COMPONENTS.relative_to(REPO)).replace("\\", "/"): sha256_file(DEV_COMPONENTS),
        str(cp.BOX_CACHE.relative_to(REPO)).replace("\\", "/"): sha256_file(cp.BOX_CACHE),
        "data/research/ncaab_market/scoreboard.jsonl": sha256_file(grader.nr.SCOREBOARD),
        "data/research/ncaab_market/v7ms_components.jsonl": sha256_file(grader.COMPONENTS),
        "data/research/ncaab_external/cbbd/games": "CBBD games cache, period scores (zero API calls)",
    }
    labels = {
        "T1_tempo_eff_total": "T1 C1 ppp x tempo per 40 + overtime expectation, variant by total RMSE v1",
        "T2_total_combiner": "T2 ridge [v6, T1, early x v6, early x T1, neutral, PIT league level] v1",
        "T3_total_scale": "T3 v6 + training median residual, normal sd s0 sqrt(poss/68)(1 + c1 early) v1",
    }
    fits = {str(s): f["fit_seasons"] for s, f in folds.items()}
    for grid, d in GRID_DIRS.items():
        pred = pd.concat(parts[grid], ignore_index=True).sort_values(["game_date", "event_id"])
        d.mkdir(parents=True, exist_ok=True)
        path = d / "preds.jsonl"
        pred.to_json(path, orient="records", lines=True)
        meta = {
            "candidate": labels[grid],
            "grid_name": grid,
            "protocol_sha256": grader.PROTOCOL_SHA256,
            "addendum_v3_sha256": grader_v3.ADDENDUM_V3_SHA256,
            "code_sha256": hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest(),
            "code_files_sha256": code,
            "fit_seasons_by_predicted_season": fits,
            "history_cutoff": HISTORY_CUTOFF,
            "data_sources": sources,
            "folds": {str(s): f for s, f in folds.items()},
        }
        (d / "preds.meta.json").write_text(json.dumps(meta, indent=1, default=float), encoding="utf-8")
        print(grid, len(pred), "rows; sha256", sha256_file(path))
    n_ot_cov = Counter(df["n_ot"].notna())
    print("periods known on dev D1 rows:", dict(n_ot_cov))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("walk-dev", "predict-dev"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    return {"walk-dev": cmd_walk_dev, "predict-dev": cmd_predict_dev}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
