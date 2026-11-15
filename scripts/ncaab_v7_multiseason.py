"""NCAAB v7 multi-season re-fit and evaluation, SHADOW ONLY (quick 261115-m5r).

Reads the archives written by quick 261115-1m8 (data/research/ncaab_market/, untracked), calls v6 read-only,
and writes only outputs/ncaab/ncaab_v7_multiseason.{md,json} plus untracked caches. No ledger writes (the
runtime ledger is opened mode=ro), no config or promotion change, v7 stays unregistered.

Stages (repo root; ``OPENBLAS_NUM_THREADS=1 PYTHONPATH=src:scripts .venv/Scripts/python scripts/ncaab_v7_multiseason.py STAGE``):
    components  outcome-free as-of-map replay of v6 and the ridge grid for every game day (cache)
    gate        today's-map v6 must equal the existing PIT projections exactly (no outcome read)
    fit         reads outcomes of the FIT seasons only; selects, fits and freezes the config
    final       the single read of 2024-25, the contaminated line-free windows, the ledger checks, the report

The pinned rule lives in ncaab_v7_multiseason_pin.py and was committed before any outcome was joined.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import ncaab_residual as nr
from ncaab_v7_multiseason_pin import PIN, PIN_SHA256

from model_prediction.models.ncaab_v7_shadow import student_t_scale_mle

DATA = REPO / "data" / "research" / "ncaab_market"
OUT = REPO / "outputs" / "ncaab"
COMPONENTS = DATA / "v7ms_components.jsonl"
FROZEN = OUT / "ncaab_v7_multiseason_frozen.json"
FIT_SEASONS = tuple(PIN["windows_by_espn_season_year"]["fit"])
DECISION_SEASONS = tuple(PIN["windows_by_espn_season_year"]["decision"])
STAGE_MAX_SEASON = {"components": 0, "gate": 0, "fit": max(FIT_SEASONS), "final": 2027}


def load_outcomes(stage: str) -> dict[str, dict[str, Any]]:
    """event id -> scores, restricted to the seasons the stage is allowed to read (structural guard)."""
    cap = STAGE_MAX_SEASON[stage]
    if cap == 0:
        raise RuntimeError(f"stage {stage!r} may not read outcomes")
    out: dict[str, dict[str, Any]] = {}
    for e in nr.load_scoreboard_events():
        if int(e["season_year"]) < 2023 or int(e["season_year"]) > cap:
            continue
        out[e["event_id"]] = {
            "margin": int(e["home_score"]) - int(e["away_score"]),
            "total": int(e["home_score"]) + int(e["away_score"]),
        }
    return out


# ----------------------------------------------------------------------------------------- components
def _worker(days: list[str]) -> list[dict[str, Any]]:
    from model_prediction.domain import EASTERN
    from model_prediction.models.basketball import UpcomingGame
    from model_prediction.models.ncaab_v7_multiseason import NCAABV7MultiSeasonModel, build_asof_conferences

    events = nr.load_scoreboard_events()
    games = nr.to_game_records(events)
    order = sorted(range(len(games)), key=lambda i: games[i].start)
    games = [games[i] for i in order]
    starts = [g.start for g in games]
    neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
    asof = os.environ.get("V7MS_TODAY_MAP") != "1"
    model = NCAABV7MultiSeasonModel(build_asof_conferences(events), stats_root=nr.STATS_ROOT, asof_map=asof)
    rows: list[dict[str, Any]] = []
    for day in days:
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN)
        hist = games[: bisect.bisect_left(starts, cutoff)]
        todays = [g for g in by_day.get(day, []) if int(g.season_year or 0) >= 2023]
        if len(hist) < 20 or not todays:
            continue
        up = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=neutral[g.event_id],
            )
            for g in todays
        ]
        by_id = {g.event_id: g for g in todays}
        for row in model.components(hist, up):
            g = by_id[row["event_id"]]
            rows.append(
                {**row, "day": day, "season_year": int(g.season_year or 0), "start": g.event_start_utc}
            )
    return rows


def cmd_components(args: argparse.Namespace) -> int:
    events = nr.load_scoreboard_events()
    days = sorted({nr.et_day(e["start_utc"]) for e in events if e["season_year"] >= 2023})
    if args.limit_days:
        days = days[: args.limit_days]
    if args.stride > 1:
        days = days[:: args.stride]
    # interleaved chunks balance the cost (late-season days carry the longest history)
    chunks = [days[i :: args.workers] for i in range(args.workers)]
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        parts = list(ex.map(_worker, chunks))
    rows = sorted((r for p in parts for r in p), key=lambda r: (r["start"], r["event_id"]))
    path = Path(args.out) if args.out else COMPONENTS
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"components {len(rows)} rows, {len(days)} days, {time.time() - t0:.0f}s -> {path}", flush=True)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """With today's map the replay's v6 margin must equal the existing PIT projections (no outcome read)."""
    proj = {r["event_id"]: r for r in nr._read_jsonl(nr.PROJECTIONS)}
    path = Path(args.components)
    comps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    diffs = [abs(c["v6_mean"] - proj[c["event_id"]]["sp_mean"]) for c in comps if c["event_id"] in proj]
    tdiffs = [
        abs(c["v6_total"] - proj[c["event_id"]]["tp_mean"])
        for c in comps
        if c["event_id"] in proj and "v6_total" in c and "tp_mean" in proj[c["event_id"]]
    ]
    res = {
        "rows_compared": len(diffs),
        "spread_max_abs_diff": max(diffs) if diffs else None,
        "total_max_abs_diff": max(tdiffs) if tdiffs else None,
    }
    print("gate (today's map vs v6_projections.jsonl)", res)
    return 0 if diffs and max(diffs) < 0.011 and (not tdiffs or max(tdiffs) < 0.011) else 1


# ----------------------------------------------------------------------------------------- model math
BUCKET_CUTS = (1, 3, 8)  # sample buckets 0 | 1-2 | 3-7 | 8+
N_BUCKETS = 4
TAIL_KNOT = 12.0
FORMS = ("F1", "F2", "F3")
FORM_MARGIN = 0.01  # a more complex form must beat the simpler one by more than this RMSE
TOTAL_MARGIN = 0.05
DF_T = 8.0


def sbucket(sample: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BUCKET_CUTS), np.asarray(sample), side="right")


def tail_term(v6: np.ndarray) -> np.ndarray:
    return np.sign(v6) * np.maximum(np.abs(v6) - TAIL_KNOT, 0.0)


def form_design(form: str, ridge: np.ndarray, v6: np.ndarray, sample: np.ndarray) -> np.ndarray:
    onehot = np.eye(N_BUCKETS)[sbucket(sample)]
    t = tail_term(v6)
    if form == "F1":
        return np.column_stack([ridge, t])
    if form == "F2":
        return np.column_stack([onehot * ridge[:, None], t])
    if form == "F3":
        return np.column_stack([onehot * ridge[:, None], t, onehot])
    raise KeyError(form)


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.solve(x.T @ x + 1e-6 * np.eye(x.shape[1]), x.T @ y)


def is_d1(df: pd.DataFrame) -> pd.Series:
    return df["d1_home"].astype(bool) & df["d1_away"].astype(bool)


def fit_config(train: pd.DataFrame, variant: str, form: str, total_mode: str) -> dict[str, Any]:
    """Fit every v7 coefficient and scale on ``train`` rows (which carry margin / total)."""
    d1 = train[is_d1(train) & train[variant].notna()]
    x = form_design(form, d1[variant].to_numpy(float), d1["v6_mean"].to_numpy(float), d1["sample"].to_numpy())
    coef = ols(x, d1["margin"].to_numpy(float))
    resid = d1["margin"].to_numpy(float) - x @ coef
    b = sbucket(d1["sample"].to_numpy())
    scales = [student_t_scale_mle(resid[b == k], DF_T) for k in range(N_BUCKETS)]
    nd = train[~is_d1(train)]
    xn = np.column_stack([nd["v6_mean"].to_numpy(float), np.ones(len(nd))])
    cn = ols(xn, nd["margin"].to_numpy(float))
    rn = nd["margin"].to_numpy(float) - xn @ cn
    cfg: dict[str, Any] = {
        "variant": variant,
        "form": form,
        "coef": coef.tolist(),
        "scales": scales,
        "nond1": {
            "alpha": float(cn[0]),
            "beta": float(cn[1]),
            "scale": student_t_scale_mle(rn, DF_T),
            "n": len(nd),
        },
        "n_d1": len(d1),
        "total": {"mode": total_mode},
    }
    tr = train[train["v6_total"].notna()]
    bt = sbucket(tr["sample"].to_numpy())
    alpha, beta, tscales = [], [], []
    for k in range(N_BUCKETS):
        sel = bt == k
        v, y = tr["v6_total"].to_numpy(float)[sel], tr["total"].to_numpy(float)[sel]
        if total_mode == "recal" and sel.sum() >= 30:
            c = ols(np.column_stack([v, np.ones(sel.sum())]), y)
        else:
            c = np.array([1.0, 0.0])
        alpha.append(float(c[1]))
        beta.append(float(c[0]))
        tscales.append(student_t_scale_mle(y - (c[0] * v + c[1]), DF_T))
    cfg["total"].update({"alpha": alpha, "beta": beta, "scales": tscales})
    return cfg


def apply_config(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """v7 mean, t-scale, total mean and total scale for every row (non-D1 rows use the linear rule)."""
    mean = np.full(len(df), np.nan)
    scale = np.full(len(df), np.nan)
    d1 = is_d1(df).to_numpy() & df[cfg["variant"]].notna().to_numpy()
    if d1.any():
        sub = df[d1]
        x = form_design(
            cfg["form"],
            sub[cfg["variant"]].to_numpy(float),
            sub["v6_mean"].to_numpy(float),
            sub["sample"].to_numpy(),
        )
        mean[d1] = x @ np.array(cfg["coef"])
        scale[d1] = np.array(cfg["scales"])[sbucket(sub["sample"].to_numpy())]
    nd = ~is_d1(df).to_numpy()
    if nd.any():
        mean[nd] = cfg["nond1"]["alpha"] * df["v6_mean"].to_numpy(float)[nd] + cfg["nond1"]["beta"]
        scale[nd] = cfg["nond1"]["scale"]
    tot = np.full(len(df), np.nan)
    tscale = np.full(len(df), np.nan)
    has = df["v6_total"].notna().to_numpy()
    bt = sbucket(df["sample"].to_numpy())
    a, b, ts = (np.array(cfg["total"][k]) for k in ("alpha", "beta", "scales"))
    tot[has] = a[bt[has]] + b[bt[has]] * df["v6_total"].to_numpy(float)[has]
    tscale[has] = ts[bt[has]]
    return pd.DataFrame(
        {"v7_mean": mean, "v7_scale": scale, "v7_total": tot, "v7_total_scale": tscale}, index=df.index
    )


def load_components(max_season_year: int) -> pd.DataFrame:
    df = pd.read_json(COMPONENTS, lines=True, dtype={"event_id": str})
    df["event_id"] = df["event_id"].astype(str)
    return df[df["season_year"] <= max_season_year].copy()


def ridge_keys(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("rg_c")]


def rmse(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(a)))) if len(a) else float("nan")


# ----------------------------------------------------------------------------------------- fit stage
def with_outcomes(df: pd.DataFrame, outcomes: dict[str, dict[str, Any]]) -> pd.DataFrame:
    df = df[df["event_id"].isin(outcomes)].copy()
    df["margin"] = df["event_id"].map(lambda e: outcomes[e]["margin"])
    df["total"] = df["event_id"].map(lambda e: outcomes[e]["total"])
    return df


def cmd_fit(_args: argparse.Namespace) -> int:
    """Select the ridge variant and combiner form by leave-one-season-out, then freeze everything."""
    outcomes = load_outcomes("fit")
    df = with_outcomes(load_components(max(FIT_SEASONS)), outcomes)
    assert set(df["season_year"]) <= set(FIT_SEASONS), "fit stage read a non-fit season"
    d1 = df[is_d1(df)]
    # 1. ridge variant: raw ridge RMSE per fit season (no fitted parameter)
    table: dict[str, Any] = {}
    for key in ridge_keys(df):
        sub = d1[d1[key].notna()]
        per = {int(s): rmse((g["margin"] - g[key]).to_numpy(float)) for s, g in sub.groupby("season_year")}
        table[key] = {"rmse_by_season": per, "score": float(np.mean(list(per.values())))}
    variant = min(table, key=lambda k: (table[k]["score"], k))
    # 2. combiner form by leave-one-season-out
    form_scores: dict[str, float] = {}
    for form in FORMS:
        errs = []
        for hold in FIT_SEASONS:
            cfg = fit_config(df[df["season_year"] != hold], variant, form, "identity")
            test = df[(df["season_year"] == hold) & is_d1(df)]
            pred = apply_config(test, cfg)["v7_mean"].to_numpy(float)
            errs.append(rmse(test["margin"].to_numpy(float) - pred))
        form_scores[form] = float(np.mean(errs))
    form = FORMS[0]
    for cand in FORMS[1:]:
        if form_scores[form] - form_scores[cand] > FORM_MARGIN:
            form = cand
    # 3. totals: recalibration only when it beats v6 as-of by more than TOTAL_MARGIN (leave-one-season-out)
    base_err, recal_err = [], []
    for hold in FIT_SEASONS:
        test = df[(df["season_year"] == hold) & df["v6_total"].notna()]
        cfg = fit_config(df[df["season_year"] != hold], variant, form, "recal")
        base_err.append(rmse(test["total"].to_numpy(float) - test["v6_total"].to_numpy(float)))
        recal_err.append(
            rmse(test["total"].to_numpy(float) - apply_config(test, cfg)["v7_total"].to_numpy(float))
        )
    total_mode = "recal" if np.mean(base_err) - np.mean(recal_err) > TOTAL_MARGIN else "identity"
    cfg_final = fit_config(df, variant, form, total_mode)
    # leave-one-season-out predictions for the fit rows (the market rule's fit window)
    loso_parts = []
    for hold in FIT_SEASONS:
        cfg_h = fit_config(df[df["season_year"] != hold], variant, form, total_mode)
        test = df[df["season_year"] == hold]
        pred = apply_config(test, cfg_h)
        pred["event_id"] = test["event_id"].to_numpy()
        loso_parts.append(pred)
    pd.concat(loso_parts).to_json(DATA / "v7ms_loso_fit_preds.jsonl", orient="records", lines=True)
    selection = {
        "ridge_variant_table": table,
        "ridge_variant_selected": variant,
        "form_loso_rmse": form_scores,
        "form_selected": form,
        "total_loso_rmse_v6_asof": float(np.mean(base_err)),
        "total_loso_rmse_recal": float(np.mean(recal_err)),
        "total_mode": total_mode,
    }
    frozen = {
        "pin_id": PIN["id"],
        "pin_sha256": PIN_SHA256,
        "components_sha256": nr.sha256_file(COMPONENTS),
        "fit_seasons": list(FIT_SEASONS),
        "fit_rows": len(df),
        "config": cfg_final,
        "selection": selection,
    }
    FROZEN.write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    best5 = sorted(table, key=lambda k: table[k]["score"])[:5]
    print("top ridge variants", [(k, round(table[k]["score"], 4)) for k in best5])
    print(
        "selected", variant, "form", form, "form loso rmse", {k: round(v, 4) for k, v in form_scores.items()}
    )
    print(
        "coef", [round(c, 4) for c in cfg_final["coef"]], "scales", [round(x, 3) for x in cfg_final["scales"]]
    )
    print(
        "total loso rmse v6 as-of / recal",
        round(float(np.mean(base_err)), 4),
        round(float(np.mean(recal_err)), 4),
        total_mode,
    )
    print("wrote", FROZEN)
    return 0


# ----------------------------------------------------------------------------------------- final stage
LINE_SEASONS = (*FIT_SEASONS, *DECISION_SEASONS)  # the only seasons whose lines are ever joined to outcomes
CONTAMINATED = {
    2026: "2025-26 (contaminated: shaped v7 in 1m7)",
    2027: "2026-27 partial (contaminated: 1m7 holdout)",
}
LINE_BUCKETS = [("<5", 0, 5), ("5-10", 5, 10), ("10-15", 10, 15), ("15-20", 15, 20), (">=20", 20, 1e9)]
GAMES_BUCKETS = [("0", 0, 1), ("1-2", 1, 3), ("3-7", 3, 8), ("8-15", 8, 16), ("16+", 16, 10**6)]
MODELS_SPREAD = ("v7", "v6_asof", "v6_served", "elo", "ridge")
N_BOOT = 4000
BOOT_SEED = 17


def t_cdf(x: np.ndarray) -> np.ndarray:
    from scipy import stats

    return stats.t.cdf(x, df=DF_T)


def win_prob(mean: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return np.clip(1.0 - t_cdf((0.5 - mean) / scale), 0.0001, 0.9999)


def home_cover_prob(mean: np.ndarray, scale: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(home covers) given the market home margin ``line``; the push mass is excluded on integer lines."""
    integer = np.mod(line, 1.0) == 0
    away_int = t_cdf((line - 0.5 - mean) / scale)
    home_int = 1.0 - t_cdf((line + 0.5 - mean) / scale)
    home_half = 1.0 - t_cdf((line - mean) / scale)
    return np.where(integer, home_int / np.maximum(home_int + away_int, 1e-12), home_half)


