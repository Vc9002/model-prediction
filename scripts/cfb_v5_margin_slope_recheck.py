"""Recheck cfb-structural-v5's margin calibration (intercept -0.4143, slope 1.3189) and margin SD table.

Question (operator, 2026-11-21): after the 2026-11-21 team-identity / FCS-class / efficiency-sign fixes, a
427-game single-season check regressed actual margin on v5's RAW projected home margin and got slope 1.53.
Should the served slope (and the SD table that turns the margin into probabilities) be refit?

Stage 1 `replay`: point-in-time replay of the v5 feature extractor as currently served (current code) over
every completed FBS-involved game in the live history (2016 -> today). Each game sees only games that
kicked off strictly before it (same bisect rule as scripts/replay_cfb_v4_all_settled.py). It records the
RAW projected home margin (CFBMatchupFeatures.projected_margin_home, before margin_calibration) plus the
slice inputs of the SD table. The live history files are first copied (complete lines only) into the
cache dir so a daily run appending mid-replay cannot move the inputs; nothing under data/ is written.

Stage 2 `analyze`: actual-on-raw OLS by season / week band / class; a 60/20/20 complete-date chronological
split at the artifact's own recorded boundaries (validation.chronological_split, explicit dates); refit of
intercept, slope and the slice SD table on train only; validation comparison; one locked-holdout scoring of
artifact vs refit (margin MAE, moneyline Brier / log loss through the served negative-binomial engine,
calibration by favourite size); and a market check against ESPN archived closing spreads
(data/research/ncaaf_v6/espn_lines.jsonl, the v6 pinned median rule).

Prices are percentages only. Research only: no config, artifact, ledger or db is touched.

Usage (repo root):
  PYTHONPATH=src OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/cfb_v5_margin_slope_recheck.py replay --workers 4
  PYTHONPATH=src .venv/Scripts/python scripts/cfb_v5_margin_slope_recheck.py analyze
Writes outputs/ncaaf/cfb_v5_margin_slope_recheck.json and .md; the replay row cache lives outside the repo
(default: <system temp>/cfb_v5_margin_slope_recheck/rows.jsonl).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import multiprocessing as mp
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

LIVE_PROCESSED = REPO / "data" / "processed" / "ncaaf" / "games.jsonl"
LIVE_HISTORICAL = REPO / "data" / "historical" / "ncaaf_games_all.jsonl"
ESPN_LINES = REPO / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v5_margin_slope_recheck.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v5_margin_slope_recheck.md"
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "cfb_v5_margin_slope_recheck"

# The artifact's own recorded split (config/models/cfb-structural-v5.json training.split): inclusive ends.
TRAIN_END = "2022-11-11"
VALIDATION_END = "2024-11-14"
BREAK_EVEN = 0.524  # flat spread price, as a probability
WIN_PAYOUT = (1.0 - BREAK_EVEN) / BREAK_EVEN
FAV_BUCKETS = ((0, 3, "0-3"), (3, 7, "3-7"), (7, 14, "7-14"), (14, 21, "14-21"), (21, 999, "21+"))
N_SIM = 200_000


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.strip()


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _replay_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "replay_cfb", REPO / "scripts" / "replay_cfb_v4_all_settled.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# =========================================================================== stage 1: replay
def _snapshot(cache: Path) -> dict[str, Any]:
    """Copy the live history (complete lines) so every worker reads one frozen input."""
    meta = {}
    for src, rel in (
        (LIVE_PROCESSED, ("processed", "ncaaf", "games.jsonl")),
        (LIVE_HISTORICAL, ("historical", "ncaaf_games_all.jsonl")),
    ):
        data = src.read_bytes()
        data = data[: data.rfind(b"\n") + 1]
        dst = cache.joinpath("history", *rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        meta[str(src.relative_to(REPO)).replace("\\", "/")] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "rows": data.count(b"\n"),
        }
    return meta


def _fbs_involved(g: Any) -> bool:
    # Legacy rows (no feed groups) are the FBS historical file; feed 81 alone is FCS-vs-FCS, never priced.
    return g.feed_groups is None or 80 in g.feed_groups


def _worker(args: tuple[int, int, str]) -> int:
    k, n_workers, cache_s = args
    cache = Path(cache_s)
    from model_prediction.models.cfb_structural_v5 import cfb_structural_v5_model
    from model_prediction.models.college_football import _load_cfb_history

    replay = _replay_module()
    facts = replay.load_schedule_facts()
    model = cfb_structural_v5_model()
    history = sorted(_load_cfb_history(cache / "history"), key=lambda g: (g.start, str(g.event_id)))
    starts = [g.start for g in history]
    season_first: dict[int, datetime] = {}
    for g in history:
        s = replay.season_of(g.start)
        if s not in season_first or g.start < season_first[s]:
            season_first[s] = g.start
    part = cache / f"rows.part{k}.jsonl"
    done: set[str] = set()
    if part.exists():
        for line in part.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["event_id"])
    todo = [g for g in history if _fbs_involved(g) and g.home_score is not None and g.away_score is not None]
    todo = todo[k::n_workers]
    t0 = time.time()
    with part.open("a", encoding="utf-8") as out:
        for i, g in enumerate(todo):
            eid = str(g.event_id)
            if eid in done:
                continue
            past = history[: bisect.bisect_left(starts, g.start)]
            fact = facts.get(eid, {})
            week = fact.get("week")
            if week is None:
                week = replay.derive_week(g.start, season_first)
            season = replay.season_of(g.start)
            neutral = bool(fact.get("is_neutral_site", False))
            f = model.extractor.extract_features(
                history=past,
                away_team=g.away_team,
                home_team=g.home_team,
                event_id=eid,
                game_start_utc=g.event_start_utc,
                season_year=season,
                week=int(week),
                is_neutral_site=neutral,
                away_team_id=g.away_team_id,
                home_team_id=g.home_team_id,
            )
            row = {
                "event_id": eid,
                "start_utc": g.start.isoformat(),
                "date": g.start.date().isoformat(),
                "season": season,
                "week": int(week),
                "neutral": neutral,
                "home": g.home_team,
                "away": g.away_team,
                "feed_groups": list(g.feed_groups) if g.feed_groups else None,
                "raw_margin": float(f.projected_margin_home),
                "raw_total": float(f.projected_total),
                "sample_games": int(f.sample_games),
                "is_fcs": bool(f.is_fbs_vs_fcs),
                "is_conf": bool(f.is_conference_matchup),
                "uncertainty": float(f.uncertainty),
                "margin": float(g.home_score) - float(g.away_score),
                "total": float(g.home_score) + float(g.away_score),
                "past_n": len(past),
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            out.flush()
            if (i + 1) % 250 == 0:
                print(f"  worker {k}: {i + 1}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    return len(todo)


def cmd_replay(args: argparse.Namespace) -> int:
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    meta_path = cache / "snapshot_meta.json"
    if args.fresh or not meta_path.exists():
        for p in cache.glob("rows.part*.jsonl"):
            p.unlink()
        meta = {
            "snapshot_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": _snapshot(cache),
            "head": _git("rev-parse", "HEAD"),
            "workers": args.workers,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    n_workers = int(meta["workers"])
    t0 = time.time()
    with mp.Pool(n_workers) as pool:
        counts = pool.map(_worker, [(k, n_workers, str(cache)) for k in range(n_workers)])
    rows = []
    for k in range(n_workers):
        for line in (cache / f"rows.part{k}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    (cache / "rows.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    print(
        f"replayed {len(rows)} games (expected {sum(counts)}) in {time.time() - t0:.0f}s -> {cache / 'rows.jsonl'}"
    )
    return 0


# =========================================================================== stage 2: analysis helpers
def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def ols(xs: list[float], ys: list[float], ws: list[float] | None = None) -> dict[str, float]:
    ws = ws or [1.0] * len(xs)
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs, strict=True)) / sw
    my = sum(w * y for w, y in zip(ws, ys, strict=True)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs, strict=True))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys, strict=True))
    slope = sxy / sxx if sxx else float("nan")
    icpt = my - slope * mx
    n = len(xs)
    resid = [y - icpt - slope * x for x, y in zip(xs, ys, strict=True)]
    s2 = sum(r * r for r in resid) / max(1, n - 2)
    se = math.sqrt(s2 / sxx) if sxx and ws is not None else float("nan")
    return {"n": n, "intercept": icpt, "slope": slope, "slope_se": se, "resid_sd": math.sqrt(s2)}


def ols_block(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) < 30:
        return {"n": len(rows)} if rows else None
    fit = ols([r["raw_margin"] for r in rows], [r["margin"] for r in rows])
    return {k: round(v, 4) for k, v in fit.items()} | {"n": len(rows)}


def pearson(xs: list[float], ys: list[float]) -> float:
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / math.sqrt(sxx * syy)


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def p_home_normal(mu: float, sd: float) -> float:
    """Gaussian stand-in for the served engine: integer margins, OT ties split 52% home."""
    lo, hi = ncdf((-0.5 - mu) / sd), ncdf((0.5 - mu) / sd)
    return (1.0 - hi) + 0.52 * (hi - lo)


def week_band(week: int) -> str:
    if week >= 15:
        return "postseason"
    return "weeks_0_4" if week <= 4 else "weeks_5_14"


def game_class(r: dict[str, Any]) -> str:
    return "fbs_vs_fcs" if r["is_fcs"] else "fbs_vs_fbs"


def load_espn_lines() -> dict[str, float]:
    """event_id -> line-implied home margin (= -home spread), v6's pinned selected median close."""
    out: dict[str, float] = {}
    for line in ESPN_LINES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        sp = (rec.get("selected") or {}).get("home_spread_line")
        if sp is not None:
            out[str(rec["event_id"])] = -float(sp)
    return out


