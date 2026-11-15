"""NCAAB market-anchored residual test on ESPN closing lines (quick-261115-1m8, Tier 3).

Question: does anything we can compute before tip-off (v6 model-minus-line, tempo, rest, neutral site,
early-season sample size, non-D1 flag) explain (actual - closing line)? If not, a model anchored on the
market (line + predicted residual) has no side to take and the verdict is NO PICK.

Research only. Reads the archives written by scripts/ncaab_espn_lines.py (scoreboard.jsonl, espn_lines.jsonl
under data/research/ncaab_market/, untracked), calls the existing served-parameter v6 models READ-ONLY to
rebuild point-in-time projections, and writes only outputs/ncaab/ncaab_residual.{json,md} plus untracked
caches under data/research/ncaab_market/. No ledger writes, no config or promotion changes.

Commands (repo root; OPENBLAS_NUM_THREADS=1 PYTHONPATH=src .venv/Scripts/python scripts/ncaab_residual.py ...):
    project   rebuild PIT v6 spread/total projections for every game day (cache: v6_projections.jsonl)
    features  PIT candidate features (tempo, rest, sample size, ...) per game (cache: pit_features.jsonl)
    run       join lines + outcomes and apply the pinned decision rule; writes the outputs
    selftest  plumbing check on a label-shuffled copy of the outcomes (no real outcome is read)

Outcomes are joined ONLY inside `run`/`selftest`. The rule below is hashed and printed before any
window statistic exists; the holdout window is scored only for a model the rule accepted on validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

DATA = REPO / "data" / "research" / "ncaab_market"
SCOREBOARD = DATA / "scoreboard.jsonl"
LINES = DATA / "espn_lines.jsonl"
STATS_ROOT = DATA / "raw_stats"
PROJECTIONS = DATA / "v6_projections.jsonl"
FEATURES = DATA / "pit_features.jsonl"
OUT = REPO / "outputs" / "ncaab"
OUT_JSON = OUT / "ncaab_residual.json"
OUT_MD = OUT / "ncaab_residual.md"
LOCAL_HISTORY = REPO / "data" / "historical" / "ncaab_games_all.jsonl"
REPRO = DATA / "repro.json"  # written by the repro command, read into the report

# ----------------------------------------------------------------------------------------------------------
# THE PINNED RULE. Written and hashed before any outcome is joined; the hash is printed first. The holdout
# (2025-26) is read only inside score_locked_windows(), for a model the rule accepted on validation.
# ----------------------------------------------------------------------------------------------------------
RULE: dict[str, Any] = {
    "id": "ncaab-residual-rule-1",
    "targets": {
        "spread": "home margin minus implied home margin (implied = -home_spread_line)",
        "total": "actual total minus total line",
    },
    "line": "ESPN pinned median of non-live providers (rule ncaab-espn-lines-rule-1), CLOSING benchmark",
    "windows_by_espn_season_year": {
        "fit": [2023, 2024],
        "validation": [2025],
        "locked_holdout": [2026],
        "final_live_check": [2027],
        "note": "season year 2023 = 2022-23; 2022 (2021-22) is rating-history burn-in only",
    },
    "cohort": "completed lined events with a rebuilt PIT v6 projection and all of the model's features",
    "v6": "ncaab_spread_v6_model()/ncaab_total_v6_model() served parameters, history strictly before the "
    "game's ET calendar day, current neutral-site flag from the scoreboard, scoreboard box stats and AP ranks "
    "for tempo/rank priors",
    "feature_groups": {
        "v6": "x = v6 projection - line (always included)",
        "tempo": "mean of the two teams' last-10-game shot attempts (FGA + 0.44 FTA, game average) "
        "minus the running league mean of all prior games, PIT",
        "rest": "min(rest days home, 7) - min(rest days away, 7); no prior game = 7",
        "neutral": "neutral-site flag",
        "sample": "min(prior current-season games of the two teams, 15) and x * 1[that < 8]",
        "nond1": "either side lacks an ESPN conference id (non-Division-I opponent), and x * flag",
        "movement": "closing minus opening line (spread: implied home margin; total: total points). "
        "CLOSING-BASED: not usable at decision time, diagnostic only",
    },
    "model_family_per_market": [
        "m0_v6",
        "m_tempo",
        "m_rest",
        "m_neutral",
        "m_sample",
        "m_nond1",
        "m_joint (v6+tempo+rest+neutral+sample+nond1)",
    ],
    "movement_models": "m_move (v6+movement) and m_joint_move join the family only if rows with an opening "
    "line number at least 2000 in the fit window and at least 1000 in the validation window; otherwise "
    "movement is reported as a labelled exploratory diagnostic (a movement-only ridge on the rows carrying an "
    "opening line, chronological 60/20/20 by date, which spends part of the 2025-26 and 2026-27 windows for "
    "movement alone) that cannot affect acceptance. Either way a movement model is never an accepted pick model",
    "model": "ridge on standardised features (alpha 1000 fixed, standardisation and coefficients from the FIT "
    "window only) predicting actual - line; the prediction used everywhere is 0.5 x the ridge prediction "
    "(shrinkage); baseline = line only = fit-window mean residual",
    "bootstrap": {
        "resamples": 4000,
        "cluster": "game date",
        "seed": 17,
        "interval": "percentile; a model's lower bounds use alpha = 0.025 / K, K = models in the market's "
        "family (Bonferroni across the family); the unadjusted 95% bound is reported alongside",
    },
    "accept_if_all_on_VALIDATION_window": [
        (
            "(a) K-adjusted bootstrap lower bound of the validation-window OLS slope of (actual - line) on the "
            "model's shrunk prediction is above zero"
        ),
        (
            "(b) K-adjusted bootstrap lower bound of the out-of-sample MSE delta (baseline MSE - model MSE, fit-"
            "window coefficients applied to validation games) is above zero"
        ),
        "(c) the model uses no movement feature (a closing-based feature cannot serve a decision)",
    ],
    "if_not_accepted": "NO PICK; the locked holdout and the live check are NOT scored for that market",
    "if_accepted": "the accepted model with the largest validation MSE delta is scored once on 2025-26; only if "
    "it also passes (a) and (b) there with the plain 95% bounds is 2026-27 scored, once; side record follows "
    "the sign of the shrunk prediction when |prediction| >= 1 point, pushes excluded. A model whose v6 "
    "coefficient is negative is labelled fade-v6, not v6 skill",
    "price_assumptions_pct": {"break_even_implied": 52.4, "executable_ask": 53.5},
    "no_tuning": "no grids, no per-bucket cuts, no other features; alpha, shrinkage, threshold fixed above",
}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
N_BOOT = RULE["bootstrap"]["resamples"]
SEED = RULE["bootstrap"]["seed"]
RIDGE_ALPHA = 1000.0
SHRINK = 0.5
MIN_GAP = 1.0
WINDOW_SEASONS = {
    "fit": (2023, 2024),
    "validation": (2025,),
    "holdout": (2026,),
    "live": (2027,),
}
MOVE_MIN_FIT, MOVE_MIN_VAL = 2000, 1000
MARKETS = ("spread", "total")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------ scoreboard data
def load_scoreboard_events() -> list[dict[str, Any]]:
    """All completed scoreboard events (every season year incl. burn-in), first occurrence per event id."""
    seen: dict[str, dict[str, Any]] = {}
    with SCOREBOARD.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("http_status") != 200:
                continue
            for ev in rec["events"]:
                if not ev["completed"] or ev["home_score"] is None or ev["away_score"] is None:
                    continue
                seen.setdefault(ev["event_id"], {**ev, "sb_date": rec["date"]})
    return sorted(seen.values(), key=lambda e: (e["start_utc"], e["event_id"]))


def et_day(start_utc: str) -> str:
    from model_prediction.domain import EASTERN

    dt = datetime.strptime(start_utc, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
    return dt.astimezone(EASTERN).date().isoformat()


def build_stats_root(events: list[dict[str, Any]]) -> int:
    """Write scoreboard-shaped files the existing NCAABScoreboardStats loader reads (raw_stats/<date>/...)."""
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names = {
        "fieldGoalsAttempted": "fieldGoalsAttempted",
        "fieldGoalsMade": "fieldGoalsMade",
        "threePointFieldGoalsAttempted": "threePointFieldGoalsAttempted",
        "threePointFieldGoalsMade": "threePointFieldGoalsMade",
        "freeThrowsAttempted": "freeThrowsAttempted",
        "freeThrowsMade": "freeThrowsMade",
        "rebounds": "rebounds",
        "assists": "assists",
    }
    for ev in events:
        comps = []
        for side in ("home", "away"):
            stats = [
                {"name": n, "displayValue": str(int(ev[f"{side}_{n}"]))}
                for n in names
                if ev.get(f"{side}_{n}") is not None
            ]
            comp: dict[str, Any] = {
                "homeAway": side,
                "team": {"displayName": ev[f"{side}_team"]},
                "score": str(ev[f"{side}_score"]),
                "statistics": stats,
            }
            if ev.get(f"{side}_ap_rank") is not None:
                comp["curatedRank"] = {"current": ev[f"{side}_ap_rank"]}
            comps.append(comp)
        by_day[ev["sb_date"]].append(
            {
                "id": ev["event_id"],
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "conferenceCompetition": ev["conference_competition"],
                        "neutralSite": ev["neutral"],
                        "competitors": comps,
                    }
                ],
            }
        )
    for day, evs in by_day.items():
        path = STATS_ROOT / day / "scores_ncaab.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"events": evs}, separators=(",", ":")), encoding="utf-8")
    return len(by_day)


def to_game_records(events: list[dict[str, Any]]) -> list[Any]:
    from model_prediction.features.base import GameRecord

    return [
        GameRecord(
            event_id=e["event_id"],
            event_start_utc=e["start_utc"],
            league="NCAAB",
            away_team=e["away_team"],
            home_team=e["home_team"],
            away_score=int(e["away_score"]),
            home_score=int(e["home_score"]),
            season_type=e["season_type"],
            season_year=int(e["season_year"]),
            competition_type=e["competition_type"],
        )
        for e in events
    ]


# ------------------------------------------------------------------------------------------ projections
def _worker_project(args: tuple[list[str], int, int]) -> list[dict[str, Any]]:
    """Rebuild PIT v6 projections for a list of ET days (runs in a worker process)."""
    import bisect

    from model_prediction.domain import EASTERN
    from model_prediction.features.ncaab_scoreboard_stats import NCAABScoreboardStats
    from model_prediction.models.basketball import UpcomingGame
    from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v6_model
    from model_prediction.models.ncaab_total_v6 import ncaab_total_v6_model

    days, min_season_year, history_min_season = args
    events = load_scoreboard_events()
    games = [g for g in to_game_records(events) if int(g.season_year or 0) >= history_min_season]
    neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
    order = sorted(range(len(games)), key=lambda i: games[i].start)
    games = [games[i] for i in order]
    starts = [g.start for g in games]
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
    sm, tm = ncaab_spread_v6_model(), ncaab_total_v6_model()
    stats = NCAABScoreboardStats(STATS_ROOT)
    if getattr(sm, "_stats", None) is not None:
        sm._stats = stats
    if getattr(tm, "_stats", None) is not None:
        tm._stats = stats
    rows: list[dict[str, Any]] = []
    for day in days:
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN)
        hist = games[: bisect.bisect_left(starts, cutoff)]
        todays = [g for g in by_day.get(day, []) if int(g.season_year or 0) >= min_season_year]
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
        ps = {p.event_id: p for p in sm.predict_games(hist, up) if p.market_type == "moneyline"}
        pt = {p.event_id: p for p in tm.predict_games(hist, up) if p.market_type == "total"}
        for g in todays:
            row: dict[str, Any] = {"event_id": g.event_id, "day": day, "season_year": g.season_year}
            p, t = ps.get(g.event_id), pt.get(g.event_id)
            if p is not None:
                row["sp_mean"] = float(p.inference_inputs["mean"])
                row["sp_sd"] = float(p.inference_inputs["sd"])
                row["sp_history_games"] = p.feature_basis.get("history_games")
                row["sp_current_season_sample"] = p.feature_basis.get("current_season_sample")
            if t is not None:
                row["tp_mean"] = float(t.inference_inputs["mean"])
                row["tp_sd"] = float(t.inference_inputs["sd"])
            rows.append(row)
    return rows


def model_code_hashes() -> dict[str, str]:
    paths = [
        REPO / "src/model_prediction/models/ncaab_spread_v6.py",
        REPO / "src/model_prediction/models/ncaab_total_v6.py",
        REPO / "src/model_prediction/models/basketball.py",
        REPO / "config/models/ncaab-spread-v6.json",
        REPO / "config/models/ncaab-total-v6.json",
        REPO / "src/model_prediction/features/ncaab_scoreboard_stats.py",
    ]
    return {str(p.relative_to(REPO)).replace("\\", "/"): sha256_file(p) for p in paths}


def cmd_project(args: argparse.Namespace) -> int:
    events = load_scoreboard_events()
    n_files = build_stats_root(events)
    print(f"stats root: {n_files} date files, {len(events)} completed events", flush=True)
    days = sorted({et_day(e["start_utc"]) for e in events if e["season_year"] >= 2023})
    chunks = [days[i :: args.workers] for i in range(args.workers)]
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        results = list(ex.map(_worker_project, [(c, 2023, 0) for c in chunks]))
    rows = sorted((r for c in results for r in c), key=lambda r: (r["day"], r["event_id"]))
    with PROJECTIONS.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    meta = {"rows": len(rows), "seconds": round(time.time() - t0, 1), "code_sha256": model_code_hashes()}
    (DATA / "v6_projections.meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("projections", meta, flush=True)
    return 0


def cmd_repro(args: argparse.Namespace) -> int:
    """Exact reproduction gate: history restricted to 2025-26 onward must match the structure review's rebuild."""
    ref = {r["event_id"]: r for r in json.loads(Path(args.wf_rows).read_text(encoding="utf-8"))}
    events = {e["event_id"]: e for e in load_scoreboard_events()}
    days = sorted({r["day"] for r in ref.values() if r["day"] >= "2026-01-10"})[: args.days]
    rows = _worker_project((days, 2026, 2026))
    sp, tp = [], []
    for r in rows:
        eid = r["event_id"]
        if eid not in ref or events[eid]["neutral"]:
            continue
        if "sp" in ref[eid] and "sp_mean" in r:
            sp.append(r["sp_mean"] - ref[eid]["sp"]["mean"])
        if "tp" in ref[eid] and "tp_mean" in r:
            tp.append(r["tp_mean"] - ref[eid]["tp"]["mean"])
    out: dict[str, Any] = {"days": days, "history": "scoreboard-derived, season year >= 2026 only"}
    for label, v in (("spread_mean", sp), ("total_mean", tp)):
        a = np.abs(np.array(v))
        out[label] = {"n": len(v), "mean_abs_diff": float(a.mean()), "max_abs_diff": float(a.max())}
        print(label, out[label])
    REPRO.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------------------------ PIT features
