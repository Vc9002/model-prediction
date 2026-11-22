"""Refit cfb-structural-v7 as cfb-structural-v7.1 with the known v7 bugs fixed (quick-261122-v71).

Operator approval 2026-11-22 ("Refit NCAAF v7"). v7.1 is a PAPER CHALLENGER (Flat/research rows only), never a
champion. Everything v7 pinned stays untouched: this script imports ``cfb_v7_fit`` for the shared arithmetic and
writes its own ``outputs/ncaaf/cfb_v71_*`` files.

What changes against v7 (bug hunt E:/MP/outputs/ncaaf/bug_hunt/models_v7.md):

1. Stage-2 margin calibration gets a separate neutral-site intercept, fit on train:
   ``margin = a_home * (1 - neutral) + a_neutral * neutral + b * raw_margin``. v7 pooled one intercept (-1.62)
   over home and neutral games, so a neutral game leaned 1.6 points toward ESPN's away team.
2. CFBD covariates are standardised over, and applied to, the season's FBS teams only (FCS teams carry none in
   any season). CFBD stopped carrying FCS talent from 2025, so v7's within-season z measured FBS teams against a
   different population in serving than in training. The CFBD prior gammas are re-estimated (2016-2021 finals,
   unchanged rule) and the 16 CFBD walks re-run; the 8 core walks do not read CFBD and are reused from v7.
3./4./5. are serving-side (fit time, points-only rows, 404 retry, feature snapshot) and change no research
   number.

Windows, grid, selection rule and pmf fitting are v7's (``cfb_v7_fit.RULE``). Holdout: the v7 holdout was read
once (2026-11-21T21:12:25Z) and the bug hunt read holdout neutral residuals, so the v7.1 holdout read is the
SECOND read of that window: confirmatory only, pinned before it happens, nothing tuned on it.

Subcommands (worktree root, PYTHONPATH=src, OPENBLAS_NUM_THREADS=1):
  prep      CFBD prior arms with FBS-only covariates -> outputs/ncaaf/cfb_v71_prior_arms.json
  walk      PIT features for the CFBD arms (holdout rows to separate files, written not read)
  fit       stage 2 with the neutral intercept for all 48 candidates, pinned selection, pmf fit -> cfb_v71_spec.json
  v7repro   v7's published holdout row from v7's own files (no v7.1 holdout file opened)
  pin       outputs/ncaaf/cfb_v71_rule.json
  holdout   the one confirmatory read -> cfb_v71_report.{json,md}
  artifact  config/models/cfb-structural-v7.1.json + parity fixture
Percentages and points only; no American odds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cfb_v7_fit as v7

from model_prediction.features import cfb_features_v7 as core

MAIN = v7.MAIN
V7_WORK = MAIN / "data" / "research" / "ncaaf_v7_work"  # read-only: v7 walks, v5 replay rows, v7 read marker
WORK = REPO / "data" / "research" / "ncaaf_v71_work"  # untracked; v7.1 CFBD walks and the v7.1 read marker
HOLDOUT_MARK = WORK / "holdout_read.json"
OUT = REPO / "outputs" / "ncaaf"
PRIOR_ARMS_JSON = OUT / "cfb_v71_prior_arms.json"
SPEC = OUT / "cfb_v71_spec.json"
RULE_PATH = OUT / "cfb_v71_rule.json"
REPORT_JSON = OUT / "cfb_v71_report.json"
REPORT_MD = OUT / "cfb_v71_report.md"
V7_REPRO = OUT / "cfb_v71_v7_repro.json"
FIXTURE = OUT / "cfb_v71_parity_fixture.json"
ARTIFACT = REPO / "config" / "models" / "cfb-structural-v7.1.json"
MODEL_VERSION = "cfb-structural-v7.1"
CFBD_ARMS = ("cfbd", "cfbd_portal")
COVARIATE_POPULATION = "fbs"

# v7's fit script resolves WORK inside the worktree it runs from; v7's files live in the main checkout.
v7.WORK = V7_WORK
v7.V5_ROWS = V7_WORK / "v5_replay_rows.jsonl"


# ================================================================================================ fix 2
def fbs_population(classes: dict[tuple[str, int], str], season: int) -> set[str]:
    return {t for (t, s), c in classes.items() if s == season and c == core.FBS}


def load_cfbd_covariates_v71(classes: dict[tuple[str, int], str]) -> dict[int, dict[str, dict[str, float]]]:
    """season -> ESPN team id -> covariates z-scored over that season's FBS teams; FCS teams get none."""
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with v7.CFBD_PRIORS.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                by_season[int(r["season"])].append(r)
    return {
        season: core.standardise_covariates(rows, population=fbs_population(classes, season))
        for season, rows in by_season.items()
    }


def cmd_prep(_: argparse.Namespace) -> int:
    t0 = time.time()
    rows, _facts = v7.load_team_games()
    classes = core.season_classes(rows)
    seasons = sorted({r.season for r in rows})
    finals = {s: core.fit_finals(rows, s, classes) for s in seasons if s <= v7.PRIOR_FIT_LAST_SEASON}
    cov = load_cfbd_covariates_v71(classes)
    arms = {arm: v7.estimate_prior(rows, classes, finals, cov, arm) for arm in CFBD_ARMS}
    payload = {
        "arms": arms,
        "covariate_population": COVARIATE_POPULATION,
        "inputs": {
            "pbp_sha256": v7.file_sha256(v7.PBP),
            "snapshot_sha256": v7.file_sha256(v7.SNAPSHOT),
            "cfbd_priors_sha256": v7.file_sha256(v7.CFBD_PRIORS),
        },
        "note": (
            "v7.1 CFBD arms: finals of 2016-2021 and CFBD covariates of those seasons only, z-scored over and "
            "applied to each season's FBS teams only (FBS = plays any FBS-FBS game that season). Every CFBD value "
            "is current-revised (as_published_verified false); portal_out exists from 2021 only"
        ),
    }
    PRIOR_ARMS_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for arm, pr in arms.items():
        for k, v in pr["diag"].items():
            print(arm, k, v["rho"], v["gamma"], v["lambda_games"])
    print(f"prep done in {time.time() - t0:.0f}s")
    return 0


