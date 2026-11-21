"""Fit and validate cfb-structural-v6.2 (quick-261121-v62, NCAAF overhaul plan step 3).

v6.2 = frozen v6 ratings and calibration (never refit here) plus
  (a) a discrete margin distribution with fitted key-number mass and a spread/total-dependent SD, and a total
      distribution with a per-total-bucket SD; moneyline, spread cover and total come from the same pmfs;
  (b) max-edge side selection (serving module, nothing to fit);
  (c) a blend ``fair = w * anchor + (1 - w) * v6`` on margin and total, w by least squares on the train window.

Windows are the v6 residual rule's: train <= 2022-11-11, validation 2022-11-12..2024-11-14, holdout >= 2024-11-15.
The fit reads train rows only; the ridge strength on the key multipliers is the one choice made on validation.
Holdout rows are dropped at load by ``fit`` (only their dates are counted, for the cohort check); ``holdout``
scores them once, after the artifact is hashed, and refuses a second read.

Every ESPN lined window here was read before (v6 residual test, v6-espn check, v6.1 comparison, line movement),
so every market-relative number is DESIGN-INFORMED, not an independent test. The historical anchor is ESPN's
pinned CLOSING median (untimestamped); serving uses the timestamped forward DraftKings capture as of decision
time, which is a less sharp anchor than a close, so the fitted w overstates how much a decision-time anchor knows.

Inputs live in the main checkout (untracked, read-only here) and are sha-pinned in the spec. Outputs:
  outputs/ncaaf/cfb_v62_spec.json, cfb_v62_report.{json,md}, cfb_v62_parity_fixture.json,
  config/models/cfb-structural-v6.2.json; large intermediates under E:/MP/data/research/ncaaf_v62/.

Usage (worktree root): OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python scripts/cfb_v62_fit.py fit
                       OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python scripts/cfb_v62_fit.py holdout
Percentages and points only; no American odds.
"""

# ruff: noqa: ISC004
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import ndtr

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

MAIN = Path(r"E:\MP")
BASE_ROWS = MAIN / "outputs" / "ncaaf" / "cfb_v6_base_rows.jsonl"
ESPN_LINES = MAIN / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
WORK = MAIN / "data" / "research" / "ncaaf_v62"
HOLDOUT_MARK = WORK / "holdout_read.json"
FROZEN = REPO / "outputs" / "ncaaf" / "cfb_v6_frozen_fit.json"
V6_ARTIFACT = REPO / "config" / "models" / "cfb-structural-v6.json"
OUT = REPO / "outputs" / "ncaaf"
SPEC = OUT / "cfb_v62_spec.json"
REPORT_JSON = OUT / "cfb_v62_report.json"
REPORT_MD = OUT / "cfb_v62_report.md"
FIXTURE = OUT / "cfb_v62_parity_fixture.json"
ARTIFACT = REPO / "config" / "models" / "cfb-structural-v6.2.json"

EXPECTED_FROZEN_SHA = "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"
TRAIN_END = "2022-11-11"
VAL_END = "2024-11-14"
BURN_IN_SEASON = 2016

# Margin / total support and the pmf construction. These are structure, not fitted values.
MARGIN_SUPPORT = 130  # |margin| <= 130 (the data's largest is 2 games above 90)
TOTAL_SUPPORT = 180
KEY_MAX = 28  # |margin| 1..28 carry a fitted multiplier; beyond it the multiplier is 1
RECENTRE_ITERATIONS = 4
SD_TOTAL_CENTRE = 55.0
SD_SCALE = 10.0
TOTAL_BUCKETS = ((0.0, 45.0), (45.0, 52.0), (52.0, 60.0), (60.0, 999.0))
SIDES_2022 = {3: 2.7, 7: 2.1, 10: 1.3, 4: 1.1, 1: 0.9, 6: 0.8, 2: 0.7, 5: 0.7, 8: 0.7, 11: 0.7, 9: 0.4}
RIDGE_GRID = (1.0, 10.0, 100.0)