def cmd_features(_args: argparse.Namespace) -> int:
    """Per-game PIT candidate features from history strictly before the game's ET day."""
    events = load_scoreboard_events()
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_day[et_day(e["start_utc"])].append(e)
    last10: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))
    last_day: dict[str, date] = {}
    season_games: dict[tuple[str, int], int] = defaultdict(int)
    league_sum = league_n = 0.0
    out_rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        d = date.fromisoformat(day)
        todays = by_day[day]
        league_mean = league_sum / league_n if league_n else None
        for e in todays:
            if e["season_year"] < 2023:
                continue
            h, a = e["home_team"], e["away_team"]
            tempos = []
            for team in (h, a):
                q = last10[team]
                tempos.append(sum(q) / len(q) if q else None)
            tempo_adj = None
            if league_mean is not None and all(t is not None for t in tempos):
                tempo_adj = (tempos[0] + tempos[1]) / 2.0 - league_mean
            rests = []
            for team in (h, a):
                prev = last_day.get(team)
                rests.append(7 if prev is None else min((d - prev).days, 7))
            gp = [season_games[(h, e["season_year"])], season_games[(a, e["season_year"])]]
            out_rows.append(
                {
                    "event_id": e["event_id"],
                    "day": day,
                    "season_year": e["season_year"],
                    "tempo_adj": tempo_adj,
                    "rest_diff": rests[0] - rests[1],
                    "neutral": 1.0 if e["neutral"] else 0.0,
                    "n_min": float(min(min(gp), 15)),
                    "nond1": 1.0
                    if (e["home_conference_id"] is None or e["away_conference_id"] is None)
                    else 0.0,
                }
            )
        # update state with the day's games AFTER computing every game of the day (no same-day leakage)
        for e in todays:
            fga_h, fta_h = e.get("home_fieldGoalsAttempted"), e.get("home_freeThrowsAttempted")
            fga_a, fta_a = e.get("away_fieldGoalsAttempted"), e.get("away_freeThrowsAttempted")
            if None not in (fga_h, fta_h, fga_a, fta_a):
                attempts = ((fga_h + 0.44 * fta_h) + (fga_a + 0.44 * fta_a)) / 2.0
                last10[e["home_team"]].append(attempts)
                last10[e["away_team"]].append(attempts)
                league_sum += attempts
                league_n += 1
            for team in (e["home_team"], e["away_team"]):
                last_day[team] = d
                season_games[(team, e["season_year"])] += 1
    with FEATURES.open("w", encoding="utf-8") as fh:
        for r in out_rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    print("features", len(out_rows), flush=True)
    return 0