def load_prior(arm: str) -> dict[str, Any]:
    if arm == "core":
        return json.loads(v7.PRIOR_JSON.read_text(encoding="utf-8"))
    return json.loads(PRIOR_ARMS_JSON.read_text(encoding="utf-8"))["arms"][arm]


def walk_combo(args: tuple[str, float, float | None]) -> dict[str, Any]:
    """``cfb_v7_fit.walk_combo`` with the v7.1 prior arms and FBS-only covariates (a top-level function so the
    Windows spawn workers import it with this module's configuration)."""
    arm, mult, half_life = args
    key = v7.combo_key(mult, half_life, arm)
    params = v7.prior_params(load_prior(arm), mult, half_life)
    rows, facts = v7.load_team_games()
    classes = core.season_classes(rows)
    covs = load_cfbd_covariates_v71(classes)
    evals = v7.load_eval_rows()
    by_season_rows: dict[int, list[core.TeamGame]] = defaultdict(list)
    for r in rows:
        by_season_rows[r.season].append(r)
    finals_cache: dict[int, core.RatingState] = {}

    def finals(season: int) -> core.RatingState | None:
        if season not in by_season_rows:
            return None
        if season not in finals_cache:
            finals_cache[season] = core.fit_finals(by_season_rows[season], season, classes)
        return finals_cache[season]

    todo: dict[tuple[int, datetime], list[dict[str, Any]]] = defaultdict(list)
    missing = []
    for ev in evals:
        f = facts.get(ev["event_id"])
        if f is None or "home_id" not in f:
            missing.append(ev["event_id"])
            continue
        todo[(f["season"], core.decision_time_for(f["kickoff"]))].append(ev)
    t0 = time.time()
    out_tv = WORK / f"walk_{key}.jsonl"
    out_h = WORK / f"walk_{key}.holdout.jsonl"
    n_tv = n_h = no_call = 0
    with out_tv.open("w", encoding="utf-8") as ftv, out_h.open("w", encoding="utf-8") as fh:
        for season, decision in sorted(todo):
            games = todo[(season, decision)]
            teams = {facts[g["event_id"]]["home_id"] for g in games} | {
                facts[g["event_id"]]["away_id"] for g in games
            }
            state = core.fit_state(
                by_season_rows[season],
                season,
                decision,
                classes,
                finals(season - 1),
                params,
                sorted(teams),
                covs.get(season),
            )
            for ev in games:
                f = facts[ev["event_id"]]
                rec: dict[str, Any] = {
                    "event_id": ev["event_id"],
                    "date": ev["date"],
                    "season": ev["season"],
                    "week": ev["week"],
                    "window": v7.window_of(ev["date"]),
                    "decision_utc": decision.isoformat(),
                    "neutral": bool(f["neutral"]),
                    "home_id": f["home_id"],
                    "away_id": f["away_id"],
                    "margin": ev["margin"],
                    "total": ev["total"],
                }
                try:
                    mf = core.matchup_features(
                        state, f["home_id"], f["away_id"], neutral=f["neutral"], classes=classes
                    )
                except core.V7NoCall as exc:
                    rec["no_call"] = exc.reason
                    no_call += 1
                else:
                    rec.update(
                        home_e=mf.home.values,
                        away_e=mf.away.values,
                        home_games=mf.home_games,
                        away_games=mf.away_games,
                        is_fcs=mf.is_fbs_vs_fcs,
                    )
                if rec["window"] == "holdout":
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
                    n_h += 1
                else:
                    ftv.write(json.dumps(rec, sort_keys=True) + "\n")
                    n_tv += 1
    return {
        "combo": key,
        "train_validation_rows": n_tv,
        "holdout_rows_written_not_read": n_h,
        "no_call": no_call,
        "missing_facts": len(missing),
        "seconds": round(time.time() - t0, 1),
    }


def cmd_walk(args: argparse.Namespace) -> int:
    if HOLDOUT_MARK.exists():
        raise SystemExit("v7.1 holdout already read; the walk files are frozen")
    combos = [(a, m, h) for a in CFBD_ARMS for m in v7.LAMBDA_MULTIPLIERS for h in v7.HALF_LIVES]
    if args.only:
        only = set(
            args.only.split(",")
        )  # a rerun after a host memory failure redoes just the unfinished ones
        combos = [c for c in combos if v7.combo_key(c[1], c[2], c[0]) in only]
    WORK.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=min(args.workers, len(combos))) as pool:
        for res in pool.map(walk_combo, combos):
            print(json.dumps(res), flush=True)
    return 0