# --------------------------------------------------------------------------------------------------------------
# The pinned fit rule, hashed before any validation number exists. It is also the artifact's promotion protocol
# hash: what was fitted, on which window, and how the one validation choice is made.
# --------------------------------------------------------------------------------------------------------------
RULE: dict[str, Any] = {
    "id": "cfb-v62-fit-rule-1",
    "base": "frozen cfb-structural-v6 (params sha256 6e38b14c...), calibrated margin and total; no refit",
    "anchor_historical": "ESPN pinned median of non-live providers (espn-lines-rule-1), closing, untimestamped",
    "anchor_serving": "DraftKings line from the forward ESPN pregame capture, observed_at <= decision and < kickoff",
    "cohort": "v6-covered lined events (cfb_v6_base_rows rows with a grid), season > 2016, both lines present",
    "windows": {
        "train": f"date <= {TRAIN_END}",
        "validation": f"{TRAIN_END} < date <= {VAL_END}",
        "holdout": f"date > {VAL_END}, scored at most once after the artifact is hashed",
    },
    "blend": "w = argmin sum (y - w*a - (1-w)*v)^2 on train, per market (margin, total); clipped to [0, 1]",
    "margin_pmf": (
        "p(m) proportional to [Phi((m+.5-loc)/sd) - Phi((m-.5-loc)/sd)] * g(|m|), g(0)=0 (no ties in "
        "the data), g(k)=exp(theta_k) for 1<=k<=28 else 1; loc solved by "
        f"{RECENTRE_ITERATIONS} Newton steps so E[m] equals the blended mean"
    ),
    "margin_sd": "sd = exp(s0 + s1*|mu|/10 + s2*(anchor total - 55)/10), MLE jointly with theta on train",
    "ridge": f"penalty lambda * sum(theta^2), lambda in {list(RIDGE_GRID)} chosen by validation margin log score",
    "total_pmf": "discretised normal at the blended total mean, sd per anchor-total bucket "
    f"{[list(b) for b in TOTAL_BUCKETS]}, MLE on train",
    "model_only_fallback": "v6 means, v6 slice SD tables, same theta (no anchor at decision time)",
    "metrics": "validation: Brier/log (moneyline, cover, over), integer log score, key mass, cover near 3/7, MAE",
}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ data
def load_rows(*, include_holdout: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    if frozen["params_sha256"] != EXPECTED_FROZEN_SHA:
        raise SystemExit("frozen v6 params sha mismatch")
    sel = frozen["selected"]
    gk = f"{sel['lambda']:g}|{sel['w_prev']:g}"
    mc, tc = frozen["params"]["margin_calibration"], frozen["params"]["total_calibration"]
    min_sample = int(frozen["params"]["established_min_sample"])

    lines: dict[str, dict[str, Any]] = {}
    for ln in ESPN_LINES.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            lines[str(rec["event_id"])] = rec.get("selected") or {}

    rows: list[dict[str, Any]] = []
    check: dict[str, list[tuple[float, float]]] = {"total": [], "spread": []}
    counts = {
        "total_lined_all_windows": 0,
        "spread_lined_all_windows": 0,
        "both_lined_all_windows": 0,
        "holdout_rows_not_read": 0,
        "windows": {"train": 0, "validation": 0},
    }
    with BASE_ROWS.open(encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            eid = str(r["event_id"])
            if eid not in lines or "grid" not in r or r["season"] <= BURN_IN_SEASON:
                continue
            sel_line = lines[eid]
            sp, tt = sel_line.get("home_spread_line"), sel_line.get("total_line")
            counts["total_lined_all_windows"] += tt is not None
            counts["spread_lined_all_windows"] += sp is not None
            if r["date"] <= TRAIN_END:
                pm0, pt0 = r["grid"][gk][:2]
                if tt is not None:
                    check["total"].append((tc["intercept"] + tc["slope"] * pt0 - tt, r["total"] - tt))
                if sp is not None:
                    check["spread"].append((mc["intercept"] + mc["slope"] * pm0 + sp, r["margin"] + sp))
            if sp is None or tt is None:
                continue
            counts["both_lined_all_windows"] += 1
            if r["date"] > VAL_END and not include_holdout:
                counts["holdout_rows_not_read"] += 1  # date only: outcome and projection never parsed
                continue
            pm, pt = r["grid"][gk][:2]
            window = (
                "train" if r["date"] <= TRAIN_END else ("validation" if r["date"] <= VAL_END else "holdout")
            )
            counts["windows"][window] = counts["windows"].get(window, 0) + 1
            slice_ = (
                "fbs_vs_fcs"
                if r["is_fcs"]
                else ("established" if int(r["sample_games"]) >= min_sample else "thin_sample")
            )
            rows.append(
                {
                    "event_id": eid,
                    "date": r["date"],
                    "window": window,
                    "neutral": bool(r["neutral"]),
                    "margin": float(r["margin"]),
                    "total": float(r["total"]),
                    "v6_margin": mc["intercept"] + mc["slope"] * pm,
                    "v6_total": tc["intercept"] + tc["slope"] * pt,
                    "spread_home": float(sp),
                    "a_margin": -float(sp),
                    "a_total": float(tt),
                    "slice": slice_,
                }
            )
    meta = {
        "grid_key": gk,
        "counts": counts,
        "frozen_params_sha256": frozen["params_sha256"],
        "v6_sd_tables": {
            "margin": frozen["params"]["margin_sd_table"],
            "total": frozen["params"]["total_sd_table"],
        },
        "train_check_pairs": check,
    }
    return rows, meta


def cohort_check(rows: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Reproduce the residual report's cohort and fit-window slopes before fitting anything."""
    c = meta["counts"]
    if (c["total_lined_all_windows"], c["spread_lined_all_windows"]) != (8279, 8376):
        raise SystemExit(f"cohort mismatch vs cfb_v6_residual: {c}")
    out = {}
    for market, pairs in meta["train_check_pairs"].items():
        x = np.array([p[0] for p in pairs])
        z = np.array([p[1] for p in pairs])
        b = float(((x - x.mean()) * (z - z.mean())).sum() / ((x - x.mean()) ** 2).sum())
        out[market] = {
            "train_n_own_lined_set": len(pairs),
            "train_slope_actual_minus_line_on_v6_minus_line": b,
        }
    # The residual report's fit-window slopes (each market on its own lined set): total -0.012, spread -0.009.
    for market, ref in (("total", -0.012), ("spread", -0.009)):
        if abs(out[market]["train_slope_actual_minus_line_on_v6_minus_line"] - ref) > 0.0006:
            raise SystemExit(f"fit-window slope for {market} does not reproduce the residual report: {out}")
    return out


# ------------------------------------------------------------------------------------------------ pmfs
M_GRID = np.arange(-MARGIN_SUPPORT, MARGIN_SUPPORT + 1, dtype=float)
T_GRID = np.arange(0, TOTAL_SUPPORT + 1, dtype=float)


def key_multipliers(theta: np.ndarray) -> np.ndarray:
    absm = np.abs(M_GRID).astype(int)
    g = np.ones_like(M_GRID)
    inner = (absm >= 1) & (absm <= KEY_MAX)
    g[inner] = np.exp(theta[absm[inner] - 1])
    g[absm == 0] = 0.0
    return g


def _interval_mass(grid: np.ndarray, loc: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """Normal mass on [k - .5, k + .5]; the upper tail uses survival differences so it does not cancel to 0."""
    edges = np.append(grid - 0.5, grid[-1] + 0.5)
    z = (edges[None, :] - loc[:, None]) / sd[:, None]
    cdf, sf = ndtr(z), ndtr(-z)
    return np.where(z[:, :-1] > 0, sf[:, :-1] - sf[:, 1:], cdf[:, 1:] - cdf[:, :-1])


def margin_pmf(mu: np.ndarray, sd: np.ndarray, g: np.ndarray) -> np.ndarray:
    loc = mu.astype(float).copy()
    for _ in range(RECENTRE_ITERATIONS):
        w = _interval_mass(M_GRID, loc, sd) * g[None, :]
        p = w / w.sum(axis=1, keepdims=True)
        mean = p @ M_GRID
        var = p @ (M_GRID**2) - mean**2
        loc = loc + (mu - mean) * sd**2 / var
    w = _interval_mass(M_GRID, loc, sd) * g[None, :]
    return w / w.sum(axis=1, keepdims=True)


def total_pmf(mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    w = _interval_mass(T_GRID, mu, sd)
    return w / w.sum(axis=1, keepdims=True)


def margin_sd(params: np.ndarray, mu: np.ndarray, total_anchor: np.ndarray) -> np.ndarray:
    s0, s1, s2 = params
    return np.exp(s0 + s1 * np.abs(mu) / SD_SCALE + s2 * (total_anchor - SD_TOTAL_CENTRE) / SD_SCALE)


def total_bucket(line: np.ndarray) -> np.ndarray:
    out = np.zeros(len(line), dtype=int)
    for i, (lo, hi) in enumerate(TOTAL_BUCKETS):
        out[(line >= lo) & (line < hi)] = i
    return out


# ------------------------------------------------------------------------------------------------ fit
def _arr(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


def fit_blend(train: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for market, (y, v, a) in {
        "margin": ("margin", "v6_margin", "a_margin"),
        "total": ("total", "v6_total", "a_total"),
    }.items():
        yy, vv, aa = _arr(train, y), _arr(train, v), _arr(train, a)
        d = aa - vv
        w_raw = float(((yy - vv) * d).sum() / (d * d).sum())
        out[market] = {"w_raw": w_raw, "w": min(1.0, max(0.0, w_raw))}
    return out


def _neg_ll(
    x: np.ndarray,
    mu: np.ndarray,
    tot: np.ndarray,
    idx: np.ndarray,
    lam: float,
    fixed_theta: np.ndarray | None,
) -> float:
    sdp = x[:3]
    theta = fixed_theta if fixed_theta is not None else x[3:]
    p = margin_pmf(mu, margin_sd(sdp, mu, tot), key_multipliers(theta))
    ll = np.log(np.maximum(p[np.arange(len(mu)), idx], 1e-300)).sum()
    pen = 0.0 if fixed_theta is not None else lam * float((theta**2).sum())
    return float(-ll + pen)


def fit_margin(
    train_mu: np.ndarray,
    train_tot: np.ndarray,
    train_margin: np.ndarray,
    lam: float,
    fixed_theta: np.ndarray | None = None,
) -> dict[str, Any]:
    idx = (np.clip(train_margin, -MARGIN_SUPPORT, MARGIN_SUPPORT) + MARGIN_SUPPORT).astype(int)
    x0 = [np.log(15.5), 0.0, 0.0]
    if fixed_theta is None:
        x0 += [float(np.log(SIDES_2022.get(k, 1.0))) for k in range(1, KEY_MAX + 1)]
    t0 = time.time()
    res = minimize(
        _neg_ll,
        np.array(x0),
        args=(train_mu, train_tot, idx, lam, fixed_theta),
        method="L-BFGS-B",
        options={"maxiter": 400},
    )
    theta = fixed_theta if fixed_theta is not None else res.x[3:]
    return {
        "lambda": lam,
        "sd_params": [float(v) for v in res.x[:3]],
        "theta": [float(v) for v in theta],
        "neg_ll": float(res.fun),
        "converged": bool(res.success),
        "message": str(res.message),
        "seconds": round(time.time() - t0, 1),
    }


def fit_total_sds(mu: np.ndarray, line: np.ndarray, actual: np.ndarray) -> list[float]:
    b = total_bucket(line)
    sds = []
    for i in range(len(TOTAL_BUCKETS)):
        m = b == i
        idx = np.clip(actual[m], 0, TOTAL_SUPPORT).astype(int)

        def nll(log_sd: float, m: np.ndarray = m, idx: np.ndarray = idx) -> float:
            p = total_pmf(mu[m], np.full(int(m.sum()), np.exp(log_sd)))
            return float(-np.log(np.maximum(p[np.arange(len(idx)), idx], 1e-300)).sum())

        sds.append(float(np.exp(minimize_scalar(nll, bounds=(np.log(8), np.log(30)), method="bounded").x)))
    return sds


# ------------------------------------------------------------------------------------------------ evaluation
def market_probs_from_pmfs(
    pm: np.ndarray, pt: np.ndarray, spread_home: np.ndarray, total_line: np.ndarray
) -> dict[str, np.ndarray]:
    home_win = pm[:, M_GRID > 0].sum(axis=1)
    thr = -spread_home  # home covers when margin > -spread_home
    cover = (pm * (M_GRID[None, :] > thr[:, None])).sum(axis=1)
    push_s = (pm * (M_GRID[None, :] == thr[:, None])).sum(axis=1)
    over = (pt * (T_GRID[None, :] > total_line[:, None])).sum(axis=1)
    push_t = (pt * (T_GRID[None, :] == total_line[:, None])).sum(axis=1)
    return {
        "home_win": home_win,
        "away_win": 1.0 - home_win,
        "home_cover": cover,
        "push_spread": push_s,
        "away_cover": 1.0 - cover - push_s,
        "over": over,
        "push_total": push_t,
        "under": 1.0 - over - push_t,
    }


def _brier_log(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return {
        "n": len(y),
        "brier": float(((p - y) ** 2).mean()),
        "log_loss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
        "mean_p": float(p.mean()),
        "rate": float(y.mean()),
    }


def evaluate(
    rows: list[dict[str, Any]],
    probs: dict[str, np.ndarray],
    pm: np.ndarray | None,
    pt: np.ndarray | None,
    mu_m: np.ndarray,
    mu_t: np.ndarray,
) -> dict[str, Any]:
    margin, total = _arr(rows, "margin"), _arr(rows, "total")
    sp, tl, a = _arr(rows, "spread_home"), _arr(rows, "a_total"), _arr(rows, "a_margin")
    out: dict[str, Any] = {"n": len(rows)}
    out["moneyline_home"] = _brier_log(probs["home_win"], (margin > 0).astype(float))
    cov_mask = (margin + sp) != 0
    p_cov = probs["home_cover"] / np.maximum(1e-12, probs["home_cover"] + probs["away_cover"])
    out["spread_home_cover_ex_push"] = _brier_log(
        p_cov[cov_mask], ((margin + sp) > 0)[cov_mask].astype(float)
    )
    tot_mask = total != tl
    p_ov = probs["over"] / np.maximum(1e-12, probs["over"] + probs["under"])
    out["total_over_ex_push"] = _brier_log(p_ov[tot_mask], (total > tl)[tot_mask].astype(float))
    out["mae_margin_mean_vs_actual"] = float(np.abs(margin - mu_m).mean())
    out["mae_total_mean_vs_actual"] = float(np.abs(total - mu_t).mean())
    out["mean_abs_margin_mean_minus_line"] = float(np.abs(mu_m - a).mean())
    out["mean_abs_total_mean_minus_line"] = float(np.abs(mu_t - tl).mean())
    out["mae_line_margin"] = float(np.abs(margin - a).mean())
    out["mae_line_total"] = float(np.abs(total - tl).mean())
    if pm is not None:
        idx = (np.clip(margin, -MARGIN_SUPPORT, MARGIN_SUPPORT) + MARGIN_SUPPORT).astype(int)
        out["margin_integer_log_score"] = float(
            np.log(np.maximum(pm[np.arange(len(rows)), idx], 1e-300)).mean()
        )
        absm = np.abs(margin)
        out["key_mass"] = {}
        for k in (1, 2, 3, 4, 6, 7, 8, 10, 14, 17, 21):
            pred = pm[:, M_GRID == k].ravel() + pm[:, M_GRID == -k].ravel()
            out["key_mass"][str(k)] = {
                "pred_pct": 100 * float(pred.mean()),
                "actual_pct": 100 * float((absm == k).mean()),
            }
        out["near_key"] = {}
        fav_sign = np.where(a >= 0, 1.0, -1.0)
        fav_margin = margin * fav_sign
        for key, (lo, hi) in {"3": (2.5, 3.5), "7": (6.5, 7.5)}.items():
            m = (np.abs(sp) >= lo) & (np.abs(sp) <= hi)
            k = int(key)
            blk: dict[str, Any] = {"n": int(m.sum())}
            for label, cond in (
                (f"fav_by_ge_{k}", lambda x, k=k: x >= k),
                (f"fav_by_ge_{k + 1}", lambda x, k=k: x >= k + 1),
                (f"fav_by_eq_{k}", lambda x, k=k: x == k),
            ):
                pr = (pm[m] * cond(M_GRID[None, :] * fav_sign[m][:, None])).sum(axis=1)
                blk[label] = {
                    "pred_pct": 100 * float(pr.mean()),
                    "actual_pct": 100 * float(cond(fav_margin[m]).mean()),
                }
            cm = m & cov_mask
            blk["home_cover"] = _brier_log(p_cov[cm], ((margin + sp) > 0)[cm].astype(float))
            out["near_key"][key] = blk
    if pt is not None:
        idx = np.clip(total, 0, TOTAL_SUPPORT).astype(int)
        out["total_integer_log_score"] = float(
            np.log(np.maximum(pt[np.arange(len(rows)), idx], 1e-300)).mean()
        )
    return out


def v6_engine_probs(
    rows: list[dict[str, Any]], sd_tables: dict[str, dict[str, float]]
) -> dict[str, np.ndarray]:
    """v6 as served: its NB joint engine at the v6 means and slice SDs (own RNG, research only)."""
    from model_prediction.models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine

    keys = ("home_win", "home_cover", "away_cover", "push_spread", "over", "under", "push_total")
    out: dict[str, list[float]] = {k: [] for k in keys}
    for i, r in enumerate(rows):
        mu_home = max(3.0, (r["v6_total"] + r["v6_margin"]) / 2.0)
        mu_away = max(3.0, (r["v6_total"] - r["v6_margin"]) / 2.0)
        eng = CFBJointDistributionEngine(
            distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL,
            margin_sd=sd_tables["margin"][r["slice"]],
            total_sd=sd_tables["total"][r["slice"]],
            n_simulations=50_000,
            random_seed=1000 + i,
        )
        j = eng.compute_market_probabilities(
            mu_away=mu_away, mu_home=mu_home, spread_home_line=r["spread_home"], total_line=r["a_total"]
        )
        for k, v in (
            ("home_win", j.p_home_win),
            ("home_cover", j.p_home_cover),
            ("away_cover", j.p_away_cover),
            ("push_spread", j.p_push_spread),
            ("over", j.p_over),
            ("under", j.p_under),
            ("push_total", j.p_push_total),
        ):
            out[k].append(float(v))
    return {k: np.array(v) for k, v in out.items()}


def score_models(rows: list[dict[str, Any]], spec: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    w_m, w_t = spec["blend"]["margin"]["w"], spec["blend"]["total"]["w"]
    a_m, a_t, v_m, v_t = (
        _arr(rows, "a_margin"),
        _arr(rows, "a_total"),
        _arr(rows, "v6_margin"),
        _arr(rows, "v6_total"),
    )
    sp = _arr(rows, "spread_home")
    theta = np.array(spec["margin"]["theta"])
    sdp = np.array(spec["margin"]["sd_params"])
    tsds = np.array(spec["total"]["bucket_sds"])
    res: dict[str, Any] = {}

    def run(
        name: str, mu_m: np.ndarray, mu_t: np.ndarray, sd_m: np.ndarray, g: np.ndarray, sd_t: np.ndarray
    ) -> None:
        pm, pt = margin_pmf(mu_m, sd_m, g), total_pmf(mu_t, sd_t)
        res[name] = evaluate(rows, market_probs_from_pmfs(pm, pt, sp, a_t), pm, pt, mu_m, mu_t)

    mu_m, mu_t = w_m * a_m + (1 - w_m) * v_m, w_t * a_t + (1 - w_t) * v_t
    run("v6.2", mu_m, mu_t, margin_sd(sdp, mu_m, mu_t), key_multipliers(theta), tsds[total_bucket(mu_t)])
    smooth = spec["margin_smooth_baseline"]
    run(
        "anchor_only_smooth",
        a_m,
        a_t,
        margin_sd(np.array(smooth["sd_params"]), a_m, a_t),
        key_multipliers(np.zeros(KEY_MAX)),
        tsds[total_bucket(a_t)],
    )
    sides = np.array([np.log(SIDES_2022.get(k, 1.0)) for k in range(1, KEY_MAX + 1)])
    run(
        "anchor_sides2022_weights",
        a_m,
        a_t,
        margin_sd(sdp, a_m, a_t),
        key_multipliers(sides),
        tsds[total_bucket(a_t)],
    )
    sdm_tab, sdt_tab = meta["v6_sd_tables"]["margin"], meta["v6_sd_tables"]["total"]
    run(
        "v6.2_model_only_fallback",
        v_m,
        v_t,
        np.array([sdm_tab[r["slice"]] for r in rows]),
        key_multipliers(theta),
        np.array([sdt_tab[r["slice"]] for r in rows]),
    )
    res["v6_engine"] = evaluate(rows, v6_engine_probs(rows, meta["v6_sd_tables"]), None, None, v_m, v_t)
    return res


# ------------------------------------------------------------------------------------------------ fixture
def parity_cases(spec: dict[str, Any], sd_tables: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    cases = []
    raw = [
        # (anchored, anchor margin, anchor total, v6 margin, v6 total, slice, spread_home line, total line)
        (True, 3.0, 52.5, 6.1, 55.0, "established", -3.0, 52.5),
        (True, 7.0, 48.0, 2.0, 50.2, "established", -6.5, 48.0),
        (True, -7.5, 61.0, -12.0, 58.0, "thin_sample", 7.5, 61.0),
        (True, 0.0, 45.0, 0.0, 45.0, "established", 0.0, 45.0),
        (True, 14.0, 55.5, 17.3, 60.1, "established", -14.0, 56.0),
        (True, 24.5, 64.0, 30.2, 66.0, "fbs_vs_fcs", -24.5, 63.5),
        (True, -2.5, 39.5, 1.5, 42.0, "established", 3.5, 40.0),
        (False, None, None, 4.2, 53.3, "established", -3.0, 53.5),
        (False, None, None, -10.8, 47.9, "thin_sample", 10.5, 47.0),
        (False, None, None, 35.0, 62.0, "fbs_vs_fcs", -34.5, 61.5),
    ]
    w_m, w_t = spec["blend"]["margin"]["w"], spec["blend"]["total"]["w"]
    theta = np.array(spec["margin"]["theta"])
    for anchored, am, at, vm, vt, sl, spl, tl in raw:
        if anchored:
            mu_m, mu_t = w_m * am + (1 - w_m) * vm, w_t * at + (1 - w_t) * vt
            sd_m = margin_sd(np.array(spec["margin"]["sd_params"]), np.array([mu_m]), np.array([mu_t]))
            sd_t = np.array(spec["total"]["bucket_sds"])[total_bucket(np.array([mu_t]))]
        else:
            mu_m, mu_t = vm, vt
            sd_m, sd_t = np.array([sd_tables["margin"][sl]]), np.array([sd_tables["total"][sl]])
        pm = margin_pmf(np.array([mu_m]), sd_m, key_multipliers(theta))
        pt = total_pmf(np.array([mu_t]), sd_t)
        pr = market_probs_from_pmfs(pm, pt, np.array([spl]), np.array([tl]))
        cases.append(
            {
                "inputs": {
                    "anchor_margin": am,
                    "anchor_total": at,
                    "v6_margin": vm,
                    "v6_total": vt,
                    "slice": sl,
                    "spread_home_line": spl,
                    "total_line": tl,
                },
                "expected": {
                    "mu_margin": mu_m,
                    "mu_total": mu_t,
                    "margin_sd": float(sd_m[0]),
                    "total_sd": float(sd_t[0]),
                    **{k: float(v[0]) for k, v in pr.items()},
                    "pmf_margin_at_3": float(pm[0, M_GRID == 3][0]),
                    "pmf_margin_at_minus_7": float(pm[0, M_GRID == -7][0]),
                },
            }
        )
    return cases


# ------------------------------------------------------------------------------------------------ commands
def _fit_one(args: tuple[float, list[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    lam, train, blend = args
    w = blend["margin"]["w"]
    wt = blend["total"]["w"]
    mu = w * _arr(train, "a_margin") + (1 - w) * _arr(train, "v6_margin")
    tot = wt * _arr(train, "a_total") + (1 - wt) * _arr(train, "v6_total")
    return fit_margin(mu, tot, _arr(train, "margin"), lam)


def _val_margin_ll(fit: dict[str, Any], val: list[dict[str, Any]], blend: dict[str, Any]) -> float:
    w, wt = blend["margin"]["w"], blend["total"]["w"]
    mu = w * _arr(val, "a_margin") + (1 - w) * _arr(val, "v6_margin")
    tot = wt * _arr(val, "a_total") + (1 - wt) * _arr(val, "v6_total")
    p = margin_pmf(
        mu, margin_sd(np.array(fit["sd_params"]), mu, tot), key_multipliers(np.array(fit["theta"]))
    )
    idx = (np.clip(_arr(val, "margin"), -MARGIN_SUPPORT, MARGIN_SUPPORT) + MARGIN_SUPPORT).astype(int)
    return float(np.log(np.maximum(p[np.arange(len(val)), idx], 1e-300)).mean())


def cmd_fit(_: argparse.Namespace) -> int:
    print("pinned rule", RULE["id"], "sha256", RULE_SHA256, flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    rows, meta = load_rows(include_holdout=False)
    cohort = cohort_check(rows, meta)
    train = [r for r in rows if r["window"] == "train"]
    val = [r for r in rows if r["window"] == "validation"]
    print("cohort", meta["counts"], cohort, flush=True)

    blend = fit_blend(train)
    print("blend", blend, flush=True)
    with ProcessPoolExecutor(max_workers=len(RIDGE_GRID)) as ex:
        fits = list(ex.map(_fit_one, [(lam, train, blend) for lam in RIDGE_GRID]))
    for f in fits:
        f["validation_margin_log_score"] = _val_margin_ll(f, val, blend)
        print(
            "ridge",
            f["lambda"],
            "val ll",
            round(f["validation_margin_log_score"], 5),
            f["seconds"],
            "s",
            f["converged"],
            flush=True,
        )
    best = max(fits, key=lambda f: f["validation_margin_log_score"])
    # Smooth baseline: same SD form, no key multipliers, anchor mean (train fit).
    smooth = fit_margin(
        _arr(train, "a_margin"),
        _arr(train, "a_total"),
        _arr(train, "margin"),
        0.0,
        fixed_theta=np.zeros(KEY_MAX),
    )
    wt = blend["total"]["w"]
    mu_t = wt * _arr(train, "a_total") + (1 - wt) * _arr(train, "v6_total")
    bucket_sds = fit_total_sds(mu_t, mu_t, _arr(train, "total"))
    print("total bucket sds", bucket_sds, flush=True)

    inputs = {
        "base_rows": {"path": str(BASE_ROWS), "sha256": file_sha256(BASE_ROWS)},
        "espn_lines": {"path": str(ESPN_LINES), "sha256": file_sha256(ESPN_LINES)},
        "frozen_v6_fit": {"path": "outputs/ncaaf/cfb_v6_frozen_fit.json", "sha256": file_sha256(FROZEN)},
        "v6_artifact": {
            "path": "config/models/cfb-structural-v6.json",
            "artifact_hash": json.loads(V6_ARTIFACT.read_text(encoding="utf-8"))["artifact_hash"],
        },
    }
    spec: dict[str, Any] = {
        "model_version": "cfb-structural-v6.2",
        "rule": RULE,
        "rule_sha256": RULE_SHA256,
        "script_sha256": file_sha256(Path(__file__)),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "inputs": inputs,
        "counts": meta["counts"],
        "cohort_check": cohort,
        "structure": {
            "margin_support": MARGIN_SUPPORT,
            "total_support": TOTAL_SUPPORT,
            "key_max": KEY_MAX,
            "recentre_iterations": RECENTRE_ITERATIONS,
            "sd_total_centre": SD_TOTAL_CENTRE,
            "sd_scale": SD_SCALE,
            "total_buckets": [list(b) for b in TOTAL_BUCKETS],
        },
        "blend": blend,
        "ridge_selection": [
            {k: f[k] for k in ("lambda", "neg_ll", "validation_margin_log_score", "converged")} for f in fits
        ],
        "margin": {k: best[k] for k in ("lambda", "sd_params", "theta", "neg_ll", "converged")},
        "margin_smooth_baseline": {k: smooth[k] for k in ("sd_params", "neg_ll")},
        "total": {"bucket_sds": bucket_sds},
        "holdout_reads": 0,
        "holdout_reads_note": (
            "0 by this script at fit time. The holdout window's v6-vs-line numbers were read "
            "before (v6-espn check, v6.1 comparison), so it is not a clean holdout."
        ),
    }
    spec["key_multipliers"] = {
        str(k): float(np.exp(t)) for k, t in zip(range(1, KEY_MAX + 1), best["theta"], strict=True)
    }
    SPEC.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")

    print("scoring validation", flush=True)
    val_scores = score_models(val, spec, meta)
    report = {
        "spec_sha256": file_sha256(SPEC),
        "rule_sha256": RULE_SHA256,
        "label": "DESIGN-INFORMED: every lined window was read before",
        "validation": val_scores,
        "holdout": "NOT READ",
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    FIXTURE.write_text(
        json.dumps(
            {"spec_sha256": report["spec_sha256"], "cases": parity_cases(spec, meta["v6_sd_tables"])},
            indent=2,
        ),
        encoding="utf-8",
    )
    write_artifact(spec, report["spec_sha256"])
    REPORT_MD.write_text(render_md(spec, report), encoding="utf-8")
    print("wrote", SPEC, REPORT_JSON, FIXTURE, ARTIFACT, REPORT_MD)
    return 0


def write_artifact(spec: dict[str, Any], spec_sha: str) -> None:
    from model_prediction.production_registry import compute_artifact_hash

    v6 = json.loads(V6_ARTIFACT.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        "model_version": "cfb-structural-v6.2",
        "base_model_version": "cfb-structural-v6",
        "base_artifact_hash": v6["artifact_hash"],
        "v6_frozen_params_sha256": EXPECTED_FROZEN_SHA,
        "league": "NCAAF",
        "sport": "ncaaf",
        "schema_version": "1",
        "method": "cfb_structural_v6_ratings_anchor_blend_discrete_key_number_pmf",
        "note": (
            "CHALLENGER (quick-261121-v62), never a champion: priced alongside v5, Flat/research tiers only. "
            "Frozen v6 ratings + walk-forward blend with a timestamped DraftKings anchor + a discrete margin "
            "pmf with fitted key-number mass + max-edge side selection. With w near 1 it is effectively "
            "'anchor vs Polymarket ask'. Fit on train only; every market-relative number is design-informed."
        ),
        "blend": {
            "w_margin": spec["blend"]["margin"]["w"],
            "w_total": spec["blend"]["total"]["w"],
            "w_margin_raw": spec["blend"]["margin"]["w_raw"],
            "w_total_raw": spec["blend"]["total"]["w_raw"],
        },
        "margin_distribution": {
            "support": MARGIN_SUPPORT,
            "key_max": KEY_MAX,
            "recentre_iterations": RECENTRE_ITERATIONS,
            "key_log_multipliers": spec["margin"]["theta"],
            "sd_params": spec["margin"]["sd_params"],
            "sd_total_centre": SD_TOTAL_CENTRE,
            "sd_scale": SD_SCALE,
            "tie_rule": "g(0)=0; the regulation-tie mass is "
            "renormalised over non-zero margins, symmetric at pick'em (no home bias)",
        },
        "total_distribution": {
            "support": TOTAL_SUPPORT,
            "buckets": [list(b) for b in TOTAL_BUCKETS],
            "bucket_sds": spec["total"]["bucket_sds"],
        },
        "anchor": {
            "source": "espn_cfb_pregame_capture",
            "provider_name": "DraftKings",
            "provider_id": "100",
            "fields": {"margin": "-home_spread_current", "total": "total_current"},
            "point_in_time": "core_odds.observed_at_utc <= decision time and < event start; latest wins",
        },
        "side_selection": "max_edge",
        "training": {
            "spec_path": "outputs/ncaaf/cfb_v62_spec.json",
            "spec_sha256": spec_sha,
            "rule_sha256": RULE_SHA256,
            "train_end": TRAIN_END,
            "validation_end": VAL_END,
            "train_n": spec["counts"]["windows"]["train"],
            "validation_n": spec["counts"]["windows"]["validation"],
            "ridge_lambda": spec["margin"]["lambda"],
            "inputs": spec["inputs"],
        },
        "qualification": {
            "status": "challenger",
            "qualified": False,
            "note": "Flat/research shakedown only; promotion only via model_promotion promote.",
        },
        "frozen_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "promotion_protocol_hash": RULE_SHA256,
    }
    payload["artifact_hash"] = compute_artifact_hash(payload)
    ARTIFACT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_holdout(_: argparse.Namespace) -> int:
    from model_prediction.production_registry import compute_artifact_hash

    if HOLDOUT_MARK.exists():
        raise SystemExit(f"holdout already read once ({HOLDOUT_MARK}); refusing a second read")
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if art["artifact_hash"] != compute_artifact_hash(art):
        raise SystemExit("artifact hash mismatch: the holdout is scored only against the hashed artifact")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if file_sha256(SPEC) != art["training"]["spec_sha256"]:
        raise SystemExit("spec changed after the artifact was built")
    HOLDOUT_MARK.write_text(
        json.dumps({"read_at_utc": datetime.now(UTC).isoformat(), "artifact_hash": art["artifact_hash"]}),
        encoding="utf-8",
    )
    rows, meta = load_rows(include_holdout=True)
    hold = [r for r in rows if r["window"] == "holdout"]
    scores = score_models(hold, spec, meta)
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    report["holdout"] = {
        "reads_by_this_task": 1,
        "artifact_hash": art["artifact_hash"],
        "note": "single read after hashing; the window's v6-vs-line numbers were seen before",
        "scores": scores,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    REPORT_MD.write_text(render_md(spec, report), encoding="utf-8")
    print("holdout scored once; wrote", REPORT_JSON, REPORT_MD)
    return 0


# ------------------------------------------------------------------------------------------------ rendering
def _row(name: str, s: dict[str, Any]) -> str:
    ml, cv, ov = s["moneyline_home"], s["spread_home_cover_ex_push"], s["total_over_ex_push"]
    il = s.get("margin_integer_log_score")
    tl = s.get("total_integer_log_score")
    return (
        f"| {name} | {ml['brier']:.5f} / {ml['log_loss']:.5f} | {cv['brier']:.5f} / {cv['log_loss']:.5f} | "
        f"{ov['brier']:.5f} / {ov['log_loss']:.5f} | {'' if il is None else f'{il:.4f}'} | "
        f"{'' if tl is None else f'{tl:.4f}'} | {s['mae_margin_mean_vs_actual']:.3f} / "
        f"{s['mae_total_mean_vs_actual']:.3f} | {s['mean_abs_margin_mean_minus_line']:.3f} / "
        f"{s['mean_abs_total_mean_minus_line']:.3f} |"
    )


def _window_md(title: str, scores: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| model | moneyline Brier / log | cover Brier / log (ex push) | over Brier / log (ex push) | "
        "margin int log score | total int log score | MAE margin / total | mean abs(mean - line) margin / total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(k, v) for k, v in scores.items()]
    v62 = scores["v6.2"]
    lines += [
        "",
        f"Line MAE (the anchor itself): margin {v62['mae_line_margin']:.3f}, total {v62['mae_line_total']:.3f}.",
        "",
        "Key mass, predicted vs actual (% of games with |margin| = k):",
        "",
        "| k | v6.2 pred | smooth pred | Sides 2022 pred | actual |",
        "|---|---|---|---|---|",
    ]
    for k, blk in v62["key_mass"].items():
        lines.append(
            f"| {k} | {blk['pred_pct']:.2f} | {scores['anchor_only_smooth']['key_mass'][k]['pred_pct']:.2f} | "
            f"{scores['anchor_sides2022_weights']['key_mass'][k]['pred_pct']:.2f} | {blk['actual_pct']:.2f} |"
        )
    for key in ("3", "7"):
        blk = v62["near_key"][key]
        sm = scores["anchor_only_smooth"]["near_key"][key]
        lines += [
            "",
            f"Near {key} (|spread| in [{int(key) - 0.5}, {int(key) + 0.5}], n {blk['n']}), predicted vs actual %:",
            "",
            "| event | v6.2 pred | smooth pred | actual |",
            "|---|---|---|---|",
        ]
        for label, v in blk.items():
            if label in ("n", "home_cover"):
                continue
            lines.append(
                f"| {label} | {v['pred_pct']:.2f} | {sm[label]['pred_pct']:.2f} | {v['actual_pct']:.2f} |"
            )
        lines.append(
            f"| home cover Brier (v6.2 / smooth) | {blk['home_cover']['brier']:.5f} | "
            f"{sm['home_cover']['brier']:.5f} | rate {100 * blk['home_cover']['rate']:.1f}% |"
        )
    return lines


def render_md(spec: dict[str, Any], report: dict[str, Any]) -> str:
    b = spec["blend"]
    lines = [
        "# cfb-structural-v6.2 fit and validation (quick-261121-v62)",
        "",
        f"Rule `{RULE['id']}` sha256 `{spec['rule_sha256']}`; spec sha256 `{report['spec_sha256']}`; "
        f"frozen v6 params `{EXPECTED_FROZEN_SHA[:12]}...`.",
        "",
        "> DESIGN-INFORMED. Every ESPN lined window 2017-2025 was read before (v6 residual test, v6-espn check, v6.1 "
        "comparison, line movement). The historical anchor is the ESPN pinned CLOSING median; serving uses the "
        "timestamped DraftKings line as of decision time, which is less sharp, so these numbers overstate what the "
        "served anchor knows. This is a pipeline shakedown challenger, not evidence of edge. No promotion decision.",
        "",
        "## Fit (train <= 2022-11-11 only)",
        "",
        f"- Cohort (both lines, season > 2016): train {spec['counts']['windows']['train']}, validation "
        f"{spec['counts']['windows']['validation']}, holdout rows not read at fit {spec['counts']['holdout_rows_not_read']}. "
        f"Residual-report cohort reproduced (8279 totals / 8376 spreads lined).",
        f"- Blend weight on the anchor: margin w = {b['margin']['w']:.3f} (raw {b['margin']['w_raw']:.4f}), total w = "
        f"{b['total']['w']:.3f} (raw {b['total']['w_raw']:.4f}). Raw above 1 means v6 adds nothing the close lacks "
        "(the v6 residual slope is about 0), so the served mean is the anchor.",
        f"- Margin SD: exp({spec['margin']['sd_params'][0]:.4f} + {spec['margin']['sd_params'][1]:.4f}*|mu|/10 + "
        f"{spec['margin']['sd_params'][2]:.4f}*(total-55)/10); ridge lambda {spec['margin']['lambda']} (validation "
        "choice: "
        + ", ".join(f"{r['lambda']}: {r['validation_margin_log_score']:.5f}" for r in spec["ridge_selection"])
        + ").",
        "- Fitted key multipliers g(|margin|) (1 = no key mass; Sides 2022 in brackets): "
        + ", ".join(
            f"{k}: {v:.2f}" + (f" [{SIDES_2022[int(k)]}]" if int(k) in SIDES_2022 else "")
            for k, v in spec["key_multipliers"].items()
        )
        + ".",
        f"- Total SD by bucket {spec['structure']['total_buckets']}: "
        + ", ".join(f"{s:.2f}" for s in spec["total"]["bucket_sds"])
        + ".",
        "",
    ]
    lines += _window_md("Validation 2022-11-12 to 2024-11-14 (design-informed)", report["validation"])
    if isinstance(report.get("holdout"), dict):
        lines += [""] + _window_md(
            "Holdout >= 2024-11-15, single read after hashing (second look at this window)",
            report["holdout"]["scores"],
        )
    else:
        lines += ["", "Holdout: NOT READ."]
    lines += [
        "",
        "Models: v6.2 = blended mean, fitted SD model and key multipliers; anchor_only_smooth = close as mean, "
        "same SD form refit with no key mass; anchor_sides2022_weights = close as mean with the Sides 2022 "
        "multipliers; v6.2_model_only_fallback = v6 means with v6 slice SDs and the fitted multipliers (served when "
        "no timestamped anchor exists); v6_engine = v6 as built (NB joint engine, 50k draws, own RNG).",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit").set_defaults(fn=cmd_fit)
    sub.add_parser("holdout").set_defaults(fn=cmd_holdout)
    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