# ------------------------------------------------------------------------------------------ statistics
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def ridge_fit(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    mu, sd = x.mean(axis=0), x.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    z = (x - mu) / sd
    ymean = float(y.mean())
    beta = np.linalg.solve(z.T @ z + RIDGE_ALPHA * np.eye(z.shape[1]), z.T @ (y - ymean))
    return {"mu": mu, "sd": sd, "beta": beta, "ymean": ymean}


def ridge_predict(fit: dict[str, Any], x: np.ndarray) -> np.ndarray:
    return SHRINK * (((x - fit["mu"]) / fit["sd"]) @ fit["beta"])


def _boot_counts(n_dates: int, rng: np.random.Generator) -> np.ndarray:
    draws = rng.integers(0, n_dates, size=(N_BOOT, n_dates))
    return np.stack([np.bincount(d, minlength=n_dates) for d in draws]).astype(float)


def _percentiles(samples: np.ndarray, q: float) -> list[float]:
    s = np.sort(samples[~np.isnan(samples)])
    return [float(s[int(q * (len(s) - 1))]), float(s[int((1 - q) * (len(s) - 1))])]


def _clusters(dates: list[str]) -> tuple[np.ndarray, int]:
    uniq, inv = np.unique(np.array(dates), return_inverse=True)
    return inv, len(uniq)


def window_stats(
    pred: np.ndarray, y: np.ndarray, dates: list[str], base_const: float, k: int, *, side: bool = False
) -> dict[str, Any]:
    """Slope of y on pred (refit), MSE delta vs the line-only baseline, bootstrap bounds (adjusted + plain)."""
    inv, d = _clusters(dates)
    cnt = _boot_counts(d, np.random.default_rng(SEED))
    agg = [
        np.bincount(inv, weights=w, minlength=d) for w in (np.ones_like(y), pred, y, pred * pred, pred * y)
    ]
    n, sx, sy, sxx, sxy = (cnt @ a for a in agg)
    with np.errstate(invalid="ignore", divide="ignore"):
        slopes = (sxy - sx * sy / n) / (sxx - sx * sx / n)
    pm, ym = pred.mean(), y.mean()
    spp = float(((pred - pm) ** 2).sum())
    slope_pt = float(((pred - pm) * (y - ym)).sum() / spp) if spp > 0 else float("nan")
    e_base = (y - base_const) ** 2
    e_mod = (y - base_const - pred) ** 2  # pred is centred on the fit-window mean residual by construction
    n_d = np.bincount(inv, minlength=d).astype(float)
    delta_d = np.bincount(inv, weights=e_base - e_mod, minlength=d)
    delta = (cnt @ delta_d) / (cnt @ n_d)
    adj = 0.025 / k
    out: dict[str, Any] = {
        "n": len(y),
        "dates": d,
        "slope": slope_pt,
        "slope_ci_95": _percentiles(slopes, 0.025),
        "slope_ci_adj": _percentiles(slopes, adj),
        "mse_baseline": float(e_base.mean()),
        "mse_model": float(e_mod.mean()),
        "mse_delta": float((e_base - e_mod).mean()),
        "mse_delta_ci_95": _percentiles(delta, 0.025),
        "mse_delta_ci_adj": _percentiles(delta, adj),
        "oos_r2_vs_baseline": float(1 - e_mod.sum() / e_base.sum()),
        "adj_alpha": adj,
    }
    if side:
        out["side_record"] = side_record(pred, y, inv, d, cnt)
    return out


def side_record(pred: np.ndarray, y: np.ndarray, inv: np.ndarray, d: int, cnt: np.ndarray) -> dict[str, Any]:
    take = (np.abs(pred) >= MIN_GAP) & (y != 0)
    win = take & ((pred > 0) == (y > 0))
    pushes = int(((np.abs(pred) >= MIN_GAP) & (y == 0)).sum())
    if not take.any():  # a no-skill model rarely reaches the threshold: no bets, nothing to bootstrap
        return {"bets": 0, "wins": 0, "losses": 0, "pushes_excluded": pushes}
    w_d = np.bincount(inv, weights=win.astype(float), minlength=d)
    t_d = np.bincount(inv, weights=take.astype(float), minlength=d)
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = (cnt @ w_d) / (cnt @ t_d)
    ci = _percentiles(rate, 0.025)
    wins, takes = int(win.sum()), int(take.sum())
    hit = wins / takes if takes else float("nan")
    out: dict[str, Any] = {
        "bets": takes,
        "wins": wins,
        "losses": takes - wins,
        "pushes_excluded": pushes,
        "hit_rate_pct": 100 * hit if takes else None,
        "hit_rate_ci_95_pct": [100 * ci[0], 100 * ci[1]],
    }
    for label, price in RULE["price_assumptions_pct"].items():
        p = price / 100.0
        out[f"roi_pct_at_{label}"] = 100 * (hit / p - 1) if takes else None
        out[f"roi_ci_95_pct_at_{label}"] = [100 * (ci[0] / p - 1), 100 * (ci[1] / p - 1)]
    return out


def power_statement() -> dict[str, Any]:
    z_a, z_b = 1.6449, 0.8416  # one-sided 5% test, 80% power
    out = {}
    for true_rate in (0.53, 0.54, 0.55, 0.56):
        delta = true_rate - 0.524
        out[f"{true_rate:.0%}"] = math.ceil((z_a + z_b) ** 2 * 0.25 / delta**2)
    return {"bets_needed_80pct_power_vs_52.4_break_even": out}


# ------------------------------------------------------------------------------------------ data join
def load_joined(shuffle_seed: int | None = None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows per market: x_v6, line, features, y; the ONLY place outcomes meet lines."""
    events = {e["event_id"]: e for e in load_scoreboard_events()}
    proj = {r["event_id"]: r for r in _read_jsonl(PROJECTIONS)}
    feats = {r["event_id"]: r for r in _read_jsonl(FEATURES)}
    lines: dict[str, dict[str, Any]] = {}
    for rec in _read_jsonl(LINES):
        lines[str(rec["event_id"])] = rec
    espn_rules = {rec.get("rule_sha256") for rec in lines.values()}
    rows: dict[str, list[dict[str, Any]]] = {m: [] for m in MARKETS}
    skip: dict[str, int] = defaultdict(int)
    for eid, rec in lines.items():
        ev, pr, ft = events.get(eid), proj.get(eid), feats.get(eid)
        if ev is None or ev["season_year"] < 2023:
            continue
        if pr is None or ft is None:
            skip["no_projection_or_features"] += 1
            continue
        sel = rec["selected"]
        margin = ev["home_score"] - ev["away_score"]
        total = ev["home_score"] + ev["away_score"]
        base = {
            "event_id": eid,
            "date": pr["day"],
            "season_year": ev["season_year"],
            "tempo": ft["tempo_adj"],
            "rest": float(ft["rest_diff"]),
            "neutral": ft["neutral"],
            "n_min": ft["n_min"],
            "nond1": ft["nond1"],
        }
        if sel["home_spread_line"] is not None and "sp_mean" in pr:
            implied = -float(sel["home_spread_line"])
            row = {**base, "line": implied, "x": pr["sp_mean"] - implied, "y": margin - implied}
            if sel["open_home_spread_line"] is not None:
                row["move"] = implied - (-float(sel["open_home_spread_line"]))
            rows["spread"].append(row)
        else:
            skip["spread_no_line_or_projection"] += 1
        if sel["total_line"] is not None and "tp_mean" in pr:
            line = float(sel["total_line"])
            row = {**base, "line": line, "x": pr["tp_mean"] - line, "y": total - line}
            if sel["open_total_line"] is not None:
                row["move"] = line - float(sel["open_total_line"])
            rows["total"].append(row)
        else:
            skip["total_no_line_or_projection"] += 1
    if shuffle_seed is not None:  # plumbing self-test: break the outcome link, keep every other column
        rng = np.random.default_rng(shuffle_seed)
        for m in MARKETS:
            ys = np.array([r["y"] for r in rows[m]])
            rng.shuffle(ys)
            for r, v in zip(rows[m], ys, strict=True):
                r["y"] = float(v)
    meta = {
        "espn_rule_sha256_values": sorted(str(v) for v in espn_rules),
        "lines_sha256": sha256_file(LINES),
        "scoreboard_sha256": sha256_file(SCOREBOARD),
        "projections_sha256": sha256_file(PROJECTIONS),
        "features_sha256": sha256_file(FEATURES),
        "skipped": dict(skip),
        "line_records": len(lines),
        "line_records_with_line": sum(1 for r in lines.values() if r["no_line_reason"] is None),
    }
    return rows, meta


MODEL_COLUMNS = {
    "m0_v6": ["x"],
    "m_tempo": ["x", "tempo"],
    "m_rest": ["x", "rest"],
    "m_neutral": ["x", "neutral"],
    "m_sample": ["x", "n_min", "x_early"],
    "m_nond1": ["x", "nond1", "x_nond1"],
    "m_joint": ["x", "tempo", "rest", "neutral", "n_min", "x_early", "nond1", "x_nond1"],
    "move_only": ["move"],
    "m_move": ["x", "move"],
    "m_joint_move": ["x", "tempo", "rest", "neutral", "n_min", "x_early", "nond1", "x_nond1", "move"],
}
BASE_FAMILY = ["m0_v6", "m_tempo", "m_rest", "m_neutral", "m_sample", "m_nond1", "m_joint"]


def window_of(season_year: int) -> str | None:
    for name, years in WINDOW_SEASONS.items():
        if season_year in years:
            return name
    return None


def row_values(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "x_early": r["x"] * (1.0 if r["n_min"] < 8 else 0.0), "x_nond1": r["x"] * r["nond1"]}


def design(rows: list[dict[str, Any]], cols: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Feature matrix, target and date clusters for the rows that carry every requested column."""
    sel = [v for v in (row_values(r) for r in rows) if all(v.get(c) is not None for c in cols)]
    x = np.array([[float(v[c]) for c in cols] for v in sel], dtype=float).reshape(len(sel), len(cols))
    return x, np.array([v["y"] for v in sel], dtype=float), [v["date"] for v in sel]


def run_market(market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_win = {w: [r for r in rows if window_of(r["season_year"]) == w] for w in WINDOW_SEASONS}
    open_fit = sum(1 for r in by_win["fit"] if r.get("move") is not None)
    open_val = sum(1 for r in by_win["validation"] if r.get("move") is not None)
    movement_in_family = open_fit >= MOVE_MIN_FIT and open_val >= MOVE_MIN_VAL
    family = list(BASE_FAMILY) + (["m_move", "m_joint_move"] if movement_in_family else [])
    k = len(family)
    result: dict[str, Any] = {
        "family": family,
        "k": k,
        "window_n": {w: len(v) for w, v in by_win.items()},
        "opening_line_rows": {w: sum(1 for r in v if r.get("move") is not None) for w, v in by_win.items()},
        "movement_in_family": movement_in_family,
        "models": {},
    }
    fits: dict[str, dict[str, Any]] = {}
    for name in family:
        cols = MODEL_COLUMNS[name]
        xf, yf, _ = design(by_win["fit"], cols)
        xv, yv, dv = design(by_win["validation"], cols)
        fit = ridge_fit(xf, yf)
        fits[name] = fit
        base_const = fit["ymean"]
        # the prediction is centred: the fit-window mean residual is the intercept (== the baseline)
        val = window_stats(ridge_predict(fit, xv), yv, dv, base_const, k, side=True)
        crit_a = val["slope_ci_adj"][0] > 0
        crit_b = val["mse_delta_ci_adj"][0] > 0
        uses_move = (
            "move" in cols
        )  # criterion (c): a closing-based feature can never be an accepted pick model
        result["models"][name] = {
            "v6_coefficient_negative_fade": bool(fit["beta"][0] < 0),
            "uses_movement": uses_move,
            "columns": cols,
            "n_fit": len(yf),
            "coefficients_standardised": {c: float(b) for c, b in zip(cols, fit["beta"], strict=True)},
            "intercept_baseline": base_const,
            "validation": val,
            "criteria": {"a_slope_adj_lower_gt_0": bool(crit_a), "b_mse_delta_adj_lower_gt_0": bool(crit_b)},
            "accepted_on_validation": bool(crit_a and crit_b and not uses_move),
        }
    accepted = [n for n in family if result["models"][n]["accepted_on_validation"]]
    result["accepted_models"] = accepted
    result["raw_v6_side_record_fit_and_validation"] = {
        w: raw_v6_side(by_win[w]) for w in ("fit", "validation")
    }
    # exploratory movement diagnostic when movement is not in the family (never affects acceptance)
    if not movement_in_family:
        result["movement_diagnostic"] = movement_diagnostic(rows)
    if not accepted:
        result["verdict"] = (
            "NO PICK: no model passes both validation criteria; locked holdout and live check not scored"
        )
        result["locked_windows"] = "NOT SCORED"
        return result
    best = max(accepted, key=lambda n: result["models"][n]["validation"]["mse_delta"])
    result["selected_model"] = best
    result["locked_windows"] = score_locked_windows(best, by_win, fits[best], k)
    lw = result["locked_windows"]
    holdout_ok = (
        isinstance(lw["holdout"], dict)
        and lw["holdout"]["slope_ci_95"][0] > 0
        and lw["holdout"]["mse_delta_ci_95"][0] > 0
    )
    result["verdict"] = (
        f"ACCEPTED on validation and confirmed on the locked holdout: {best}"
        if holdout_ok
        else f"NO PICK: {best} passed validation but failed the locked holdout"
    )
    return result


def score_locked_windows(
    name: str, by_win: dict[str, list[dict[str, Any]]], fit: dict[str, Any], k: int
) -> dict[str, Any]:
    cols = MODEL_COLUMNS[name]
    out: dict[str, Any] = {}
    for w in ("holdout", "live"):
        if w == "live":  # the live check is scored only after the holdout passes the plain 95% bounds
            prior = out["holdout"]
            if not (
                isinstance(prior, dict) and prior["slope_ci_95"][0] > 0 and prior["mse_delta_ci_95"][0] > 0
            ):
                out[w] = "NOT SCORED (holdout did not pass)"
                continue
        x, y, d = design(by_win[w], cols)
        if len(y) == 0:
            out[w] = "NO ROWS"
            continue
        out[w] = window_stats(ridge_predict(fit, x), y, d, fit["ymean"], k, side=True)
    return out


def movement_diagnostic(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Exploratory: among rows that carry an opening line, split by date (60/20/20) and test movement.

    Labelled diagnostic: a movement-only ridge (no v6 column, so the locked holdout is not spent for v6); it
    spends part of the 2025-26 and 2026-27 windows for this one closing-based feature and cannot feed
    acceptance (movement cannot serve a decision)."""
    sub = sorted((r for r in rows if r.get("move") is not None), key=lambda r: r["date"])
    if len(sub) < 600:
        return {"note": f"only {len(sub)} rows with an opening line; too few to test"}
    dates = sorted({r["date"] for r in sub})
    c1, c2 = dates[int(0.6 * len(dates)) - 1], dates[int(0.8 * len(dates)) - 1]
    part = {
        "fit": [r for r in sub if r["date"] <= c1],
        "validation": [r for r in sub if c1 < r["date"] <= c2],
        "test": [r for r in sub if r["date"] > c2],
    }
    out: dict[str, Any] = {
        "cutoffs": [c1, c2],
        "n": {k: len(v) for k, v in part.items()},
        "seasons_present": sorted({r["season_year"] for r in sub}),
    }
    for name in ("move_only",):
        cols = MODEL_COLUMNS[name]
        xf, yf, _ = design(part["fit"], cols)
        fit = ridge_fit(xf, yf)
        blk = {}
        for w in ("validation", "test"):
            x, y, d = design(part[w], cols)
            blk[w] = window_stats(ridge_predict(fit, x), y, d, fit["ymean"], 2)
        out[name] = {
            "coefficients_standardised": {c: float(b) for c, b in zip(cols, fit["beta"], strict=True)},
            **blk,
        }
    return out


def raw_v6_side(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive only: follow the raw v6-minus-line gap (|gap| >= 1) on a non-locked window."""
    x = np.array([r["x"] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=float)
    inv, d = _clusters([r["date"] for r in rows])
    cnt = _boot_counts(d, np.random.default_rng(SEED))
    return side_record(x, y, inv, d, cnt)


def descriptive_raw(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Outcome-based descriptive checks that never touch acceptance: orientation sanity and bias."""
    out: dict[str, Any] = {}
    for y in (2023, 2024, 2025, 2026, 2027):
        sub = [r for r in rows if r["season_year"] == y]
        if sub:
            out[str(y)] = {
                "n": len(sub),
                "mean_residual": float(np.mean([r["y"] for r in sub])),
                "rmse_vs_line": float(np.sqrt(np.mean([r["y"] ** 2 for r in sub]))),
            }
    return out


def favourite_check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Orientation sanity (outcome-using, kept out of the fetch script): the favourite by line wins straight up."""
    per: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["line"] == 0:
            continue
        # y = margin - implied  =>  margin = y + line (implied home margin)
        margin = r["y"] + r["line"]
        fav_home = r["line"] > 0
        s = per.setdefault(str(r["season_year"]), {"n": 0, "fav_won": 0, "dog_won": 0, "tie": 0})
        s["n"] += 1
        if margin == 0:
            s["tie"] += 1
        elif (margin > 0) == fav_home:
            s["fav_won"] += 1
        else:
            s["dog_won"] += 1
    for s in per.values():
        decided = s["fav_won"] + s["dog_won"]
        s["fav_win_pct"] = round(100 * s["fav_won"] / decided, 2) if decided else None
    return per


def pipeline_gate() -> dict[str, Any]:
    """Exact reproduction of the structure review's local-history rebuild (written by the repro command)."""
    if not REPRO.exists():
        return {"status": "repro command not run"}
    return json.loads(REPRO.read_text(encoding="utf-8"))


def local_history_overlap() -> dict[str, Any]:
    if not LOCAL_HISTORY.exists():
        return {"status": "local history file not available"}
    local = {}
    for r in _read_jsonl(LOCAL_HISTORY):
        local[str(r["event_id"])] = (r["home_score"], r["away_score"])
    sb = {
        e["event_id"]: (e["home_score"], e["away_score"])
        for e in load_scoreboard_events()
        if e["season_year"] >= 2026
    }
    both = set(local) & set(sb)
    diff = sum(1 for e in both if local[e] != sb[e])
    return {
        "local_events": len(local),
        "scoreboard_events_2026_on": len(sb),
        "in_both": len(both),
        "score_mismatches": diff,
        "only_local": len(set(local) - set(sb)),
        "only_scoreboard": len(set(sb) - set(local)),
    }


# ------------------------------------------------------------------------------------------ run + render
def build_report(shuffle_seed: int | None) -> dict[str, Any]:
    rows, meta = load_joined(shuffle_seed)
    report: dict[str, Any] = {
        "rule": RULE,
        "rule_sha256": RULE_SHA256,
        "script_sha256": sha256_file(Path(__file__)),
        "selftest_label_shuffled": shuffle_seed is not None,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": meta,
        "model_code_sha256": json.loads((DATA / "v6_projections.meta.json").read_text(encoding="utf-8"))[
            "code_sha256"
        ],
        "power": power_statement(),
        "pipeline_gate": pipeline_gate(),
        "local_history_overlap": local_history_overlap(),
        "orientation_favourite_wins": {m: favourite_check(rows[m]) for m in MARKETS},
        "residual_by_season": {m: descriptive_raw(rows[m]) for m in MARKETS},
        "markets": {m: run_market(m, rows[m]) for m in MARKETS},
    }
    return report


def _ci(c: list[float], nd: int = 3) -> str:
    return f"[{c[0]:+.{nd}f}, {c[1]:+.{nd}f}]"


def _side_text(s: dict[str, Any]) -> str:
    if not s["bets"]:
        return f"no bets (|prediction| never reached {MIN_GAP:g} point), {s['pushes_excluded']} pushes"
    return (
        f"{s['wins']}-{s['losses']} ({s['pushes_excluded']} pushes), hit {s['hit_rate_pct']:.1f}% CI "
        f"[{s['hit_rate_ci_95_pct'][0]:.1f}%, {s['hit_rate_ci_95_pct'][1]:.1f}%], ROI at 52.4% "
        f"{s['roi_pct_at_break_even_implied']:+.1f}% {_ci(s['roi_ci_95_pct_at_break_even_implied'], 1)}, at 53.5% "
        f"{s['roi_pct_at_executable_ask']:+.1f}% {_ci(s['roi_ci_95_pct_at_executable_ask'], 1)}"
    )


def render_md(rep: dict[str, Any]) -> str:
    L: list[str] = []
    add = L.append
    add("# NCAAB market-anchored residual test on ESPN closing lines (quick-261115-1m8, Tier 3)")
    add("")
    add(
        f"Pinned rule `{RULE['id']}` sha256 `{rep['rule_sha256']}` (canonical JSON of the rule block in "
        f"scripts/ncaab_residual.py, computed before any outcome was joined). Script sha256 `{rep['script_sha256']}`. "
        f"ESPN lines sha256 `{rep['inputs']['lines_sha256']}` (rule stamp {rep['inputs']['espn_rule_sha256_values']}), "
        f"scoreboard sha256 `{rep['inputs']['scoreboard_sha256']}`."
    )
    add("")
    add(
        "> CAVEATS. (1) The line is a CLOSING benchmark with no observed_at: this tests whether anything we can "
        "compute before tip-off explains what the closing market missed, not whether a model could have beaten a "
        "decision-time quote. (2) Opening-to-closing movement is closing-based and cannot serve a live decision; it "
        "is reported but can never be an accepted pick feature. (3) POWER: the archive supports detecting roughly a "
        "54% cover rate or better only (table below); a true edge of 53% would need about 43,000 bets to confirm. "
        "(4) Executable prices differ from the 52.4% break-even; ROI is shown at 52.4% and 53.5%. (5) PIT v6 "
        "projections for 2022-25 use the v6 served parameters on scoreboard-derived history; box-stat tempo and AP "
        "ranks come from the scoreboard feed as served at fetch time. (6) The served v6 parameters were selected on "
        "2025-26 data, so the 'locked holdout' is NOT out-of-sample for v6 itself. (7) The line source differs by "
        "window: a multi-book median in 2022-24, ESPN BET in 2024-25, DraftKings in 2025-26 on, so a relationship "
        "can differ across windows because the benchmark changed. (8) The conference map used by the v6 models is "
        "today's, applied to 2022-25 games. No promotion decision follows from this."
    )
    add("")
    add("## Verdict")
    add("")
    if rep["selftest_label_shuffled"]:
        add("**SELFTEST (outcome labels shuffled): this is a plumbing check, not a result.**")
        add("")
    for m in MARKETS:
        add(f"- **{m}**: {rep['markets'][m]['verdict']}")
    add("")
    add("## Power")
    add("")
    for k, v in rep["power"]["bets_needed_80pct_power_vs_52.4_break_even"].items():
        add(
            f"- true cover rate {k}: about {v:,} bets for 80% power against the 52.4% break-even (one-sided 5%)"
        )
    add("")
    add("## Pipeline checks")
    add("")
    add(f"- Local history overlap: {rep['local_history_overlap']}")
    add(
        f"- Exact projection reproduction (history restricted to 2025-26 on, non-neutral): {rep['pipeline_gate']}"
    )
    add(
        f"- Skipped rows: {rep['inputs']['skipped']}; line records {rep['inputs']['line_records']} "
        f"({rep['inputs']['line_records_with_line']} with a line)"
    )
    add("")
    add(
        "Orientation check (outcome-using, kept outside the fetch script): favourite by closing line wins straight up"
    )
    add("")
    add("| Market cohort | Season year | n | Favourite won | Underdog won | Tie | Favourite win % |")
    add("|---|---|---|---|---|---|---|")
    for m in MARKETS:
        for y, s in sorted(rep["orientation_favourite_wins"][m].items()):
            add(
                f"| {m} | {y} | {s['n']} | {s['fav_won']} | {s['dog_won']} | {s['tie']} | {s['fav_win_pct']} |"
            )
    add("")
    add("Mean residual (actual - line) and RMSE vs the line by season")
    add("")
    for m in MARKETS:
        for y, s in rep["residual_by_season"][m].items():
            add(
                f"- {m} {y}: n {s['n']}, mean residual {s['mean_residual']:+.3f}, RMSE {s['rmse_vs_line']:.2f}"
            )
    add("")
    add("## Rule")
    add("")
    add("```json")
    add(json.dumps(RULE, indent=2))
    add("```")
    add("")
    for m in MARKETS:
        r = rep["markets"][m]
        add(f"## {m.capitalize()}s")
        add("")
        add(
            f"Window n {r['window_n']}; rows with an opening line {r['opening_line_rows']}; movement models in the "
            f"family: {r['movement_in_family']}; family size K = {r['k']} (adjusted alpha {0.025 / r['k']:.5f} per tail)."
        )
        add("")
        add(
            "| Model | n fit | slope on prediction (val) | adj CI | MSE baseline / model | MSE delta | adj CI | (a) | (b) | Accepted |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|")
        for name in r["family"]:
            mm = r["models"][name]
            v = mm["validation"]
            c = mm["criteria"]
            add(
                f"| {name} | {mm['n_fit']} | {v['slope']:+.3f} | {_ci(v['slope_ci_adj'])} | "
                f"{v['mse_baseline']:.2f} / {v['mse_model']:.2f} | {v['mse_delta']:+.4f} | {_ci(v['mse_delta_ci_adj'], 4)} | "
                f"{'pass' if c['a_slope_adj_lower_gt_0'] else 'fail'} | "
                f"{'pass' if c['b_mse_delta_adj_lower_gt_0'] else 'fail'} | {mm['accepted_on_validation']} |"
            )
        add("")
        add("Standardised ridge coefficients (fit window, before the 0.5 shrink):")
        add("")
        for name in r["family"]:
            add(f"- {name}: {r['models'][name]['coefficients_standardised']}")
        add("")
        add(
            "Descriptive side records on the VALIDATION window (|0.5 x ridge prediction| >= 1, pushes excluded; not an acceptance input):"
        )
        add("")
        for name in r["family"]:
            add(f"- {name}: {_side_text(r['models'][name]['validation']['side_record'])}")
        add("")
        add("Descriptive: following the RAW v6-minus-line gap (|gap| >= 1) on the non-locked windows:")
        add("")
        for w, s in r["raw_v6_side_record_fit_and_validation"].items():
            add(f"- {w}: {_side_text(s)}")
        add("")
        fades = [n for n in r["family"] if r["models"][n]["v6_coefficient_negative_fade"]]
        add(f"Models whose v6 coefficient is negative (a fade-v6 model, not v6 skill): {fades or 'none'}.")
        add("")
        if "movement_diagnostic" in r:
            add(
                f"Movement diagnostic (exploratory, cannot affect acceptance): `{json.dumps(r['movement_diagnostic'])[:3000]}`"
            )
            add("")
        if isinstance(r["locked_windows"], dict):
            add(f"Selected model {r['selected_model']}: locked windows scored once.")
            for w, blk in r["locked_windows"].items():
                if isinstance(blk, dict):
                    add(
                        f"- {w}: n {blk['n']}, slope {blk['slope']:+.3f} {_ci(blk['slope_ci_95'])}, MSE delta "
                        f"{blk['mse_delta']:+.4f} {_ci(blk['mse_delta_ci_95'], 4)}, side {_side_text(blk['side_record'])}"
                    )
                else:
                    add(f"- {w}: {blk}")
        else:
            add(
                f"Locked windows: {r['locked_windows']} (holdout 2025-26 and live check 2026-27 kept unspent)."
            )
        add("")
    return "\n".join(L) + "\n"


def cmd_run(args: argparse.Namespace) -> int:
    print("pinned rule", RULE["id"], "sha256", RULE_SHA256, flush=True)
    shuffle = 12345 if args.command == "selftest" else None
    report = build_report(shuffle)
    if shuffle is None:
        OUT.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=float), encoding="utf-8")
        OUT_MD.write_text(render_md(report), encoding="utf-8")
        print("wrote", OUT_JSON, OUT_MD)
    for m in MARKETS:
        print(m, report["markets"][m]["verdict"])
        for name in report["markets"][m]["family"]:
            v = report["markets"][m]["models"][name]["validation"]
            print(
                "  ",
                name,
                "slope",
                round(v["slope"], 3),
                [round(z, 3) for z in v["slope_ci_adj"]],
                "mse delta",
                round(v["mse_delta"], 4),
                [round(z, 4) for z in v["mse_delta_ci_adj"]],
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    proj = sub.add_parser("project")
    proj.add_argument("--workers", type=int, default=3)
    proj.set_defaults(func=cmd_project)
    sub.add_parser("features").set_defaults(func=cmd_features)
    repro = sub.add_parser("repro")
    repro.add_argument("--wf-rows", required=True, help="json rows of the local-history reference rebuild")
    repro.add_argument("--days", type=int, default=6)
    repro.set_defaults(func=cmd_repro)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("selftest").set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