# --------------------------------------------------------------------------- parameter sets
class Params:
    def __init__(
        self,
        name: str,
        intercept: float,
        slope: float,
        table: dict[str, float],
        conf_scale: float,
        min_sample: int = 6,
    ) -> None:
        self.name = name
        self.intercept = intercept
        self.slope = slope
        self.table = table
        self.conf_scale = conf_scale
        self.min_sample = min_sample

    def mean(self, r: dict[str, Any]) -> float:
        return self.intercept + self.slope * r["raw_margin"]

    def slice(self, r: dict[str, Any]) -> str:
        if r["is_fcs"]:
            return "fbs_vs_fcs"
        return "established" if r["sample_games"] >= self.min_sample else "thin_sample"

    def multiplier(self, r: dict[str, Any]) -> float:
        """Everything except the table entry: conference scale (established only) and blowout widening."""
        from model_prediction.models.cfb_structural_v4 import (
            CFB_BLOWOUT_MARGIN_SCALE,
            CFB_BLOWOUT_SPREAD_THRESHOLD,
        )

        m = self.conf_scale if (r["is_conf"] and self.slice(r) == "established") else 1.0
        a = abs(r["pricing_line"])
        if a > CFB_BLOWOUT_SPREAD_THRESHOLD:
            m *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * min(15.0, a - CFB_BLOWOUT_SPREAD_THRESHOLD)
        return m

    def sd(self, r: dict[str, Any]) -> float:
        return round(self.table[self.slice(r)] * self.multiplier(r), 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "margin_calibration": {"intercept": round(self.intercept, 4), "slope": round(self.slope, 4)},
            "margin_sd_table": {k: round(v, 3) for k, v in self.table.items()},
            "conference_margin_scale": round(self.conf_scale, 4),
        }


def fit_orig(rows: list[dict[str, Any]], name: str) -> Params:
    """The estimator that produced the artifact (cfb_v5_research.fit_params, k=6, conference scale), unchanged:
    unweighted OLS of actual margin on raw margin over every train row, then per-slice residual SD."""
    from cfb_v5_research import fit_params

    shim = [{**r, "proj_margin": r["raw_margin"], "proj_total": r["raw_total"]} for r in rows]
    fp = fit_params(shim, 6, conference_scale=True)
    mc = fp["margin_calibration"]
    return Params(
        name, mc["intercept"], mc["slope"], dict(fp["margin_sd_table"]), fp["conference_margin_scale"], 6
    )


def fit_moneyline(rows: list[dict[str, Any]], name: str, base: Params) -> Params:
    """Diagnostic variant: a, b and a common SD factor minimising moneyline log loss (normal approx), on train."""
    import numpy as np
    from scipy.stats import norm

    sc = [r for r in rows if r["margin"] != 0]
    raw = np.array([r["raw_margin"] for r in sc])
    sd0 = np.array([base.table[base.slice(r)] * base.multiplier(r) for r in sc])
    y = np.array([1.0 if r["margin"] > 0 else 0.0 for r in sc])
    best = None
    for b in np.arange(1.0, 2.001, 0.02):
        for k in np.arange(0.80, 1.301, 0.02):
            for a in np.arange(base.intercept - 1.5, base.intercept + 1.51, 0.25):
                mu, sd = a + b * raw, k * sd0
                lo, hi = norm.cdf((-0.5 - mu) / sd), norm.cdf((0.5 - mu) / sd)
                pr = np.clip((1 - hi) + 0.52 * (hi - lo), 1e-6, 1 - 1e-6)
                ll = -np.mean(y * np.log(pr) + (1 - y) * np.log(1 - pr))
                if best is None or ll < best[0]:
                    best = (float(ll), float(a), float(b), float(k))
    _, a, b, k = best
    return Params(name, a, b, {s: v * k for s, v in base.table.items()}, base.conf_scale, base.min_sample)


# --------------------------------------------------------------------------- scoring
def price_rows(rows: list[dict[str, Any]], p: Params, *, engine: bool) -> list[dict[str, Any]]:
    """Per-row mean, SD, moneyline probability (None on a tie), Gaussian margin log score, abs error."""
    import numpy as np

    eng = None
    if engine:
        from model_prediction.models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine

        eng = CFBJointDistributionEngine(
            distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL, n_simulations=N_SIM, random_seed=42
        )
    out = []
    for r in rows:
        mu, sd = p.mean(r), p.sd(r)
        pr = None
        if r["margin"] != 0:
            if eng is not None:
                # served arithmetic (CFBStructuralV5Model.forecast_game): calibrated means, 3-point floor per
                # side, NB scores. Common random numbers per event so arms differ only by their parameters.
                total = r["cal_total"]
                mu_h, mu_a = max(3.0, (total + mu) / 2.0), max(3.0, (total - mu) / 2.0)
                eng.margin_sd, eng.total_sd = sd, r["total_sd"]
                eng.rng = np.random.default_rng(int(r["event_id"]) % (2**32))
                pr = eng.compute_market_probabilities(
                    mu_home=mu_h, mu_away=mu_a, spread_home_line=r["pricing_line"], total_line=r["raw_total"]
                ).p_home_win
            else:
                pr = p_home_normal(mu, sd)
        out.append(
            {
                "date": r["date"],
                "mu": mu,
                "sd": sd,
                "p": pr,
                "y": int(r["margin"] > 0),
                "ae": abs(r["margin"] - mu),
                "ls": -0.5 * math.log(2 * math.pi * sd * sd) - (r["margin"] - mu) ** 2 / (2 * sd * sd),
            }
        )
    return out


