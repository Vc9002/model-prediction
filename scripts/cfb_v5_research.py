"""Fit, select and score the cfb-structural-v5 challenger on walk-forward, point-in-time data.

Pipeline (ARCHITECTURE.md validation contract):

  cache   Every completed NCAAF game 2016 -> today, priced by the served v4 model with
          history strictly before kickoff (same order and seed as the 2026-11-12 replay,
          so its probabilities reproduce that report exactly). Writes
          outputs/ncaaf/cfb_v5_base_rows.jsonl.
  fit     60/20/20 split on complete kickoff dates (validation.chronological_split).
          Gate: cached v4 Brier must reproduce outputs/ncaaf/cfb_v4_replay.json.
          Fit on TRAIN only: margin/total mean calibration (OLS on v4's projections),
          margin residual SD per slice for each candidate sample threshold, conference
          scale, total residual SD. Select the variant on VALIDATION (moneyline Brier,
          Gaussian margin log score as tiebreak) using an analytic Normal evaluation.
          Score the selected v5 ONCE on the locked HOLDOUT with the real NB engine
          against cached v4, with date-clustered bootstrap CIs. Re-price every settled
          ledger row with v5. Write config/models/cfb-structural-v5.json (status
          research, not a champion), outputs/ncaaf/cfb_v5_research.{json,md}.

Usage:
  PYTHONPATH=src .venv/Scripts/python scripts/cfb_v5_research.py cache
  PYTHONPATH=src .venv/Scripts/python scripts/cfb_v5_research.py fit
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, "src")

from model_prediction.calibration import calibration_metrics
from model_prediction.models.cfb_structural_v4 import (
    load_cfb_structural_v4_artifact,
    main_gates_from_artifact,
)
from model_prediction.models.cfb_structural_v5 import CFB_V5_MODEL_VERSION, CFBStructuralV5Model
from model_prediction.models.college_football import UpcomingCFBGame, _load_cfb_history, cfb_model
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.validation import chronological_split

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs" / "ncaaf"
CACHE = OUT / "cfb_v5_base_rows.jsonl"
REPLAY_JSON = OUT / "cfb_v4_replay.json"
ARTIFACT_PATH = REPO / "config" / "models" / f"{CFB_V5_MODEL_VERSION}.json"
SAMPLE_GRID = (3, 4, 5, 6)


def _replay_module():
    spec = importlib.util.spec_from_file_location(
        "replay_cfb", REPO / "scripts" / "replay_cfb_v4_all_settled.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


# --------------------------------------------------------------------------- cache
def build_cache() -> None:
    replay = _replay_module()
    model = cfb_model()
    history = _load_cfb_history(REPO / "data")
    facts = replay.load_schedule_facts()
    starts = [h.start for h in history]
    season_first: dict[int, datetime] = {}
    for h in history:
        s = season_of(h.start)
        if s not in season_first or h.start < season_first[s]:
            season_first[s] = h.start
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with CACHE.open("w", encoding="utf-8") as out:
        for i, g in enumerate(history):
            if g.away_score is None or g.home_score is None:
                continue
            past = history[: bisect.bisect_left(starts, g.start)]
            fact = facts.get(str(g.event_id), {})
            week = fact.get("week")
            if week is None:
                week = replay.derive_week(g.start, season_first)
            up = UpcomingCFBGame(
                event_id=str(g.event_id),
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                is_neutral_site=bool(fact.get("is_neutral_site", False)),
                season_year=season_of(g.start),
                week=int(week),
            )
            preds = model.predict_matchup(past, up, project_missing_lines=True)
            if not preds:
                continue
            ml = preds[0]
            fb = ml.feature_basis
            ii = ml.inference_inputs
            row = {
                "event_id": str(g.event_id),
                "date": g.start.strftime("%Y-%m-%d"),
                "season": season_of(g.start),
                "start_utc": g.event_start_utc,
                "home": g.home_team,
                "away": g.away_team,
                "week": int(week),
                "neutral": up.is_neutral_site,
                "proj_margin": float(fb["projected_margin_home"]),
                "proj_total": float(fb["projected_total"]),
                "elo_p": float(fb["elo_home_win_prob"]),
                "uncertainty": float(fb["uncertainty"]),
                "is_conf": bool(fb["is_conference_matchup"]),
                "is_fcs": bool(fb["is_fbs_vs_fcs"]),
                "sample_games": int(fb["sample_games"]),
                "margin": float(g.home_score) - float(g.away_score),
                "total": float(g.home_score) + float(g.away_score),
                "v4_p_home": float(ml.probabilities["home"]),
                "v4_margin_sd": float(ii["parameters"]["margin_sd"]),
                "v4_total_sd": float(ii["parameters"]["total_sd"]),
                "v4_spread_home_line": float(ii["spread_home_line"]),
            }
            out.write(json.dumps(row) + "\n")
            if (i + 1) % 500 == 0:
                print(f"  cache {i + 1}/{len(history)} ({time.time() - t0:.0f}s)", flush=True)
    print("wrote", CACHE, f"{time.time() - t0:.0f}s")


# --------------------------------------------------------------------------- analytic helpers
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def p_home_normal(mu: float, sd: float) -> float:
    # Integer margins, no ties in CFB: P(margin >= 1) with a half-point continuity correction.
    return min(1 - 1e-9, max(1e-9, 1.0 - _phi((0.5 - mu) / sd)))


def gauss_log_score(x: float, mu: float, sd: float) -> float:
    return -0.5 * math.log(2 * math.pi * sd * sd) - (x - mu) ** 2 / (2 * sd * sd)


def ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx if sxx else 1.0
    return my - slope * mx, slope


def pstdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else float("nan")


def score_rows(
    rows: list[dict[str, Any]],
    *,
    p_key: str,
    mu_key: str,
    sd_key: str,
    tmu_key: str | None = None,
    tsd_key: str | None = None,
) -> dict[str, Any]:
    probs = [r[p_key] for r in rows]
    outs = [int(r["margin"] > 0) for r in rows]
    m = calibration_metrics(probs, outs) if len(rows) >= 30 else {}
    z = [(r["margin"] - r[mu_key]) / r[sd_key] for r in rows]
    out = {
        "n": len(rows),
        "brier": round(sum((p - y) ** 2 for p, y in zip(probs, outs, strict=True)) / len(rows), 5),
        "log_loss": m.get("log_loss"),
        "ece": m.get("expected_calibration_error"),
        "calibration_slope": m.get("calibration_slope"),
        "accuracy": round(
            sum(1 for p, y in zip(probs, outs, strict=True) if (p >= 0.5) == bool(y)) / len(rows), 4
        ),
        "margin_log_score": round(
            sum(gauss_log_score(r["margin"], r[mu_key], r[sd_key]) for r in rows) / len(rows), 5
        ),
        "margin_resid_sd": round(pstdev([r["margin"] - r[mu_key] for r in rows]), 3),
        "mean_margin_sd": round(statistics.fmean(r[sd_key] for r in rows), 3),
        "margin_bias": round(statistics.fmean(r["margin"] - r[mu_key] for r in rows), 3),
        "coverage_50": round(sum(1 for v in z if abs(v) < 0.6745) / len(z), 4),
        "coverage_80": round(sum(1 for v in z if abs(v) < 1.2816) / len(z), 4),
        "coverage_95": round(sum(1 for v in z if abs(v) < 1.96) / len(z), 4),
    }
    if tmu_key and tsd_key:
        zt = [(r["total"] - r[tmu_key]) / r[tsd_key] for r in rows]
        out.update(
            {
                "total_log_score": round(
                    sum(gauss_log_score(r["total"], r[tmu_key], r[tsd_key]) for r in rows) / len(rows), 5
                ),
                "total_resid_sd": round(pstdev([r["total"] - r[tmu_key] for r in rows]), 3),
                "mean_total_sd": round(statistics.fmean(r[tsd_key] for r in rows), 3),
                "total_bias": round(statistics.fmean(r["total"] - r[tmu_key] for r in rows), 3),
                "total_coverage_80": round(sum(1 for v in zt if abs(v) < 1.2816) / len(zt), 4),
            }
        )
    return out


def fit_params(train: list[dict[str, Any]], k: int, *, conference_scale: bool) -> dict[str, Any]:
    a, b = ols([r["proj_margin"] for r in train], [r["margin"] for r in train])
    c, d = ols([r["proj_total"] for r in train], [r["total"] for r in train])
    resid = {r["event_id"]: r["margin"] - (a + b * r["proj_margin"]) for r in train}
    est = [r for r in train if not r["is_fcs"] and r["sample_games"] >= k]
    thin = [r for r in train if not r["is_fcs"] and r["sample_games"] < k]
    fcs = [r for r in train if r["is_fcs"]]
    est_nonconf = [r for r in est if not r["is_conf"]] or est
    est_conf = [r for r in est if r["is_conf"]] or est
    sd_est = pstdev([resid[r["event_id"]] for r in est_nonconf])
    scale = pstdev([resid[r["event_id"]] for r in est_conf]) / sd_est if conference_scale else 1.0
    return {
        "margin_calibration": {"intercept": round(a, 4), "slope": round(b, 4)},
        "total_calibration": {"intercept": round(c, 4), "slope": round(d, 4)},
        "margin_sd_table": {
            "established": round(sd_est, 3),
            "thin_sample": round(
                pstdev([resid[r["event_id"]] for r in thin]) if len(thin) > 1 else sd_est, 3
            ),
            "fbs_vs_fcs": round(pstdev([resid[r["event_id"]] for r in fcs]) if len(fcs) > 1 else sd_est, 3),
        },
        "established_min_sample": k,
        "conference_margin_scale": round(scale, 4),
        "total_sd": round(pstdev([r["total"] - (c + d * r["proj_total"]) for r in train]), 3),
        "train_slices": {"established": len(est), "thin_sample": len(thin), "fbs_vs_fcs": len(fcs)},
    }


def apply_variant(
    rows: list[dict[str, Any]], params: dict[str, Any] | None, *, mean_cal: bool, sd_table: bool
) -> None:
    """Analytic Normal pricing of a variant into keys an_p / an_mu / an_sd / an_tmu / an_tsd."""
    for r in rows:
        if mean_cal and params:
            mu = (
                params["margin_calibration"]["intercept"]
                + params["margin_calibration"]["slope"] * r["proj_margin"]
            )
            tmu = (
                params["total_calibration"]["intercept"]
                + params["total_calibration"]["slope"] * r["proj_total"]
            )
        else:
            mu, tmu = r["proj_margin"], r["proj_total"]
        if sd_table and params:
            if r["is_fcs"]:
                sd = params["margin_sd_table"]["fbs_vs_fcs"]
            elif r["sample_games"] >= params["established_min_sample"]:
                sd = params["margin_sd_table"]["established"] * (
                    params["conference_margin_scale"] if r["is_conf"] else 1.0
                )
            else:
                sd = params["margin_sd_table"]["thin_sample"]
            tsd = params["total_sd"]
        else:
            sd, tsd = r["v4_margin_sd"], r["v4_total_sd"]
        r["an_mu"], r["an_sd"], r["an_tmu"], r["an_tsd"] = mu, sd, tmu, tsd
        r["an_p"] = p_home_normal(mu, sd)


def clustered_bootstrap_mean_delta(
    rows: list[dict[str, Any]], key: str, *, seed: int = 7, n: int = 2000
) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(float(r[key]))
    dates = sorted(by_date)
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        picked = [rng.choice(dates) for _ in dates]
        vals = [v for d in picked for v in by_date[d]]
        samples.append(sum(vals) / len(vals))
    samples.sort()
    return {
        "point_estimate": round(sum(v for d in dates for v in by_date[d]) / len(rows), 6),
        "ci_95_low": round(samples[int(0.025 * (n - 1))], 6),
        "ci_95_high": round(samples[int(0.975 * (n - 1))], 6),
        "dates": len(dates),
    }


# --------------------------------------------------------------------------- fit
def fit() -> None:
    rows = [json.loads(line) for line in CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    print("cached rows", len(rows))

    # Incumbent-reproduction gate: the cached v4 must match the replay report.
    replay = json.loads(REPLAY_JSON.read_text(encoding="utf-8"))
    replay_brier = replay["cohort_1_all_completed_games"]["overall"]["model"]["brier"]
    cached_brier = sum((r["v4_p_home"] - int(r["margin"] > 0)) ** 2 for r in rows) / len(rows)
    gate = {
        "replay_brier": replay_brier,
        "cached_v4_brier": round(cached_brier, 6),
        "pass": abs(cached_brier - replay_brier) < 1e-4,
    }
    print("incumbent reproduction gate:", gate)
    if not gate["pass"]:
        raise SystemExit("cached v4 does not reproduce the replay; refusing to fit on it")

    ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0), row=r) for r in rows]
    train_d, val_d, hold_d, split_meta = chronological_split(ducks)
    train = [d.row for d in train_d]
    val = [d.row for d in val_d]
    hold = [d.row for d in hold_d]
    print("split", split_meta, len(train), len(val), len(hold))

    # Validation tournament (analytic). v4 analytic first so the approximation error is visible.
    variants: dict[str, dict[str, Any]] = {}
    apply_variant(val, None, mean_cal=False, sd_table=False)
    variants["v4_analytic"] = {
        "params": None,
        "validation": score_rows(
            val, p_key="an_p", mu_key="an_mu", sd_key="an_sd", tmu_key="an_tmu", tsd_key="an_tsd"
        ),
    }
    variants["elo_channel"] = {
        "params": None,
        "validation": score_rows(val, p_key="elo_p", mu_key="proj_margin", sd_key="v4_margin_sd"),
    }
    variants["v4_engine"] = {
        "params": None,
        "validation": score_rows(
            val,
            p_key="v4_p_home",
            mu_key="proj_margin",
            sd_key="v4_margin_sd",
            tmu_key="proj_total",
            tsd_key="v4_total_sd",
        ),
    }
    for k in SAMPLE_GRID:
        for conf in (True, False):
            params = fit_params(train, k, conference_scale=conf)
            tag = f"k{k}_{'confscale' if conf else 'noconf'}"
            for name, mc, st in (
                ("mean_cal_only", True, False),
                ("sd_table_only", False, True),
                ("v5", True, True),
            ):
                if name != "v5" and (k != SAMPLE_GRID[0] or not conf):
                    continue  # the single-layer ablations only need one representative setting
                apply_variant(val, params, mean_cal=mc, sd_table=st)
                variants[f"{name}_{tag}" if name == "v5" else name] = {
                    "params": params,
                    "validation": score_rows(
                        val, p_key="an_p", mu_key="an_mu", sd_key="an_sd", tmu_key="an_tmu", tsd_key="an_tsd"
                    ),
                }
    candidates = {k: v for k, v in variants.items() if k.startswith("v5_")}
    best_name = min(
        candidates,
        key=lambda k: (
            candidates[k]["validation"]["brier"],
            -candidates[k]["validation"]["margin_log_score"],
        ),
    )
    best = candidates[best_name]
    print(
        "selected on validation:",
        best_name,
        best["validation"]["brier"],
        best["validation"]["margin_log_score"],
    )

    # Locked holdout, once, with the real engine.
    params = best["params"]
    v5 = CFBStructuralV5Model(
        margin_calibration=params["margin_calibration"],
        total_calibration=params["total_calibration"],
        margin_sd_table=params["margin_sd_table"],
        established_min_sample=params["established_min_sample"],
        conference_margin_scale=params["conference_margin_scale"],
        total_sd=params["total_sd"],
    )
    history = _load_cfb_history(REPO / "data")
    starts = [h.start for h in history]
    by_id = {str(h.event_id): h for h in history}
    t0 = time.time()
    for i, r in enumerate(hold):
        g = by_id[r["event_id"]]
        past = history[: bisect.bisect_left(starts, g.start)]
        up = UpcomingCFBGame(
            event_id=r["event_id"],
            event_start_utc=g.event_start_utc,
            away_team=g.away_team,
            home_team=g.home_team,
            is_neutral_site=bool(r["neutral"]),
            season_year=r["season"],
            week=r["week"],
        )
        preds = v5.predict_matchup(past, up, project_missing_lines=True)
        ml = preds[0]
        ii = ml.inference_inputs
        r["v5_p_home"] = float(ml.probabilities["home"])
        r["v5_mu"] = float(ii["mu_home"]) - float(ii["mu_away"])
        r["v5_tmu"] = float(ii["mu_home"]) + float(ii["mu_away"])
        r["v5_margin_sd"] = float(ii["parameters"]["margin_sd"])
        r["v5_total_sd"] = float(ii["parameters"]["total_sd"])
        r["d_brier"] = (r["v5_p_home"] - int(r["margin"] > 0)) ** 2 - (
            r["v4_p_home"] - int(r["margin"] > 0)
        ) ** 2
        r["d_logscore"] = gauss_log_score(r["margin"], r["v5_mu"], r["v5_margin_sd"]) - gauss_log_score(
            r["margin"], r["proj_margin"], r["v4_margin_sd"]
        )
        r["d_total_logscore"] = gauss_log_score(r["total"], r["v5_tmu"], r["v5_total_sd"]) - gauss_log_score(
            r["total"], r["proj_total"], r["v4_total_sd"]
        )
        if (i + 1) % 400 == 0:
            print(f"  holdout {i + 1}/{len(hold)} ({time.time() - t0:.0f}s)", flush=True)
    hold_ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0)) for r in hold]
    holdout = {
        "v4_engine": score_rows(
            hold,
            p_key="v4_p_home",
            mu_key="proj_margin",
            sd_key="v4_margin_sd",
            tmu_key="proj_total",
            tsd_key="v4_total_sd",
        ),
        "v5_engine": score_rows(
            hold,
            p_key="v5_p_home",
            mu_key="v5_mu",
            sd_key="v5_margin_sd",
            tmu_key="v5_tmu",
            tsd_key="v5_total_sd",
        ),
        "elo_channel": score_rows(hold, p_key="elo_p", mu_key="proj_margin", sd_key="v4_margin_sd"),
        "brier_delta_v5_minus_v4": _cluster_bootstrap_brier_delta(
            [r["v4_p_home"] for r in hold], [r["v5_p_home"] for r in hold], hold_ducks, seed=11
        ),
        "margin_log_score_delta_v5_minus_v4": clustered_bootstrap_mean_delta(hold, "d_logscore"),
        "total_log_score_delta_v5_minus_v4": clustered_bootstrap_mean_delta(hold, "d_total_logscore"),
        "by_season": {},
        "by_slice": {},
    }
    for s in sorted({r["season"] for r in hold}):
        sub = [r for r in hold if r["season"] == s]
        holdout["by_season"][str(s)] = {
            "n": len(sub),
            "v4_brier": round(sum((r["v4_p_home"] - int(r["margin"] > 0)) ** 2 for r in sub) / len(sub), 5),
            "v5_brier": round(sum((r["v5_p_home"] - int(r["margin"] > 0)) ** 2 for r in sub) / len(sub), 5),
            "v4_margin_bias": round(statistics.fmean(r["margin"] - r["proj_margin"] for r in sub), 3),
            "v5_margin_bias": round(statistics.fmean(r["margin"] - r["v5_mu"] for r in sub), 3),
            "v5_margin_resid_sd": round(pstdev([r["margin"] - r["v5_mu"] for r in sub]), 3),
            "v5_mean_margin_sd": round(statistics.fmean(r["v5_margin_sd"] for r in sub), 3),
        }
    for label, pred in (
        ("established", lambda r: not r["is_fcs"] and r["sample_games"] >= params["established_min_sample"]),
        ("thin_sample", lambda r: not r["is_fcs"] and r["sample_games"] < params["established_min_sample"]),
        ("fbs_vs_fcs", lambda r: r["is_fcs"]),
        ("conference", lambda r: r["is_conf"] and not r["is_fcs"]),
        ("neutral", lambda r: r["neutral"]),
    ):
        sub = [r for r in hold if pred(r)]
        if len(sub) >= 30:
            holdout["by_slice"][label] = {
                "v4": score_rows(sub, p_key="v4_p_home", mu_key="proj_margin", sd_key="v4_margin_sd"),
                "v5": score_rows(sub, p_key="v5_p_home", mu_key="v5_mu", sd_key="v5_margin_sd"),
            }

    # Settled ledger rows re-priced with v5 (secondary, market-relative).
    rmod = _replay_module()
    gates = main_gates_from_artifact(load_cfb_structural_v4_artifact())
    ledger_rows = rmod.load_settled_rows()
    ledger = rmod.replay_ledger(v5, history, gates, ledger_rows, rmod.load_schedule_facts())
    ledger_summary = {
        tier: {
            "all": ledger[tier]["all"],
            "by_market": ledger[tier]["by_market"],
            "by_quote_source": ledger[tier]["by_quote_source"],
        }
        for tier in ("flat", "main")
    }
    v4_ledger = replay["cohort_2_settled_ledger_rows"]
    ledger_compare = {
        tier: {
            m: {
                "n": ledger[tier]["by_market"][m]["n"],
                "v4_new_brier": v4_ledger[tier]["by_market"][m]["new_model_brier"],
                "v5_brier": ledger[tier]["by_market"][m]["new_model_brier"],
                "ask_brier": ledger[tier]["by_market"][m]["ask_brier"],
                "v4_fires": v4_ledger[tier]["by_market"][m]["new_model_fires_under_gates"],
                "v5_fires": ledger[tier]["by_market"][m]["new_model_fires_under_gates"],
            }
            for m in ("moneyline", "spread", "total")
        }
        for tier in ("flat", "main")
    }

    v4_art = load_cfb_structural_v4_artifact()
    artifact = {
        "method": "cfb_structural_v5_calibrated_bivariate_engine",
        "model_version": CFB_V5_MODEL_VERSION,
        "schema_version": "2",
        "sport": "ncaaf",
        "league": "NCAAF",
        "positive_class": "home_win",
        "base_model": "cfb-structural-v4",
        "distribution": "negative_binomial",
        "home_advantage_points": 2.5,
        "margin_calibration": params["margin_calibration"],
        "total_calibration": params["total_calibration"],
        "margin_sd_table": params["margin_sd_table"],
        "established_min_sample": params["established_min_sample"],
        "conference_margin_scale": params["conference_margin_scale"],
        "total_sd": params["total_sd"],
        "features": v4_art["features"],
        "gates": v4_art["gates"],
        "training": {
            "source": "data/historical/ncaaf_games_all.jsonl + data/processed/ncaaf/games.jsonl via _load_cfb_history",
            "base_projection": "cfb-structural-v4 point-in-time (history strictly before kickoff)",
            "split": split_meta,
            "n_train": len(train),
            "n_validation": len(val),
            "n_holdout": len(hold),
            "fitted_on": "train only; variant selected on validation; holdout scored once",
            "selected_variant": best_name,
            "train_slices": params["train_slices"],
            "harness": "scripts/cfb_v5_research.py",
            "generated_at_utc": datetime.now(UTC).isoformat(),
        },
        "calibration": {
            "validation": {k: v["validation"] for k, v in variants.items()},
            "holdout": {
                k: holdout[k]
                for k in (
                    "v4_engine",
                    "v5_engine",
                    "elo_channel",
                    "brier_delta_v5_minus_v4",
                    "margin_log_score_delta_v5_minus_v4",
                    "total_log_score_delta_v5_minus_v4",
                )
            },
        },
        "qualification": {
            "status": "research",
            "qualified": False,
            "locked_holdout": True,
            "framework": "cfb_structural_v5_calibrated_bivariate_engine",
            "note": "Challenger. Not registered in production.yaml; promotion requires the ROADMAP promotion rule.",
        },
    }
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    # Round-trip through the fail-closed loader.
    CFBStructuralV5Model.from_artifact(json.loads(ARTIFACT_PATH.read_text(encoding="utf-8")))

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "gate": gate,
        "split": split_meta,
        "counts": {"train": len(train), "validation": len(val), "holdout": len(hold)},
        "selected_variant": best_name,
        "selected_params": params,
        "validation": {k: v["validation"] for k, v in variants.items()},
        "holdout": holdout,
        "ledger_v5": ledger_summary,
        "ledger_compare_v4_vs_v5": ledger_compare,
        "artifact_hash": artifact["artifact_hash"],
    }
    (OUT / "cfb_v5_research.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "cfb_v5_research.md").write_text(render(report), encoding="utf-8")
    print("wrote", OUT / "cfb_v5_research.md", "and", ARTIFACT_PATH)


def _f(x: Any, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def render(rep: dict[str, Any]) -> str:
    p = rep["selected_params"]
    L = [
        f"# cfb-structural-v5 research — {rep['generated_at_utc'][:19]}Z",
        "",
        (
            f"Incumbent reproduction gate: cached v4 Brier {rep['gate']['cached_v4_brier']} vs replay {rep['gate']['replay_brier']} → "
            f"{'PASS' if rep['gate']['pass'] else 'FAIL'}. Split on complete kickoff dates: train {rep['counts']['train']}, "
            f"validation {rep['counts']['validation']}, holdout {rep['counts']['holdout']} games (`{json.dumps(rep['split'])}`)."
        ),
        "",
        f"Selected on validation: `{rep['selected_variant']}`. Fitted on train only:",
        "",
        (
            f"- margin mean: `m' = {p['margin_calibration']['intercept']} + {p['margin_calibration']['slope']} · m` ; total mean: "
            f"`t' = {p['total_calibration']['intercept']} + {p['total_calibration']['slope']} · t`"
        ),
        (
            f"- margin SD table (residual, after calibration): established {p['margin_sd_table']['established']} "
            f"(sample ≥ {p['established_min_sample']}), thin sample {p['margin_sd_table']['thin_sample']}, FBS-vs-FCS "
            f"{p['margin_sd_table']['fbs_vs_fcs']}; conference scale {p['conference_margin_scale']} (v4: 0.9135)"
        ),
        (
            f"- total SD {p['total_sd']} with no uncertainty widening (v4: 16.0 × uncertainty widening). Blowout-line and "
            "total-deviation widening carried over from v4 (no lines to evaluate them)."
        ),
        "",
        "## Validation tournament (analytic Normal; v4_engine row = the real engine for reference)",
        "",
        "| variant | n | ML Brier | log loss | slope | acc | margin log score | resid SD | mean SD | cov50 | cov80 | cov95 | total log score | total resid SD | mean total SD |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, v in rep["validation"].items():
        L.append(
            f"| {name} | {v['n']} | {_f(v['brier'], 5)} | {_f(v['log_loss'])} | {_f(v['calibration_slope'], 3)} | {_f(v['accuracy'])} | "
            f"{_f(v['margin_log_score'], 4)} | {_f(v['margin_resid_sd'], 2)} | {_f(v['mean_margin_sd'], 2)} | {_f(v['coverage_50'], 3)} | "
            f"{_f(v['coverage_80'], 3)} | {_f(v['coverage_95'], 3)} | {_f(v.get('total_log_score'), 4)} | {_f(v.get('total_resid_sd'), 2)} | {_f(v.get('mean_total_sd'), 2)} |"
        )
    h = rep["holdout"]
    L += [
        "",
        "## Locked holdout (real engine, scored once)",
        "",
        "| model | n | ML Brier | log loss | slope | acc | margin log score | resid SD | mean SD | cov50 | cov80 | cov95 | margin bias | total log score | total resid SD | mean total SD |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in ("v4_engine", "v5_engine", "elo_channel"):
        v = h[name]
        L.append(
            f"| {name} | {v['n']} | {_f(v['brier'], 5)} | {_f(v['log_loss'])} | {_f(v['calibration_slope'], 3)} | {_f(v['accuracy'])} | "
            f"{_f(v['margin_log_score'], 4)} | {_f(v['margin_resid_sd'], 2)} | {_f(v['mean_margin_sd'], 2)} | {_f(v['coverage_50'], 3)} | "
            f"{_f(v['coverage_80'], 3)} | {_f(v['coverage_95'], 3)} | {_f(v['margin_bias'], 2)} | {_f(v.get('total_log_score'), 4)} | {_f(v.get('total_resid_sd'), 2)} | {_f(v.get('mean_total_sd'), 2)} |"
        )
    bd = h["brier_delta_v5_minus_v4"]
    ld = h["margin_log_score_delta_v5_minus_v4"]
    td = h["total_log_score_delta_v5_minus_v4"]
    L += [
        "",
        (
            f"Date-clustered bootstrap (95% CI), v5 − v4: ML Brier {bd['point_estimate']:+.5f} [{bd['ci_95_low']:+.5f}, {bd['ci_95_high']:+.5f}] "
            f"(negative = better); margin log score {ld['point_estimate']:+.4f} [{ld['ci_95_low']:+.4f}, {ld['ci_95_high']:+.4f}] (positive = better); "
            f"total log score {td['point_estimate']:+.4f} [{td['ci_95_low']:+.4f}, {td['ci_95_high']:+.4f}]."
        ),
        "",
        "| holdout season | n | v4 Brier | v5 Brier | v4 margin bias | v5 margin bias | v5 resid SD | v5 mean SD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s, v in h["by_season"].items():
        L.append(
            f"| {s} | {v['n']} | {_f(v['v4_brier'], 5)} | {_f(v['v5_brier'], 5)} | {_f(v['v4_margin_bias'], 2)} | {_f(v['v5_margin_bias'], 2)} | {_f(v['v5_margin_resid_sd'], 2)} | {_f(v['v5_mean_margin_sd'], 2)} |"
        )
    L += [
        "",
        "| holdout slice | n | v4 Brier | v5 Brier | v4 resid/served SD | v5 resid/served SD | v4 cov80 | v5 cov80 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s, v in h["by_slice"].items():
        L.append(
            f"| {s} | {v['v4']['n']} | {_f(v['v4']['brier'], 5)} | {_f(v['v5']['brier'], 5)} | {_f(v['v4']['margin_resid_sd'], 2)}/{_f(v['v4']['mean_margin_sd'], 2)} | "
            f"{_f(v['v5']['margin_resid_sd'], 2)}/{_f(v['v5']['mean_margin_sd'], 2)} | {_f(v['v4']['coverage_80'], 3)} | {_f(v['v5']['coverage_80'], 3)} |"
        )
    L += [
        "",
        "## Settled ledger rows re-priced (recorded line and ask, history as of decision time)",
        "",
        "| tier / market | n | v4 Brier | v5 Brier | ask Brier | v4 fires / hit / units | v5 fires / hit / units |",
        "|---|---|---|---|---|---|---|",
    ]
    for tier, ms in rep["ledger_compare_v4_vs_v5"].items():
        for m, v in ms.items():
            f4, f5 = v["v4_fires"], v["v5_fires"]
            L.append(
                f"| {tier} {m} | {v['n']} | {_f(v['v4_new_brier'])} | {_f(v['v5_brier'])} | {_f(v['ask_brier'])} | "
                f"{f4['bets']} / {_f(f4['hit_rate'])} / {_f(f4['flat_units_at_ask'], 2)} | {f5['bets']} / {_f(f5['hit_rate'])} / {_f(f5['flat_units_at_ask'], 2)} |"
            )
    L += [
        "",
        f"Artifact: `config/models/cfb-structural-v5.json` (hash `{rep['artifact_hash'][:12]}…`, status research, not registered).",
        "",
        "## Caveats",
        "",
        "- No historical market lines: spread/total layers are judged by variance calibration and Gaussian log score, not cover probabilities. Settled rows (2026 only, mostly consensus asks) are the only market-relative check and are small.",
        "- Weather and QB channels inert in this data (placeholder weather fields; no PIT injury source before 2026-11).",
        "- The mean calibration is a fixed train-period fit; holdout-season bias shows whether home-advantage drift persists.",
        "- The bare Elo channel's moneyline Brier beats v5 on the holdout but loses on validation (and on the replay's 2016-2022 seasons), so swapping the structural mean for Elo is not supported; a two-regressor mean (structural + Elo-implied margin) changed validation Brier by 0.0001 and was not adopted.",
    ]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("cache", "fit"))
    args = ap.parse_args()
    if args.phase == "cache":
        build_cache()
    else:
        fit()


if __name__ == "__main__":
    main()
