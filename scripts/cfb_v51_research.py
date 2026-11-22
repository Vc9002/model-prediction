"""Fit, select and score cfb-structural-v5.1 against the served v5 on one walk-forward split.

Stages (ARCHITECTURE.md validation contract):

  cache     Every completed FBS-involved NCAAF game in a FROZEN copy of the history (``--history-root``, sha256
            recorded), with features from history strictly before kickoff for four extractors:
              v5        the served v5 extractor (static conference table, stored "EPA" fields)
              conf      v5.1 conference table, stored "EPA" fields   (ablation only)
              live      v5.1 conference table, lagged scoring proxy computed in the feature path
              drop      v5.1 conference table, "EPA" channel off (v5's PPP fallback)
            Week: ESPN postseason (season type 3) -> 15, as serving maps it (models.md finding 7); else the row's
            ESPN week; else derived from the date.
  prereg    Writes the candidate list, selection metrics and the "validated" rule BEFORE anything is fitted.
  fit       chronological_split (60/20/20 on complete dates). Fit on TRAIN only; choose on VALIDATION only.
            Writes the selection and the artifact candidate (no holdout read).
  holdout   Reads the locked holdout ONCE (refuses if the read marker exists): served v5 (NB engine, artifact
            parameters) vs v5.1 (selected), with date-clustered bootstrap CIs, and the market-relative check
            against archived closing lines. Writes config/models/cfb-structural-v5.1.json and the report.

validation.build_walk_forward_rows is not used: it builds Elo/trend rows from a FeatureStore, while the CFB
structural extractor is its own walk-forward (history strictly before kickoff), as in cfb_v5_research.py.

Usage (cwd = repo root, PYTHONPATH=src):
  python scripts/cfb_v51_research.py cache --history-root <frozen copy> --workers 11
  python scripts/cfb_v51_research.py prereg
  python scripts/cfb_v51_research.py fit
  python scripts/cfb_v51_research.py holdout
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import multiprocessing as mp
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "outputs" / "ncaaf"
WORK = OUT / "cfb_v51_work"
CACHE = WORK / "rows.jsonl"
PREREG = OUT / "cfb_v51_prereg.json"
SELECTION = OUT / "cfb_v51_selection.json"
HOLDOUT_MARKER = OUT / "cfb_v51_holdout_read.json"
REPORT_JSON = OUT / "cfb_v51_report.json"
REPORT_MD = OUT / "cfb_v51_report.md"
ARTIFACT = REPO / "config" / "models" / "cfb-structural-v5.1.json"
CONF_TABLE_REL = "config/ncaaf/cfb_conference_membership.json"
MP_DATA = Path("E:/MP/data")
FEATURE_VARIANTS = ("v5", "conf", "live", "drop")
SELECTABLE_FEATURES = ("live", "drop")
SAMPLE_GRID = (3, 4, 5, 6)
KEY_SETS = {
    "none": [],
    "k3_7": [3, 7],
    "k3_7_10_14": [3, 7, 10, 14],
    "all_1_28": list(range(1, 29)),
}
BREAK_EVEN = 0.524  # flat sportsbook price as a probability, diagnostic only
MAX_MARGIN = 120


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


def derive_week(start: datetime, season_first: dict[int, datetime]) -> int:
    if start.month in (1, 2) or (start.month == 12 and start.day >= 14):
        return 15
    first = season_first.get(season_of(start))
    return 1 if first is None else max(0, min(14, 1 + (start - first).days // 7))


def load_raw_facts(history_root: Path) -> dict[str, dict[str, Any]]:
    """event_id -> {week, is_neutral_site, postseason} from the raw rows (CFBGameRecord drops them)."""
    facts: dict[str, dict[str, Any]] = {}
    for rel in ("processed/ncaaf/games.jsonl", "historical/ncaaf_games_all.jsonl"):
        for line in (history_root / rel).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            cur = facts.setdefault(str(r.get("event_id")), {})
            if r.get("week") is not None and "week" not in cur:
                cur["week"] = int(r["week"])
            if r.get("is_neutral_site") is not None and "is_neutral_site" not in cur:
                cur["is_neutral_site"] = bool(r["is_neutral_site"])
            post = str(r.get("season_type") or "") == "post-season" or r.get("espn_season_type_id") == 3
            cur["postseason"] = bool(cur.get("postseason")) or post
    return facts


# =========================================================================== stage: cache
def _extractors() -> dict[str, Any]:
    from model_prediction.features.cfb_conference import ConferenceMembership
    from model_prediction.features.cfb_features_v51 import CFBFeatureExtractorV51
    from model_prediction.models.cfb_structural_v5 import cfb_structural_v5_model

    membership = ConferenceMembership.load(REPO / CONF_TABLE_REL)

    class ConfOnly(CFBFeatureExtractorV51):
        def _epa_reader(self, keyed_rows):  # stored fields: the conference fix alone
            return None

    return {
        "v5": cfb_structural_v5_model().extractor,
        "conf": ConfOnly(membership, epa_mode="live_proxy"),
        "live": CFBFeatureExtractorV51(membership, epa_mode="live_proxy"),
        "drop": CFBFeatureExtractorV51(membership, epa_mode="dropped"),
    }, membership


def _worker(args: tuple[int, int, str]) -> int:
    k, n_workers, root_s = args
    from model_prediction.models.cfb_structural_v51 import training_features
    from model_prediction.models.college_football import _load_cfb_history

    root = Path(root_s)
    extractors, membership = _extractors()
    facts = load_raw_facts(root)
    history = sorted(_load_cfb_history(root), key=lambda g: (g.start, str(g.event_id)))
    starts = [g.start for g in history]
    season_first: dict[int, datetime] = {}
    for g in history:
        s = season_of(g.start)
        if s not in season_first or g.start < season_first[s]:
            season_first[s] = g.start
    part = WORK / f"rows.part{k}.jsonl"
    done = (
        {json.loads(x)["event_id"] for x in part.read_text().splitlines() if x.strip()}
        if part.exists()
        else set()
    )
    todo = [
        g
        for g in history
        if (g.feed_groups is None or 80 in g.feed_groups)
        and g.home_score is not None
        and g.away_score is not None
    ][k::n_workers]
    t0 = time.time()
    with part.open("a", encoding="utf-8") as out:
        for i, g in enumerate(todo):
            eid = str(g.event_id)
            if eid in done:
                continue
            past = history[: bisect.bisect_left(starts, g.start)]
            fact = facts.get(eid, {})
            season = season_of(g.start)
            if fact.get("postseason"):
                week = 15
            elif fact.get("week") is not None:
                week = int(fact["week"])
            else:
                week = derive_week(g.start, season_first)
            neutral = bool(fact.get("is_neutral_site", False))
            row: dict[str, Any] = {
                "event_id": eid,
                "start_utc": g.start.isoformat(),
                "date": g.start.date().isoformat(),
                "season": season,
                "week": week,
                "neutral": neutral,
                "home": g.home_team,
                "away": g.away_team,
                "home_id": membership.team_id(g.home_team, g.home_team_id),
                "away_id": membership.team_id(g.away_team, g.away_team_id),
                "margin": float(g.home_score) - float(g.away_score),
                "total": float(g.home_score) + float(g.away_score),
            }
            for name, ex in extractors.items():
                f = training_features(ex, past, g, season=season, week=week, neutral=neutral)
                row[name] = {
                    "projected_margin_home": float(f.projected_margin_home),
                    "projected_total": float(f.projected_total),
                    "is_conference_matchup": bool(f.is_conference_matchup),
                    "is_fbs_vs_fcs": bool(f.is_fbs_vs_fcs),
                    "sample_games": int(f.sample_games),
                    "uncertainty": float(f.uncertainty),
                    "is_neutral_site": neutral,
                    "home_epa_net": float(f.home_epa_net),
                    "away_epa_net": float(f.away_epa_net),
                }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            if (i + 1) % 250 == 0:
                print(f"  worker {k}: {i + 1}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    return len(todo)


def cmd_cache(args: argparse.Namespace) -> None:
    root = Path(args.history_root)
    WORK.mkdir(parents=True, exist_ok=True)
    meta = {
        "history_root": str(root),
        "files": {
            rel: {"sha256": _sha(root / rel), "rows": (root / rel).read_text(encoding="utf-8").count("\n")}
            for rel in ("processed/ncaaf/games.jsonl", "historical/ncaaf_games_all.jsonl")
        },
        "conference_table_sha256": _sha(REPO / CONF_TABLE_REL),
        "started_utc": datetime.now(UTC).isoformat(),
        "workers": args.workers,
    }
    (WORK / "cache_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    t0 = time.time()
    with mp.Pool(args.workers) as pool:
        counts = pool.map(_worker, [(k, args.workers, str(root)) for k in range(args.workers)])
    rows = []
    for k in range(args.workers):
        for line in (WORK / f"rows.part{k}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    CACHE.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    print(f"cached {len(rows)} games (expected {sum(counts)}) in {time.time() - t0:.0f}s")


# =========================================================================== helpers
def ols(xs: list[list[float]], ys: list[float]) -> list[float]:
    x = np.array([[1.0, *row] for row in xs])
    coef, *_ = np.linalg.lstsq(x, np.array(ys), rcond=None)
    return [float(c) for c in coef]


def pstdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else float("nan")


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return float("nan")
    return float(np.corrcoef(np.array(xs), np.array(ys))[0, 1])


def load_rows() -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in CACHE.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    lines = load_lines()
    for r in rows:
        ln = lines.get(r["event_id"], {})
        r["line_spread_home"] = ln.get("spread_home")
        r["line_total"] = ln.get("total")
    return rows


def load_lines() -> dict[str, dict[str, float]]:
    """event_id -> archived closing home spread / total. ESPN's pinned median close (data/research/ncaaf_v6,
    rule espn-lines-rule-1) first; CFBD's consensus close in ESPN orientation for events ESPN's fetch lacks."""
    out: dict[str, dict[str, float]] = {}
    for line in (
        (MP_DATA / "research" / "ncaaf_v6" / "espn_lines.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        rec = json.loads(line)
        sel = rec.get("selected") or {}
        cur: dict[str, float] = {}
        if sel.get("home_spread_line") is not None:
            cur["spread_home"] = float(sel["home_spread_line"])
        if sel.get("total_line") is not None:
            cur["total"] = float(sel["total_line"])
        if cur:
            out[str(rec["event_id"])] = cur
    cfbd: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for line in (
        (MP_DATA / "research" / "ncaaf_v7" / "cfbd" / "lines.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        rec = json.loads(line)
        eid = rec.get("espn_event_id")
        if not eid or str(rec.get("espn_match_status", "")).startswith("NO_CALL"):
            continue
        if rec.get("spread_espn_home_close") is not None:
            cfbd[str(eid)]["spread_home"].append(float(rec["spread_espn_home_close"]))
        if rec.get("total_close") is not None:
            cfbd[str(eid)]["total"].append(float(rec["total_close"]))
    for eid, vals in cfbd.items():
        cur = out.setdefault(eid, {})
        for key, xs in vals.items():
            if key not in cur and xs:
                cur[key] = float(statistics.median(xs))
    return out


def split(rows: list[dict[str, Any]]) -> tuple[list, list, list, dict[str, Any]]:
    from model_prediction.validation import chronological_split

    ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0), row=r) for r in rows]
    tr, va, ho, meta = chronological_split(ducks)
    return [d.row for d in tr], [d.row for d in va], [d.row for d in ho], meta


def fit_params(
    train: list[dict[str, Any]], fv: str, *, k: int, neutral: bool, mean_cohort: str
) -> dict[str, Any]:
    """Every v5.1 layer from TRAIN rows of one feature variant (v5's estimators, plus the optional neutral term)."""
    from model_prediction.models.cfb_structural_v4 import (
        CFB_BLOWOUT_MARGIN_SCALE,
        CFB_BLOWOUT_SPREAD_THRESHOLD,
        CFB_TOTAL_DEVIATION_SCALE,
        CFB_TOTAL_DEVIATION_THRESHOLD,
    )

    fit_rows = [r for r in train if mean_cohort == "all" or not r[fv]["is_fbs_vs_fcs"]]
    xs = [
        [r[fv]["projected_margin_home"], *([1.0 if r["neutral"] else 0.0] if neutral else [])]
        for r in fit_rows
    ]
    coef = ols(xs, [r["margin"] for r in fit_rows])
    a, b = coef[0], coef[1]
    c = coef[2] if neutral else 0.0
    tc = ols([[r[fv]["projected_total"]] for r in train], [r["total"] for r in train])
    p: dict[str, Any] = {
        "margin_calibration": {
            "intercept": round(a, 4),
            "slope": round(b, 4),
            "neutral_intercept": round(c, 4),
        },
        "total_calibration": {"intercept": round(tc[0], 4), "slope": round(tc[1], 4)},
        "established_min_sample": k,
        "sd_widening": {
            "blowout_spread_threshold": CFB_BLOWOUT_SPREAD_THRESHOLD,
            "blowout_margin_scale": CFB_BLOWOUT_MARGIN_SCALE,
            "blowout_cap_points": 15.0,
            "total_deviation_threshold": CFB_TOTAL_DEVIATION_THRESHOLD,
            "total_deviation_scale": CFB_TOTAL_DEVIATION_SCALE,
            "total_deviation_cap_points": 20.0,
        },
        "floors": {"total_points": 24.0, "team_points": 3.0},
        "margin_pmf": {"max_margin": MAX_MARGIN, "key_multipliers": {"0": 0.0}},
    }
    from model_prediction.models.cfb_structural_v51 import V51Params, calibrated_means

    # residuals against the calibrated, floored mean exactly as served
    tmp = {
        **p,
        "margin_sd_table": dict.fromkeys(("established", "thin_sample", "fbs_vs_fcs"), 16.0),
        "conference_margin_scale": 1.0,
        "total_sd": 16.0,
    }
    prm = V51Params.from_mapping(tmp)
    resid, tresid = {}, []
    for r in train:
        f = r[fv]
        mu, tmu = calibrated_means(
            prm,
            raw_margin=f["projected_margin_home"],
            raw_total=f["projected_total"],
            is_neutral_site=r["neutral"],
        )
        resid[r["event_id"]] = r["margin"] - mu
        tresid.append(r["total"] - tmu)
    est = [r for r in train if not r[fv]["is_fbs_vs_fcs"] and r[fv]["sample_games"] >= k]
    thin = [r for r in train if not r[fv]["is_fbs_vs_fcs"] and r[fv]["sample_games"] < k]
    fcs = [r for r in train if r[fv]["is_fbs_vs_fcs"]]
    est_non = [r for r in est if not r[fv]["is_conference_matchup"]] or est
    est_conf = [r for r in est if r[fv]["is_conference_matchup"]] or est
    sd_est = pstdev([resid[r["event_id"]] for r in est_non])
    p["margin_sd_table"] = {
        "established": round(sd_est, 3),
        "thin_sample": round(pstdev([resid[r["event_id"]] for r in thin]), 3),
        "fbs_vs_fcs": round(pstdev([resid[r["event_id"]] for r in fcs]), 3),
    }
    p["conference_margin_scale"] = round(pstdev([resid[r["event_id"]] for r in est_conf]) / sd_est, 4)
    p["total_sd"] = round(pstdev(tresid), 3)
    p["train_slices"] = {
        "established": len(est),
        "thin_sample": len(thin),
        "fbs_vs_fcs": len(fcs),
        "established_conference": len(est_conf),
    }
    return p


def price_rows(
    rows: list[dict[str, Any]], fv: str, p: dict[str, Any], *, use_lines: bool
) -> list[dict[str, Any]]:
    """v5.1 arithmetic (models/cfb_structural_v51.price_features, the serving function) for each row."""
    from model_prediction.models.cfb_structural_v51 import V51Params, margin_pmf, price_features

    prm = V51Params.from_mapping(p)
    ks = np.arange(-prm.max_margin, prm.max_margin + 1)
    out = []
    for r in rows:
        f = r[fv]
        pr = price_features(
            prm,
            f,
            spread_home_line=r["line_spread_home"] if use_lines else None,
            total_line=r["line_total"] if use_lines else None,
        )
        pmf = margin_pmf(prm, pr.mu_margin, pr.margin_sd)
        actual = round(r["margin"])
        p_act = float(pmf[ks == actual].sum()) if abs(actual) <= prm.max_margin else 0.0
        out.append(
            {
                "p_home": pr.probabilities["home_win"],
                "mu": pr.mu_margin,
                "sd": pr.margin_sd,
                "tmu": pr.mu_total,
                "tsd": pr.total_sd,
                "disc_log_score": math.log(max(p_act, 1e-12)),
                "p_home_cover": pr.probabilities["home_cover"] if use_lines else None,
                "p_away_cover": pr.probabilities["away_cover"] if use_lines else None,
                "p_spread_push": pr.probabilities["spread_push"] if use_lines else None,
                "p_over": pr.probabilities["over"] if use_lines else None,
                "p_under": pr.probabilities["under"] if use_lines else None,
            }
        )
    return out


def gauss_ls(x: float, mu: float, sd: float) -> float:
    return -0.5 * math.log(2 * math.pi * sd * sd) - (x - mu) ** 2 / (2 * sd * sd)


def score(rows: list[dict[str, Any]], priced: list[dict[str, Any]]) -> dict[str, Any]:
    from model_prediction.calibration import calibration_metrics

    probs = [q["p_home"] for q in priced]
    outs = [int(r["margin"] > 0) for r in rows]
    m = calibration_metrics(probs, outs) if len(rows) >= 30 else {}
    n = len(rows)
    out = {
        "n": n,
        "brier": sum((p - y) ** 2 for p, y in zip(probs, outs, strict=True)) / n,
        "log_loss": m.get("log_loss"),
        "ece": m.get("expected_calibration_error"),
        "calibration_slope": m.get("calibration_slope"),
        "accuracy": sum(1 for p, y in zip(probs, outs, strict=True) if (p >= 0.5) == bool(y)) / n,
        "margin_mae": statistics.fmean(abs(r["margin"] - q["mu"]) for r, q in zip(rows, priced, strict=True)),
        "total_mae": statistics.fmean(abs(r["total"] - q["tmu"]) for r, q in zip(rows, priced, strict=True)),
        "margin_bias": statistics.fmean(r["margin"] - q["mu"] for r, q in zip(rows, priced, strict=True)),
        "total_bias": statistics.fmean(r["total"] - q["tmu"] for r, q in zip(rows, priced, strict=True)),
        "margin_gauss_log_score": statistics.fmean(
            gauss_ls(r["margin"], q["mu"], q["sd"]) for r, q in zip(rows, priced, strict=True)
        ),
        "total_gauss_log_score": statistics.fmean(
            gauss_ls(r["total"], q["tmu"], q["tsd"]) for r, q in zip(rows, priced, strict=True)
        ),
        "mean_margin_sd": statistics.fmean(q["sd"] for q in priced),
        "margin_resid_sd": pstdev([r["margin"] - q["mu"] for r, q in zip(rows, priced, strict=True)]),
    }
    if priced and priced[0].get("disc_log_score") is not None:
        out["margin_discrete_log_score"] = statistics.fmean(q["disc_log_score"] for q in priced)
    return out


def market_scores(rows: list[dict[str, Any]], priced: list[dict[str, Any]]) -> dict[str, Any]:
    """Cover metrics at the archived closing lines and the market-relative correlation."""
    sp = [(r, q) for r, q in zip(rows, priced, strict=True) if r["line_spread_home"] is not None]
    to = [(r, q) for r, q in zip(rows, priced, strict=True) if r["line_total"] is not None]
    out: dict[str, Any] = {"n_spread_lines": len(sp), "n_total_lines": len(to)}
    cover = [
        (
            q["p_home_cover"] / max(1e-9, q["p_home_cover"] + q["p_away_cover"]),
            int(r["margin"] + r["line_spread_home"] > 0),
        )
        for r, q in sp
        if r["margin"] + r["line_spread_home"] != 0
    ]
    if cover:
        out["spread_cover_brier"] = statistics.fmean((p - y) ** 2 for p, y in cover)
        out["spread_cover_log_loss"] = statistics.fmean(
            -math.log(min(1 - 1e-12, max(1e-12, p if y else 1 - p))) for p, y in cover
        )
        out["spread_cover_n"] = len(cover)
    whole = [(r, q) for r, q in sp if float(r["line_spread_home"]).is_integer()]
    if whole:
        out["whole_line_push"] = {
            "n": len(whole),
            "predicted": statistics.fmean(q["p_spread_push"] for _, q in whole),
            "actual": statistics.fmean(
                1.0 if r["margin"] + r["line_spread_home"] == 0 else 0.0 for r, _ in whole
            ),
        }
    ov = [
        (q["p_over"] / max(1e-9, q["p_over"] + q["p_under"]), int(r["total"] > r["line_total"]))
        for r, q in to
        if r["total"] != r["line_total"]
    ]
    if ov:
        out["total_over_brier"] = statistics.fmean((p - y) ** 2 for p, y in ov)
        out["total_over_n"] = len(ov)
    xs = [q["mu"] + r["line_spread_home"] for r, q in sp]  # model margin minus line-implied margin
    ys = [r["margin"] + r["line_spread_home"] for r, q in sp]
    out["spread_corr_model_minus_line_vs_result_minus_line"] = pearson(xs, ys)
    out["spread_corr_ci95"] = clustered_corr_ci([r["date"] for r, _ in sp], xs, ys)
    out["spread_mae_model"] = statistics.fmean(abs(r["margin"] - q["mu"]) for r, q in sp) if sp else None
    out["spread_mae_line"] = (
        statistics.fmean(abs(r["margin"] + r["line_spread_home"]) for r, _ in sp) if sp else None
    )
    txs = [q["tmu"] - r["line_total"] for r, q in to]
    tys = [r["total"] - r["line_total"] for r, q in to]
    out["total_corr_model_minus_line_vs_result_minus_line"] = pearson(txs, tys)
    out["total_corr_ci95"] = clustered_corr_ci([r["date"] for r, _ in to], txs, tys)
    for thr in (3.0, 7.0):
        w = lo = 0
        for x, y in zip(xs, ys, strict=True):
            if abs(x) < thr or y == 0:
                continue
            w, lo = (w + 1, lo) if (x > 0) == (y > 0) else (w, lo + 1)
        out[f"ats_disagree_ge_{int(thr)}"] = {
            "wins": w,
            "losses": lo,
            "win_pct": round(100 * w / (w + lo), 2) if w + lo else None,
        }
    return out


def clustered_corr_ci(
    dates: list[str], xs: list[float], ys: list[float], seed: int = 17, n: int = 1000
) -> list:
    by: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(dates):
        by[d].append(i)
    keys = sorted(by)
    if len(keys) < 10:
        return []
    rng = random.Random(seed)
    xa, ya = np.array(xs), np.array(ys)
    samples = []
    for _ in range(n):
        idx = [i for d in (rng.choice(keys) for _ in keys) for i in by[d]]
        c = pearson(list(xa[idx]), list(ya[idx]))
        if not math.isnan(c):
            samples.append(c)
    samples.sort()
    return [
        round(samples[int(0.025 * (len(samples) - 1))], 4),
        round(samples[int(0.975 * (len(samples) - 1))], 4),
    ]


def clustered_delta(dates: list[str], deltas: list[float], seed: int = 11, n: int = 2000) -> dict[str, Any]:
    by: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(dates, deltas, strict=True):
        by[d].append(v)
    keys = sorted(by)
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        vals = [v for d in (rng.choice(keys) for _ in keys) for v in by[d]]
        samples.append(sum(vals) / len(vals))
    samples.sort()
    return {
        "point_estimate": round(statistics.fmean(deltas), 6),
        "ci_95_low": round(samples[int(0.025 * (n - 1))], 6),
        "ci_95_high": round(samples[int(0.975 * (n - 1))], 6),
        "dates": len(keys),
    }


def fit_key_multipliers(
    rows: list[dict[str, Any]], fv: str, p: dict[str, Any], keys: list[int]
) -> dict[str, float]:
    """Fixed-point fit on TRAIN: multiplier m_k on |margin| = k so the expected count of |k| equals the observed."""
    from model_prediction.models.cfb_structural_v51 import V51Params, margin_pmf, price_features

    mult = {0: 0.0, **dict.fromkeys(keys, 1.0)}
    observed = Counter(int(abs(round(r["margin"]))) for r in rows)
    base = V51Params.from_mapping(
        {**p, "margin_pmf": {"max_margin": MAX_MARGIN, "key_multipliers": {"0": 0.0}}}
    )
    mus = [price_features(base, r[fv]) for r in rows]
    ks = np.arange(-MAX_MARGIN, MAX_MARGIN + 1)
    absk = np.abs(ks)
    for _ in range(40):
        prm = V51Params.from_mapping(
            {
                **p,
                "margin_pmf": {
                    "max_margin": MAX_MARGIN,
                    "key_multipliers": {str(k): v for k, v in mult.items()},
                },
            }
        )
        expected = np.zeros(MAX_MARGIN + 1)
        for pr in mus:
            pmf = margin_pmf(prm, pr.mu_margin, pr.margin_sd)
            np.add.at(expected, absk, pmf)
        for k in keys:
            if expected[k] > 0:
                mult[k] *= observed.get(k, 0) / expected[k]
    return {k: round(v, 5) for k, v in mult.items()}


def with_keys(p: dict[str, Any], mult: dict[int, float]) -> dict[str, Any]:
    return {
        **p,
        "margin_pmf": {"max_margin": MAX_MARGIN, "key_multipliers": {str(k): v for k, v in mult.items()}},
    }


# =========================================================================== stage: prereg
PREREG_TEXT = {
    "written_before_fit": True,
    "split": "validation.chronological_split, 60/20/20 on complete kickoff dates, FBS-involved completed games",
    "feature_candidates": {
        "live": "v5.1 conference table + lagged scoring proxy computed in the feature path",
        "drop": "v5.1 conference table + EPA channel off (PPP fallback)",
        "ablation_only_not_selectable": ["v5 (served features, refit)", "conf (conference fix, stored EPA)"],
    },
    "layer_candidates": {
        "established_min_sample": list(SAMPLE_GRID),
        "neutral_intercept": [False, True],
        "margin_mean_fit_cohort": ["all", "fbs_vs_fbs"],
        "conference_scale": "always fitted (established matchups)",
    },
    "stage_1_selection": (
        "lowest validation moneyline Brier under the served v5.1 arithmetic with no key multipliers "
        "(tie-free Normal point masses); tiebreak higher validation discrete margin log score"
    ),
    "stage_2_key_numbers": {
        "candidates": KEY_SETS,
        "fit": "fixed-point multipliers on TRAIN so expected |margin| counts match observed",
        "validated_if": (
            "validation discrete margin log score improves over 'none' with a date-clustered 95% CI above 0 AND "
            "validation spread-cover log loss at archived closing lines is not worse than 'none' (point estimate); "
            "among validated sets the highest validation discrete log score wins; otherwise 'none'"
        ),
    },
    "holdout": "read once by the holdout stage (marker file); served v5 vs selected v5.1; never used to choose",
    "baseline": "served cfb-structural-v5: v5 features, v5 artifact parameters, 200k NB engine",
    "note": (
        "The 2024-11..2026-11 window was already scored by v5's 2026-11-12 fit and the 2026-11-21 slope recheck; "
        "it is not a pristine holdout for NCAAF research in general, only untouched by this fit's choices."
    ),
}


def cmd_prereg(_: argparse.Namespace) -> None:
    if PREREG.exists():
        raise SystemExit(f"{PREREG} exists; the preregistration is written once")
    PREREG.write_text(
        json.dumps({**PREREG_TEXT, "written_utc": datetime.now(UTC).isoformat()}, indent=2), encoding="utf-8"
    )
    print("wrote", PREREG)


# =========================================================================== stage: fit
def cmd_fit(_: argparse.Namespace) -> None:
    if not PREREG.exists():
        raise SystemExit("run prereg first")
    rows = load_rows()
    train, val, hold, meta = split(rows)
    print("split", json.dumps(meta), len(train), len(val), len(hold))
    tournament: dict[str, Any] = {}
    params_by: dict[str, dict[str, Any]] = {}
    for fv in FEATURE_VARIANTS:
        for k in SAMPLE_GRID:
            for neutral in (False, True):
                for cohort in ("all", "fbs_vs_fbs"):
                    name = f"{fv}_k{k}_{'neu' if neutral else 'noneu'}_{cohort}"
                    p = fit_params(train, fv, k=k, neutral=neutral, mean_cohort=cohort)
                    s = score(val, price_rows(val, fv, p, use_lines=False))
                    tournament[name] = {"feature_variant": fv, "validation": s}
                    params_by[name] = p
    sel_pool = {n: v for n, v in tournament.items() if v["feature_variant"] in SELECTABLE_FEATURES}
    best = min(
        sel_pool,
        key=lambda n: (
            sel_pool[n]["validation"]["brier"],
            -sel_pool[n]["validation"]["margin_discrete_log_score"],
        ),
    )
    fv = tournament[best]["feature_variant"]
    base = params_by[best]
    print("stage 1 selected", best, tournament[best]["validation"]["brier"])
    # best ablation arm per feature variant (same layer settings as the selected one), for the report
    tag = best.split("_", 1)[1]
    ablation = {
        v: {"name": f"{v}_{tag}", "validation": tournament[f"{v}_{tag}"]["validation"]}
        for v in FEATURE_VARIANTS
    }

    # stage 2: key-number multipliers
    keys_report: dict[str, Any] = {}
    none_val = price_rows(val, fv, base, use_lines=True)
    none_market = market_scores(val, none_val)
    for key_name, keys in KEY_SETS.items():
        mult = {0: 0.0} if not keys else fit_key_multipliers(train, fv, base, keys)
        p = with_keys(base, mult)
        priced = price_rows(val, fv, p, use_lines=True)
        d = [a["disc_log_score"] - b["disc_log_score"] for a, b in zip(priced, none_val, strict=True)]
        mk = market_scores(val, priced)
        keys_report[key_name] = {
            "multipliers": {str(k): v for k, v in mult.items()},
            "validation_discrete_log_score": statistics.fmean(q["disc_log_score"] for q in priced),
            "delta_vs_none": clustered_delta([r["date"] for r in val], d),
            "spread_cover_log_loss": mk.get("spread_cover_log_loss"),
            "whole_line_push": mk.get("whole_line_push"),
            "moneyline_brier": score(val, priced)["brier"],
        }
    validated = [
        n
        for n, v in keys_report.items()
        if n != "none"
        and v["delta_vs_none"]["ci_95_low"] > 0
        and v["spread_cover_log_loss"] <= none_market["spread_cover_log_loss"]
    ]
    key_choice = (
        max(validated, key=lambda n: keys_report[n]["validation_discrete_log_score"]) if validated else "none"
    )
    final = with_keys(base, {int(k): v for k, v in keys_report[key_choice]["multipliers"].items()})
    print("stage 2 key set", key_choice, "validated:", validated)
    sel = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "split": meta,
        "counts": {"train": len(train), "validation": len(val), "holdout": len(hold)},
        "stage_1_selected": best,
        "feature_variant": fv,
        "ablation_same_layers": ablation,
        "tournament": tournament,
        "key_numbers": {"report": keys_report, "validated": validated, "selected": key_choice},
        "params": final,
        "cache_meta": json.loads((WORK / "cache_meta.json").read_text(encoding="utf-8")),
    }
    SELECTION.write_text(json.dumps(sel, indent=2, default=str), encoding="utf-8")
    print("wrote", SELECTION)