# ================================================================================================ fix 1
def fit_stage2(train: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    """v7's stage 2 (OLS team points on both sides of every train game; linear total calibration), with the margin
    calibration ``a_home * (1 - neutral) + a_neutral * neutral + b * raw_margin`` fit on train."""
    s2 = v7.fit_stage2(train, names)
    c = np.array(s2["coef"])
    xh, xa = v7.side_matrix(train, names, "home"), v7.side_matrix(train, names, "away")
    raw_m = (xh - xa) @ c
    nt = np.array([float(bool(r["neutral"])) for r in train])
    design = np.column_stack([1.0 - nt, nt, raw_m])
    beta, *_ = np.linalg.lstsq(design, np.array([r["margin"] for r in train], dtype=float), rcond=None)
    s2["margin_calibration"] = {
        "intercept": float(beta[0]),
        "neutral_intercept": float(beta[1]),
        "slope": float(beta[2]),
    }
    return s2


def project(rows: list[dict[str, Any]], s2: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """v7's projection; a calibration with ``neutral_intercept`` applies it to neutral-site rows (v7.1)."""
    names = tuple(s2["features"])
    c = np.array(s2["coef"])
    xh, xa = v7.side_matrix(rows, names, "home"), v7.side_matrix(rows, names, "away")
    raw_m = (xh - xa) @ c
    raw_t = 2 * s2["intercept"] + (xh + xa) @ c
    mc, tc = s2["margin_calibration"], s2["total_calibration"]
    icpt = np.full(len(rows), float(mc["intercept"]))
    if "neutral_intercept" in mc:
        nt = np.array([bool(r["neutral"]) for r in rows], dtype=bool)
        icpt[nt] = float(mc["neutral_intercept"])
    return icpt + mc["slope"] * raw_m, tc["intercept"] + tc["slope"] * raw_t


# v7's probability, scoring and pmf helpers look ``project`` up in their module; this one equals v7's for a
# calibration without ``neutral_intercept`` (every v7 spec), so v7 numbers are unchanged.
v7.project = project


# ================================================================================================ fit / select
def walk_path(key: str, arm: str, *, holdout: bool = False, model: str = "v71") -> Path:
    """v7.1's walk file for a combo: CFBD arms were re-walked (WORK); core walks are v7's (V7_WORK). ``model="v7"``
    is v7's own file for every arm (the published v7 arm must be scored on v7's features, not v7.1's)."""
    base = V7_WORK if (arm == "core" or model == "v7") else WORK
    return base / (f"walk_{key}.holdout.jsonl" if holdout else f"walk_{key}.jsonl")


def load_walk(key: str, arm: str, *, holdout: bool = False, model: str = "v71") -> list[dict[str, Any]]:
    path = walk_path(key, arm, holdout=holdout, model=model)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def fit_pieces(cand: dict[str, Any]) -> dict[str, Any]:
    rows = load_walk(cand["combo"], cand["arm"])
    tr = v7.called([r for r in rows if r["window"] == "train"])
    va = v7.called([r for r in rows if r["window"] == "validation"])
    s2 = cand["stage2"]
    m_tr, t_tr = project(tr, s2)
    early_tr = np.array([float(min(r["home_games"], r["away_games"]) < v7.EARLY_GAMES) for r in tr])
    fcs_tr = np.array([float(r["is_fcs"]) for r in tr])
    pmf = v7.fit_margin_pmf(m_tr, t_tr, early_tr, fcs_tr, np.array([r["margin"] for r in tr]), balanced=False)
    tsd = v7.fit_total_sds(t_tr, np.array([r["total"] for r in tr]))
    return {
        "candidate": {
            k: cand[k] for k in ("arm", "combo", "lambda_multiplier", "half_life_days", "feature_set")
        },
        "stage2": s2,
        "margin_pmf": pmf,
        "total_sds": tsd,
        "validation_scores": v7.score_v7(va, s2, pmf, tsd),
        "train_scores": v7.score_v7(tr, s2, pmf, tsd),
    }


def cmd_fit(_: argparse.Namespace) -> int:
    cands = []
    walk_sha: dict[str, str] = {}
    for arm in v7.ARMS:
        for mult, hl in [(m, h) for m in v7.LAMBDA_MULTIPLIERS for h in v7.HALF_LIVES]:
            key = v7.combo_key(mult, hl, arm)
            walk_sha[f"{'v7' if arm == 'core' else 'v71'}/walk_{key}.jsonl"] = v7.file_sha256(
                walk_path(key, arm)
            )
            rows = load_walk(key, arm)
            if any(r["window"] == "holdout" for r in rows):
                raise SystemExit("holdout row in a train/validation walk file")
            tr = v7.called([r for r in rows if r["window"] == "train"])
            va = v7.called([r for r in rows if r["window"] == "validation"])
            for fs, names in v7.FEATURE_SETS.items():
                s2 = fit_stage2(tr, names)
                m_tr, t_tr = project(tr, s2)
                m_va, t_va = project(va, s2)
                y_tr = np.array([r["margin"] for r in tr])
                y_va = np.array([r["margin"] for r in va])
                cands.append(
                    {
                        "arm": arm,
                        "combo": key,
                        "lambda_multiplier": mult,
                        "half_life_days": hl,
                        "feature_set": fs,
                        "stage2": s2,
                        "train_n": len(tr),
                        "validation_n": len(va),
                        "train_margin_mae": float(np.abs(y_tr - m_tr).mean()),
                        "train_total_mae": float(np.abs(np.array([r["total"] for r in tr]) - t_tr).mean()),
                        "validation_margin_mae": float(np.abs(y_va - m_va).mean()),
                        "validation_total_mae": float(
                            np.abs(np.array([r["total"] for r in va]) - t_va).mean()
                        ),
                        "validation_margin_rmse": float(np.sqrt(((y_va - m_va) ** 2).mean())),
                    }
                )
                print(
                    f"{key:22s} {fs:5s} val margin MAE {cands[-1]['validation_margin_mae']:.4f} "
                    f"total MAE {cands[-1]['validation_total_mae']:.4f}",
                    flush=True,
                )

    def rank(c: dict[str, Any]) -> tuple[int, float]:  # v7's pinned selection rule, unchanged
        return (round(c["validation_margin_mae"] / 0.005), c["validation_total_mae"])

    best = min(cands, key=rank)
    sel = fit_pieces(best)
    spec = {
        "as_of_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head": v7._git("rev-parse", "HEAD"),
        "model_version": MODEL_VERSION,
        "covariate_population": COVARIATE_POPULATION,
        "prior_sha256": v7.file_sha256(v7.PRIOR_JSON),
        "prior_arms_sha256": v7.file_sha256(PRIOR_ARMS_JSON),
        "walk_files_sha256": walk_sha,
        "candidates": cands,
        "selected": sel["candidate"],
        "stage2": sel["stage2"],
        "margin_pmf": sel["margin_pmf"],
        "total_sds": sel["total_sds"],
        "validation_scores": sel["validation_scores"],
        "train_scores": sel["train_scores"],
        "v7_selected": json.loads(v7.SPEC.read_text(encoding="utf-8"))["selected"],
    }
    SPEC.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("selected", spec["selected"], json.dumps(spec["validation_scores"]))
    return 0


# ================================================================================================ comparison
def arm_values(rows: list[dict[str, Any]], s2: dict[str, Any], pmf: dict[str, Any], tsd: list[float]) -> dict:
    pr = v7.v7_probabilities(rows, s2, pmf, tsd)
    return {
        r["event_id"]: {"mu": float(pr["mu"][i]), "tot": float(pr["tot"][i]), "p": float(pr["p_home"][i])}
        for i, r in enumerate(rows)
    }


def residuals(games: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    """Mean actual-minus-model margin, home vs neutral-site games (the fix-1 diagnostic)."""
    out: dict[str, Any] = {}
    for label, flag in (("home", False), ("neutral", True)):
        r = [g["margin"] - g[arm]["mu"] for g in games if g[arm] is not None and g["neutral"] == flag]
        if len(r) >= 2:
            out[label] = {
                "n": len(r),
                "mean": round(statistics.fmean(r), 3),
                "se": round(statistics.stdev(r) / math.sqrt(len(r)), 3),
            }
    return out


def compare(window: str, spec: dict[str, Any], v7spec: dict[str, Any], v5_by_id: dict | None = None) -> dict:
    """v7 (as published) vs v7.1 on the games both call in ``window``; v5 alongside when given."""
    v7_combo, v71_sel = v7spec["selected"]["combo"], spec["selected"]

    def rows_of(key: str, arm: str, model: str) -> list[dict[str, Any]]:
        if window == "holdout":
            return load_walk(key, arm, holdout=True, model=model)
        return [r for r in load_walk(key, arm, model=model) if r["window"] == window]

    r7 = v7.called(rows_of(v7_combo, v7spec["selected"]["arm"], "v7"))
    r71 = v7.called(rows_of(v71_sel["combo"], v71_sel["arm"], "v71"))
    a7 = arm_values(r7, v7spec["stage2"], v7spec["margin_pmf"], v7spec["total_sds"])
    a71 = arm_values(r71, spec["stage2"], spec["margin_pmf"], spec["total_sds"])
    lines = v7.load_lines()
    by_id = {r["event_id"]: r for r in r71}
    games = []
    for eid in sorted(set(a7) & set(a71), key=lambda e: (by_id[e]["date"], e)):
        r = by_id[eid]
        ln = lines.get(eid) or {}
        g = {
            "event_id": eid,
            "date": r["date"],
            "season": r["season"],
            "band": v7.week_band(int(r["week"])),
            "neutral": bool(r["neutral"]),
            "fcs": bool(r["is_fcs"]),
            "margin": r["margin"],
            "total": r["total"],
            "line_margin": ln.get("margin"),
            "line_total": ln.get("total"),
            "v7": a7[eid],
            "v71": a71[eid],
        }
        if v5_by_id is not None:
            g["v5"] = v5_by_id.get(eid)
        games.append(g)
    arms = ["v7", "v71"] + (["v5"] if v5_by_id is not None else [])
    subsets = {
        "all": games,
        "home": [g for g in games if not g["neutral"]],
        "neutral": [g for g in games if g["neutral"]],
        "fbs_vs_fbs": [g for g in games if not g["fcs"]],
        "fbs_vs_fcs": [g for g in games if g["fcs"]],
        "weeks_0_4": [g for g in games if g["band"] == "weeks_0_4"],
        "weeks_5_14": [g for g in games if g["band"] == "weeks_5_14"],
        "postseason": [g for g in games if g["band"] == "postseason"],
    }
    outcomes = {}
    for name, sub in subsets.items():
        blk: dict[str, Any] = {arm: v7.arm_summary(sub, arm) for arm in arms}
        blk["paired_v71_minus_v7"] = v7.paired(sub, "v7", "v71")
        if v5_by_id is not None:
            blk["paired_v71_minus_v5"] = v7.paired(sub, "v5", "v71")
        outcomes[name] = blk
    return {
        "window": window,
        "counts": {"v7_called": len(r7), "v71_called": len(r71), "scored_both": len(games)},
        "outcomes": outcomes,
        "neutral_residual": {arm: residuals(games, arm) for arm in arms},
        "favourite_buckets": {arm: v7.fav_buckets(games, arm) for arm in arms},
        "market": {
            arm: {m: v7.market_block(games, arm, m) for m in ("spread", "total")} for arm in ("v7", "v71")
        },
    }


def cmd_validate(_: argparse.Namespace) -> int:
    """v7 vs v7.1 on VALIDATION (selection-window numbers; no holdout file opened)."""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    v7spec = json.loads(v7.SPEC.read_text(encoding="utf-8"))
    rep = compare("validation", spec, v7spec)
    (WORK / "validation_compare.json").write_text(
        json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    o = rep["outcomes"]["all"]
    print(json.dumps({"v7": o["v7"], "v71": o["v71"], "paired": o["paired_v71_minus_v7"]}, indent=1))
    print(json.dumps(rep["neutral_residual"], indent=1))
    return 0


def cmd_v7repro(_: argparse.Namespace) -> int:
    """v7's published holdout row (cfb_v7_report.json) from v7's own pinned files. No v7.1 holdout file opened."""
    v7rule = json.loads(v7.RULE_PATH.read_text(encoding="utf-8"))
    v7spec = json.loads(v7.SPEC.read_text(encoding="utf-8"))
    combo = v7spec["selected"]["combo"]
    path = V7_WORK / f"walk_{combo}.holdout.jsonl"
    if v7rule["holdout_walk_files_sha256"][path.name] != v7.file_sha256(path):
        raise SystemExit("v7 holdout walk file differs from the v7 pin")
    rows = v7.called(load_walk(combo, v7spec["selected"]["arm"], holdout=True, model="v7"))
    a7 = arm_values(rows, v7spec["stage2"], v7spec["margin_pmf"], v7spec["total_sds"])
    games = [
        {"date": r["date"], "margin": r["margin"], "total": r["total"], "v7": a7[r["event_id"]]} for r in rows
    ]
    got = v7.arm_summary(games, "v7")
    published = json.loads(v7.REPORT_JSON.read_text(encoding="utf-8"))["outcomes"]["all"]["v7"]
    ok = all(got[k] == published[k] for k in ("n", "margin_mae", "total_mae", "ml_brier", "ml_log_loss"))
    V7_REPRO.write_text(
        json.dumps({"got": got, "published": published, "pass": ok}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("v7 reproduction", "PASS" if ok else "FAIL", got)
    return 0 if ok else 1


# ================================================================================================ pinned rule
RULE: dict[str, Any] = {
    "id": "cfb-v71-holdout-rule-1",
    "model_version": MODEL_VERSION,
    "approval": "operator 2026-11-22: 'Refit NCAAF v7'; paper challenger, Flat only, never champion",
    "inherits": "cfb-v7-holdout-rule-1 (windows, point in time, grid, selection rule, pmf fitting, scored set)",
    "changes_vs_v7": [
        "stage-2 margin calibration a_home*(1-neutral) + a_neutral*neutral + b*raw_margin, fit on train",
        (
            "CFBD covariates z-scored over and applied to the season's FBS teams only; CFBD prior gammas "
            "re-estimated on 2016-2021 finals; the 16 CFBD walks re-run; the 8 core walks reused from v7"
        ),
    ],
    "holdout_status": (
        "SECOND READ. The v7 holdout window (2024-11-15 onwards) was read for v7 on 2026-11-21T21:12:25Z "
        "(cfb_v7_report.md), and the 2026-11-21/22 bug hunt read holdout neutral-site residuals (+2.02, n 115). "
        "This read is confirmatory only: nothing is selected or tuned on it, and the neutral-site before/after is "
        "design-informed."
    ),
    "pre_pin_exposure": (
        "Disclosed: before this pin, a path bug in `v7repro` (fixed) scored v7's published stage 2 and pmfs on the "
        "v7.1 holdout walk of the selected CFBD combo (FBS-only covariates) instead of v7's own walk: n 1948, margin "
        "MAE 12.5031, Brier 0.16391, log loss 0.48721 (v7 published 12.4674 / 0.16356 / 0.48661). That is a "
        "holdout look at the fix-2 features under v7's stage 2, seen while `fit` was already running. Nothing was "
        "changed after it: the grid, the selection rule and the pmf fit are v7's, applied on train/validation only."
    ),
    "scored_set": "holdout games both v7 (as published) and v7.1 call; v5 as reproduced for v7 alongside",
    "bootstrap": "paired v7.1 - v7 (and v7.1 - v5) deltas, date-clustered, 2000 resamples, seed 11, 95% CI",
    "verdict": {
        "WORSE (stop)": (
            "v7.1 - v7 on all scored games: margin MAE delta CI low > 0, or moneyline Brier delta CI low > 0, or "
            "log loss delta CI low > 0. Then no v7.1 artifact is routed; v7 stays the routed challenger and the "
            "operator decides"
        ),
        "CONFIRMED": (
            "not WORSE, and point deltas within v7's no_point_regression (margin MAE <= 0, Brier <= +0.0005, log "
            "loss <= +0.0015), and |mean actual - model| on neutral-site games smaller for v7.1 than for v7"
        ),
        "NOT_CONFIRMED": "neither: v7.1 is packaged as the routed challenger and the shortfall is reported",
    },
    "reported_not_gating": [
        "v7.1 vs v5 with v7's gate (coverage, significant improvement, no point regression)",
        "home / neutral / FBS-FBS / FBS-FCS / weeks 0-4 / 5-14 / postseason",
        "neutral-site and home mean residual with se, v7 vs v7.1",
        "calibration: ECE and favourite buckets",
        "market-relative vs ESPN close (DESIGN-INFORMED): corr(model - line, result - line), slopes, ATS",
    ],
}


def rule_sha256() -> str:
    return hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cmd_pin(_: argparse.Namespace) -> int:
    if not json.loads(V7_REPRO.read_text(encoding="utf-8"))["pass"]:
        raise SystemExit("v7 reproduction did not pass")
    sha = rule_sha256()
    payload = {
        "rule": RULE,
        "rule_sha256": sha,
        "pinned_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spec_sha256": v7.file_sha256(SPEC),
        "prior_arms_sha256": v7.file_sha256(PRIOR_ARMS_JSON),
        "holdout_walk_files_sha256": {
            p.name: v7.file_sha256(p) for p in sorted(WORK.glob("walk_*.holdout.jsonl"))
        },
    }
    RULE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(sha)
    return 0


def _committed_unmodified(path: Path) -> bool:
    rel = str(path.relative_to(REPO)).replace("\\", "/")
    return bool(v7._git("ls-files", rel)) and not v7._git("status", "--porcelain", "--", rel)


def verdict_of(rep: dict[str, Any]) -> str:
    pv = rep["outcomes"]["all"]["paired_v71_minus_v7"]
    if any(pv[k]["ci_95"][0] > 0 for k in ("margin_mae", "ml_brier", "ml_log_loss")):
        return "WORSE (stop)"
    nr = rep["neutral_residual"]
    neutral_better = abs(nr["v71"]["neutral"]["mean"]) < abs(nr["v7"]["neutral"]["mean"])
    if (
        pv["margin_mae"]["delta"] <= 0.0
        and pv["ml_brier"]["delta"] <= 0.0005
        and pv["ml_log_loss"]["delta"] <= 0.0015
        and neutral_better
    ):
        return "CONFIRMED"
    return "NOT_CONFIRMED"


def cmd_holdout(args: argparse.Namespace) -> int:
    if HOLDOUT_MARK.exists():
        raise SystemExit(f"v7.1 holdout already read: {HOLDOUT_MARK.read_text(encoding='utf-8')}")
    if REPORT_JSON.exists() or v7._git("log", "--oneline", "-1", "--", "outputs/ncaaf/cfb_v71_report.json"):
        raise SystemExit("v7.1 holdout already read: cfb_v71_report.json exists in the tree or history")
    pinned = json.loads(RULE_PATH.read_text(encoding="utf-8"))
    sha = rule_sha256()
    if pinned["rule_sha256"] != sha or args.rule_sha != sha or pinned["rule"] != RULE:
        raise SystemExit("pinned rule sha256 mismatch")
    for path in (RULE_PATH, SPEC, Path(__file__).resolve(), PRIOR_ARMS_JSON, V7_REPRO):
        if not _committed_unmodified(path):
            raise SystemExit(f"{path.name} is not committed or has local changes")
    if pinned["spec_sha256"] != v7.file_sha256(SPEC):
        raise SystemExit("spec changed after the pin")
    for p in sorted(WORK.glob("walk_*.holdout.jsonl")):
        if pinned["holdout_walk_files_sha256"].get(p.name) != v7.file_sha256(p):
            raise SystemExit(f"holdout walk file {p.name} changed after the pin")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    v7spec = json.loads(v7.SPEC.read_text(encoding="utf-8"))
    v5 = json.loads(v7.V5_REPRO.read_text(encoding="utf-8"))["per_event"]
    mark = {
        "read_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule_sha256": sha,
        "head": v7._git("rev-parse", "HEAD"),
        "note": "second read of the v7 holdout window; confirmatory",
    }
    WORK.mkdir(parents=True, exist_ok=True)
    HOLDOUT_MARK.write_text(json.dumps(mark, indent=2) + "\n", encoding="utf-8")  # just before the read
    rep = compare("holdout", spec, v7spec, v5)
    v7_report = json.loads(v7.REPORT_JSON.read_text(encoding="utf-8"))
    coverage = rep["counts"]["v71_called"] / max(1, v7_report["counts"]["v5_rows"])
    report = {
        "as_of_utc": mark["read_at_utc"],
        "holdout_read": mark,
        "rule_sha256": sha,
        "v7_reproduction": json.loads(V7_REPRO.read_text(encoding="utf-8")),
        **rep,
        "v5_gate_for_v71": v7.gate_for(rep["outcomes"]["all"]["paired_v71_minus_v5"], coverage),
        "verdict": verdict_of(rep),
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    REPORT_MD.write_text(render_md(report, spec), encoding="utf-8")
    print(report["verdict"])
    return 0


# ================================================================================================ report
def _ci(d: dict[str, Any], nd: int = 4) -> str:
    lo, hi = d["ci_95"]
    return f"{d['delta']:+.{nd}f} [{lo:+.{nd}f}, {hi:+.{nd}f}]"


def render_md(rep: dict[str, Any], spec: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# cfb-structural-v7.1 vs v7: confirmatory holdout read (quick-261122-v71)")
    L.append("")
    L.append(
        f"Rule `{RULE['id']}` sha256 `{rep['rule_sha256']}`; read at {rep['holdout_read']['read_at_utc']}."
    )
    L.append("")
    L.append(f"**{RULE['holdout_status']}**")
    L.append("")
    L.append(f"Verdict: **{rep['verdict']}**. Selected v7.1 candidate: `{json.dumps(spec['selected'])}`.")
    L.append("")
    L.append(
        f"v7 reproduction from its own files: {'PASS' if rep['v7_reproduction']['pass'] else 'FAIL'} "
        f"(n {rep['v7_reproduction']['got']['n']}, MAE {rep['v7_reproduction']['got']['margin_mae']}, "
        f"Brier {rep['v7_reproduction']['got']['ml_brier']})."
    )
    L.append("")
    L.append(
        "| subset | n | v7 MAE | v7.1 MAE | v7 Brier | v7.1 Brier | v7 log loss | v7.1 log loss | "
        "MAE delta [CI] | Brier delta [CI] | log loss delta [CI] |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, blk in rep["outcomes"].items():
        a, b, p = blk["v7"], blk["v71"], blk["paired_v71_minus_v7"]
        if not a["n"]:
            continue
        L.append(
            f"| {name} | {a['n']} | {a['margin_mae']} | {b['margin_mae']} | {a['ml_brier']} | {b['ml_brier']} | "
            f"{a['ml_log_loss']} | {b['ml_log_loss']} | {_ci(p['margin_mae'], 3)} | {_ci(p['ml_brier'])} | "
            f"{_ci(p['ml_log_loss'])} |"
        )
    L.append("")
    nr = rep["neutral_residual"]
    L.append("Mean actual minus model margin (se):")
    L.append("")
    for arm in nr:
        L.append(
            f"- {arm}: "
            + ", ".join(f"{k} {v['mean']:+.2f} ({v['se']:.2f}, n {v['n']})" for k, v in nr[arm].items())
        )
    L.append("")
    o = rep["outcomes"]["all"]
    L.append(
        f"ECE: v7 {o['v7']['ml_ece']:.4f}, v7.1 {o['v71']['ml_ece']:.4f}. Total MAE: v7 {o['v7']['total_mae']}, "
        f"v7.1 {o['v71']['total_mae']}."
    )
    L.append("")
    pv5 = o["paired_v71_minus_v5"]
    L.append(
        f"v7.1 minus v5: MAE {_ci(pv5['margin_mae'], 3)}, Brier {_ci(pv5['ml_brier'])}, log loss "
        f"{_ci(pv5['ml_log_loss'])}; v7's gate for v7.1: {rep['v5_gate_for_v71']}."
    )
    L.append("")
    L.append("Market-relative vs ESPN close (DESIGN-INFORMED, not gating):")
    L.append("")
    L.append("| arm | market | n | corr [CI] | orthogonalised slope [CI] | ATS 3+ | ATS 7+ | verdict |")
    L.append("|---|---|---|---|---|---|---|---|")
    for arm, mk in rep["market"].items():
        for m, blk in mk.items():
            if "corr_ci95" not in blk:
                continue
            a3, a7 = blk["ats_disagree_ge_3"], blk["ats_disagree_ge_7"]
            L.append(
                f"| {arm} | {m} | {blk['n']} | {blk['corr_model_minus_line_vs_result_minus_line']:+.4f} "
                f"{blk['corr_ci95']} | {blk['slope_orthogonalised']:+.4f} {blk['slope_orthogonalised_ci95']} | "
                f"{a3['wins']}-{a3['losses']} ({a3['win_pct']}%) | {a7['wins']}-{a7['losses']} ({a7['win_pct']}%) | "
                f"{blk['verdict']} |"
            )
    L.append("")
    L.append(
        "Calibration by favourite size (projected favourite margin bucket: model win % vs realised win %):"
    )
    L.append("")
    for arm in ("v7", "v71"):
        L.append(
            f"- {arm}: "
            + "; ".join(
                f"{b['bucket']} n {b['n']}: {b['fav_model_win_pct']}% vs {b['fav_realised_win_pct']}%"
                for b in rep["favourite_buckets"][arm]
            )
        )
    L.append("")
    return "\n".join(L) + "\n"


# ================================================================================================ artifact
def artifact_payload(spec: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from model_prediction.production_registry import compute_artifact_hash

    sel = spec["selected"]
    prior = load_prior(sel["arm"])
    mult = float(sel["lambda_multiplier"])
    payload: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "league": "NCAAF",
        "sport": "ncaaf",
        "schema_version": "1",
        "method": "cfb_v7_pit_pbp_ridge_ratings_linear_points_keyed_margin_pmf",
        "note": (
            "PAPER CHALLENGER (quick-261122-v71): cfb-structural-v7 refit with the 2026-11-22 bug-hunt fixes. "
            "Flat/research rows only; never champion, never Main, never the auto-buyer. Fit on train, selected on "
            "validation by v7's rule; the holdout read is a confirmatory second read of v7's window."
        ),
        "point_in_time": {
            "result_buffer_hours": core.RESULT_BUFFER_HOURS,
            "research_decision_time": "min(kickoff, 08:00 America/New_York on the kickoff's ET date)",
            "rule": "a game feeds ratings only if kickoff + result_buffer_hours <= decision time",
            "serving_fit_rule": "per_game_research_decision_time",
        },
        "serving": {"score_only_rows": "terminal_without_pbp", "feature_capture": True},
        "prior": {
            "arm": sel["arm"],
            "rho_off": prior["rho_off"],
            "rho_def": prior["rho_def"],
            "lam_off": {k: mult * v for k, v in prior["lam_off"].items()},
            "lam_def": {k: mult * v for k, v in prior["lam_def"].items()},
            "cov_off": prior.get("cov_off") or {},
            "cov_def": prior.get("cov_def") or {},
            "half_life_days": sel["half_life_days"],
            "lambda_multiplier": mult,
            "finals_team_lambda": core.FINALS_TEAM_LAMBDA,
            "league_lambda": core.LEAGUE_LAMBDA,
            "cfbd_fields": dict(core.CFBD_FIELDS) if sel["arm"] != "core" else {},
            "covariate_population": COVARIATE_POPULATION if sel["arm"] != "core" else None,
            "cfbd_values": "current-revised (as_published_verified false); preseason rows only",
        },
        "stage2": spec["stage2"],
        "margin_distribution": {
            "support": v7.MARGIN_SUPPORT,
            "key_max": v7.KEY_MAX,
            "recentre_iterations": v7.RECENTRE_ITERATIONS,
            "centring": "mean",
            "key_log_multipliers": spec["margin_pmf"]["theta"],
            "sd_coef": spec["margin_pmf"]["sd_coef"],
            "sd_terms": spec["margin_pmf"]["sd_terms"],
            "sd_scale": v7.SD_SCALE,
            "sd_total_centre": v7.SD_TOTAL_CENTRE,
            "early_games": v7.EARLY_GAMES,
        },
        "total_distribution": {
            "support": v7.TOTAL_SUPPORT,
            "buckets": [list(b) for b in v7.TOTAL_BUCKETS],
            "bucket_sds": spec["total_sds"],
        },
        "training": {
            "spec_sha256": v7.file_sha256(SPEC),
            "rule_sha256": rule_sha256(),
            "report_sha256": v7.file_sha256(REPORT_JSON),
            "train_end": v7.TRAIN_END,
            "validation_end": v7.VAL_END,
            "selected": sel,
            "holdout_verdict": report["verdict"],
            "refit_of": "cfb-structural-v7",
        },
        "qualification": {
            "status": "challenger",
            "qualified": False,
            "note": "Paper challenger; the 2027 forward gate (docs/CFB_V7_2027_GATE.md) decides; promotion only via "
            "model_promotion promote.",
        },
        "frozen_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "promotion_protocol_hash": rule_sha256(),
    }
    payload["artifact_hash"] = compute_artifact_hash(payload)
    return payload


def parity_fixture(spec: dict[str, Any]) -> dict[str, Any]:
    """v7's 30-team 2022-2023 fixture universe priced by THIS pipeline, with the raw CFBD rows of every team in it
    (FCS included) and the FBS list, so the serving test runs the FBS-only standardisation itself."""
    rows_raw = []
    with v7.PBP.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                if r["season"] in (v7.FIXTURE_SEASON - 1, v7.FIXTURE_SEASON):
                    rows_raw.append(r)
    counts: dict[str, int] = defaultdict(int)
    for r in rows_raw:
        if r["game_class"] == "FBS-FBS" and r["season"] == v7.FIXTURE_SEASON:
            counts[r["team_id"]] += 1
    teams = set(sorted(counts, key=lambda t: (-counts[t], int(t)))[: v7.FIXTURE_TEAMS])
    sub = [
        {k: r.get(k) for k in v7.FIXTURE_FIELDS}
        for r in rows_raw
        if r["team_id"] in teams and r["opponent_id"] in teams
    ]
    rows = sorted(
        (core.team_game_from_row(r) for r in sub), key=lambda r: (r.kickoff, r.event_id, -r.home_sign)
    )
    # The season-level FBS list comes from the FULL research spine (a 30-team subset would call everyone FBS).
    full_rows, _ = v7.load_team_games()
    full_classes = core.season_classes(full_rows)
    fbs = sorted(fbs_population(full_classes, v7.FIXTURE_SEASON), key=int)
    classes = {k: c for k, c in full_classes.items() if k[0] in teams and k[1] in (2022, 2023)}
    cfbd_rows = [
        json.loads(ln)
        for ln in v7.CFBD_PRIORS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and int(json.loads(ln)["season"]) == v7.FIXTURE_SEASON
    ]
    covs_all = (
        core.standardise_covariates(cfbd_rows, population=fbs) if spec["selected"]["arm"] != "core" else {}
    )
    covs = {t: z for t, z in covs_all.items() if t in teams}
    sel = spec["selected"]
    params = v7.prior_params(load_prior(sel["arm"]), float(sel["lambda_multiplier"]), sel["half_life_days"])
    finals = core.fit_finals(
        [r for r in rows if r.season == v7.FIXTURE_SEASON - 1], v7.FIXTURE_SEASON - 1, classes
    )
    ordered = sorted(teams, key=int)
    cases = []
    for i, day in enumerate(("2023-08-26", "2023-09-16", "2023-10-14", "2023-11-11")):
        decision = datetime.fromisoformat(f"{day}T12:00:00+00:00")
        state = core.fit_state(rows, v7.FIXTURE_SEASON, decision, classes, finals, params, ordered, covs)
        made = 0
        for k in range(len(ordered)):
            if made == 3:
                break
            home, away = ordered[(3 * i + 2 * k) % len(ordered)], ordered[(3 * i + 2 * k + 7) % len(ordered)]
            neutral = made == 2
            try:
                mf = core.matchup_features(state, home, away, neutral=neutral, classes=classes)
            except core.V7NoCall:
                continue
            made += 1
            walk_row = {
                "home_e": mf.home.values,
                "away_e": mf.away.values,
                "home_games": mf.home_games,
                "away_games": mf.away_games,
                "is_fcs": mf.is_fbs_vs_fcs,
                "neutral": neutral,
            }
            pr = v7.v7_probabilities([walk_row], spec["stage2"], spec["margin_pmf"], spec["total_sds"])
            pt = v7.total_pmf(pr["tot"], pr["tsd"])[0]
            pm = pr["pmf"][0]
            cases.append(
                {
                    "decision_utc": decision.isoformat(),
                    "home_id": home,
                    "away_id": away,
                    "neutral": neutral,
                    "spread_home_line": -3.5,
                    "total_line": 50.5,
                    "expected": {
                        "mu_margin": float(pr["mu"][0]),
                        "mu_total": float(pr["tot"][0]),
                        "margin_sd": float(pr["sd"][0]),
                        "total_sd": float(pr["tsd"][0]),
                        "home_win": float(pr["p_home"][0]),
                        "home_cover": float(pm[v7.M_GRID > 3.5].sum()),
                        "over": float(pt[v7.T_GRID > 50.5].sum()),
                    },
                }
            )
    return {
        "season": v7.FIXTURE_SEASON,
        "spec_sha256": v7.file_sha256(SPEC),
        "rows": sub,
        "classes": [[t, s, c] for (t, s), c in sorted(classes.items())],
        "fbs_team_ids": fbs,
        "cfbd_rows": cfbd_rows,
        "covariates": covs,
        "cases": cases,
    }


def cmd_artifact(_: argparse.Namespace) -> int:
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    if report["verdict"].startswith("WORSE"):
        raise SystemExit(f"verdict {report['verdict']}: no artifact is written")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    payload = artifact_payload(spec, report)
    ARTIFACT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FIXTURE.write_text(json.dumps(parity_fixture(spec), sort_keys=True) + "\n", encoding="utf-8")
    print(ARTIFACT, payload["artifact_hash"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prep").set_defaults(func=cmd_prep)
    w = sub.add_parser("walk")
    w.add_argument("--workers", type=int, default=4)
    w.add_argument("--only", default=None)
    w.set_defaults(func=cmd_walk)
    sub.add_parser("fit").set_defaults(func=cmd_fit)
    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("v7repro").set_defaults(func=cmd_v7repro)
    sub.add_parser("pin").set_defaults(func=cmd_pin)
    h = sub.add_parser("holdout")
    h.add_argument("--rule-sha", required=True)
    h.set_defaults(func=cmd_holdout)
    sub.add_parser("artifact").set_defaults(func=cmd_artifact)
    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
