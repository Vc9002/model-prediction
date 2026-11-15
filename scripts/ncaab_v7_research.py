"""NCAAB v7 shadow-challenger research runner (quick task 261115-1m7). Inert: reads v6, writes only outputs/.

Stages (each can be run alone; results are cached as JSON under outputs/ncaab/):

    components  day-by-day point-in-time feature components from models/ncaab_v7_shadow.py
                (serving-faithful: real ESPN neutral flag, v6 served through its hash-verified artifact)
    parity      item 9: v6 retune train/serve parity (config, inputs, neutral flag, HCA grid)
    ideas       items 1-8: every feature idea as an isolated delta, fit on train -> verdict on validation
    final       freeze the combined config to disk, then ONE look at the locked holdout (item 10)

Usage:
    env PYTHONPATH=src:. python scripts/ncaab_v7_research.py [stage ...]

Split (frozen, identical everywhere; same graded-day rule as scripts/ncaab_v6_review_ablation.py):
    warm        2025-11-03 .. 2025-11-30   (season-opener regime; fit-side only, as in ablation round 2)
    train       2025-12-01 .. 2026-02-19
    validation  2026-02-20 .. 2026-03-18
    holdout     2026-03-19 .. now          (last 20% of 2025-26 dates + all of 2026-27)
Walk-forward: a game on ET date D sees only games that started before midnight ET of D.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import ncaab_v6_review_ablation as ab
import numpy as np
import pandas as pd

from model_prediction.features.ncaab_scoreboard_stats import NCAABScoreboardStats
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_v7_shadow import (
    NCAABV7ShadowModel,
    V7Components,
    V7Config,
    fit_least_squares,
    history_neutral,
    margin_design,
    scale_bucket,
    spread_distribution,
    student_t_scale_mle,
    total_feature,
    total_from_components,
    win_probability,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "ncaab"
COMPONENTS_PATH = OUT / "ncaab_v7_components.jsonl"

TRAIN_START = "2025-12-01"
TRAIN_END = "2026-02-19"
VAL_END = "2026-03-18"


def split_of(day: str) -> str:
    if day < TRAIN_START:
        return "warm"
    if day <= TRAIN_END:
        return "train"
    if day <= VAL_END:
        return "validation"
    return "holdout"


# ----------------------------------------------------------------------------- components


def _component_worker(days: list[str]) -> list[dict[str, Any]]:
    games = ab.load_games()
    starts = [g.start for g in games]
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(ab.EASTERN).date().isoformat()].append(g)
    model = NCAABV7ShadowModel()
    rows: list[dict[str, Any]] = []
    for day in days:
        import bisect

        history = games[: bisect.bisect_left(starts, ab._et_cutoff(day))]
        todays = by_day[day]
        if len(history) < 20 or not todays:
            continue
        upcoming = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=history_neutral(g, model._stats),
            )
            for g in todays
        ]
        actual = {g.event_id: g for g in todays}
        for comp in model.components(history, upcoming):
            g = actual[comp.event_id]
            rows.append(
                {
                    **comp.as_row(),
                    "day": day,
                    "split": split_of(day),
                    "season": g.season_year,
                    "ctype": g.competition_type,
                    "start": g.event_start_utc,
                    "margin": g.margin,
                    "total": g.total,
                }
            )
    return rows


def build_components(workers: int = 3) -> pd.DataFrame:
    games = ab.load_games()
    days = sorted({g.start.astimezone(ab.EASTERN).date().isoformat() for g in games})
    chunks = [days[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(_component_worker, chunks))
    rows = sorted((r for p in parts for r in p), key=lambda r: (r["start"], r["event_id"]))
    COMPONENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with COMPONENTS_PATH.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return pd.DataFrame(rows)


def load_components(rebuild: bool = False) -> pd.DataFrame:
    if rebuild or not COMPONENTS_PATH.exists():
        return build_components()
    return pd.read_json(COMPONENTS_PATH, lines=True, dtype={"event_id": str})


# ----------------------------------------------------------------------------- item 9: parity


def _parity_worker(args: tuple[str, list[str], dict[str, Any], str]) -> list[dict[str, Any]]:
    """v6 expected margins for ``days`` under one parameter override and one neutral-flag policy."""
    import bisect

    from model_prediction.models.ncaab_spread_v6 import (
        ARTIFACT_PATH,
        NCAABSpreadModelV6,
        load_verified_artifact,
    )

    label, days, overrides, neutral_mode = args
    games = ab.load_games()
    starts = [g.start for g in games]
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(ab.EASTERN).date().isoformat()].append(g)
    model = NCAABSpreadModelV6.from_artifact(load_verified_artifact(ARTIFACT_PATH), **overrides)
    stats = NCAABScoreboardStats()
    rows: list[dict[str, Any]] = []
    for day in days:
        history = games[: bisect.bisect_left(starts, ab._et_cutoff(day))]
        todays = by_day[day]
        if len(history) < 20 or not todays:
            continue
        flags = {
            "flag": {g.event_id: history_neutral(g, stats) for g in todays},
            "proxy": {g.event_id: g.competition_type == "TRNMNT" for g in todays},
            "none": {g.event_id: False for g in todays},
        }[neutral_mode]
        upcoming = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=flags[g.event_id],
            )
            for g in todays
        ]
        actual = {g.event_id: g for g in todays}
        for p in model.predict_games(history, upcoming):
            if p.market_type != "moneyline":
                continue
            g = actual[p.event_id]
            rows.append(
                {
                    "label": label,
                    "day": day,
                    "split": split_of(day),
                    "event_id": p.event_id,
                    "neutral_flag": flags[p.event_id],
                    "d1": g.home_team in model.team_priors and g.away_team in model.team_priors,
                    "mean": float(p.inference_inputs["mean"]),
                    "scale": float(p.inference_inputs["scale"]),
                    "p_home": float(p.probabilities["home"]),
                    "sample": p.feature_basis["current_season_sample"],
                    "margin": g.margin,
                }
            )
    return rows


def _run_parity_grid(
    jobs: list[tuple[str, dict[str, Any], str]], day_filter: Any, workers: int = 3
) -> pd.DataFrame:
    games = ab.load_games()
    all_days = {g.start.astimezone(ab.EASTERN).date().isoformat() for g in games}
    days = sorted(d for d in all_days if day_filter(d))
    tasks = [(label, days[i::workers], ov, nm) for label, ov, nm in jobs for i in range(workers)]
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(_parity_worker, tasks))
    return pd.DataFrame([r for p in parts for r in p])


def _spread_summary(g: pd.DataFrame) -> dict[str, Any]:
    rows = [
        {
            "mean": r.mean,
            "scale": r.scale,
            "df": 8.0,
            "base_scale": 10.5,
            "t_scale_matched": False,
            "p_home": r.p_home,
            "margin": r.margin,
        }
        for r in g.itertuples()
    ]
    out = ab.summarise_spread(rows)
    d1 = g[g.d1]
    out["d1_rmse"] = round(float(np.sqrt(((d1.margin - d1["mean"]) ** 2).mean())), 3)
    nn = d1[~d1.neutral_flag]
    out["d1_nonneutral_bias"] = round(float((nn.margin - nn["mean"]).mean()), 3)
    out["d1_nonneutral_n"] = len(nn)
    return out


def ablated_overrides(model: Any) -> dict[str, Any]:
    """Overrides that make the artifact factory reproduce an arbitrary constructed model."""
    names = [
        "margin_sd",
        "elo_weight",
        "trend_weight",
        "rest_weight",
        "home_court_points",
        "rest_penalty_home",
        "rest_penalty_away",
        "schedule_density_drag",
        "dynamic_volatility",
        "use_logistic_moneyline",
        "logistic_scale",
        "offseason_gap_days",
        "offseason_retention",
        "isolate_postseason",
        "elo_home_advantage",
        "student_t_df",
        "use_student_t_spreads",
        "non_d1_prior",
        "coherent_moneyline",
        "student_t_scale_matches_sd",
        "elo_weight_ramp_games",
        "tiered_trend_priors",
        "additive_matchup",
        "non_d1_prior_from_history",
        "non_d1_min_games",
        "rank_prior_per_slot",
    ]
    return {n: getattr(model, n) for n in names}


def run_parity(components: pd.DataFrame) -> dict[str, Any]:
    from model_prediction.models.ncaab_spread_v6 import (
        ARTIFACT_PATH,
        NCAABSpreadModelV6,
        load_verified_artifact,
        ncaab_spread_v6_model,
    )

    result: dict[str, Any] = {}
    served = ncaab_spread_v6_model()
    ablated = NCAABSpreadModelV6(**ab.SPREAD_VARIANTS["shipped_nond1_hist_rank_8"])
    skip = {"team_priors", "_stats", "high_altitude_hca"}
    result["config_diffs_ablation_kwargs_vs_artifact"] = {
        k: [getattr(served, k), getattr(ablated, k)]
        for k in vars(served)
        if k not in skip and getattr(served, k) != getattr(ablated, k)
    }
    result["artifact_hash_ok"] = bool(load_verified_artifact(ARTIFACT_PATH)["artifact_hash"])

    # identical inputs -> identical margins (every 6th day); the one input that differs is the flag
    def sub(d: str) -> bool:
        return int(d.replace("-", "")) % 6 == 0

    art_none = _run_parity_grid([("artifact", {}, "none")], sub)
    abl_none = _run_parity_grid([("ablation", ablated_overrides(ablated), "none")], sub)
    merged = art_none.merge(abl_none, on="event_id", suffixes=("_a", "_b"))
    result["same_input_max_abs_margin_diff"] = float((merged.mean_a - merged.mean_b).abs().max())
    result["same_input_games"] = len(merged)

    c = components
    result["neutral_flag_vs_trnmnt_proxy"] = {
        "both": int((c.neutral & (c.ctype == "TRNMNT")).sum()),
        "flag_only": int((c.neutral & (c.ctype != "TRNMNT")).sum()),
        "proxy_only": int((~c.neutral & (c.ctype == "TRNMNT")).sum()),
        "neither": int((~c.neutral & (c.ctype != "TRNMNT")).sum()),
    }
    result["neutral_games_by_split"] = {s: int(g.neutral.sum()) for s, g in c.groupby("split")}
    result["games_by_split"] = {s: len(g) for s, g in c.groupby("split")}

    # HCA grid on the windows the retune used (holdout deliberately excluded)
    grid = {
        "hca7.2_elo70": {"home_court_points": 7.2, "elo_home_advantage": 70.0},
        "hca6.0_elo55": {"home_court_points": 6.0, "elo_home_advantage": 55.0},
        "hca5.0_elo45": {"home_court_points": 5.0, "elo_home_advantage": 45.0},
    }
    jobs = [(f"{k}|{mode}", v, mode) for k, v in grid.items() for mode in ("none", "flag")]
    df = _run_parity_grid(jobs, lambda d: d <= VAL_END)
    table: dict[str, Any] = {}
    for (label, split), g in df.groupby(["label", "split"]):
        table.setdefault(label, {})[split] = _spread_summary(g)
    result["hca_grid"] = table
    return result


# ----------------------------------------------------------------------------- items 1-8: ideas

COMPONENT_FIELDS = [f.name for f in dataclasses.fields(V7Components) if f.name != "extras"]
OPTIONAL_FLOATS = ("v6_total", "v6_total_sd", "effpace_total", "effpace_poss")


def to_components(df: pd.DataFrame) -> list[V7Components]:
    comps: list[V7Components] = []
    for rec in df[COMPONENT_FIELDS].to_dict("records"):
        for key in OPTIONAL_FLOATS:
            v = rec[key]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                rec[key] = None
        comps.append(V7Components(**rec))
    return comps


def err_stats(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    e = y - pred
    return {
        "n": len(e),
        "rmse": round(float(np.sqrt(np.mean(e**2))), 3),
        "mae": round(float(np.mean(np.abs(e))), 3),
        "bias": round(float(np.mean(e)), 3),
    }


def paired_delta(
    y: np.ndarray, base: np.ndarray, cand: np.ndarray, days: np.ndarray, boots: int = 600, seed: int = 7
) -> dict[str, float]:
    """RMSE(cand) - RMSE(base) with a 90% day-cluster bootstrap interval (negative = candidate better)."""
    se_b = (y - base) ** 2
    se_c = (y - cand) ** 2
    uniq, inv = np.unique(days, return_inverse=True)
    sum_b = np.bincount(inv, weights=se_b)
    sum_c = np.bincount(inv, weights=se_c)
    cnt = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    deltas = np.empty(boots)
    for i in range(boots):
        pick = rng.integers(0, len(uniq), len(uniq))
        n = cnt[pick].sum()
        deltas[i] = math.sqrt(sum_c[pick].sum() / n) - math.sqrt(sum_b[pick].sum() / n)
    point = math.sqrt(se_c.mean()) - math.sqrt(se_b.mean())
    return {
        "delta_rmse": round(point, 3),
        "ci90_lo": round(float(np.quantile(deltas, 0.05)), 3),
        "ci90_hi": round(float(np.quantile(deltas, 0.95)), 3),
    }


def fit_margin(comps: Sequence[V7Components], y: np.ndarray, names: Sequence[str]) -> np.ndarray:
    return fit_least_squares(margin_design(comps, names), y)


def predict_margin(comps: Sequence[V7Components], names: Sequence[str], coef: np.ndarray) -> np.ndarray:
    return margin_design(comps, names) @ coef


MARGIN_VARIANTS: dict[str, tuple[str, tuple[str, ...]]] = {
    # item 0: controls
    "refit_v6_components": ("0 control", ("elo", "trend", "rest")),
    # item 1: mismatch rescale / heavier tail
    "rescale_slope": ("1", ("v6",)),
    "rescale_slope_const": ("1", ("v6", "const")),
    "rescale_slope_thin": ("1", ("v6", "v6_thin")),
    "rescale_slope_tail": ("1", ("v6", "v6_tail")),
    "rescale_thin_tail": ("1", ("v6", "v6_thin", "v6_tail")),
    # item 2: ridge (Massey) opponent adjustment with the conference-tier prior
    "ridge_alone": ("2", ("ridge_c0",)),
    "ridge_replaces_trend": ("2", ("elo", "ridge_c0", "rest")),
    "ridge_adds_to_v6": ("2", ("elo", "trend", "ridge_c0", "rest")),
    "ridge_plus_v6_thin": ("2", ("v6", "ridge_c0", "v6_thin", "ridge_thin")),
    # item 5: venue
    "team_hca_k10": ("5", ("v6", "team_hca_k10")),
    "team_hca_k20": ("5", ("v6", "team_hca_k20")),
    "neutral_listed_home_edge": ("5", ("v6", "neutral")),
    # item 6: rest / schedule density (isolated on the v6 components, without v6's own rest term)
    "no_rest_term": ("6", ("elo", "trend")),
    "rest_v6_diff": ("6", ("elo", "trend", "rest")),
    "rest_b2b_dens": ("6", ("elo", "trend", "b2b", "dens")),
    # item 8: home-vs-away-favourite asymmetry
    "away_fav_asymmetry": ("8", ("v6", "afav")),
}


def run_margin_variants(df: pd.DataFrame) -> dict[str, Any]:
    d1 = df[df.d1]
    fit = d1[d1.split.isin(["warm", "train"])]
    val = d1[d1.split == "validation"]
    c_fit, c_val = to_components(fit), to_components(val)
    y_fit, y_val = fit.margin.to_numpy(float), val.margin.to_numpy(float)
    served_val = val.v6_mean.to_numpy(float)
    out: dict[str, Any] = {
        "v6_served": {
            "item": "0 control",
            "fit_window": err_stats(y_fit, fit.v6_mean.to_numpy(float)),
            "validation": err_stats(y_val, served_val),
        }
    }
    for name, (item, names) in MARGIN_VARIANTS.items():
        coef = fit_margin(c_fit, y_fit, names)
        p_val = predict_margin(c_val, names, coef)
        out[name] = {
            "item": item,
            "features": list(names),
            "coef": [round(float(c), 4) for c in coef],
            "fit_window": err_stats(y_fit, predict_margin(c_fit, names, coef)),
            "validation": err_stats(y_val, p_val),
            "vs_v6_served": paired_delta(y_val, served_val, p_val, val.day.to_numpy()),
        }
    # delta of each idea against its own refit control, so re-weighting is not credited to the idea
    controls = {
        "2": "refit_v6_components",
        "6": "no_rest_term",
    }
    for name, row in out.items():
        ctrl = controls.get(str(row.get("item")))
        if ctrl and name != ctrl and "coef" in row:
            names_c = MARGIN_VARIANTS[ctrl][1]
            p_c = predict_margin(c_val, names_c, fit_margin(c_fit, y_fit, names_c))
            p_i = predict_margin(c_val, MARGIN_VARIANTS[name][1], np.array(row["coef"]))
            row["vs_refit_control"] = {"control": ctrl, **paired_delta(y_val, p_c, p_i, val.day.to_numpy())}
    for name in ("rescale_slope_const", "rescale_slope_thin", "rescale_slope_tail", "rescale_thin_tail"):
        ref = out["rescale_slope"]
        p_i = predict_margin(c_val, MARGIN_VARIANTS[name][1], np.array(out[name]["coef"]))
        p_r = predict_margin(c_val, ("v6",), np.array(ref["coef"]))
        out[name]["vs_single_slope"] = paired_delta(y_val, p_r, p_i, val.day.to_numpy())
    for name in ("team_hca_k10", "team_hca_k20", "neutral_listed_home_edge", "away_fav_asymmetry"):
        p_i = predict_margin(c_val, MARGIN_VARIANTS[name][1], np.array(out[name]["coef"]))
        p_r = served_val * out["rescale_slope"]["coef"][0]
        out[name]["vs_single_slope"] = paired_delta(y_val, p_r, p_i, val.day.to_numpy())
    return out


def run_non_d1(df: pd.DataFrame) -> dict[str, Any]:
    """Item 4. Validation holds only a handful of non-D1 games, so: fit on the Nov-2025 window,
    score on Dec-Feb (both early-season-like, 270 and 241 games); validation is reported for
    completeness with its n."""
    nd = df[~df.d1]
    fit = nd[nd.split == "warm"]
    test = nd[nd.split == "train"]
    val = nd[nd.split == "validation"]
    out: dict[str, Any] = {
        "n": {"warm": len(fit), "train": len(test), "validation": len(val)},
        "v6_served": {
            "warm": err_stats(fit.margin.to_numpy(float), fit.v6_mean.to_numpy(float)),
            "train": err_stats(test.margin.to_numpy(float), test.v6_mean.to_numpy(float)),
        },
    }
    for label, names in (("offset", ("const",)), ("linear", ("v6", "const")), ("slope_only", ("v6",))):
        coef = fit_margin(to_components(fit), fit.margin.to_numpy(float), names)
        out[label] = {
            "coef": [round(float(c), 3) for c in coef],
            "train": err_stats(test.margin.to_numpy(float), predict_margin(to_components(test), names, coef)),
        }
    # who is the non-D1 team? the visitor in nearly every game, the margin is huge either way
    out["home_is_non_d1_share"] = round(float((~nd.d1_home).mean()), 3)
    # decline rules: what do they remove, and what is left?
    d_all = df[df.split.isin(["warm", "train", "validation"])]
    y = d_all.margin.to_numpy(float)
    p = d_all.v6_mean.to_numpy(float)
    rules: dict[str, np.ndarray] = {
        "decline_non_d1": d_all.d1.to_numpy(),
        "decline_min_games_3": (np.minimum(d_all.games_home, d_all.games_away) >= 3).to_numpy(),
        "decline_min_games_5": (np.minimum(d_all.games_home, d_all.games_away) >= 5).to_numpy(),
        "decline_non_d1_or_min_games_3": (
            d_all.d1.to_numpy() & (np.minimum(d_all.games_home, d_all.games_away) >= 3).to_numpy()
        ),
    }
    out["decline_rules"] = {
        "all_games": err_stats(y, p),
        **{
            k: {
                "kept": err_stats(y[m], p[m]),
                "declined": err_stats(y[~m], p[~m]),
                "kept_share": round(float(m.mean()), 3),
            }
            for k, m in rules.items()
        },
    }
    return out


TOTAL_VARIANTS_V7: dict[str, tuple[str, ...]] = {
    "v6_plus_const": ("v6_total", "const"),
    "effpace_alone": ("effpace_total",),
    "effpace_const": ("effpace_total", "const"),
    "v6_effpace": ("v6_total", "effpace_total"),
    "v6_effpace_const": ("v6_total", "effpace_total", "const"),
}


def run_totals(df: pd.DataFrame) -> dict[str, Any]:
    ok = df[df.v6_total.notna() & df.effpace_total.notna()]
    fit = ok[ok.split.isin(["warm", "train"])]
    val = ok[ok.split == "validation"]
    c_fit, c_val = to_components(fit), to_components(val)
    y_fit, y_val = fit.total.to_numpy(float), val.total.to_numpy(float)
    served = val.v6_total.to_numpy(float)
    out: dict[str, Any] = {
        "v6_served": {
            "fit_window": err_stats(y_fit, fit.v6_total.to_numpy(float)),
            "validation": err_stats(y_val, served),
        },
        "effpace_raw": {
            "fit_window": err_stats(y_fit, fit.effpace_total.to_numpy(float)),
            "validation": err_stats(y_val, val.effpace_total.to_numpy(float)),
        },
    }
    for name, names in TOTAL_VARIANTS_V7.items():
        x_fit = np.array([[total_feature(c, n) for n in names] for c in c_fit])
        x_val = np.array([[total_feature(c, n) for n in names] for c in c_val])
        coef = fit_least_squares(x_fit, y_fit)
        pred = x_val @ coef
        out[name] = {
            "features": list(names),
            "coef": [round(float(c), 4) for c in coef],
            "fit_window": err_stats(y_fit, x_fit @ coef),
            "validation": err_stats(y_val, pred),
            "vs_v6_served": paired_delta(y_val, served, pred, val.day.to_numpy()),
        }
    out["coverage"] = {
        "rows_with_both": len(ok),
        "rows_total": len(df),
        "v6_total_corr_validation": round(float(np.corrcoef(served, y_val)[0, 1]), 3),
        "effpace_corr_validation": round(float(np.corrcoef(val.effpace_total, y_val)[0, 1]), 3),
    }
    return out


def run_ideas(components: pd.DataFrame) -> dict[str, Any]:
    df = components.copy()
    df["d1"] = df.d1_home & df.d1_away
    dev = df[df.split != "holdout"]  # the holdout is not read by this stage
    return {
        "margin": run_margin_variants(dev),
        "non_d1": run_non_d1(dev),
        "totals": run_totals(dev),
    }


# ----------------------------------------------------------------------------- isolated effects


def ols_hc1(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """OLS with intercept first; heteroskedasticity-robust (HC1) standard errors."""
    x1 = np.column_stack([np.ones(len(y)), x])
    beta = np.linalg.lstsq(x1, y, rcond=None)[0]
    resid = y - x1 @ beta
    bread = np.linalg.inv(x1.T @ x1)
    meat = x1.T @ (x1 * (resid**2)[:, None])
    cov = bread @ meat @ bread * len(y) / (len(y) - x1.shape[1])
    return beta, np.sqrt(np.diag(cov))


def run_isolated_effects(components: pd.DataFrame) -> dict[str, Any]:
    """Items 5, 6, 8 as plain residual regressions on windows without the holdout.

    The base is the tier-prior ridge margin (no free parameter), so the residual is "what the
    strength model does not already explain"; each effect is estimated per split with its
    robust standard error.
    """
    df = components[components.split != "holdout"].copy()
    df["d1"] = df.d1_home & df.d1_away
    d1 = df[df.d1].copy()
    d1["resid"] = d1.margin - d1.ridge_c0
    out: dict[str, Any] = {}

    def coef_table(g: pd.DataFrame, cols: list[str]) -> dict[str, Any]:
        beta, se = ols_hc1(g[cols].to_numpy(float), g.resid.to_numpy(float))
        res = {"n": len(g), "intercept": [round(float(beta[0]), 3), round(float(se[0]), 3)]}
        for i, c in enumerate(cols):
            res[c] = [round(float(beta[i + 1]), 3), round(float(se[i + 1]), 3)]
        return res

    nn = d1[~d1.neutral].copy()
    nn["b2b"] = (nn.rest_days_away <= 1.2).astype(float) - (nn.rest_days_home <= 1.2).astype(float)
    nn["dens"] = (nn.games4_away >= 2).astype(float) - (nn.games4_home >= 2).astype(float)
    nn["long_rest"] = (nn.rest_days_home >= 4.0).astype(float) - (nn.rest_days_away >= 4.0).astype(float)
    nn["rest_days_gap"] = (nn.rest_days_home.clip(upper=7) - nn.rest_days_away.clip(upper=7)).astype(float)
    out["rest_v6_weight"] = 0.15  # served: expected_margin += 0.15 * rest_diff
    out["rest_effect_points_per_unit"] = {
        sp: {
            "v6_rest_diff": coef_table(g, ["rest_diff"]),
            "b2b_dens_long": coef_table(g, ["b2b", "dens", "long_rest"]),
            "rest_days_gap": coef_table(g, ["rest_days_gap"]),
        }
        for sp, g in nn.groupby("split")
    }
    out["rest_b2b_counts"] = {
        sp: {
            "home_b2b": int((g.rest_days_home <= 1.2).sum()),
            "away_b2b": int((g.rest_days_away <= 1.2).sum()),
            "n": len(g),
        }
        for sp, g in nn.groupby("split")
    }

    # item 8: asymmetry on non-neutral D1 games, by sign of the ridge/v6 projection
    asym: dict[str, Any] = {}
    for sp, g in nn.groupby("split"):
        row: dict[str, Any] = {}
        for label, col in (("v6", "v6_mean"), ("ridge", "ridge_c0")):
            pred = g[col]
            e = g.margin - pred
            row[label] = {
                "home_fav": {"n": int((pred > 0).sum()), "bias": round(float(e[pred > 0].mean()), 3)},
                "away_fav": {"n": int((pred < 0).sum()), "bias": round(float(e[pred < 0].mean()), 3)},
            }
            beta, se = ols_hc1((pred < 0).to_numpy(float) * pred.to_numpy(float), e.to_numpy(float))
            row[label]["away_fav_slope_extra"] = [round(float(beta[1]), 3), round(float(se[1]), 3)]
        asym[sp] = row
    out["away_favourite_asymmetry"] = asym

    # item 5: neutral games, listed-home edge and D1 home advantage level
    neutral: dict[str, Any] = {}
    for sp, g in d1.groupby("split"):
        gn = g[g.neutral]
        gh = g[~g.neutral]
        neutral[sp] = {
            "neutral_n": len(gn),
            "neutral_v6_bias": round(float((gn.margin - gn.v6_mean).mean()), 3) if len(gn) else None,
            "neutral_ridge_bias": round(float((gn.margin - gn.ridge_c0).mean()), 3) if len(gn) else None,
            "home_court_n": len(gh),
            "home_court_v6_bias": round(float((gh.margin - gh.v6_mean).mean()), 3),
            "home_court_ridge_bias": round(float((gh.margin - gh.ridge_c0).mean()), 3),
            "mean_ridge_fitted_hca": round(float(g.ridge_hca.mean()), 3),
        }
    out["neutral_and_hca"] = neutral

    # item 9 attribution: residual = a + b * v6 on non-neutral D1 games; b > 0 is compression
    attr: dict[str, Any] = {}
    for sp, g in nn.groupby("split"):
        e = (g.margin - g.v6_mean).to_numpy(float)
        beta, se = ols_hc1(g.v6_mean.to_numpy(float).reshape(-1, 1), e)
        attr[sp] = {
            "n": len(g),
            "intercept": [round(float(beta[0]), 3), round(float(se[0]), 3)],
            "slope_extra": [round(float(beta[1]), 3), round(float(se[1]), 3)],
            "implied_slope_of_actual_on_v6": round(1.0 + float(beta[1]), 3),
        }
    out["v6_bias_attribution"] = attr
    return out


# ----------------------------------------------------------------------------- freeze + final

CANDIDATE_MARGIN_NAMES = ("ridge_c05", "v6_tail")  # carryover 0.5 declared ex ante, not selectable in dev
CANDIDATE_NOND1_NAMES = ("v6", "const")
CANDIDATE_TOTAL_NAMES = ("v6_total", "effpace_total")
SCALE_EDGES = (0, 1, 3, 8)
FROZEN_PATH = OUT / "ncaab_v7_frozen_config.json"


def fit_frozen_config(components: pd.DataFrame) -> V7Config:
    """Everything fitted on warm+train only (validation selected, holdout unseen)."""
    df = components[components.split.isin(["warm", "train"])].copy()
    df["d1"] = df.d1_home & df.d1_away
    d1 = df[df.d1]
    c_d1 = to_components(d1)
    y = d1.margin.to_numpy(float)
    coef = fit_margin(c_d1, y, CANDIDATE_MARGIN_NAMES)
    warm_nd = df[(~df.d1) & (df.split == "warm")]
    nd_coef = fit_margin(to_components(warm_nd), warm_nd.margin.to_numpy(float), CANDIDATE_NOND1_NAMES)
    resid = y - predict_margin(c_d1, CANDIDATE_MARGIN_NAMES, coef)
    samples = d1["sample"].to_numpy()
    edges = SCALE_EDGES
    values = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else 10**9
        m = (samples >= lo) & (samples < hi)
        values.append(round(student_t_scale_mle(resid[m]), 3))
    nd_all = df[~df.d1]
    nd_resid = nd_all.margin.to_numpy(float) - predict_margin(
        to_components(nd_all), CANDIDATE_NOND1_NAMES, nd_coef
    )
    tot = df[df.v6_total.notna() & df.effpace_total.notna()]
    c_tot = to_components(tot)
    x_tot = np.array([[total_feature(c, n) for n in CANDIDATE_TOTAL_NAMES] for c in c_tot])
    t_coef = fit_least_squares(x_tot, tot.total.to_numpy(float))
    t_sd = float(np.std(tot.total.to_numpy(float) - x_tot @ t_coef))
    return V7Config(
        margin_names=CANDIDATE_MARGIN_NAMES,
        margin_coef=tuple(round(float(c), 4) for c in coef),
        nond1_rule="linear",
        nond1_names=CANDIDATE_NOND1_NAMES,
        nond1_coef=tuple(round(float(c), 4) for c in nd_coef),
        scale_edges=edges,
        scale_values=tuple(values),
        nond1_scale=round(student_t_scale_mle(nd_resid), 3),
        total_names=CANDIDATE_TOTAL_NAMES,
        total_coef=tuple(round(float(c), 4) for c in t_coef),
        total_sd=round(t_sd, 3),
        notes="fit on warm+train (2025-11-03..2026-02-19); non-D1 linear rule fit on 2025-11 only; scale = MLE t(8)",
    )


def v7_frame(df: pd.DataFrame, config: V7Config) -> pd.DataFrame:
    """Per-game v7 mean / scale / p_home / total from the shared combiner."""
    comps = to_components(df)
    mean, scale, p_home, total = [], [], [], []
    for c in comps:
        m, s, p = spread_distribution(c, config)
        mean.append(m)
        scale.append(s)
        p_home.append(p)
        total.append(total_from_components(c, config))
    out = df.copy()
    out["v7_mean"], out["v7_scale"], out["v7_p_home"], out["v7_total"] = mean, scale, p_home, total
    return out


def _spread_rows(frame: pd.DataFrame, mean_col: str, scale_col: str, p_col: str) -> list[dict[str, Any]]:
    return [
        {
            "mean": float(getattr(r, mean_col)),
            "scale": float(getattr(r, scale_col)),
            "df": 8.0,
            "base_scale": 10.5,
            "t_scale_matched": False,
            "p_home": float(getattr(r, p_col)),
            "margin": float(r.margin),
        }
        for r in frame.itertuples()
    ]


def score_spread(frame: pd.DataFrame, mean_col: str, scale_col: str, p_col: str) -> dict[str, Any]:
    out = ab.summarise_spread(_spread_rows(frame, mean_col, scale_col, p_col))
    d1 = frame[frame.d1]
    out["d1_rmse"] = round(float(np.sqrt(((d1.margin - d1[mean_col]) ** 2).mean())), 3)
    out["d1_bias"] = round(float((d1.margin - d1[mean_col]).mean()), 3)
    out["rmse_all"] = round(float(np.sqrt(((frame.margin - frame[mean_col]) ** 2).mean())), 3)
    return out


def add_comparators(frame: pd.DataFrame, fit_frame: pd.DataFrame, config: V7Config) -> pd.DataFrame:
    """Elo-only, ridge-only and re-scaled v6 get the SAME fitted Student-t scale procedure as v7,
    so a difference in cover/Brier is a difference in the margin, not in the scale."""
    fit = fit_frame[fit_frame.d1_home & fit_frame.d1_away]
    out = frame.copy()
    for label, col in (("elo", "elo_margin"), ("ridge", "ridge_c0"), ("v6relearn", "v6_mean")):
        res = fit.margin.to_numpy(float) - fit[col].to_numpy(float)
        values = []
        for i, lo in enumerate(config.scale_edges):
            hi = config.scale_edges[i + 1] if i + 1 < len(config.scale_edges) else 10**9
            m = (fit["sample"].to_numpy() >= lo) & (fit["sample"].to_numpy() < hi)
            values.append(student_t_scale_mle(res[m]))
        out[f"{label}_mean"] = out[col]
        out[f"{label}_scale"] = [
            values[scale_bucket(s, config.scale_edges)] if both else config.nond1_scale
            for s, both in zip(out["sample"], out.d1, strict=True)
        ]
        out[f"{label}_p"] = [
            win_probability(m, s) for m, s in zip(out[f"{label}_mean"], out[f"{label}_scale"], strict=True)
        ]
    return out


def evaluate_window(frame: pd.DataFrame, config: V7Config) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(frame), "n_d1": int(frame.d1.sum())}
    out["v6_served"] = score_spread(frame, "v6_mean", "v6_scale", "v6_p_home")
    out["v6_relearned_scale"] = score_spread(frame, "v6relearn_mean", "v6relearn_scale", "v6relearn_p")
    out["elo_only"] = score_spread(frame, "elo_mean", "elo_scale", "elo_p")
    out["ridge_only"] = score_spread(frame, "ridge_mean", "ridge_scale", "ridge_p")
    out["v7"] = score_spread(frame, "v7_mean", "v7_scale", "v7_p_home")
    d1 = frame[frame.d1]
    y = d1.margin.to_numpy(float)
    days = d1.day.to_numpy()
    out["v7_vs_v6_d1"] = paired_delta(y, d1.v6_mean.to_numpy(float), d1.v7_mean.to_numpy(float), days)
    out["v7_vs_ridge_d1"] = paired_delta(y, d1.ridge_c0.to_numpy(float), d1.v7_mean.to_numpy(float), days)
    ya = frame.margin.to_numpy(float)
    out["v7_vs_v6_all"] = paired_delta(
        ya, frame.v6_mean.to_numpy(float), frame.v7_mean.to_numpy(float), frame.day.to_numpy()
    )
    tot = frame[frame.v7_total.notna()]
    if len(tot):
        yt = tot.total.to_numpy(float)
        out["total_v6"] = err_stats(yt, tot.v6_total.to_numpy(float))
        out["total_v7"] = err_stats(yt, tot.v7_total.to_numpy(float))
        out["total_v7_vs_v6"] = paired_delta(
            yt, tot.v6_total.to_numpy(float), tot.v7_total.to_numpy(float), tot.day.to_numpy()
        )
        rows6 = [
            {"total": float(r.total), "mean": float(r.v6_total), "sd": float(r.v6_total_sd)}
            for r in tot.itertuples()
        ]
        rows7 = [
            {"total": float(r.total), "mean": float(r.v7_total), "sd": config.total_sd}
            for r in tot.itertuples()
        ]
        out["total_v6_dist"] = ab.summarise_total(rows6)
        out["total_v7_dist"] = ab.summarise_total(rows7)
    return out


def carryover_sensitivity(frame: pd.DataFrame, config: V7Config) -> dict[str, Any]:
    """Item 3: same coefficients, ridge carry weight 0 / 0.5 / 1 (0.5 was declared ex ante)."""
    d1 = frame[frame.d1]
    y = d1.margin.to_numpy(float)
    out: dict[str, Any] = {}
    for label, col in (("carry_0.0", "ridge_c0"), ("carry_0.5", "ridge_c05"), ("carry_1.0", "ridge_c10")):
        names = (col, "v6_tail")
        pred = predict_margin(to_components(d1), names, np.array(config.margin_coef))
        raw = d1[col].to_numpy(float)
        out[label] = {"v7_form": err_stats(y, pred), "ridge_raw": err_stats(y, raw)}
    return out


def decision_variants(frame: pd.DataFrame, config: V7Config) -> dict[str, Any]:
    """Item 4 on the holdout: non-D1 rule alternatives under the frozen coefficients."""
    y = frame.margin.to_numpy(float)
    keep_d1 = frame.d1.to_numpy()
    out = {
        "v7_with_nond1_linear_rule": err_stats(y, frame.v7_mean.to_numpy(float)),
        "v7_d1_form_nond1_stays_v6": err_stats(
            y, np.where(keep_d1, frame.v7_mean.to_numpy(float), frame.v6_mean.to_numpy(float))
        ),
        "v6_served": err_stats(y, frame.v6_mean.to_numpy(float)),
        "decline_non_d1_kept_v7": err_stats(y[keep_d1], frame.v7_mean.to_numpy(float)[keep_d1]),
        "decline_non_d1_kept_v6": err_stats(y[keep_d1], frame.v6_mean.to_numpy(float)[keep_d1]),
        "declined_share": round(float((~keep_d1).mean()), 3),
        "non_d1_only_v6": err_stats(y[~keep_d1], frame.v6_mean.to_numpy(float)[~keep_d1]),
        "non_d1_only_v7": err_stats(y[~keep_d1], frame.v7_mean.to_numpy(float)[~keep_d1]),
    }
    for n in (3, 5):
        m = keep_d1 & (np.minimum(frame.games_home, frame.games_away) >= n).to_numpy()
        out[f"decline_non_d1_or_min_games_{n}_kept_v6"] = {
            **err_stats(y[m], frame.v6_mean.to_numpy(float)[m]),
            "kept_share": round(float(m.mean()), 3),
        }
        out[f"decline_non_d1_or_min_games_{n}_kept_v7"] = err_stats(y[m], frame.v7_mean.to_numpy(float)[m])
    return out


def replay_check(frame: pd.DataFrame, config: V7Config) -> dict[str, Any]:
    """Item 10 replay: the settled v6 replay rows re-priced by v7 at the same line.

    Market-relative content is deliberately thin: 1,167 settled rows, every one with a current-season
    sample of 0-3 games, 1,157 of 1,167 asks are indicative slate prices, not executable quotes.
    """
    from scipy import stats

    path = OUT / "ncaab_v6_settled_replay.json"
    rows = pd.DataFrame(json.loads(path.read_text(encoding="utf-8"))["rows"])
    key = frame.set_index(["home", "away", "day"])
    keep = ~key.index.duplicated(keep="first")
    key = key[keep]
    rows = rows.rename(columns={"cutoff": "day"})
    joined = rows.join(
        key[["v7_mean", "v7_scale", "v6_mean", "v6_scale", "margin", "total", "v7_total", "d1"]],
        on=["home", "away", "day"],
    )
    joined = joined[joined.v7_mean.notna()]
    out: dict[str, Any] = {"rows_total": len(rows), "rows_joined": len(joined)}
    sp = joined[joined.market == "spread"].copy()

    def side_prob(mean: float, scale: float, line: float, selection: str) -> float:
        """P(selection covers) with the push mass excluded on integer lines (same rule as v6)."""
        if float(line).is_integer():
            away = float(stats.t.cdf((line - 0.5 - mean) / scale, df=8.0))
            home = 1.0 - float(stats.t.cdf((line + 0.5 - mean) / scale, df=8.0))
        else:
            away = float(stats.t.cdf((line - mean) / scale, df=8.0))
            home = 1.0 - away
        return away if selection == "away" else home

    sp["p_v7"] = [side_prob(r.v7_mean, r.v7_scale, r.line, r.selection) for r in sp.itertuples()]
    out["spread_rows"] = len(sp)
    out["spread_sample_0_3_share"] = round(float((sp.current_season_sample <= 3).mean()), 3)
    for label, col in (("v6_fixed_as_replayed", "p_fixed"), ("v7", "p_v7")):
        p = sp[col].to_numpy(float)
        o = sp.outcome.to_numpy(float)
        hi = p >= 0.8
        out[label] = {
            "brier_recorded_side": round(float(np.mean((p - o) ** 2)), 4),
            "mean_p": round(float(p.mean()), 3),
            "hit_rate": round(float(o.mean()), 3),
            "n_p_ge_0.8": int(hi.sum()),
            "hit_rate_p_ge_0.8": round(float(o[hi].mean()), 3) if hi.any() else None,
            "mean_p_p_ge_0.8": round(float(p[hi].mean()), 3) if hi.any() else None,
            "n_p_ge_0.7": int((p >= 0.7).sum()),
            "hit_rate_p_ge_0.7": round(float(o[p >= 0.7].mean()), 3) if (p >= 0.7).any() else None,
        }
    # market-relative: the spread line is the market's home margin; who is closer to the result?
    y = sp.margin.to_numpy(float)
    line = sp.line.to_numpy(float)
    model = sp.v7_mean.to_numpy(float)
    v6 = sp.v6_mean.to_numpy(float)
    out["market_relative_thin"] = {
        "n": len(sp),
        "rmse_market_line": round(float(np.sqrt(np.mean((y - line) ** 2))), 3),
        "rmse_v7": round(float(np.sqrt(np.mean((y - model) ** 2))), 3),
        "rmse_v6": round(float(np.sqrt(np.mean((y - v6) ** 2))), 3),
        "corr_v7_minus_line_vs_result_minus_line": round(float(np.corrcoef(model - line, y - line)[0, 1]), 3),
        "corr_v6_minus_line_vs_result_minus_line": round(float(np.corrcoef(v6 - line, y - line)[0, 1]), 3),
        "mean_v7_minus_line": round(float(np.mean(model - line)), 3),
        "mean_v6_minus_line": round(float(np.mean(v6 - line)), 3),
        "mean_result_minus_line": round(float(np.mean(y - line)), 3),
    }
    big = sp.line.to_numpy(float) >= 25
    out["market_relative_thin"]["line_ge_25"] = {
        "n": int(big.sum()),
        "mean_line": round(float(line[big].mean()), 2) if big.any() else None,
        "mean_v6": round(float(v6[big].mean()), 2) if big.any() else None,
        "mean_v7": round(float(model[big].mean()), 2) if big.any() else None,
        "mean_result": round(float(y[big].mean()), 2) if big.any() else None,
    }
    tot = joined[(joined.market == "total") & joined.v7_total.notna()]
    if len(tot):
        yt = tot.total.to_numpy(float)
        tline = tot.line.to_numpy(float)
        out["total_rows_market_relative_thin"] = {
            "n": len(tot),
            "rmse_market_line": round(float(np.sqrt(np.mean((yt - tline) ** 2))), 3),
            "rmse_v7_total": round(float(np.sqrt(np.mean((yt - tot.v7_total.to_numpy(float)) ** 2))), 3),
            "rmse_v6_total": round(float(np.sqrt(np.mean((yt - tot.v6_total.to_numpy(float)) ** 2))), 3)
            if "v6_total" in tot
            else None,
        }
    return out


def run_freeze(components: pd.DataFrame) -> V7Config:
    config = fit_frozen_config(components)
    payload = {
        "model": "ncaab-v7-shadow",
        "status": "SHADOW_RESEARCH_INERT",
        "frozen_before_holdout_read": True,
        "fit_windows": "warm 2025-11-03..2025-11-30 + train 2025-12-01..2026-02-19; validation 2026-02-20..2026-03-18 selected",
        "carryover_weight_declared_ex_ante": 0.5,
        "config": dataclasses.asdict(config),
    }
    FROZEN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config


def load_frozen() -> V7Config:
    cfg = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))["config"]
    for key in (
        "margin_names",
        "margin_coef",
        "nond1_names",
        "nond1_coef",
        "scale_edges",
        "scale_values",
        "total_names",
        "total_coef",
    ):
        cfg[key] = tuple(cfg[key])
    return V7Config(**cfg)


def run_dev_calibration(components: pd.DataFrame, config: V7Config) -> dict[str, Any]:
    """Dev-side evidence for the frozen config: warm/train (fit) and validation (selection)."""
    df = components.copy()
    df["d1"] = df.d1_home & df.d1_away
    fit = df[df.split.isin(["warm", "train"])]
    out: dict[str, Any] = {}
    frame = add_comparators(v7_frame(df[df.split != "holdout"], config), fit, config)
    for sp, g in frame.groupby("split"):
        out[sp] = evaluate_window(g, config)
    return out


def descriptive_holdout(hold: pd.DataFrame) -> dict[str, Any]:
    """Diagnostics of the single holdout look (nothing is selected from these)."""
    d1 = hold[hold.d1]
    out: dict[str, Any] = {"slope_of_actual_on_predicted_d1": {}, "d1_rmse_by_sample": {}}
    for label, col in (("v6", "v6_mean"), ("ridge_c0", "ridge_c0"), ("v7", "v7_mean")):
        beta, se = ols_hc1(d1[col].to_numpy(float).reshape(-1, 1), d1.margin.to_numpy(float))
        out["slope_of_actual_on_predicted_d1"][label] = {
            "intercept": [round(float(beta[0]), 3), round(float(se[0]), 3)],
            "slope": [round(float(beta[1]), 3), round(float(se[1]), 3)],
        }
    for lo, hi, label in ((0, 0, "0"), (1, 2, "1-2"), (3, 7, "3-7"), (8, 999, "8+")):
        g = d1[(d1["sample"] >= lo) & (d1["sample"] <= hi)]
        if len(g):
            out["d1_rmse_by_sample"][label] = {
                "n": len(g),
                "v6": err_stats(g.margin.to_numpy(float), g.v6_mean.to_numpy(float)),
                "v7": err_stats(g.margin.to_numpy(float), g.v7_mean.to_numpy(float)),
            }
    nonneutral = d1[~d1.neutral]
    out["d1_non_neutral"] = {
        "v6": err_stats(nonneutral.margin.to_numpy(float), nonneutral.v6_mean.to_numpy(float)),
        "v7": err_stats(nonneutral.margin.to_numpy(float), nonneutral.v7_mean.to_numpy(float)),
    }
    out["d1_away_favourite_bias"] = {
        label: {
            "home_fav": round(float((nonneutral.margin - nonneutral[col])[nonneutral[col] > 0].mean()), 3),
            "away_fav": round(float((nonneutral.margin - nonneutral[col])[nonneutral[col] < 0].mean()), 3),
            "n_away_fav": int((nonneutral[col] < 0).sum()),
        }
        for label, col in (("v6", "v6_mean"), ("v7", "v7_mean"))
    }
    return out


def run_final(components: pd.DataFrame) -> dict[str, Any]:
    """ONE read of the locked holdout with the frozen config."""
    config = load_frozen()
    df = components.copy()
    df["d1"] = df.d1_home & df.d1_away
    fit = df[df.split.isin(["warm", "train"])]
    hold = add_comparators(v7_frame(df[df.split == "holdout"], config), fit, config)
    early = hold[hold.day >= "2026-10-01"]
    late = hold[hold.day < "2026-10-01"]
    out: dict[str, Any] = {
        "config": dataclasses.asdict(config),
        "holdout_all": evaluate_window(hold, config),
        "holdout_2025_26_tail": evaluate_window(late, config) if len(late) else {},
        "holdout_2026_27_early": evaluate_window(early, config) if len(early) else {},
        "carryover": carryover_sensitivity(hold, config),
        "non_d1_decisions": decision_variants(hold, config),
        "replay": replay_check(hold, config),
        "descriptive": descriptive_holdout(hold),
    }
    return out


# ----------------------------------------------------------------------------- report

# Verdicts are written against the numbers in the stage JSONs (ideas / isolated / parity /
# dev_calibration / final); "dev" = warm+train fit with validation as the non-degradation check,
# "holdout" = the single look. Wording is deliberately conservative.
VERDICTS: list[dict[str, str]] = [
    {
        "item": "1",
        "idea": "mismatch rescale / heavier tail",
        "verdict": "SUPPORTED for early season and large gaps; NOT a mature-season gain",
        "evidence": (
            "v6 slope of actual on predicted D1 margin: 1.62 (Nov-2025), 1.43 (Dec-Feb), 1.17 (Mar), 1.61 on the "
            "holdout. A single slope fitted on warm+train (1.35) makes validation worse (RMSE 11.192 vs 11.109, "
            "+0.084 [-0.016,+0.186]); scheduling it by sample and adding a tail term beyond 12 points ties v6 on "
            "validation (11.107) and cuts the fit window 12.369 -> 11.983. Kept in v7 as the v6 tail term "
            "(coef 0.93). Holdout slope: v6 1.61, v7 1.07."
        ),
    },
    {
        "item": "2",
        "idea": "ridge (Massey) with conference-tier prior",
        "verdict": "SUPPORTED (core of v7)",
        "evidence": (
            "Ridge alone, coefficient 0.995 (no fitted parameter): D1 RMSE 12.026 vs v6 12.369 on warm+train, "
            "validation 11.090 vs 11.109 (-0.018 [-0.113,+0.058], a tie), Nov-2025 13.38 vs 13.93. Against its own "
            "refit control it is -0.25 [-0.42,-0.11]. Replacing v6's trend half through a refit (elo+ridge+rest) "
            "is worse than v6 on validation (+0.141 [+0.019,+0.268]) because early-season refit weights do not "
            "transfer to March, so v7 uses the ridge as the strength model instead. Holdout ridge-only D1 RMSE "
            "13.82 vs v6 14.20; v7 vs ridge-only -0.54 [-1.17,+0.03] (not separable). lam=5 not re-tuned."
        ),
    },
    {
        "item": "3",
        "idea": "last-season carryover (weight 0.5-1)",
        "verdict": "WEAKLY SUPPORTED, selectable only on the holdout (untestable in dev)",
        "evidence": (
            "Data starts 2025-11-03, so no dev game has a prior season; 0.5 was declared ex ante. Holdout D1 "
            "(n=688, 85 of them 2025-26 games unaffected): v7 form RMSE carry 0 = 13.425 (bias -0.06), 0.5 = "
            "13.283 (bias +1.94), 1.0 = 13.355; raw ridge 13.824 / 13.472 / 13.523. An earlier read-only agent "
            "saw the same direction on this data, so this is not a blind confirmation. v6 already carries Elo "
            "and trend ratings over at retention 0.35."
        ),
    },
    {
        "item": "4",
        "idea": "non-D1 handling (rule or exclusion)",
        "verdict": "SUPPORTED as a policy: decline non-D1 picks; the fitted rule fixes bias but not accuracy",
        "evidence": (
            "v6 on non-D1 games: RMSE 24.2 / 22.2, bias +14.2 / +12.4 (Nov-2025 / Dec-Feb; n=270 / 241; "
            "validation has 4). Linear rule fit on Nov-2025 only (0.608*v6 + 23.5) scores 17.9, bias -0.4 on "
            "Dec-Feb (offset-only 20.4, slope-only 20.5). Holdout non-D1 (n=213): v6 20.15, bias +8.2; rule "
            "18.97, bias -3.0 (bias is not stationary across Novembers). Declining them removes 23.6% of "
            "holdout games and takes v6 D1 RMSE from 15.8 to 14.2; declining also < 3 current-season games "
            "keeps 21.8%. No decline rule is wired in; it is a Main/Flat selection decision."
        ),
    },
    {
        "item": "5",
        "idea": "per-team / per-venue HCA (shrunk), neutral-site handling",
        "verdict": "NOT SUPPORTED",
        "evidence": (
            "Shrunk team home residual (k=10/20) gets coefficient 0.58/0.87 and moves validation RMSE by "
            "-0.032/-0.031 vs a single slope ([-0.059,+0.003]): immaterial. A listed-home edge on neutral "
            "games (coef +0.70) is worse on validation (+0.038 [+0.018,+0.065]); v6 neutral-game bias is +1.0 "
            "(n=74), -0.7 (n=273), +0.7 (n=268) by split, i.e. none. Flag-aware home court is fine: D1 non-neutral "
            "validation bias +0.22 (v6), fitted ridge HCA 3.2-4.2 mature (7.6 in Nov-2025, confounded by buy games)."
        ),
    },
    {
        "item": "6",
        "idea": "rest / schedule-density terms",
        "verdict": "NOT SUPPORTED (untestable at current back-to-back frequency)",
        "evidence": (
            "Residual of the ridge margin on v6's rest_diff (v6 weight 0.15): 0.17+-0.75 (Dec-Feb), 0.35+-1.31 "
            "(Mar), 2.07+-1.07 (Nov-2025), indistinguishable from 0, 0.15 or 1. Back-to-backs are rare "
            "(home/away team on a b2b: 14/29 games in Nov-2025, 13/16 in Dec-Feb, 6/7 in Mar); the b2b "
            "coefficient is +8.5+-2.0, +1.5+-1.5, +6.2+-4.0 (Dec-Feb, Mar, Nov-2025): unstable. Adding fitted rest terms worsens validation (+0.115 [+0.044,+0.202] vs none)."
        ),
    },
    {
        "item": "7",
        "idea": "possessions efficiency x pace decomposition for totals",
        "verdict": "NOT SUPPORTED out of sample (dev gain did not replicate)",
        "evidence": (
            "Ridge pace/efficiency total alone: validation RMSE 16.60 vs v6 16.57 (+0.04 [-0.16,+0.26]). "
            "Blend 0.55*v6 + 0.44*effpace: validation 16.42 (-0.152 [-0.247,-0.056]), warm+train 17.65 vs 17.89; "
            "holdout 18.11 vs 18.13 (-0.021 [-0.285,+0.233]), bias +1.02 vs -0.18, and the frozen total sd "
            "(17.65, no early-season widening) gives 0.498/0.790/0.890 coverage vs v6's 0.569/0.869/0.952. "
            "Matches the 2026-11-13 finding that exact possessions do not improve tempo."
        ),
    },
    {
        "item": "8",
        "idea": "home-vs-away favourite asymmetry",
        "verdict": "A v6 symptom, not a separate effect: NOT SUPPORTED as a term",
        "evidence": (
            "v6 bias, away-fav vs home-fav: -2.9 vs +0.8 (Dec-Feb), -0.5 vs +0.5 (Mar), -1.8 vs +3.8 (Nov-2025), "
            "-1.1 (n=54) vs +5.1 on the holdout. The ridge margin has no asymmetry (extra away-fav slope 0.06+-0.08, "
            "0.00+-0.16) and an explicit term adds nothing over a single slope (+0.015 [-0.019,+0.045]). v7 "
            "holdout: home-fav +2.4, away-fav -0.1."
        ),
    },
    {
        "item": "9",
        "idea": "v6 retune train/serve parity (HCA 5.0, Elo 45)",
        "verdict": "Code and parameters match exactly; one INPUT differs (neutral flag); retune survives it",
        "evidence": (
            "Ablation kwargs vs artifact factory: 0 parameter diffs; same inputs -> max |margin diff| 0.0 on "
            "1,069 games. The ablation never passes neutral_site while serving does; validation is 273/1,043 "
            "neutral games (ESPN flag; the TRNMNT proxy disagrees on 266 of 6,947). With the real flag the "
            "HCA grid 7.2/70, 6.0/55, 5.0/45 on validation has bias -1.22, -0.57, -0.06 (home-only inputs: "
            "-2.50, -1.62, -0.93): 5.0/45 is still selected (also best cover Brier 0.2104 and D1 RMSE 11.109). "
            "The post-retune positive live bias is early-season compression (slope 1.62 in Nov-2025, 1.61 on the "
            "holdout, intercept -2.0+-0.8 / -0.9+-0.7), not HCA."
        ),
    },
    {
        "item": "10",
        "idea": "v7 combined candidate on the locked holdout",
        "verdict": "Better calibrated early-season margin than v6; no market-relative claim possible",
        "evidence": (
            "Holdout (n=901, 688 D1; 816 are 2026-27 early season): D1 RMSE 13.28 vs v6 14.20 (-0.91 "
            "[-1.33,-0.60]), all-games 14.83 vs 15.81, bias +1.94 vs +4.14, slope 1.07 vs 1.61. Moneyline "
            "Brier 0.1159 vs 0.1156 (tie), cover Brier 0.2266 vs 0.2279, PIT 50/80/90 = 0.482/0.800/0.906. "
            "Validation (non-degradation guard): D1 RMSE 11.093 vs 11.109. Replay on 357 of 408 settled "
            "spread rows: v7 does not fix p>=0.8 (54 rows, hit 42.6% vs v6 60 rows, 50.0%) and recorded-side "
            "Brier is worse (0.3117 vs 0.2939). Market-relative content is thin and selected (see md)."
        ),
    },
]


def run_report() -> dict[str, Any]:
    stage = {
        name: json.loads((OUT / f"ncaab_v7_{name}.json").read_text(encoding="utf-8"))
        for name in ("parity", "ideas", "isolated", "dev_calibration", "final")
    }
    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    report = {
        "task": "261115-1m7",
        "status": "SHADOW_RESEARCH_INERT: nothing registered, promoted or served",
        "split": {
            "warm": "2025-11-03..2025-11-30",
            "train": f"{TRAIN_START}..{TRAIN_END}",
            "validation": f"{TRAIN_END}+1d..{VAL_END}",
            "holdout": f"{VAL_END}+1d..2026-11-14",
        },
        "verdicts": VERDICTS,
        "frozen_config": frozen,
        **stage,
    }
    (OUT / "ncaab_v7_research.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    stages: Sequence[str] = sys.argv[1:] or ["components"]
    if "components" in stages:
        df = build_components()
        print(len(df), df.split.value_counts().to_dict())
    if "report" in stages:
        run_report()
    if "isolated" in stages:
        res = run_isolated_effects(load_components())
        (OUT / "ncaab_v7_isolated.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if "freeze" in stages:
        cfg = run_freeze(load_components())
        dev = run_dev_calibration(load_components(), cfg)
        (OUT / "ncaab_v7_dev_calibration.json").write_text(
            json.dumps(dev, indent=2, default=str), encoding="utf-8"
        )
    if "final" in stages:
        res = run_final(load_components())
        (OUT / "ncaab_v7_final.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if "ideas" in stages:
        res = run_ideas(load_components())
        (OUT / "ncaab_v7_ideas.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if "parity" in stages:
        res = run_parity(load_components())
        (OUT / "ncaab_v7_parity.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
        print(json.dumps(res, indent=2, default=str))