def summarize(priced: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    from model_prediction.calibration import calibration_metrics

    sc = [q for q in priced if q["p"] is not None]
    probs, outs = [q["p"] for q in sc], [q["y"] for q in sc]
    m = calibration_metrics(probs, outs) if len(probs) >= 30 else {}
    return {
        "n": len(priced),
        "margin_mae": round(mean([q["ae"] for q in priced]), 4),
        "margin_bias_actual_minus_model": round(
            mean([r["margin"] - q["mu"] for r, q in zip(rows, priced, strict=True)]), 4
        ),
        "margin_log_score": round(mean([q["ls"] for q in priced]), 5),
        "margin_z_sd": round(
            statistics.pstdev([(r["margin"] - q["mu"]) / q["sd"] for r, q in zip(rows, priced, strict=True)]),
            4,
        ),
        "ml_n": len(sc),
        "ml_brier": round(mean([(p - y) ** 2 for p, y in zip(probs, outs, strict=True)]), 5),
        "ml_log_loss": round(
            mean(
                [
                    -math.log(min(1 - 1e-9, max(1e-9, p if y else 1 - p)))
                    for p, y in zip(probs, outs, strict=True)
                ]
            ),
            5,
        ),
        "ml_ece": m.get("expected_calibration_error"),
        "ml_calibration_slope": m.get("calibration_slope"),
    }


def paired_deltas(a: list[dict[str, Any]], b: list[dict[str, Any]], *, n: int = 2000) -> dict[str, Any]:
    """b minus a, date-clustered bootstrap (Brier, log loss: lower better; log score: higher better; MAE)."""
    import random

    by_date: dict[str, list[tuple[float, float, float, float, int]]] = defaultdict(list)
    for qa, qb in zip(a, b, strict=True):
        if qa["p"] is None:
            db = dl = 0.0
            w = 0
        else:
            y = qa["y"]
            db = (qb["p"] - y) ** 2 - (qa["p"] - y) ** 2
            la = -math.log(min(1 - 1e-9, max(1e-9, qa["p"] if y else 1 - qa["p"])))
            lb = -math.log(min(1 - 1e-9, max(1e-9, qb["p"] if y else 1 - qb["p"])))
            dl = lb - la
            w = 1
        by_date[qa["date"]].append((db, dl, qb["ls"] - qa["ls"], qb["ae"] - qa["ae"], w))
    dates = sorted(by_date)

    def stat(days: list[str]) -> tuple[float, float, float, float]:
        vals = [v for d in days for v in by_date[d]]
        nw = sum(v[4] for v in vals) or 1
        return (
            sum(v[0] for v in vals) / nw,
            sum(v[1] for v in vals) / nw,
            sum(v[2] for v in vals) / len(vals),
            sum(v[3] for v in vals) / len(vals),
        )

    point = stat(dates)
    rng = random.Random(11)
    samples = [stat([rng.choice(dates) for _ in dates]) for _ in range(n)]
    names = ("ml_brier", "ml_log_loss", "margin_log_score", "margin_mae")
    out = {}
    for i, name in enumerate(names):
        col = sorted(s[i] for s in samples)
        out[name] = {
            "delta": round(point[i], 6),
            "ci_95": [round(col[int(0.025 * (n - 1))], 6), round(col[int(0.975 * (n - 1))], 6)],
        }
    out["dates"] = len(dates)
    return out


def fav_buckets(rows: list[dict[str, Any]], priced: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """By served favourite size: mean projected vs actual favourite margin, model vs realised win %."""
    out = []
    for lo, hi, label in FAV_BUCKETS:
        idx = [i for i, q in enumerate(priced) if lo <= abs(q["mu"]) < hi]
        if not idx:
            continue
        sg = {i: (1.0 if priced[i]["mu"] >= 0 else -1.0) for i in idx}
        act = [sg[i] * rows[i]["margin"] for i in idx]
        sc = [i for i in idx if priced[i]["p"] is not None]
        out.append(
            {
                "bucket": label,
                "n": len(idx),
                "fav_projected_margin": round(mean([abs(priced[i]["mu"]) for i in idx]), 2),
                "fav_actual_margin": round(mean(act), 2),
                "fav_model_win_pct": round(
                    100.0 * mean([priced[i]["p"] if sg[i] > 0 else 1 - priced[i]["p"] for i in sc]), 1
                ),
                "fav_realised_win_pct": round(
                    100.0 * mean([1.0 if sg[i] * rows[i]["margin"] > 0 else 0.0 for i in sc]), 1
                ),
            }
        )
    return out


def market_block(rows: list[dict[str, Any]], p: Params) -> dict[str, Any]:
    lined = [r for r in rows if r.get("line_margin") is not None]
    if len(lined) < 30:
        return {"n": len(lined)}
    xs = [p.mean(r) - r["line_margin"] for r in lined]
    ys = [r["margin"] - r["line_margin"] for r in lined]
    out: dict[str, Any] = {
        "n": len(lined),
        "corr_model_minus_line_vs_actual_minus_line": round(pearson(xs, ys), 4),
        "ols_slope_actual_minus_line_on_model_minus_line": round(ols(xs, ys)["slope"], 4),
        "mean_abs_disagreement": round(mean([abs(x) for x in xs]), 3),
        "mae_model": round(mean([abs(r["margin"] - p.mean(r)) for r in lined]), 3),
        "mae_line": round(mean([abs(r["margin"] - r["line_margin"]) for r in lined]), 3),
    }
    for thr in (3.0, 7.0):
        w = lo = pu = 0
        for x, y in zip(xs, ys, strict=True):
            if abs(x) < thr:
                continue
            if y == 0:
                pu += 1
            elif (x > 0) == (y > 0):
                w += 1
            else:
                lo += 1
        n = w + lo
        out[f"ats_disagree_ge_{int(thr)}"] = {
            "wins": w,
            "losses": lo,
            "pushes": pu,
            "win_pct": round(100.0 * w / n, 2) if n else None,
            "roi_pct_at_52_4": round(100.0 * (w * WIN_PAYOUT - lo) / n, 2) if n else None,
        }
    return out


def clustered_corr_ci(
    rows: list[dict[str, Any]], p: Params, seed: int = 17, n_boot: int = 1000
) -> list[float] | None:
    import random

    lined = [r for r in rows if r.get("line_margin") is not None]
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(lined):
        by_date[r["date"]].append(i)
    keys = sorted(by_date)
    if len(keys) < 10:
        return None
    xs = [p.mean(r) - r["line_margin"] for r in lined]
    ys = [r["margin"] - r["line_margin"] for r in lined]
    rng = random.Random(seed)
    samples = []
    for _ in range(n_boot):
        idx = [i for d in (rng.choice(keys) for _ in keys) for i in by_date[d]]
        c = pearson([xs[i] for i in idx], [ys[i] for i in idx])
        if not math.isnan(c):
            samples.append(c)
    samples.sort()
    return [
        round(samples[int(0.025 * (len(samples) - 1))], 4),
        round(samples[int(0.975 * (len(samples) - 1))], 4),
    ]


def raw_vs_line_regression(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """actual = c0 + c1*raw + c2*line: does the raw projection carry information the closing line lacks?"""
    import numpy as np

    lined = [r for r in rows if r.get("line_margin") is not None]
    if len(lined) < 30:
        return None
    X = np.array([[1.0, r["raw_margin"], r["line_margin"]] for r in lined])
    y = np.array([r["margin"] for r in lined])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = float(resid @ resid) / (len(y) - 3)
    cov = s2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    return {
        "n": len(lined),
        "intercept": round(float(beta[0]), 3),
        "coef_raw": round(float(beta[1]), 4),
        "coef_raw_se": round(float(se[1]), 4),
        "coef_line": round(float(beta[2]), 4),
        "coef_line_se": round(float(se[2]), 4),
    }


# =========================================================================== stage 2: analyze
# Pinned before the first holdout scoring (2026-11-21); the verdict is read off these, not tuned to numbers.
RULE: dict[str, Any] = {
    "id": "cfb-v5-margin-slope-recheck-rule-1",
    "split": {
        "method": "explicit_date_boundaries (the artifact's recorded split)",
        "train_end": TRAIN_END,
        "validation_end": VALIDATION_END,
    },
    "estimator": "cfb_v5_research.fit_params(k=6, conference_scale=True) on train: the artifact's own estimator",
    "selection": "among refit candidates, lowest validation moneyline Brier (Gaussian stand-in), then margin log score",
    "holdout_scoring": (
        "served negative-binomial engine, 200k sims, common random numbers per event; paired deltas refit minus "
        "artifact with a date-clustered bootstrap (2000 resamples, seed 11)"
    ),
    "REFIT_requires_all": {
        "significant_improvement": "margin_log_score CI low > 0, or ml_brier CI high < 0, or ml_log_loss CI high < 0",
        "no_point_regression": {
            "ml_brier_at_most": 0.0002,
            "ml_log_loss_at_most": 0.0005,
            "margin_log_score_at_least": -0.001,
        },
        "robust_without_trigger_window": (
            "holdout before 2026-10-01: margin_log_score delta >= 0 and ml_brier delta <= 0.0002"
        ),
        "market_not_worse": "holdout-window corr(served - line, actual - line) of refit >= artifact - 0.02",
    },
    "LEAVE_AS_IS_if": (
        "no significant improvement and point deltas within noise (|ml_brier| < 0.0005 and |margin_log_score| < "
        "0.002) or favouring the artifact"
    ),
    "otherwise": "INCONCLUSIVE",
}
TRIGGER_FROM = "2026-10-01"


def wilson(w: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    z = 1.96
    ph = w / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def _ats(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    w = sum(1 for x, y in pairs if y != 0 and (x > 0) == (y > 0))
    lo = sum(1 for x, y in pairs if y != 0 and (x > 0) != (y > 0))
    pu = sum(1 for _, y in pairs if y == 0)
    n = w + lo
    return {
        "wins": w,
        "losses": lo,
        "pushes": pu,
        "win_pct": round(100.0 * w / n, 2) if n else None,
        "win_pct_ci95": wilson(w, n),
        "roi_pct_at_52_4": round(100.0 * (w * WIN_PAYOUT - lo) / n, 2) if n else None,
    }


def incremental_games(rows: list[dict[str, Any]], art: Params, ref: Params) -> dict[str, Any]:
    """Games that reach a disagreement threshold only under the refit: the direct test of the steeper slope."""
    out: dict[str, Any] = {}
    lined = [r for r in rows if r.get("line_margin") is not None]
    for thr in (3.0, 7.0):
        pairs = []
        for r in lined:
            xa, xr = art.mean(r) - r["line_margin"], ref.mean(r) - r["line_margin"]
            if abs(xr) >= thr and abs(xa) < thr:
                pairs.append((xr, r["margin"] - r["line_margin"]))
        out[f"only_refit_ge_{int(thr)}"] = _ats(pairs)
    flips = [
        (ref.mean(r) - r["line_margin"], r["margin"] - r["line_margin"])
        for r in lined
        if (art.mean(r) - r["line_margin"]) * (ref.mean(r) - r["line_margin"]) < 0
    ]
    out["side_flips_refit_side"] = _ats(flips)
    return out


def _write(rep: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(rep), encoding="utf-8")


def _r4(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 4) for k, v in d.items()}


def cmd_analyze(args: argparse.Namespace) -> int:
    import random

    from model_prediction.models.cfb_structural_v5 import (
        cfb_structural_v5_model,
        load_cfb_structural_v5_artifact,
    )
    from model_prediction.models.college_football import _load_cfb_history
    from model_prediction.validation import chronological_split

    cache = Path(args.cache)
    rows_path = cache / "rows.jsonl"
    rows = [json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    art_json = load_cfb_structural_v5_artifact()
    model = cfb_structural_v5_model()
    tc = art_json["total_calibration"]
    lines = load_espn_lines()
    for r in rows:
        # Served inputs with no market line (predict_matchup(project_missing_lines=True)): the projected spread is
        # built from the RAW margin, so the blowout-widening set is identical in every arm.
        r["pricing_line"] = round(-r["raw_margin"] * 2.0) / 2.0
        r["cal_total"] = max(24.0, tc["intercept"] + tc["slope"] * r["raw_total"])
        r["total_sd"] = model.compute_total_sd(projected_total=r["cal_total"], total_line=r["raw_total"])
        r["line_margin"] = lines.get(r["event_id"])
    meta = json.loads((cache / "snapshot_meta.json").read_text(encoding="utf-8"))
    rep: dict[str, Any] = {
        "as_of_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": {
            "path": "scripts/cfb_v5_margin_slope_recheck.py",
            "git_blob_sha": _git("hash-object", "scripts/cfb_v5_margin_slope_recheck.py"),
            "head_at_run": _git("rev-parse", "HEAD"),
        },
        "replay_snapshot": meta,
        "row_cache": {"path": str(rows_path), "sha256": _sha(rows_path), "rows": len(rows)},
        "espn_lines_sha256": _sha(ESPN_LINES),
        "rule": RULE,
        "artifact": {
            "margin_calibration": art_json["margin_calibration"],
            "margin_sd_table": art_json["margin_sd_table"],
            "conference_margin_scale": art_json["conference_margin_scale"],
            "artifact_hash": art_json["artifact_hash"],
        },
    }
    art = Params(
        "artifact",
        art_json["margin_calibration"]["intercept"],
        art_json["margin_calibration"]["slope"],
        dict(art_json["margin_sd_table"]),
        art_json["conference_margin_scale"],
        art_json["established_min_sample"],
    )

    # ---------------------------------------------------------------- 1. replay sanity
    sanity: dict[str, Any] = {}
    base = {}
    for x in (REPO / "outputs" / "ncaaf" / "cfb_v5_base_rows.jsonl").read_text(encoding="utf-8").splitlines():
        if x.strip():
            b = json.loads(x)
            base[b["event_id"]] = b
    joined = [(base[r["event_id"]], r) for r in rows if r["event_id"] in base]
    seasons = sorted({r["season"] for r in rows})
    sanity["new_raw_on_old_raw_2026_11_12_base_rows"] = {
        "all": _r4(ols([b["proj_margin"] for b, _ in joined], [r["raw_margin"] for _, r in joined])),
        "by_season": {
            str(se): _r4(
                ols(
                    [b["proj_margin"] for b, r in joined if r["season"] == se],
                    [r["raw_margin"] for b, r in joined if r["season"] == se],
                )
            )
            for se in seasons
            if sum(1 for _, r in joined if r["season"] == se) >= 30
        },
        "mean_abs_change": round(mean([abs(r["raw_margin"] - b["proj_margin"]) for b, r in joined]), 3),
        "new_rows_not_in_base": len(rows) - len(joined),
        "note": "current replay's raw margin regressed on the 2026-11-12 replay's raw margin, same event ids",
    }
    trig = [r for r in rows if TRIGGER_FROM <= r["date"] <= "2026-11-19"]
    sanity["trigger_window_2026_10_01_to_11_19"] = ols_block(trig)
    sanity["trigger_window_fbs_only"] = ols_block([r for r in trig if not r["is_fcs"]])
    # A fresh model on a physically truncated history must reproduce the cached raw margin (no cross-game leak).
    hist = sorted(_load_cfb_history(cache / "history"), key=lambda g: (g.start, str(g.event_id)))
    by_id = {str(g.event_id): g for g in hist}
    diffs = []
    for r in random.Random(5).sample(rows, 20):
        g = by_id[r["event_id"]]
        fresh = cfb_structural_v5_model()
        f = fresh.extractor.extract_features(
            history=[h for h in hist if h.start < g.start],
            away_team=g.away_team,
            home_team=g.home_team,
            event_id=r["event_id"],
            game_start_utc=g.event_start_utc,
            season_year=r["season"],
            week=r["week"],
            is_neutral_site=r["neutral"],
            away_team_id=g.away_team_id,
            home_team_id=g.home_team_id,
        )
        diffs.append(abs(float(f.projected_margin_home) - r["raw_margin"]))
    sanity["fresh_model_spot_check_20"] = {"max_abs_diff": max(diffs), "pass": max(diffs) < 1e-9}
    gc = {}
    with (REPO / "data" / "research" / "ncaaf_v6" / "ncaaf_games_backfill.jsonl").open(
        encoding="utf-8"
    ) as fh:
        for x in fh:
            if x.strip():
                b = json.loads(x)
                gc[str(b["event_id"])] = b.get("game_class")
    cross: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cross[str(r["season"])][f"{game_class(r)}|{gc.get(r['event_id'], 'missing')}"] += 1
    sanity["is_fcs_vs_backfill_game_class_by_season"] = {k: dict(v) for k, v in sorted(cross.items())}
    rep["sanity"] = sanity
    _write(rep)
    print(
        "sanity done",
        sanity["trigger_window_2026_10_01_to_11_19"],
        sanity["fresh_model_spot_check_20"],
        flush=True,
    )

    # ---------------------------------------------------------------- 2. actual-on-raw slopes
    fbs = [r for r in rows if not r["is_fcs"]]
    sl: dict[str, Any] = {
        "all": ols_block(rows),
        "fbs_vs_fbs": ols_block(fbs),
        "fbs_vs_fcs": ols_block([r for r in rows if r["is_fcs"]]),
        "by_season": {
            str(se): {
                "all": ols_block([r for r in rows if r["season"] == se]),
                "fbs_vs_fbs": ols_block([r for r in fbs if r["season"] == se]),
            }
            for se in seasons
        },
        "by_week_band": {
            wb: {
                "all": ols_block([r for r in rows if week_band(r["week"]) == wb]),
                "fbs_vs_fbs": ols_block([r for r in fbs if week_band(r["week"]) == wb]),
            }
            for wb in ("weeks_0_4", "weeks_5_14", "postseason")
        },
        "by_sample_games_fbs_vs_fbs": {
            "lt_6_thin": ols_block([r for r in fbs if r["sample_games"] < 6]),
            "ge_6_established": ols_block([r for r in fbs if r["sample_games"] >= 6]),
        },
        "by_season_week_band_fbs_vs_fbs": {
            f"{se}:{wb}": ols_block([r for r in fbs if r["season"] == se and week_band(r["week"]) == wb])
            for se in seasons
            for wb in ("weeks_0_4", "weeks_5_14")
        },
    }
    rep["slopes_actual_on_raw"] = sl
    _write(rep)

    # ---------------------------------------------------------------- 3. split, reproduce, refit, validate
    ducks = [SimpleNamespace(date=r["date"], row=r) for r in rows]
    tr_d, va_d, ho_d, split_meta = chronological_split(
        ducks, train_end_date=TRAIN_END, validation_end_date=VALIDATION_END
    )
    train, val, hold = [d.row for d in tr_d], [d.row for d in va_d], [d.row for d in ho_d]
    rep["split"] = split_meta
    old_train = [
        {**b, "raw_margin": b["proj_margin"], "raw_total": b["proj_total"]}
        for b in base.values()
        if b["date"] <= TRAIN_END
    ]
    old_fit = fit_orig(old_train, "repro")
    rep["artifact_reproduction"] = {
        "on": "2026-11-12 base rows (pre-fix replay), artifact train window",
        "n": len(old_train),
        "fit": old_fit.as_dict(),
        "pass": abs(old_fit.slope - art.slope) < 1e-3 and abs(old_fit.intercept - art.intercept) < 1e-3,
    }
    refit = fit_orig(train, "refit_orig")
    fbs_mean = fit_orig([r for r in train if not r["is_fcs"]], "refit_fbs_mean")
    fbs_mean.table["fbs_vs_fcs"] = refit.table["fbs_vs_fcs"]
    refit_ml = fit_moneyline(train, "refit_ml_logloss", refit)
    cands = [refit, fbs_mean, refit_ml]
    rep["refits"] = {p.name: p.as_dict() for p in cands}
    rep["train_slopes"] = {
        "all": ols_block(train),
        "fbs_vs_fbs": ols_block([r for r in train if not r["is_fcs"]]),
    }
    vblock: dict[str, Any] = {}
    va_art = price_rows(val, art, engine=False)
    vblock["artifact"] = summarize(va_art, val)
    for p in cands:
        pr = price_rows(val, p, engine=False)
        vblock[p.name] = summarize(pr, val) | {"paired_vs_artifact": paired_deltas(va_art, pr)}
    best = min(cands, key=lambda p: (vblock[p.name]["ml_brier"], -vblock[p.name]["margin_log_score"]))
    vblock["selected"] = best.name
    vblock["val_slopes"] = {
        "all": ols_block(val),
        "fbs_vs_fbs": ols_block([r for r in val if not r["is_fcs"]]),
    }
    rep["validation"] = vblock
    _write(rep)
    print("validation selected", best.name, best.as_dict(), flush=True)

    # ---------------------------------------------------------------- 4. locked holdout (scored once)
    engine = not args.no_engine
    arms = [art, *cands]
    hb: dict[str, Any] = {"engine": "negative_binomial_200k" if engine else "gaussian_stand_in"}
    priced = {p.name: price_rows(hold, p, engine=engine) for p in arms}
    hb["holdout_slopes"] = {
        "all": ols_block(hold),
        "fbs_vs_fbs": ols_block([r for r in hold if not r["is_fcs"]]),
    }
    subsets = {
        "all": list(range(len(hold))),
        "before_2026_10_01": [i for i, r in enumerate(hold) if r["date"] < TRIGGER_FROM],
        "from_2026_10_01": [i for i, r in enumerate(hold) if r["date"] >= TRIGGER_FROM],
        "fbs_vs_fbs": [i for i, r in enumerate(hold) if not r["is_fcs"]],
    }
    for sname, idx in subsets.items():
        sub_rows = [hold[i] for i in idx]
        blk = {p.name: summarize([priced[p.name][i] for i in idx], sub_rows) for p in arms}
        for p in cands:
            blk[p.name]["paired_vs_artifact"] = paired_deltas(
                [priced["artifact"][i] for i in idx], [priced[p.name][i] for i in idx]
            )
        hb[sname] = blk
    hold_fbs_idx = subsets["fbs_vs_fbs"]
    hb["favourite_buckets"] = {p.name: fav_buckets(hold, priced[p.name]) for p in (art, best)}
    hb["favourite_buckets_fbs_vs_fbs"] = {
        p.name: fav_buckets([hold[i] for i in hold_fbs_idx], [priced[p.name][i] for i in hold_fbs_idx])
        for p in (art, best)
    }
    rep["holdout"] = hb
    _write(rep)
    print("holdout scored", flush=True)

    # ---------------------------------------------------------------- 5. market check (ESPN closing spreads)
    mk: dict[str, Any] = {}
    windows = {
        "fit_window_through_2024_11_14": [r for r in rows if r["date"] <= VALIDATION_END],
        "holdout_window": hold,
        "holdout_window_fbs_vs_fbs": [r for r in hold if not r["is_fcs"]],
        "holdout_before_2026_10_01": [r for r in hold if r["date"] < TRIGGER_FROM],
    }
    market_arms = [art, best] if best is refit else [art, best, refit]
    for wname, src in windows.items():
        lined = [r for r in src if r["line_margin"] is not None]
        blk: dict[str, Any] = {"n_lined": len(lined)}
        if lined:
            fav = [r for r in lined if r["line_margin"] != 0 and r["margin"] != 0]
            blk["orientation_check"] = {
                "corr_line_margin_vs_actual": round(
                    pearson([r["line_margin"] for r in lined], [r["margin"] for r in lined]), 4
                ),
                "line_favourite_wins_pct": round(
                    100.0 * mean([1.0 if (r["line_margin"] > 0) == (r["margin"] > 0) else 0.0 for r in fav]),
                    1,
                ),
            }
        for p in market_arms:
            blk[p.name] = market_block(src, p) | {"corr_ci95_date_clustered": clustered_corr_ci(src, p)}
        blk["incremental_refit_only_games"] = incremental_games(src, art, best)
        blk["actual_on_raw_and_line"] = raw_vs_line_regression(src)
        mk[wname] = blk
    mk["by_season_corr"] = {
        str(se): {
            p.name: market_block([r for r in rows if r["season"] == se], p).get(
                "corr_model_minus_line_vs_actual_minus_line"
            )
            for p in (art, best)
        }
        for se in seasons
    }
    rep["market"] = mk
    _write(rep)

    # ---------------------------------------------------------------- 6. verdict (pinned rule)
    d = hb["all"][best.name]["paired_vs_artifact"]
    pre = hb["before_2026_10_01"][best.name]["paired_vs_artifact"]
    sig = (
        d["margin_log_score"]["ci_95"][0] > 0
        or d["ml_brier"]["ci_95"][1] < 0
        or d["ml_log_loss"]["ci_95"][1] < 0
    )
    no_reg = (
        d["ml_brier"]["delta"] <= 0.0002
        and d["ml_log_loss"]["delta"] <= 0.0005
        and d["margin_log_score"]["delta"] >= -0.001
    )
    robust = pre["margin_log_score"]["delta"] >= 0 and pre["ml_brier"]["delta"] <= 0.0002
    c_art = mk["holdout_window"]["artifact"].get("corr_model_minus_line_vs_actual_minus_line")
    c_ref = mk["holdout_window"][best.name].get("corr_model_minus_line_vs_actual_minus_line")
    market_ok = c_art is not None and c_ref is not None and c_ref >= c_art - 0.02
    checks = {
        "significant_improvement": sig,
        "no_point_regression": no_reg,
        "robust_without_trigger_window": robust,
        "market_not_worse": market_ok,
    }
    noise = abs(d["ml_brier"]["delta"]) < 0.0005 and abs(d["margin_log_score"]["delta"]) < 0.002
    favours_artifact = d["ml_brier"]["delta"] >= 0 and d["margin_log_score"]["delta"] <= 0
    if all(checks.values()):
        verdict = "REFIT"
    elif not sig and (noise or favours_artifact):
        verdict = "LEAVE_AS_IS"
    else:
        verdict = "INCONCLUSIVE"
    rep["verdict"] = {
        "verdict": verdict,
        "selected_refit": best.name,
        "checks": checks,
        "proposed_if_refit": best.as_dict(),
    }
    _write(rep)
    print("verdict", verdict, checks)
    return 0


def _f(x: Any, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def render_md(rep: dict[str, Any]) -> str:
    out = ["# cfb-structural-v5 margin-slope recheck", ""]
    v = rep.get("verdict")
    if v:
        out += [
            (
                f"Verdict: **{v['verdict']}** (rule `{RULE['id']}`, pinned in the script before the holdout was scored). "
                f"Selected refit: `{v['selected_refit']}`. Checks: {v['checks']}."
            ),
            "",
        ]
    art = rep["artifact"]
    snap = rep["replay_snapshot"]
    out += [
        (
            f"As of {rep['as_of_utc']}. Replay snapshot {snap['snapshot_utc']}, HEAD `{snap['head'][:8]}`, "
            f"{rep['row_cache']['rows']} FBS-involved completed games (row cache sha256 "
            f"`{rep['row_cache']['sha256'][:16]}`)."
        ),
        (
            f"Artifact: intercept {art['margin_calibration']['intercept']}, slope {art['margin_calibration']['slope']}, "
            f"SD table {art['margin_sd_table']}, conference scale {art['conference_margin_scale']}."
        ),
        "",
    ]
    s = rep.get("sanity")
    if s:
        o = s["new_raw_on_old_raw_2026_11_12_base_rows"]
        out += [
            "## 1. Replay sanity",
            "",
            f"- Current raw margin regressed on the 2026-11-12 (pre-fix) replay's raw margin, same events "
            f"(n={o['all']['n']}): slope {o['all']['slope']:.4f}, intercept {o['all']['intercept']:.3f}; mean abs "
            f"change {o['mean_abs_change']} points. By season: "
            + ", ".join(f"{k} {b['slope']:.3f}" for k, b in o["by_season"].items())
            + ".",
            (
                f"- Trigger window 2026-10-01..11-19, actual on raw: {s['trigger_window_2026_10_01_to_11_19']}; "
                f"FBS-only {s['trigger_window_fbs_only']}."
            ),
            f"- Fresh model on physically truncated history, 20 random games: {s['fresh_model_spot_check_20']}.",
            "",
        ]
    sl = rep.get("slopes_actual_on_raw")
    if sl:
        out += [
            "## 2. Actual margin on raw projected margin (OLS)",
            "",
            "| cohort | n | slope | se | intercept | resid SD |",
            "|---|---|---|---|---|---|",
        ]

        def row(name: str, b: dict[str, Any] | None) -> None:
            if b and "slope" in b:
                out.append(
                    f"| {name} | {b['n']} | {b['slope']:.3f} | {b['slope_se']:.3f} | {b['intercept']:.2f} | "
                    f"{b['resid_sd']:.2f} |"
                )

        row("all", sl["all"])
        row("FBS vs FBS", sl["fbs_vs_fbs"])
        row("FBS vs FCS", sl["fbs_vs_fcs"])
        for se, b in sl["by_season"].items():
            row(f"season {se}", b["all"])
            row(f"season {se} FBS-FBS", b["fbs_vs_fbs"])
        for wb, b in sl["by_week_band"].items():
            row(wb, b["all"])
            row(f"{wb} FBS-FBS", b["fbs_vs_fbs"])
        for k, b in sl["by_sample_games_fbs_vs_fbs"].items():
            row(f"FBS-FBS sample_games {k}", b)
        out.append("")
    if "refits" in rep:
        out += [
            "## 3. Split, reproduction, refit, validation",
            "",
            f"Split: {rep['split']}.",
            "",
            f"Artifact reproduction: {rep['artifact_reproduction']}.",
            "",
        ]
        for name, p in rep["refits"].items():
            out.append(f"- `{name}`: {p}")
        out += [
            "",
            f"Train slopes {rep['train_slopes']}; validation slopes {rep['validation']['val_slopes']}.",
            "",
            "| validation (Gaussian stand-in) | n | MAE | margin log score | Brier | log loss | z SD |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, b in rep["validation"].items():
            if isinstance(b, dict) and "margin_mae" in b:
                out.append(
                    f"| {name} | {b['n']} | {b['margin_mae']:.3f} | {b['margin_log_score']:.4f} | "
                    f"{b['ml_brier']:.5f} | {b['ml_log_loss']:.5f} | {b['margin_z_sd']:.3f} |"
                )
        out += ["", f"Selected on validation: `{rep['validation']['selected']}`.", ""]
    h = rep.get("holdout")
    if h:
        out += [
            f"## 4. Locked holdout ({h['engine']})",
            "",
            f"Holdout slopes: {h['holdout_slopes']}.",
            "",
            (
                "| subset | arm | n | MAE | log score | Brier | log loss | ECE | d Brier [CI] | d log loss [CI] | "
                "d log score [CI] | d MAE [CI] |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for sname in ("all", "before_2026_10_01", "from_2026_10_01", "fbs_vs_fbs"):
            for arm, b in h[sname].items():
                pv = b.get("paired_vs_artifact")
                if pv:
                    dd = " | ".join(
                        f"{pv[k]['delta']:+.5f} [{pv[k]['ci_95'][0]:+.4f}, {pv[k]['ci_95'][1]:+.4f}]"
                        for k in ("ml_brier", "ml_log_loss", "margin_log_score", "margin_mae")
                    )
                else:
                    dd = " | | | "
                out.append(
                    f"| {sname} | {arm} | {b['n']} | {b['margin_mae']:.3f} | {b['margin_log_score']:.4f} | "
                    f"{b['ml_brier']:.5f} | {b['ml_log_loss']:.5f} | {_f(b['ml_ece'])} | {dd} |"
                )
        out += [
            "",
            "Favourite-size buckets (served favourite; model vs realised win %):",
            "",
            "| cohort | arm | bucket | n | proj fav margin | actual fav margin | model win % | realised win % |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for cohort, key in (("all", "favourite_buckets"), ("FBS-FBS", "favourite_buckets_fbs_vs_fbs")):
            for arm, bl in h[key].items():
                for b in bl:
                    out.append(
                        f"| {cohort} | {arm} | {b['bucket']} | {b['n']} | {b['fav_projected_margin']} | "
                        f"{b['fav_actual_margin']} | {b['fav_model_win_pct']}% | {b['fav_realised_win_pct']}% |"
                    )
        out.append("")
    mk = rep.get("market")
    if mk:
        out += [
            "## 5. Market check (ESPN archived closing spreads)",
            "",
            (
                "| window | arm | n | corr [95% CI] | ATS >=3 W-L (win %, ROI at 52.4%) | ATS >=7 W-L (win %, ROI) | "
                "mean abs disagreement | MAE model / line |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
        for wname, blk in mk.items():
            if wname == "by_season_corr":
                continue
            for arm, b in blk.items():
                if not isinstance(b, dict) or "corr_model_minus_line_vs_actual_minus_line" not in b:
                    continue
                a3, a7 = b["ats_disagree_ge_3"], b["ats_disagree_ge_7"]
                out.append(
                    f"| {wname} | {arm} | {b['n']} | {b['corr_model_minus_line_vs_actual_minus_line']:+.4f} "
                    f"{b['corr_ci95_date_clustered']} | {a3['wins']}-{a3['losses']} ({_f(a3['win_pct'], 1)}%, "
                    f"{_f(a3['roi_pct_at_52_4'], 1)}%) | {a7['wins']}-{a7['losses']} ({_f(a7['win_pct'], 1)}%, "
                    f"{_f(a7['roi_pct_at_52_4'], 1)}%) | {b['mean_abs_disagreement']} | "
                    f"{b['mae_model']} / {b['mae_line']} |"
                )
        out.append("")
        for wname, blk in mk.items():
            if wname == "by_season_corr":
                continue
            out.append(
                f"- {wname}: orientation {blk.get('orientation_check')}; refit-only games "
                f"{blk.get('incremental_refit_only_games')}; actual ~ raw + line {blk.get('actual_on_raw_and_line')}"
            )
        out += ["", f"Corr by season (artifact vs selected refit): {mk['by_season_corr']}", ""]
    dg = rep.get("diagnostics_after_verdict")
    if dg:
        out += [
            "## 6. Decomposition (informational, run after the verdict; does not change it)",
            "",
            (
                "Which layer carries the holdout change? Each arm differs from the artifact in one layer only; "
                "same engine, same common random numbers."
            ),
            "",
            "| arm | subset | d Brier [CI] | d log loss [CI] | d log score [CI] | d MAE [CI] |",
            "|---|---|---|---|---|---|",
        ]
        for arm, blk in dg["arms"].items():
            for sname, pv in blk["paired_vs_artifact"].items():
                cells = " | ".join(
                    f"{pv[k]['delta']:+.5f} [{pv[k]['ci_95'][0]:+.4f}, {pv[k]['ci_95'][1]:+.4f}]"
                    for k in ("ml_brier", "ml_log_loss", "margin_log_score", "margin_mae")
                )
                out.append(f"| {arm} | {sname} | {cells} |")
        out += ["", f"Arms: {json.dumps({a: b['params'] for a, b in dg['arms'].items()})}", ""]
    if INTERPRETATION:
        out += ["## Reading", "", *INTERPRETATION, ""]
    out += [
        "## Caveats",
        "",
        (
            "- History rows are not vintaged (scores only). Team identity and FBS/FCS class are rebuilt from rows "
            "before kickoff, but a row's feed membership is as fetched later."
        ),
        "- Weather: history files carry placeholders, so no weather term (as production with no verified forecast).",
        "- QB availability has no point-in-time source before 2026-11 (channel inert).",
        (
            "- ESPN lines are archived closing / final pregame lines with no per-quote observed_at: a closing benchmark, "
            "not decision-time quotes. From 2024 each event carries one book."
        ),
        (
            "- Moneyline probabilities use the projected (raw-margin) spread for blowout widening in every arm, as the "
            "served path does when no market spread is supplied."
        ),
        "- The 427-game trigger sample lies inside the locked holdout; the holdout is reported with and without it.",
        "- Prices are percentages; no American odds. Nothing promoted; artifact unchanged.",
        "",
    ]
    return "\n".join(out)


# Written after reading sections 1-6 of the 2026-11-21 run; numbers are those in the json.
INTERPRETATION: list[str] = [
    (
        "- The 1.53 trigger does not reproduce with real weeks. On the identical 427 events (2026-10-02..11-19, "
        "weeks 5-12) the replay gives actual-on-raw slope 1.22 (SE 0.09) and FBS-vs-FBS 1.15, with Brier 0.2128 "
        "(FBS-vs-FBS 0.2146). Forcing week=1 (UpcomingCFBGame's default) gives Brier 0.2145, FBS-vs-FBS 0.2168, "
        "calibrated MAE 13.02 and slope 1.67. The idf summary published 0.2149 / 0.2170 / 13.06 / 1.53. The idf "
        "check therefore almost certainly priced these mid-season games as week 1. That leans on preseason priors, "
        "which compress projections. The served slate passes the real ESPN week, so 1.53 is a harness artifact. "
        "Lookahead checks: passing the full history to the extractor returns identical raw margins, because the "
        "extractor filters on kickoff itself; a fresh model on truncated history matches exactly."
    ),
    (
        "- Validation window, FBS-vs-FBS only (Gaussian stand-in): every refit delta's CI spans 0. For refit_fbs_mean: "
        "Brier -0.0001 [-0.0005, +0.0003], log loss -0.0005 [-0.0018, +0.0008], margin log score -0.0011 "
        "[-0.0032, +0.0010]. The holdout neutrality on FBS-vs-FBS predates the holdout. refit_orig and "
        "refit_fbs_mean tied on pooled validation (Brier 0.16952 vs 0.16951); the choice between them is noise."
    ),
    (
        "- The fixes rescaled raw margins by about 0.95 (current raw on 2026-11-12 raw, same 7,960 events, every "
        "season 0.935-0.988). With the artifact's own estimator, which reproduces 1.3189 / -0.4143 exactly on the old "
        "rows, the train refit is 1.3948 / -0.4026 (FBS mean fit 1.3993 / -0.6596). That is a mechanical 6% "
        "steepening, not a jump to 1.5."
    ),
    (
        "- By-season FBS-vs-FBS slopes run 1.32-1.52 with no trend; 2026 FBS-vs-FBS is 1.32. Compression is week-"
        "dependent: weeks 0-4 FBS-vs-FBS 1.53, weeks 5-14 1.32, postseason 1.06; thin samples 1.44, established 1.32. "
        "A single slope is the wrong tool for that. A week- or sample-dependent slope would be its own challenger."
    ),
    (
        "- Holdout: the pinned rule returns REFIT on the pooled cohort. Decomposition shows where the gain comes from. "
        "The FBS-vs-FCS games, which the Main gate blocks, gain from both the new SD slice (19.73 to 18.24, which had "
        "been fit under the old fail-open labels) and the mean. On FBS-vs-FBS games the slope/intercept change is "
        "neutral (Brier +0.0001, CI -0.0003 to +0.0005), and the SD table gives a small log-loss gain (-0.00017, CI "
        "excludes 0). In the 2026-10-01+ window the slope change is worse (Brier +0.0008, CI excludes 0)."
    ),
    (
        "- Market: the correlation of (served minus line) with (actual minus line) is about 0 under both arms, in "
        "every window and season. Against the spread at a 52.4% break-even, neither arm clears it at disagreements of "
        "3 or more or 7 or more points. Holding the line fixed, the raw projection adds nothing (fit window: coefficient "
        "-0.005, SE 0.041). The steeper slope widens no edge; there is none to widen."
    ),
]


def cmd_decompose(args: argparse.Namespace) -> int:
    """One-layer-at-a-time holdout arms, appended to the existing report (informational only)."""
    from model_prediction.models.cfb_structural_v5 import (
        cfb_structural_v5_model,
        load_cfb_structural_v5_artifact,
    )

    rep = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    cache = Path(args.cache)
    rows = [
        json.loads(x) for x in (cache / "rows.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    art_json = load_cfb_structural_v5_artifact()
    model = cfb_structural_v5_model()
    tc = art_json["total_calibration"]
    hold = [r for r in rows if r["date"] > VALIDATION_END]
    for r in hold:
        r["pricing_line"] = round(-r["raw_margin"] * 2.0) / 2.0
        r["cal_total"] = max(24.0, tc["intercept"] + tc["slope"] * r["raw_total"])
        r["total_sd"] = model.compute_total_sd(projected_total=r["cal_total"], total_line=r["raw_total"])
    am = art_json["margin_calibration"]
    art = Params(
        "artifact",
        am["intercept"],
        am["slope"],
        dict(art_json["margin_sd_table"]),
        art_json["conference_margin_scale"],
        art_json["established_min_sample"],
    )
    sel = rep["refits"][rep["verdict"]["selected_refit"]]
    sm, st, sc = sel["margin_calibration"], sel["margin_sd_table"], sel["conference_margin_scale"]
    fcs_only = dict(art.table)
    fcs_only["fbs_vs_fcs"] = st["fbs_vs_fcs"]
    arms = [
        Params("slope_intercept_only", sm["intercept"], sm["slope"], dict(art.table), art.conf_scale, 6),
        Params("sd_table_only", art.intercept, art.slope, dict(st), sc, 6),
        Params("fcs_sd_slice_only", art.intercept, art.slope, fcs_only, art.conf_scale, 6),
    ]
    base = price_rows(hold, art, engine=True)
    subsets = {
        "all": list(range(len(hold))),
        "fbs_vs_fbs": [i for i, r in enumerate(hold) if not r["is_fcs"]],
        "fbs_vs_fcs": [i for i, r in enumerate(hold) if r["is_fcs"]],
        "from_2026_10_01": [i for i, r in enumerate(hold) if r["date"] >= TRIGGER_FROM],
    }
    out: dict[str, Any] = {"note": "informational; computed after the pinned verdict", "arms": {}}
    for p in arms:
        pr = price_rows(hold, p, engine=True)
        out["arms"][p.name] = {
            "params": p.as_dict(),
            "paired_vs_artifact": {
                s: paired_deltas([base[i] for i in idx], [pr[i] for i in idx]) for s, idx in subsets.items()
            },
        }
        print(p.name, json.dumps(out["arms"][p.name]["paired_vs_artifact"]["all"]), flush=True)
    sel_p = Params("selected", sm["intercept"], sm["slope"], dict(st), sc, 6)
    pr = price_rows(hold, sel_p, engine=True)
    out["arms"]["selected_refit_by_class"] = {
        "params": sel_p.as_dict(),
        "paired_vs_artifact": {
            s: paired_deltas([base[i] for i in idx], [pr[i] for i in idx])
            for s, idx in subsets.items()
            if s == "fbs_vs_fcs"
        },
    }
    rep["diagnostics_after_verdict"] = out
    _write(rep)
    return 0


def cmd_extras(args: argparse.Namespace) -> int:
    """Tie the replay to the idf summary's published arm-E numbers on the 427 trigger events, with the file's
    weeks and with week forced to 1 (UpcomingCFBGame's default), plus validation-window FBS-vs-FBS paired deltas
    (Gaussian stand-in). Informational; appended to the report."""
    from model_prediction.models.cfb_structural_v5 import (
        cfb_structural_v5_model,
        load_cfb_structural_v5_artifact,
    )
    from model_prediction.models.college_football import _load_cfb_history

    rep = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    cache = Path(args.cache)
    rows = [
        json.loads(x) for x in (cache / "rows.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    aj = load_cfb_structural_v5_artifact()
    model = cfb_structural_v5_model()
    tc = aj["total_calibration"]

    def prep(rs: list[dict[str, Any]]) -> None:
        for r in rs:
            r["pricing_line"] = round(-r["raw_margin"] * 2.0) / 2.0
            r["cal_total"] = max(24.0, tc["intercept"] + tc["slope"] * r["raw_total"])
            r["total_sd"] = model.compute_total_sd(projected_total=r["cal_total"], total_line=r["raw_total"])

    am = aj["margin_calibration"]
    art = Params(
        "artifact",
        am["intercept"],
        am["slope"],
        dict(aj["margin_sd_table"]),
        aj["conference_margin_scale"],
        6,
    )

    def block(rs: list[dict[str, Any]]) -> dict[str, Any]:
        pr = price_rows(rs, art, engine=True)
        fb = [i for i, r in enumerate(rs) if not r["is_fcs"]]
        s = summarize(pr, rs)
        sf = summarize([pr[i] for i in fb], [rs[i] for i in fb])
        return {
            "n": s["n"],
            "ml_brier": s["ml_brier"],
            "calibrated_margin_mae": s["margin_mae"],
            "fbs_vs_fbs_n": sf["n"],
            "fbs_vs_fbs_ml_brier": sf["ml_brier"],
            "actual_on_raw": ols_block(rs),
        }

    trig = [r for r in rows if TRIGGER_FROM <= r["date"] <= "2026-11-19"]
    prep(trig)
    hist = sorted(_load_cfb_history(cache / "history"), key=lambda g: (g.start, str(g.event_id)))
    by_id = {str(g.event_id): g for g in hist}
    week1 = []
    for r in trig:
        g = by_id[r["event_id"]]
        f = model.extractor.extract_features(
            history=[h for h in hist if h.start < g.start],
            away_team=g.away_team,
            home_team=g.home_team,
            event_id=r["event_id"],
            game_start_utc=g.event_start_utc,
            season_year=r["season"],
            week=1,
            is_neutral_site=r["neutral"],
            away_team_id=g.away_team_id,
            home_team_id=g.home_team_id,
        )
        week1.append(
            {
                **r,
                "raw_margin": float(f.projected_margin_home),
                "raw_total": float(f.projected_total),
                "sample_games": int(f.sample_games),
                "is_fcs": bool(f.is_fbs_vs_fcs),
                "is_conf": bool(f.is_conference_matchup),
            }
        )
    prep(week1)
    tie = {
        "idf_published_arm_e": {
            "ml_brier": 0.2149,
            "fbs_vs_fbs_ml_brier": 0.2170,
            "calibrated_margin_mae": 13.06,
            "actual_on_raw_slope": 1.53,
            "n": 427,
        },
        "replay_file_weeks": block(trig),
        "replay_week_forced_1": block(week1),
        "file_weeks_range": [min(r["week"] for r in trig), max(r["week"] for r in trig)],
    }
    val = [r for r in rows if TRAIN_END < r["date"] <= VALIDATION_END and not r["is_fcs"]]
    for r in val:
        r["pricing_line"] = round(-r["raw_margin"] * 2.0) / 2.0
    vfbs: dict[str, Any] = {}
    base = price_rows(val, art, engine=False)
    for name, p in rep["refits"].items():
        if name == "refit_ml_logloss":
            continue
        cand = Params(
            name,
            p["margin_calibration"]["intercept"],
            p["margin_calibration"]["slope"],
            dict(p["margin_sd_table"]),
            p["conference_margin_scale"],
            6,
        )
        slope_only = Params(
            name + "_slope_only", cand.intercept, cand.slope, dict(art.table), art.conf_scale, 6
        )
        vfbs[name] = paired_deltas(base, price_rows(val, cand, engine=False))
        vfbs[name + "_slope_only"] = paired_deltas(base, price_rows(val, slope_only, engine=False))
    rep.setdefault("diagnostics_after_verdict", {})["idf_tie_in"] = tie
    rep["diagnostics_after_verdict"]["validation_fbs_vs_fbs_paired_vs_artifact"] = vfbs
    _write(rep)
    print(json.dumps(tie, indent=1))
    print(
        json.dumps(
            {
                k: {m: v[m]["delta"] for m in ("ml_brier", "ml_log_loss", "margin_log_score")}
                for k, v in vfbs.items()
            }
        )
    )
    return 0


def cmd_render(_: argparse.Namespace) -> int:
    _write(json.loads(OUT_JSON.read_text(encoding="utf-8")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay")
    rp.add_argument("--workers", type=int, default=4)
    rp.add_argument("--cache", default=str(DEFAULT_CACHE))
    rp.add_argument("--fresh", action="store_true")
    rp.set_defaults(fn=cmd_replay)
    an = sub.add_parser("analyze")
    an.add_argument("--cache", default=str(DEFAULT_CACHE))
    an.add_argument("--no-engine", action="store_true", help="score holdout with the Gaussian stand-in only")
    an.set_defaults(fn=cmd_analyze)
    dc = sub.add_parser("decompose")
    dc.add_argument("--cache", default=str(DEFAULT_CACHE))
    dc.set_defaults(fn=cmd_decompose)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    ex = sub.add_parser("extras")
    ex.add_argument("--cache", default=str(DEFAULT_CACHE))
    ex.set_defaults(fn=cmd_extras)
    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