# =========================================================================== stage: holdout
def v5_served(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Served v5 on its own features: artifact parameters and the 200k-draw NB engine (one engine, advancing RNG)."""
    from model_prediction.models.cfb_distribution import CFBJointDistributionEngine
    from model_prediction.models.cfb_structural_v5 import cfb_structural_v5_model

    model = cfb_structural_v5_model()
    rng = model.distribution_engine.rng
    out = []
    for r in rows:
        f = r["v5"]
        line = (
            r["line_spread_home"]
            if r["line_spread_home"] is not None
            else round(-f["projected_margin_home"] * 2) / 2
        )
        tline = r["line_total"] if r["line_total"] is not None else f["projected_total"]
        margin, total = model.calibrate_means(f["projected_margin_home"], f["projected_total"])
        mu_h, mu_a = max(3.0, (total + margin) / 2), max(3.0, (total - margin) / 2)
        sd = model.compute_margin_sd(
            spread_home_line=line,
            is_fbs_vs_fcs=f["is_fbs_vs_fcs"],
            sample_games=f["sample_games"],
            is_conference_matchup=f["is_conference_matchup"],
        )
        tsd = model.compute_total_sd(projected_total=mu_h + mu_a, total_line=tline)
        eng = CFBJointDistributionEngine(
            distribution_type=model.distribution_type,
            margin_sd=sd,
            total_sd=tsd,
            n_simulations=model.n_simulations,
        )
        eng.rng = rng
        j = eng.compute_market_probabilities(
            mu_away=mu_a, mu_home=mu_h, spread_home_line=line, total_line=tline
        )
        out.append(
            {
                "p_home": j.p_home_win,
                "mu": mu_h - mu_a,
                "sd": sd,
                "tmu": mu_h + mu_a,
                "tsd": tsd,
                "disc_log_score": None,
                "p_home_cover": j.p_home_cover,
                "p_away_cover": j.p_away_cover,
                "p_spread_push": j.p_push_spread,
                "p_over": j.p_over,
                "p_under": j.p_under,
            }
        )
    return out


def v5_spread_side(r: dict[str, Any], q: dict[str, Any], ask: float) -> str:
    """v5's served spread side rule (college_football.py key-window/conference rule) at equal asks on both sides."""
    edge_a, edge_h = q["p_away_cover"] - ask, q["p_home_cover"] - ask
    is_h_fav = r["line_spread_home"] < 0
    mag = abs(r["line_spread_home"])
    if 3.5 < mag <= 9.5:
        pick_away = (
            not (edge_h > 0.08 and edge_h > edge_a) if is_h_fav else (edge_a > 0.08 and edge_a > edge_h)
        )
    elif not r["v5"]["is_conference_matchup"]:
        pick_away = (
            (edge_a > 0.08 and edge_a > edge_h) if is_h_fav else not (edge_h > 0.08 and edge_h > edge_a)
        )
    else:
        pick_away = edge_a >= edge_h
    return "away" if pick_away else "home"


def side_record(rows, priced, chooser) -> dict[str, Any]:
    w = lo = pu = 0
    for r, q in zip(rows, priced, strict=True):
        if r["line_spread_home"] is None:
            continue
        side = chooser(r, q)
        res = r["margin"] + r["line_spread_home"]
        if res == 0:
            pu += 1
        elif (res > 0) == (side == "home"):
            w += 1
        else:
            lo += 1
    n = w + lo
    return {
        "wins": w,
        "losses": lo,
        "pushes": pu,
        "win_pct": round(100 * w / n, 2) if n else None,
        "roi_pct_at_52_4": round(100 * (w * (1 - BREAK_EVEN) / BREAK_EVEN - lo) / n, 2) if n else None,
    }


def cmd_holdout(args: argparse.Namespace) -> None:
    from model_prediction.features.cfb_conference import table_content_sha256
    from model_prediction.production_registry import compute_artifact_hash
    from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta

    dry = bool(getattr(args, "dry_run_on_validation", False))
    if HOLDOUT_MARKER.exists() and not dry:
        raise SystemExit(f"locked holdout already read ({HOLDOUT_MARKER}); refusing to read it again")
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))
    rows = load_rows()
    train, val, hold, meta = split(rows)
    if meta != sel["split"]:
        raise SystemExit("split differs from the fit stage's; refusing")
    # Incumbent-reproduction gate (train rows only): the cache's v5 arm must reproduce the 2026-11-21 slope
    # recheck's train refit (rows <= 2022-11-11: slope 1.3948, intercept -0.4026, n 5704). The postseason week
    # fix (31 rows) is the only intended difference.
    gate_rows = [r for r in rows if r["date"] <= "2022-11-11"]
    g_int, g_slope = ols(
        [[r["v5"]["projected_margin_home"]] for r in gate_rows], [r["margin"] for r in gate_rows]
    )
    gate = {
        "n": len(gate_rows),
        "slope": round(g_slope, 4),
        "intercept": round(g_int, 4),
        "reference": {"n": 5704, "slope": 1.3948, "intercept": -0.4026},
    }
    gate["pass"] = gate["n"] == 5704 and abs(g_slope - 1.3948) < 0.01 and abs(g_int + 0.4026) < 0.1
    print("incumbent reproduction gate:", gate)
    if not gate["pass"]:
        raise SystemExit("cached v5 arm does not reproduce the slope recheck; refusing to read the holdout")
    if dry:
        # Exercise every line of this stage on the VALIDATION cohort so a crash cannot strand a read holdout.
        hold = val
    else:
        HOLDOUT_MARKER.write_text(
            json.dumps(
                {"read_utc": datetime.now(UTC).isoformat(), "selection_sha256": _sha(SELECTION)}, indent=2
            ),
            encoding="utf-8",
        )
    fv, p = sel["feature_variant"], sel["params"]
    v5p = v5_served(hold)
    v51p = price_rows(hold, fv, p, use_lines=True)
    # Per-game predictions, so every later table or CI comes from this file and never from a second read.
    (WORK / ("dry_predictions.jsonl" if dry else "holdout_predictions.jsonl")).write_text(
        "".join(
            json.dumps({"row": r, "v5": a, "v5.1": b}, sort_keys=True) + "\n"
            for r, a, b in zip(hold, v5p, v51p, strict=True)
        ),
        encoding="utf-8",
    )
    s5, s51 = score(hold, v5p), score(hold, v51p)
    m5, m51 = market_scores(hold, v5p), market_scores(hold, v51p)
    dates = [r["date"] for r in hold]
    ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0)) for r in hold]

    def ll(pp: float, y: int) -> float:
        return -math.log(min(1 - 1e-12, max(1e-12, pp if y else 1 - pp)))

    deltas = {
        "brier_v51_minus_v5": _cluster_bootstrap_brier_delta(
            [q["p_home"] for q in v5p], [q["p_home"] for q in v51p], ducks, seed=11
        ),
        "log_loss_v51_minus_v5": clustered_delta(
            dates,
            [
                ll(b["p_home"], int(r["margin"] > 0)) - ll(a["p_home"], int(r["margin"] > 0))
                for r, a, b in zip(hold, v5p, v51p, strict=True)
            ],
        ),
        "margin_mae_v51_minus_v5": clustered_delta(
            dates,
            [
                abs(r["margin"] - b["mu"]) - abs(r["margin"] - a["mu"])
                for r, a, b in zip(hold, v5p, v51p, strict=True)
            ],
        ),
        "total_mae_v51_minus_v5": clustered_delta(
            dates,
            [
                abs(r["total"] - b["tmu"]) - abs(r["total"] - a["tmu"])
                for r, a, b in zip(hold, v5p, v51p, strict=True)
            ],
        ),
        "margin_gauss_log_score_v51_minus_v5": clustered_delta(
            dates,
            [
                gauss_ls(r["margin"], b["mu"], b["sd"]) - gauss_ls(r["margin"], a["mu"], a["sd"])
                for r, a, b in zip(hold, v5p, v51p, strict=True)
            ],
        ),
        "accuracy_v51_minus_v5": clustered_delta(
            dates,
            [
                float((b["p_home"] >= 0.5) == (r["margin"] > 0))
                - float((a["p_home"] >= 0.5) == (r["margin"] > 0))
                for r, a, b in zip(hold, v5p, v51p, strict=True)
            ],
        ),
    }
    lined = [
        (r, a, b)
        for r, a, b in zip(hold, v5p, v51p, strict=True)
        if r["line_spread_home"] is not None and r["margin"] + r["line_spread_home"] != 0
    ]

    def cov(q):
        return q["p_home_cover"] / max(1e-9, q["p_home_cover"] + q["p_away_cover"])

    deltas["spread_cover_brier_v51_minus_v5"] = clustered_delta(
        [r["date"] for r, _, _ in lined],
        [
            (cov(b) - int(r["margin"] + r["line_spread_home"] > 0)) ** 2
            - (cov(a) - int(r["margin"] + r["line_spread_home"] > 0)) ** 2
            for r, a, b in lined
        ],
    )
    sides = {
        "v5_rule_v5_probs": side_record(hold, v5p, lambda r, q: v5_spread_side(r, q, BREAK_EVEN)),
        "max_edge_v5_probs": side_record(
            hold, v5p, lambda r, q: "home" if q["p_home_cover"] >= q["p_away_cover"] else "away"
        ),
        "max_edge_v51_probs": side_record(
            hold, v51p, lambda r, q: "home" if q["p_home_cover"] >= q["p_away_cover"] else "away"
        ),
    }
    by_slice: dict[str, Any] = {}
    for label, pred in (
        ("fbs_vs_fbs", lambda r: not r["v5"]["is_fbs_vs_fcs"]),
        ("fbs_vs_fcs", lambda r: r["v5"]["is_fbs_vs_fcs"]),
        ("season_2026", lambda r: r["season"] == 2026),
        ("from_2026_10_01", lambda r: r["date"] >= "2026-10-01"),
        ("neutral", lambda r: r["neutral"]),
        (
            "conference_flag_changed",
            lambda r: r["v5"]["is_conference_matchup"] != r[fv]["is_conference_matchup"],
        ),
    ):
        idx = [i for i, r in enumerate(hold) if pred(r)]
        if len(idx) >= 30:
            sub = [hold[i] for i in idx]
            by_slice[label] = {
                "v5": score(sub, [v5p[i] for i in idx]),
                "v5.1": score(sub, [v51p[i] for i in idx]),
                "brier_delta": _cluster_bootstrap_brier_delta(
                    [v5p[i]["p_home"] for i in idx],
                    [v51p[i]["p_home"] for i in idx],
                    [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0)) for r in sub],
                    seed=11,
                ),
            }
    conf_change = Counter(
        (r["season"], r["v5"]["is_conference_matchup"], r[fv]["is_conference_matchup"]) for r in rows
    )
    report = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "split": meta,
        "counts": sel["counts"],
        "selected": {
            "stage_1": sel["stage_1_selected"],
            "feature_variant": fv,
            "key_set": sel["key_numbers"]["selected"],
        },
        "holdout": {
            "v5": s5,
            "v5.1": s51,
            "market_v5": m5,
            "market_v5.1": m51,
            "deltas": deltas,
            "spread_side_rules_at_52_4": sides,
            "by_slice": by_slice,
        },
        "incumbent_reproduction_gate": gate,
        "conference_flag_v5_vs_v51_by_season": {
            f"{s}|v5={a}|v51={b}": n for (s, a, b), n in sorted(conf_change.items())
        },
    }
    report_path = WORK / "dry_report.json" if dry else REPORT_JSON
    artifact_path = WORK / "dry_artifact.json" if dry else ARTIFACT
    report["cohort"] = "validation (dry run)" if dry else "locked holdout (read once)"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    artifact = {
        "method": "cfb_structural_v51_fixed_v5_integer_margin_pmf",
        "model_version": "cfb-structural-v5.1",
        "schema_version": "1",
        "sport": "ncaaf",
        "league": "NCAAF",
        "positive_class": "home_win",
        "base_model": "cfb-structural-v5",
        "features": {
            "extractor": "features/cfb_features_v51.CFBFeatureExtractorV51",
            "epa_mode": "live_proxy" if fv == "live" else "dropped",
            "home_advantage_points": 2.5,
        },
        "conference_source": {
            "path": CONF_TABLE_REL,
            "sha256": table_content_sha256(json.loads((REPO / CONF_TABLE_REL).read_text(encoding="utf-8"))),
            "hash": "content sha256 of the parsed table (features/cfb_conference.table_content_sha256)",
        },
        "parameters": {k: v for k, v in p.items() if k != "train_slices"},
        "serving": {
            "tiers": ["flat", "research"],
            "main": False,
            "auto_buyer": False,
            "quotes": "captured executable Polymarket book observed at or before the decision only",
            "side_selection": "max_edge; emitted only when round(edge, 4) > 0",
            "max_history_staleness_days": 14,
        },
        "training": {
            "harness": "scripts/cfb_v51_research.py",
            "split": meta,
            "n_train": len(train),
            "n_validation": len(val),
            "n_holdout": len(hold),
            "fitted_on": "train only; variant and key set selected on validation; holdout scored once",
            "selected_variant": sel["stage_1_selected"],
            "key_set": sel["key_numbers"]["selected"],
            "train_slices": p.get("train_slices"),
            "history_snapshot": sel["cache_meta"]["files"],
            "preregistration": "outputs/ncaaf/cfb_v51_prereg.json",
            "generated_at_utc": datetime.now(UTC).isoformat(),
        },
        "evaluation": {
            "holdout_v5": s5,
            "holdout_v51": s51,
            "holdout_deltas_v51_minus_v5": deltas,
            "market_relative": {"v5": m5, "v5.1": m51},
        },
        "qualification": {
            "status": "paper_challenger",
            "qualified": False,
            "promotion_approved": False,
            "note": "Flat/research rows only; never Main, never the auto-buyer. v5 stays champion.",
        },
    }
    artifact = json.loads(json.dumps(artifact, default=float))
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    from model_prediction.models.cfb_structural_v51 import (
        CFBStructuralV51Model,
        load_cfb_structural_v51_artifact,
    )

    CFBStructuralV51Model.from_artifact(load_cfb_structural_v51_artifact(artifact_path))
    print("wrote", report_path, artifact_path, artifact["artifact_hash"])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cache")
    c.add_argument("--history-root", required=True)
    c.add_argument("--workers", type=int, default=10)
    sub.add_parser("prereg")
    sub.add_parser("fit")
    h = sub.add_parser("holdout")
    h.add_argument("--dry-run-on-validation", action="store_true")
    args = ap.parse_args()
    {"cache": cmd_cache, "prereg": cmd_prereg, "fit": cmd_fit, "holdout": cmd_holdout}[args.cmd](args)


if __name__ == "__main__":
    main()