def load_lines() -> dict[str, dict[str, float | None]]:
    """event id -> implied home margin and total line (only seasons allowed to meet outcomes)."""
    events = {e["event_id"]: int(e["season_year"]) for e in nr.load_scoreboard_events()}
    out: dict[str, dict[str, float | None]] = {}
    for rec in nr._read_jsonl(nr.LINES):
        eid = str(rec["event_id"])
        if events.get(eid) not in LINE_SEASONS:
            continue
        sel = rec["selected"]
        hs = sel.get("home_spread_line")
        out[eid] = {
            "line": -float(hs) if hs is not None else None,
            "total_line": float(sel["total_line"]) if sel.get("total_line") is not None else None,
        }
    return out


def day_boot(dates: list[str]) -> tuple[np.ndarray, int, np.ndarray]:
    inv, d = nr._clusters(dates)
    return inv, d, nr._boot_counts(d, np.random.default_rng(BOOT_SEED))


def boot_stat(
    dates: list[str], num: dict[str, np.ndarray], fn: Any, level: float = 0.05
) -> tuple[float, list[float]]:
    """Cluster-bootstrap a statistic of per-day sums: ``fn`` receives {name: summed array over resample}."""
    inv, d, cnt = day_boot(dates)
    agg = {k: np.bincount(inv, weights=v, minlength=d) for k, v in num.items()}
    point = fn({k: np.array([a.sum()]) for k, a in agg.items()})[0]
    samples = fn({k: cnt @ a for k, a in agg.items()})
    return float(point), nr._percentiles(samples, level)


def err_summary(y: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    ok = ~np.isnan(pred) & ~np.isnan(y)
    if not ok.any():
        return {"n": 0, "rmse": None, "bias": None}
    e = y[ok] - pred[ok]
    return {"n": int(ok.sum()), "rmse": round(rmse(e), 3), "bias": round(float(e.mean()), 3)}


def add_models(df: pd.DataFrame, cfg: dict[str, Any], elo_scale: float, ridge_scale: float) -> pd.DataFrame:
    df = df.copy()
    df = pd.concat([df, apply_config(df, cfg)], axis=1)
    proj = {r["event_id"]: r for r in nr._read_jsonl(nr.PROJECTIONS)}
    df["sp_mean"] = df["event_id"].map(lambda e: proj.get(e, {}).get("sp_mean", np.nan))
    df["sp_sd"] = df["event_id"].map(lambda e: proj.get(e, {}).get("sp_sd", np.nan))
    df["tp_mean"] = df["event_id"].map(lambda e: proj.get(e, {}).get("tp_mean", np.nan))
    pred = {
        "v7": (df["v7_mean"], df["v7_scale"]),
        "v6_asof": (df["v6_mean"], df["v6_scale"]),
        "v6_served": (df["sp_mean"], df["sp_sd"]),
        "elo": (df["elo_margin"], pd.Series(elo_scale, index=df.index)),
        "ridge": (df[cfg["variant"]], pd.Series(ridge_scale, index=df.index)),
    }
    for name, (mean, scale) in pred.items():
        df[f"{name}_mean"] = mean.astype(float)
        df[f"{name}_scale"] = scale.astype(float)
        df[f"{name}_pwin"] = win_prob(df[f"{name}_mean"].to_numpy(float), df[f"{name}_scale"].to_numpy(float))
    df["v6_asof_pwin"] = df["v6_p_home"]  # v6's own served win probability (logistic blend)
    df["v6_asof_total"] = df["v6_total"]
    df["v6_served_total"] = df["tp_mean"]
    return df


def sample_bucket_name(sample: float) -> str:
    for name, lo, hi in GAMES_BUCKETS:
        if lo <= sample < hi:
            return name
    return "16+"


def games_table(df: pd.DataFrame, models: tuple[str, ...], kind: str = "margin") -> dict[str, Any]:
    """RMSE and bias by games-played bucket for margin or total predictions (line-free)."""
    out: dict[str, Any] = {}
    y = df["margin" if kind == "margin" else "total"].to_numpy(float)
    for label, lo, hi in [("all", 0, 10**6), *GAMES_BUCKETS]:
        mask = ((df["sample"] >= lo) & (df["sample"] < hi)).to_numpy()
        cell: dict[str, Any] = {}
        for m in models:
            col = f"{m}_mean" if kind == "margin" else f"{m}_total"
            if col in df:
                cell[m] = err_summary(y[mask], df[col].to_numpy(float)[mask])
        out[label] = cell
    return out


def win_summary(df: pd.DataFrame, models: tuple[str, ...]) -> dict[str, Any]:
    y = (df["margin"] > 0).to_numpy(float)
    out: dict[str, Any] = {}
    for m in models:
        p = df[f"{m}_pwin"].to_numpy(float)
        ok = ~np.isnan(p)
        hi = ok & ((p >= 0.8) | (p <= 0.2))
        fav_hit = np.where(p >= 0.5, y, 1 - y)
        out[m] = {
            "n": int(ok.sum()),
            "brier": round(float(np.mean((p[ok] - y[ok]) ** 2)), 4) if ok.any() else None,
            "n_p_ge_0.8": int(hi.sum()),
            "hit_p_ge_0.8": round(float(fav_hit[hi].mean()), 3) if hi.any() else None,
            "mean_fav_p_p_ge_0.8": round(float(np.maximum(p, 1 - p)[hi].mean()), 3) if hi.any() else None,
        }
    return out


def cover_summary(df: pd.DataFrame, models: tuple[str, ...]) -> dict[str, Any]:
    """Cover-probability calibration on lined games (decision/fit windows only): Brier, reliability, p >= 0.8."""
    line = df["line"].to_numpy(float)
    push = df["margin"].to_numpy(float) == line
    keep = ~np.isnan(line) & ~push
    y = (df["margin"].to_numpy(float) > line).astype(float)
    out: dict[str, Any] = {}
    for m in models:
        mean, scale = df[f"{m}_mean"].to_numpy(float), df[f"{m}_scale"].to_numpy(float)
        with np.errstate(invalid="ignore"):
            p_home = home_cover_prob(mean, scale, line)
        ok = keep & ~np.isnan(p_home)
        fav_p = np.maximum(p_home, 1 - p_home)
        fav_hit = np.where(p_home >= 0.5, y, 1 - y)
        bins = {}
        for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]:
            sel = ok & (fav_p >= lo) & (fav_p < hi)
            bins[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = {
                "n": int(sel.sum()),
                "mean_p": round(float(fav_p[sel].mean()), 3) if sel.any() else None,
                "hit": round(float(fav_hit[sel].mean()), 3) if sel.any() else None,
            }
        out[m] = {
            "n": int(ok.sum()),
            "home_cover_brier": round(float(np.mean((p_home[ok] - y[ok]) ** 2)), 4) if ok.any() else None,
            "reliability_favoured_side": bins,
        }
    return out


def line_bucket_table(df: pd.DataFrame, models: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    absl = df["line"].abs().to_numpy(float)
    y = df["margin"].to_numpy(float)
    for label, lo, hi in LINE_BUCKETS:
        mask = (absl >= lo) & (absl < hi)
        cell = {m: err_summary(y[mask], df[f"{m}_mean"].to_numpy(float)[mask]) for m in models}
        cell["market_line"] = err_summary(y[mask], df["line"].to_numpy(float)[mask])
        out[label] = cell
    return out


def paired_rmse_delta(df: pd.DataFrame, a: str, b: str, mask: np.ndarray) -> dict[str, Any]:
    """RMSE(a) - RMSE(b) over ``mask`` rows, 90% day-cluster bootstrap (negative favours a)."""
    sub = df[mask & df[f"{a}_mean"].notna().to_numpy() & df[f"{b}_mean"].notna().to_numpy()]
    y = sub["margin"].to_numpy(float)
    ea = (y - sub[f"{a}_mean"].to_numpy(float)) ** 2
    eb = (y - sub[f"{b}_mean"].to_numpy(float)) ** 2

    def fn(s: dict[str, np.ndarray]) -> np.ndarray:
        return np.sqrt(s["ea"] / s["n"]) - np.sqrt(s["eb"] / s["n"])

    point, ci = boot_stat(sub["day"].tolist(), {"ea": ea, "eb": eb, "n": np.ones(len(sub))}, fn)
    return {"n": len(sub), "delta_rmse": round(point, 3), "ci90": [round(c, 3) for c in ci]}


def paired_brier_delta(df: pd.DataFrame, a: str, b: str, col: str = "pwin") -> dict[str, Any]:
    sub = df[df[f"{a}_{col}"].notna() & df[f"{b}_{col}"].notna()]
    y = (sub["margin"] > 0).to_numpy(float)
    da = (sub[f"{a}_{col}"].to_numpy(float) - y) ** 2
    db = (sub[f"{b}_{col}"].to_numpy(float) - y) ** 2

    def fn(s: dict[str, np.ndarray]) -> np.ndarray:
        return (s["da"] - s["db"]) / s["n"]

    point, ci = boot_stat(sub["day"].tolist(), {"da": da, "db": db, "n": np.ones(len(sub))}, fn)
    return {"n": len(sub), "delta_brier": round(point, 4), "ci90": [round(c, 4) for c in ci]}


def market_rule(
    name: str,
    pred_fit: np.ndarray,
    y_fit: np.ndarray,
    pred_val: np.ndarray,
    y_val: np.ndarray,
    dates_val: list[str],
    k: int,
) -> dict[str, Any]:
    """ncaab-residual-rule-1 mechanics with x = prediction - line; ``pred_*`` are already (model - line)."""
    fit = nr.ridge_fit(pred_fit[:, None], y_fit)
    shrunk = nr.ridge_predict(fit, pred_val[:, None])
    val = nr.window_stats(shrunk, y_val, dates_val, fit["ymean"], k, side=True)
    plain = nr.window_stats(pred_val, y_val, dates_val, fit["ymean"], 1)
    a = val["slope_ci_adj"][0] > 0
    b = val["mse_delta_ci_adj"][0] > 0
    return {
        "model": name,
        "n_fit": len(y_fit),
        "n_val": len(y_val),
        "standardised_coef": float(fit["beta"][0]),
        "validation": val,
        "plain_slope_actual_minus_line_on_model_minus_line": {
            "slope": plain["slope"],
            "ci_95": plain["slope_ci_95"],
        },
        "criteria": {"a_slope_adj_lower_gt_0": bool(a), "b_mse_delta_adj_lower_gt_0": bool(b)},
        "accepted": bool(a and b),
        "k": k,
    }


def cmd_final(_args: argparse.Namespace) -> int:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    if frozen["pin_sha256"] != PIN_SHA256:
        raise SystemExit("frozen config carries a different pin hash; refusing to read the decision window")
    if frozen["components_sha256"] != nr.sha256_file(COMPONENTS):
        raise SystemExit("components changed since the config was frozen; refusing")
    cfg = frozen["config"]
    variant = cfg["variant"]
    outcomes = load_outcomes("final")
    comps = load_components(2027)
    df = with_outcomes(comps, outcomes)
    lines = load_lines()
    # comparator scales fitted on the FIT seasons only
    fit_rows = df[df["season_year"].isin(FIT_SEASONS) & is_d1(df)]
    elo_scale = student_t_scale_mle((fit_rows["margin"] - fit_rows["elo_margin"]).to_numpy(float), DF_T)
    rf = fit_rows[fit_rows[variant].notna()]
    ridge_scale = student_t_scale_mle((rf["margin"] - rf[variant]).to_numpy(float), DF_T)
    df = add_models(df, cfg, elo_scale, ridge_scale)
    # fit-window v7 predictions are the leave-one-season-out ones (v7 was fitted on those outcomes)
    loso = pd.read_json(DATA / "v7ms_loso_fit_preds.jsonl", lines=True, dtype={"event_id": str}).set_index(
        "event_id"
    )
    fit_idx = df["season_year"].isin(FIT_SEASONS)
    for col in ("v7_mean", "v7_scale", "v7_total", "v7_total_scale"):
        df.loc[fit_idx, col] = df.loc[fit_idx, "event_id"].map(loso[col]).to_numpy()
    df["v7_pwin"] = win_prob(df["v7_mean"].to_numpy(float), df["v7_scale"].to_numpy(float))
    df["line"] = df["event_id"].map(lambda e: lines.get(e, {}).get("line", np.nan))
    df["total_line"] = df["event_id"].map(lambda e: lines.get(e, {}).get("total_line", np.nan))
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["total_line"] = pd.to_numeric(df["total_line"], errors="coerce")
    rep: dict[str, Any] = {
        "pin_id": PIN["id"],
        "pin_sha256": PIN_SHA256,
        "frozen_config": cfg,
        "selection": frozen["selection"],
        "comparator_scales": {"elo": elo_scale, "ridge": ridge_scale},
        "rows": {int(s): int((df["season_year"] == s).sum()) for s in sorted(df["season_year"].unique())},
    }
    dec = df[df["season_year"].isin(DECISION_SEASONS)]
    dec_d1 = dec[is_d1(dec)]
    # ---- scorecard A (decision window)
    lined = dec_d1[dec_d1["line"].notna()]
    rep["scorecard_a"] = {
        "window": "2024-25 (season year 2025), D1-vs-D1 as-of games",
        "n_d1_games": len(dec_d1),
        "n_lined_d1_games": len(lined),
        "margin_by_games_played": games_table(dec_d1, MODELS_SPREAD),
        "margin_by_abs_line_lined": line_bucket_table(lined, MODELS_SPREAD),
        "cover_calibration_lined": cover_summary(lined, MODELS_SPREAD),
        "win_probability_all_d1": win_summary(dec_d1, MODELS_SPREAD),
        "total_by_games_played": games_table(dec, ("v6_served", "v6_asof", "v7"), kind="total"),
        "bootstrap": {
            "v7_minus_v6_asof_d1_rmse": paired_rmse_delta(
                dec_d1, "v7", "v6_asof", np.ones(len(dec_d1), bool)
            ),
            "v7_minus_v6_asof_d1_rmse_games_0_2": paired_rmse_delta(
                dec_d1, "v7", "v6_asof", (dec_d1["sample"] < 3).to_numpy()
            ),
            "v7_minus_v6_asof_d1_rmse_games_3plus": paired_rmse_delta(
                dec_d1, "v7", "v6_asof", (dec_d1["sample"] >= 3).to_numpy()
            ),
            "v7_minus_ridge_d1_rmse": paired_rmse_delta(dec_d1, "v7", "ridge", np.ones(len(dec_d1), bool)),
            "v6_asof_minus_v6_served_d1_rmse_map_risk": paired_rmse_delta(
                dec_d1, "v6_asof", "v6_served", np.ones(len(dec_d1), bool)
            ),
            "v7_minus_v6_asof_win_brier": paired_brier_delta(dec_d1, "v7", "v6_asof"),
            "v7_minus_v6_served_win_brier": paired_brier_delta(dec_d1, "v7", "v6_served"),
        },
    }
    all_games = dec
    rep["scorecard_a"]["all_games_incl_non_d1"] = {
        "n": len(all_games),
        "margin": {
            m: err_summary(all_games["margin"].to_numpy(float), all_games[f"{m}_mean"].to_numpy(float))
            for m in MODELS_SPREAD
        },
        "non_d1_only": {
            m: err_summary(
                dec[~is_d1(dec)]["margin"].to_numpy(float), dec[~is_d1(dec)][f"{m}_mean"].to_numpy(float)
            )
            for m in MODELS_SPREAD
        },
    }
    b = rep["scorecard_a"]["bootstrap"]
    brier_pt = b["v7_minus_v6_asof_win_brier"]["delta_brier"]
    rep["scorecard_a"]["pass_criteria"] = {
        "i_rmse_upper_bound_lt_0": b["v7_minus_v6_asof_d1_rmse"]["ci90"][1] < 0,
        "ii_win_brier_point_le_0": brier_pt <= 0,
        "iii_rmse_0_2_point_lt_0": b["v7_minus_v6_asof_d1_rmse_games_0_2"]["delta_rmse"] < 0,
    }
    rep["scorecard_a"]["passed"] = all(rep["scorecard_a"]["pass_criteria"].values())
    cov = rep["scorecard_a"]["cover_calibration_lined"]["v7"]["reliability_favoured_side"]["0.8-1.0"]
    abs20 = rep["scorecard_a"]["margin_by_abs_line_lined"][">=20"]
    rep["scorecard_a"]["not_gating"] = {
        "v7_p_ge_0.8_cover_hit_within_0.05_n_ge_50": bool(
            cov["n"] >= 50 and cov["hit"] is not None and abs(cov["hit"] - cov["mean_p"]) <= 0.05
        ),
        "abs_line_ge_20_bias_v7_lt_v6_asof": bool(
            abs20["v7"]["bias"] is not None and abs(abs20["v7"]["bias"]) < abs(abs20["v6_asof"]["bias"])
        ),
    }
    # ---- fit window reference (v7 leave-one-season-out)
    fit = df[df["season_year"].isin(FIT_SEASONS)]
    fit_d1 = fit[is_d1(fit)]
    rep["fit_window_reference"] = {
        "note": "v7 is leave-one-season-out; elo/ridge scales and the ridge variant used these outcomes",
        "by_season_d1_margin": {
            int(s): {
                m: err_summary(g["margin"].to_numpy(float), g[f"{m}_mean"].to_numpy(float))
                for m in MODELS_SPREAD
            }
            for s, g in fit_d1.groupby("season_year")
        },
    }
    # ---- contaminated windows, line-free only
    cont: dict[str, Any] = {}
    for season, label in CONTAMINATED.items():
        w = df[df["season_year"] == season]
        wd = w[is_d1(w)]
        cont[label] = {
            "n_games": len(w),
            "n_d1": len(wd),
            "margin_by_games_played_d1": games_table(wd, MODELS_SPREAD),
            "win_probability_d1": win_summary(wd, MODELS_SPREAD),
            "total_by_games_played": games_table(w, ("v6_served", "v6_asof", "v7"), kind="total"),
            "v7_minus_v6_asof_d1_rmse": paired_rmse_delta(wd, "v7", "v6_asof", np.ones(len(wd), bool)),
            "v7_minus_v6_served_d1_rmse": paired_rmse_delta(wd, "v7", "v6_served", np.ones(len(wd), bool)),
        }
    rep["contaminated_line_free"] = cont
    # ---- scorecard B (decision window)
    rep["scorecard_b"] = scorecard_b(df, loso)
    # ---- mismatch cohort
    rep["mismatch"] = mismatch_block(df)
    # ---- ledger checks
    rep["ledger"] = ledger_block(df)
    out_json = OUT / "ncaab_v7_multiseason.json"
    out_json.write_text(json.dumps(rep, indent=2, sort_keys=True, default=_jsonable), encoding="utf-8")
    (OUT / "ncaab_v7_multiseason.md").write_text(render_md(rep), encoding="utf-8")
    print("scorecard A passed:", rep["scorecard_a"]["passed"], rep["scorecard_a"]["pass_criteria"])
    for mkt, blk in rep["scorecard_b"].items():
        print("scorecard B", mkt, {k: v["accepted"] for k, v in blk["models"].items()})
    print("wrote", out_json)
    return 0


def _jsonable(o: Any) -> Any:
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def scorecard_b(df: pd.DataFrame, loso: pd.DataFrame) -> dict[str, Any]:
    """Market-relative rule ncaab-v7-market-rule-2 on 2024-25 (K counts the nine models 1m8 already read)."""
    fit = df[df["season_year"].isin(FIT_SEASONS)]
    val = df[df["season_year"].isin(DECISION_SEASONS)]
    out: dict[str, Any] = {}
    # spread
    k_spread = 9 + 3
    block: dict[str, Any] = {"k": k_spread, "models": {}}
    for name, col in (("v7", "v7_mean"), ("ridge", "ridge_mean"), ("v6_asof", "v6_asof_mean")):
        f = fit[fit["line"].notna() & fit[col].notna()]
        v = val[val["line"].notna() & val[col].notna()]
        block["models"][name] = market_rule(
            name,
            (f[col] - f["line"]).to_numpy(float),
            (f["margin"] - f["line"]).to_numpy(float),
            (v[col] - v["line"]).to_numpy(float),
            (v["margin"] - v["line"]).to_numpy(float),
            v["day"].tolist(),
            k_spread,
        )
    out["spread"] = block
    # total
    k_total = 9 + 2
    block = {"k": k_total, "models": {}}
    for name, col in (("v6_asof_total", "v6_asof_total"), ("v7_total", "v7_total")):
        f = fit[fit["total_line"].notna() & fit[col].notna()]
        v = val[val["total_line"].notna() & val[col].notna()]
        block["models"][name] = market_rule(
            name,
            (f[col] - f["total_line"]).to_numpy(float),
            (f["total"] - f["total_line"]).to_numpy(float),
            (v[col] - v["total_line"]).to_numpy(float),
            (v["total"] - v["total_line"]).to_numpy(float),
            v["day"].tolist(),
            k_total,
        )
    out["total"] = block
    # descriptive RMSE vs the line on the same validation rows
    vv = val[val["line"].notna()]
    out["spread"]["descriptive_val_rmse"] = {
        "market_line": rmse((vv["margin"] - vv["line"]).to_numpy(float)),
        **{
            m: rmse((vv["margin"] - vv[f"{m}_mean"]).dropna().to_numpy(float))
            for m in ("v7", "v6_asof", "v6_served")
        },
        "n": len(vv),
    }
    vt = val[val["total_line"].notna() & val["v7_total"].notna()]
    out["total"]["descriptive_val_rmse"] = {
        "market_line": rmse((vt["total"] - vt["total_line"]).to_numpy(float)),
        "v6_asof_total": rmse((vt["total"] - vt["v6_asof_total"]).to_numpy(float)),
        "v7_total": rmse((vt["total"] - vt["v7_total"]).to_numpy(float)),
        "n": len(vt),
    }
    return out


def mismatch_block(df: pd.DataFrame) -> dict[str, Any]:
    """Home tier >= 1550 vs away tier <= 1400, both D1, non-neutral (pinned definition)."""
    cohort = is_d1(df) & ~df["neutral"].astype(bool) & (df["tier_home"] >= 1550) & (df["tier_away"] <= 1400)
    out: dict[str, Any] = {"definition": PIN["mismatch_cohort"]["definition"]}
    for season in (*DECISION_SEASONS, *CONTAMINATED):
        w = df[cohort & (df["season_year"] == season)]
        cell: dict[str, Any] = {
            "n": len(w),
            "margin": {
                m: err_summary(w["margin"].to_numpy(float), w[f"{m}_mean"].to_numpy(float))
                for m in MODELS_SPREAD
            },
            "win_probability": win_summary(w, MODELS_SPREAD),
        }
        if season in DECISION_SEASONS:
            lw = w[w["line"].notna()]
            cell["lined_n"] = len(lw)
            cell["market_line"] = err_summary(lw["margin"].to_numpy(float), lw["line"].to_numpy(float))
            cell["margin_lined"] = {
                m: err_summary(lw["margin"].to_numpy(float), lw[f"{m}_mean"].to_numpy(float))
                for m in MODELS_SPREAD
            }
            cell["cover_calibration"] = cover_summary(lw, MODELS_SPREAD)
        out[str(season)] = cell
    d = out[str(DECISION_SEASONS[0])]
    b7, b6 = d["margin"]["v7"], d["margin"]["v6_asof"]
    c7 = d["cover_calibration"]["v7"]["reliability_favoured_side"]["0.8-1.0"]
    c6 = d["cover_calibration"]["v6_asof"]["reliability_favoured_side"]["0.8-1.0"]
    smaller = abs(b7["bias"]) < abs(b6["bias"]) and b7["rmse"] < b6["rmse"]
    hit_ok = (c7["hit"] is not None and c6["hit"] is not None and c7["hit"] >= c6["hit"]) or c7["n"] == 0
    out["pinned_verdict"] = {
        "bias_and_rmse_smaller_than_v6_asof_2024_25": bool(smaller),
        "p_ge_0.8_cover_hit_not_below_v6_asof": bool(hit_ok),
        "fix_claimed": bool(smaller and hit_ok),
    }
    return out


def ledger_block(df: pd.DataFrame) -> dict[str, Any]:
    """Read-only runtime ledger: the non-D1 predicate on settled Flat v6 rows, and the mismatch-loss replay."""
    import sqlite3

    from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v6_model

    d1_names = set(ncaab_spread_v6_model().team_priors)
    uri = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        led = pd.read_sql(
            "select pick_id, market_type, model_id, event_start_utc, model_probability, result, pnl_units, settled_at_utc, "
            "decision_payload_json from ledger_records where sport='ncaab' and ledger_tier='flat' "
            "and status='settled' and model_id in ('ncaab-spread-v6','ncaab-total-v6')",
            conn,
        )
    payload = led["decision_payload_json"].map(json.loads)
    led["home"] = payload.map(lambda p: p.get("home_team"))
    led["away"] = payload.map(lambda p: p.get("away_team"))
    led["declined"] = ~(led["home"].isin(d1_names) & led["away"].isin(d1_names))
    won = led["pnl_units"] > 0
    lost = led["pnl_units"] < 0
    hi = led["model_probability"] >= 0.8
    res: dict[str, Any] = {
        "filter": "ledger_tier='flat' and status='settled' and model_id in (ncaab-spread-v6, ncaab-total-v6), read-only",
        "rows": len(led),
        "rows_by_market": {k: int(v) for k, v in led["market_type"].value_counts().items()},
        "rows_declined_by_non_d1_rule": int(led["declined"].sum()),
        "declined_by_market": {
            k: int(v) for k, v in led[led["declined"]]["market_type"].value_counts().items()
        },
        "declined_pnl_units": round(float(led[led["declined"]]["pnl_units"].sum()), 3),
        "all_rows_pnl_units": round(float(led["pnl_units"].sum()), 3),
        "losing_rows": int(lost.sum()),
        "losing_rows_declined": int((lost & led["declined"]).sum()),
        "p_ge_0.8_rows": int(hi.sum()),
        "p_ge_0.8_losing_rows": int((hi & lost).sum()),
        "p_ge_0.8_losing_rows_declined": int((hi & lost & led["declined"]).sum()),
        "p_ge_0.8_won_rows_declined": int((hi & won & led["declined"]).sum()),
    }
    first = led.sort_values("settled_at_utc").head(610)
    res["first_610_settled_rows"] = {
        "note": "the plan's 610 was the settled count at about 2026-11-15T05:34Z; rows settled since are excluded here",
        "rows": len(first),
        "declined": int(first["declined"].sum()),
        "declined_markets": {
            k: int(v) for k, v in first[first["declined"]]["market_type"].value_counts().items()
        },
        "losing_rows_declined": int(((first["pnl_units"] < 0) & first["declined"]).sum()),
        "p_ge_0.8_losing_rows": int(((first["model_probability"] >= 0.8) & (first["pnl_units"] < 0)).sum()),
        "p_ge_0.8_losing_rows_declined": int(
            ((first["model_probability"] >= 0.8) & (first["pnl_units"] < 0) & first["declined"]).sum()
        ),
    }
    # mismatch replay on the settled-row replay file (spread rows), v6 as served vs v6 as-of vs v7
    rp = pd.DataFrame(json.loads((OUT / "ncaab_v6_settled_replay.json").read_text(encoding="utf-8"))["rows"])
    rp = rp[rp["market"] == "spread"].rename(columns={"cutoff": "day"})
    names = df[["event_id", "day"]].copy()
    ev = {e["event_id"]: e for e in nr.load_scoreboard_events() if e["season_year"] == 2027}
    names["home"] = names["event_id"].map(lambda e: ev.get(e, {}).get("home_team"))
    names["away"] = names["event_id"].map(lambda e: ev.get(e, {}).get("away_team"))
    full = df.assign(home=names["home"], away=names["away"]).dropna(subset=["home"])
    full = full.drop_duplicates(["home", "away", "day"]).set_index(["home", "away", "day"])
    cols = [
        "v7_mean",
        "v7_scale",
        "v6_asof_mean",
        "v6_asof_scale",
        "v6_served_mean",
        "v6_served_scale",
        "margin",
        "tier_home",
        "tier_away",
        "d1_home",
        "d1_away",
    ]
    jn = rp.join(full[cols], on=["home", "away", "day"])
    jn = jn[jn["v7_mean"].notna()].copy()
    jn["mkt_home_margin"] = np.where(jn["selection"] == "away", jn["line"], -jn["line"])

    def side_p(mean: str, scale: str) -> np.ndarray:
        ph = home_cover_prob(
            jn[mean].to_numpy(float), jn[scale].to_numpy(float), jn["mkt_home_margin"].to_numpy(float)
        )
        return np.where(jn["selection"] == "away", 1 - ph, ph)

    o = jn["outcome"].to_numpy(float)
    rep_rows: dict[str, Any] = {"rows_joined": len(jn), "rows_total_spread": len(rp)}
    for name in ("v6_served", "v6_asof", "v7"):
        p = side_p(f"{name}_mean", f"{name}_scale")
        hi_m = p >= 0.8
        rep_rows[name] = {
            "recorded_side_brier": round(float(np.mean((p - o) ** 2)), 4),
            "n_p_ge_0.8": int(hi_m.sum()),
            "hit_p_ge_0.8": round(float(o[hi_m].mean()), 3) if hi_m.any() else None,
            "n_p_ge_0.7": int((p >= 0.7).sum()),
            "hit_p_ge_0.7": round(float(o[p >= 0.7].mean()), 3) if (p >= 0.7).any() else None,
        }
    p6 = side_p("v6_served_mean", "v6_served_scale")
    hi6 = p6 >= 0.8
    nond1 = ~(jn["d1_home"].astype(bool) & jn["d1_away"].astype(bool))
    mis = (~nond1) & (jn["tier_home"] >= 1550) & (jn["tier_away"] <= 1400)
    rep_rows["v6_served_p_ge_0.8_composition"] = {
        "n": int(hi6.sum()),
        "wins": int(o[hi6].sum()),
        "losses": int((1 - o[hi6]).sum()),
        "non_d1_either_side_as_of": int((hi6 & nond1.to_numpy()).sum()),
        "mismatch_cohort": int((hi6 & mis.to_numpy()).sum()),
        "losses_non_d1": int(((1 - o) * (hi6 & nond1.to_numpy())).sum()),
        "losses_mismatch_cohort": int(((1 - o) * (hi6 & mis.to_numpy())).sum()),
    }
    res["replay_spread_rows"] = rep_rows
    return res


def md_table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(lines)


def _cell(c: dict[str, Any] | None) -> str:
    if not c or c.get("rmse") is None:
        return "n/a"
    return f"{c['rmse']:.2f} / {c['bias']:+.2f} (n {c['n']})"


def _margin_grid(tbl: dict[str, Any], models: tuple[str, ...]) -> str:
    rows = [[label, *[_cell(cell.get(m)) for m in models]] for label, cell in tbl.items()]
    return md_table(["games played", *models], rows)


def _win_rows(w: dict[str, Any]) -> str:
    rows = [
        [m, v["n"], v["brier"], v["n_p_ge_0.8"], v["hit_p_ge_0.8"], v["mean_fav_p_p_ge_0.8"]]
        for m, v in w.items()
    ]
    return md_table(["model", "n", "Brier", "n p>=0.8", "hit p>=0.8", "mean fav p"], rows)


def render_md(rep: dict[str, Any]) -> str:
    a, b = rep["scorecard_a"], rep["scorecard_b"]
    cfg, sel = rep["frozen_config"], rep["selection"]
    out: list[str] = []
    out.append("# NCAAB v7 multi-season re-fit and evaluation (quick 261115-m5r)\n")
    out.append(
        "SHADOW and INERT: nothing is registered, no artifact, no champion route, v6 untouched, no ledger write. "
        f"Pin `{rep['pin_id']}` sha256 `{rep['pin_sha256']}` was committed before any outcome was joined to the "
        "multi-season components; the windows below follow it. Pin 1 (commit b4a61cf0) was amended once, to "
        "extend the ridge grid, after its fit-window selection landed on the grid corner and before any 2024-25 "
        "outcome was read (see the pin file).\n"
    )
    out.append(
        "- **Fit** 2022-23 and 2023-24 (hyperparameters, coefficients, scales; leave-one-season-out selection). "
        "**Decision** 2024-25 only. **2025-26** shaped v7's design in 1m7 and **2026-27** (partial) was its "
        "holdout there: both are CONTAMINATED and reported line-free only; no line x outcome x model number is "
        "read on them, so the 1m8 locked windows stay unspent.\n"
        "- 2024-25 was read for nine models per market in 1m8; the multiplicity in scorecard B counts that.\n"
        "- Conference map: AS-OF-SEASON, from the ESPN conference id on every scoreboard game. Realigned teams carry "
        "their own season's tier. The tier table is today's, so the old Pac-12 (id 21 through 2023-24) carries an "
        "explicit era override of 1660 (the table's 1550 describes the 2026 rebuilt league). Map risk is quantified "
        "by v6 as-of vs v6 as served.\n"
    )
    out.append("## Selected configuration (fit window only)\n")
    out.append(
        f"Ridge variant `{sel['ridge_variant_selected']}` (carry^age, lambda, kappa from the key), combiner form "
        f"`{sel['form_selected']}`, coefficients {[round(c, 4) for c in cfg['coef']]}, t-scales by games-played bucket "
        f"{[round(x, 2) for x in cfg['scales']]}, non-D1 rule margin = {cfg['nond1']['alpha']:.3f} * v6 + "
        f"{cfg['nond1']['beta']:.2f} (scale {cfg['nond1']['scale']:.2f}), total mode `{sel['total_mode']}`.\n"
    )
    top = sorted(sel["ridge_variant_table"].items(), key=lambda kv: kv[1]["score"])[:6]
    out.append(
        md_table(
            ["ridge variant", "mean fit-season D1 RMSE"],
            [[k, round(v["score"], 4)] for k, v in top],
        )
    )
    out.append(
        f"\nCombiner forms, leave-one-season-out RMSE: {sel['form_loso_rmse']}. Totals: v6 as-of "
        f"{sel['total_loso_rmse_v6_asof']:.3f} vs recalibrated {sel['total_loso_rmse_recal']:.3f}.\n"
    )
    out.append("## Scorecard A: forecast quality on UNSELECTED games, decision window 2024-25\n")
    out.append(
        f"D1-vs-D1 as-of games: {a['n_d1_games']} ({a['n_lined_d1_games']} with a closing line). Cells are RMSE / bias (actual minus predicted).\n"
    )
    out.append("### Margin by games played (all D1 games)\n")
    out.append(_margin_grid(a["margin_by_games_played"], MODELS_SPREAD) + "\n")
    out.append("### Margin by |closing line| (lined D1 games; the market line is the extra column)\n")
    lb = a["margin_by_abs_line_lined"]
    out.append(
        md_table(
            ["|line|", *MODELS_SPREAD, "market line"],
            [
                [k, *[_cell(c.get(m)) for m in MODELS_SPREAD], _cell(c.get("market_line"))]
                for k, c in lb.items()
            ],
        )
        + "\n"
    )
    out.append("### Home-win probability, all D1 games\n")
    out.append(_win_rows(a["win_probability_all_d1"]) + "\n")
    out.append("### Spread cover probability, lined D1 games (favoured side)\n")
    cv = a["cover_calibration_lined"]
    rows = []
    for m, c in cv.items():
        r = c["reliability_favoured_side"]
        rows.append(
            [m, c["n"], c["home_cover_brier"]]
            + [f"{r[k]['n']} / {r[k]['mean_p']} / {r[k]['hit']}" for k in r]
        )
    out.append(
        md_table(["model", "n", "home-cover Brier", "p 0.5-0.6 n/p/hit", "0.6-0.7", "0.7-0.8", ">=0.8"], rows)
        + "\n"
    )
    out.append("### Totals by games played (RMSE / bias, all games)\n")
    out.append(_margin_grid(a["total_by_games_played"], ("v6_served", "v6_asof", "v7")) + "\n")
    out.append("### Paired deltas (90% day-cluster bootstrap)\n")
    brows = []
    for k, v in a["bootstrap"].items():
        d = v.get("delta_rmse", v.get("delta_brier"))
        brows.append([k, v["n"], d, v["ci90"]])
    out.append(md_table(["comparison", "n", "delta (negative favours the first)", "90% CI"], brows) + "\n")
    out.append(
        f"All games including non-D1 (v7 uses the fitted non-D1 rule; ridge is undefined there): {a['all_games_incl_non_d1']}\n"
    )
    out.append(
        f"**Pinned pass criteria:** {a['pass_criteria']} -> **{'PASSED' if a['passed'] else 'NOT PASSED'}**. Not gating: {a['not_gating']}\n"
    )
    out.append("## Scorecard B: market-relative (rule `ncaab-v7-market-rule-2`), decision window 2024-25\n")
    out.append(
        "(model - closing line) on the residual test of `ncaab-residual-rule-1`: ridge alpha 1000 on the single standardised feature, 0.5 shrink, fit-window coefficients (v7 fit rows are leave-one-season-out), K-adjusted bootstrap by game date. Plain slope = OLS slope of (actual - line) on (model - line), 95% CI.\n"
    )
    for mkt, blk in b.items():
        out.append(f"### {mkt} (K = {blk['k']})\n")
        rows = []
        for name, r in blk["models"].items():
            v = r["validation"]
            ps = r["plain_slope_actual_minus_line_on_model_minus_line"]
            rows.append(
                [
                    name,
                    r["n_val"],
                    f"{v['slope']:+.3f} [{v['slope_ci_adj'][0]:+.3f}, {v['slope_ci_adj'][1]:+.3f}]",
                    f"{v['mse_delta']:+.4f} [{v['mse_delta_ci_adj'][0]:+.4f}, {v['mse_delta_ci_adj'][1]:+.4f}]",
                    f"{ps['slope']:+.3f} [{ps['ci_95'][0]:+.3f}, {ps['ci_95'][1]:+.3f}]",
                    "ACCEPTED" if r["accepted"] else "no",
                ]
            )
        out.append(
            md_table(
                [
                    "model",
                    "n val",
                    "slope on shrunk pred, K-adj CI",
                    "MSE delta, K-adj CI",
                    "plain slope vs line, 95% CI",
                    "rule",
                ],
                rows,
            )
            + "\n"
        )
        out.append(f"Validation RMSE vs outcome on the lined rows: {blk['descriptive_val_rmse']}\n")
        for name, r in blk["models"].items():
            sr = r["validation"].get("side_record", {})
            out.append(f"- {name} side record (|pred| >= 1 point): {sr}\n")
    out.append("## Mismatch fix (item 4)\n")
    mm = rep["mismatch"]
    out.append(f"Cohort: {mm['definition']}.\n")
    for season in ("2025", "2026", "2027"):
        c = mm[season]
        out.append(
            f"### season year {season} (n {c['n']}{'; lined ' + str(c['lined_n']) if 'lined_n' in c else ''}){'  CONTAMINATED, line-free' if season != '2025' else ''}\n"
        )
        out.append(
            md_table(["model", "margin RMSE / bias"], [[m, _cell(c["margin"][m])] for m in MODELS_SPREAD])
            + "\n"
        )
        if "cover_calibration" in c:
            rows = [
                [m, v["n"], v["home_cover_brier"], v["reliability_favoured_side"]["0.8-1.0"]]
                for m, v in c["cover_calibration"].items()
            ]
            out.append(
                md_table(["model (lined)", "n", "home-cover Brier", "p>=0.8 n/mean p/hit"], rows) + "\n"
            )
            out.append(f"Market line in cohort: {_cell(c['market_line'])}\n")
    out.append(f"**Pinned verdict:** {mm['pinned_verdict']}\n")
    led = rep["ledger"]
    out.append("## Settled-ledger checks (read-only runtime ledger)\n")
    out.append(
        f"Non-D1 no-pick rule (fc7ff8b8 predicate: either team absent from v6 team_priors) on settled Flat v6 rows: {led['rows']} rows ({led['rows_by_market']}; the plan said 610, the ledger holds this many now), declined {led['rows_declined_by_non_d1_rule']} ({led['declined_by_market']}), P&L of declined rows {led['declined_pnl_units']}u of {led['all_rows_pnl_units']}u; losing rows {led['losing_rows']} of which declined {led['losing_rows_declined']}; rows with model p >= 0.8: {led['p_ge_0.8_rows']}, losing {led['p_ge_0.8_losing_rows']}, of which declined {led['p_ge_0.8_losing_rows_declined']}.\n"
    )
    out.append(f"First 610 settled rows (the plan's snapshot): {led['first_610_settled_rows']}\n")
    out.append(
        f"Replay of the settled spread rows (contaminated, ledger lines): {led['replay_spread_rows']}\n"
    )
    out.append("## Contaminated windows, line-free\n")
    for label, c in rep["contaminated_line_free"].items():
        out.append(f"### {label}: {c['n_games']} games, {c['n_d1']} D1\n")
        out.append(_margin_grid(c["margin_by_games_played_d1"], MODELS_SPREAD) + "\n")
        out.append(_win_rows(c["win_probability_d1"]) + "\n")
        out.append(
            f"v7 minus v6 as-of D1 RMSE {c['v7_minus_v6_asof_d1_rmse']}; v7 minus v6 as served {c['v7_minus_v6_served_d1_rmse']}\n"
        )
    out.append("## Fit-window reference (v7 leave-one-season-out)\n")
    for s, d in rep["fit_window_reference"]["by_season_d1_margin"].items():
        out.append(f"- season year {s}: " + ", ".join(f"{m} {_cell(d[m])}" for m in MODELS_SPREAD))
    out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("components")
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--limit-days", type=int, default=0)
    c.add_argument("--stride", type=int, default=1)
    c.add_argument("--out", default="")
    g = sub.add_parser("gate")
    g.add_argument("--components", required=True)
    for name in ("fit", "final"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    print(f"pin {PIN['id']} sha256 {PIN_SHA256}", flush=True)
    if args.cmd == "components":
        return cmd_components(args)
    if args.cmd == "gate":
        return cmd_gate(args)
    if args.cmd == "fit":
        return cmd_fit(args)
    if args.cmd == "final":
        return cmd_final(args)
    raise SystemExit(f"stage {args.cmd} is added in a later commit")


if __name__ == "__main__":
    raise SystemExit(main())
