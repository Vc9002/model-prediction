"""Fit, select and evaluate cfb-structural-v7 (quick-261121-v7, NCAAF overhaul plan step 4).

v7 = point-in-time opponent-adjusted play-by-play ratings (``features/cfb_features_v7.py``, the single rating core
shared with serving) -> a linear team-points model -> margin and total -> a keyed discrete margin pmf (v6.2's
key-number idea, refit here) and a discretised normal total pmf.

Windows are v5's artifact / recheck / v6.2 boundaries: train <= 2022-11-11, validation 2022-11-12..2024-11-14,
locked holdout >= 2024-11-15. Everything is fit on train; one candidate is chosen on validation by the pinned rule;
``holdout`` reads the holdout rows once, only after ``pin`` wrote the rule file whose sha256 it checks, and refuses
a second read. ESPN closing lines never enter a v7 fit or selection: only the holdout market block reads them.
Every ESPN lined window 2017-2025 was read before (v6, v6.1, v6.2, the v5 recheck), so market-relative numbers are
DESIGN-INFORMED, and this holdout was already read for v5 / v6 / v6.1 / v6.2 (it is unread only for v7).

Subcommands (worktree root, PYTHONPATH=src, OPENBLAS_NUM_THREADS=1):
  prep     spine + per-season finals + prior parameters (rho, lambda) from train seasons
  walk     point-in-time features for every scored game, per grid point (holdout rows to separate files)
  fit      stage-2 points model on train, validation scores, selection, pmf fit -> outputs/ncaaf/cfb_v7_spec.json
  pin      write outputs/ncaaf/cfb_v7_rule.json (selection rule, gate, CFBD-arm rule) and print its sha256
  holdout  one read: v5 reproduction, v7 vs v5 vs consensus on the same games -> cfb_v7_report.{json,md}
  artifact (only after a PASS) config/models/cfb-structural-v7.json + parity fixture
Percentages and points only; no American odds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtr

REPO = Path(__file__).resolve().parents[1]
MAIN = Path(r"E:\MP")  # read-only inputs (untracked research data in the main checkout)
PBP = MAIN / "data" / "research" / "ncaaf_v7" / "team_game_pbp.jsonl"
SNAPSHOT = MAIN / "data" / "research" / "ncaaf_v6" / "history_snapshot.jsonl"
ESPN_LINES = MAIN / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
CFBD_DIR = MAIN / "data" / "research" / "ncaaf_v7" / "cfbd"
WORK = REPO / "data" / "research" / "ncaaf_v7_work"  # untracked; survives the session
V5_ROWS = (
    WORK / "v5_replay_rows.jsonl"
)  # copy of the v5 recheck replay cache (sha256 prefix 68caa21f0fc0f4e3)
V5_ROWS_SHA_PREFIX = "68caa21f0fc0f4e3"
HOLDOUT_MARK = WORK / "holdout_read.json"
OUT = REPO / "outputs" / "ncaaf"
PRIOR_JSON = OUT / "cfb_v7_prior.json"
SPEC = OUT / "cfb_v7_spec.json"
RULE_PATH = OUT / "cfb_v7_rule.json"
REPORT_JSON = OUT / "cfb_v7_report.json"
REPORT_MD = OUT / "cfb_v7_report.md"
FIXTURE = OUT / "cfb_v7_parity_fixture.json"
ARTIFACT = REPO / "config" / "models" / "cfb-structural-v7.json"

TRAIN_END = "2022-11-11"
VAL_END = "2024-11-14"
PRIOR_FIT_LAST_SEASON = 2021  # finals of seasons <= 2021 end before TRAIN_END (bowls in Jan 2022)
MIN_GAMES_FOR_PRIOR_FIT = 6
LAMBDA_MULTIPLIERS = (0.25, 0.5, 1.0, 2.0)  # 0.25 added after 0.5 won at the grid edge (validation only)
HALF_LIVES: tuple[float | None, ...] = (None, 45.0)
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "full": ("e_sr", "e_ypp", "e_exp", "e_ppd", "e_pts", "e_plays", "e_to"),
    "eff": ("e_sr", "e_ypp", "e_exp", "e_ppd", "e_plays", "e_to"),
    "pts": ("e_pts",),
}
MARGIN_SUPPORT = 130
TOTAL_SUPPORT = 180
KEY_MAX = 28
RECENTRE_ITERATIONS = 4
SD_SCALE = 10.0
SD_TOTAL_CENTRE = 55.0
KEY_RIDGE = 10.0  # v6.2's validation-selected key-multiplier ridge, reused (not re-selected here)
TOTAL_BUCKETS = ((0.0, 45.0), (45.0, 52.0), (52.0, 60.0), (60.0, 999.0))
EARLY_GAMES = 4  # "early" = either team has fewer than 4 admitted current-season games
BREAK_EVEN = 0.524  # flat spread / total price, as a probability
WIN_PAYOUT = (1.0 - BREAK_EVEN) / BREAK_EVEN
FAV_BUCKETS = ((0, 3, "0-3"), (3, 7, "3-7"), (7, 14, "7-14"), (14, 21, "14-21"), (21, 999, "21+"))
N_SIM = 200_000

sys.path.insert(0, str(REPO / "src"))
from model_prediction.features import cfb_features_v7 as core


# ================================================================================================ helpers
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.strip()


def window_of(date: str) -> str:
    return "train" if date <= TRAIN_END else ("validation" if date <= VAL_END else "holdout")


def week_band(week: int) -> str:
    if week >= 15:
        return "postseason"
    return "weeks_0_4" if week <= 4 else "weeks_5_14"


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


def combo_key(mult: float, half_life: float | None, arm: str = "core") -> str:
    base = f"m{mult:g}_hl{'none' if half_life is None else f'{half_life:g}'}"
    return base if arm == "core" else f"{arm}_{base}"


# ================================================================================================ spine
def load_team_games() -> tuple[list[core.TeamGame], dict[str, dict[str, Any]]]:
    """pbp rows plus score-only rows for snapshot games without play-by-play; and a per-event fact table."""
    from model_prediction.domain import parse_utc

    rows: list[core.TeamGame] = []
    facts: dict[str, dict[str, Any]] = {}
    with PBP.open(encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            tg = core.team_game_from_row(r)
            rows.append(tg)
            ev = facts.setdefault(
                tg.event_id,
                {
                    "kickoff": tg.kickoff,
                    "season": tg.season,
                    "neutral": bool(r.get("neutral_site")),
                    "pbp": True,
                },
            )
            if r.get("is_home"):
                ev["home_id"] = tg.team_id
                ev["away_id"] = tg.opponent_id
            ev["game_class"] = r.get("game_class")
    with SNAPSHOT.open(encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            s = json.loads(ln)
            eid = str(s["event_id"])
            if (
                eid in facts
                or s.get("is_forfeit")
                or s.get("home_score") is None
                or s.get("away_score") is None
            ):
                continue
            kick = parse_utc(s["start_utc"])
            season = season_of(kick)
            neutral = bool(s.get("neutral"))
            facts[eid] = {
                "kickoff": kick,
                "season": season,
                "neutral": neutral,
                "pbp": False,
                "home_id": str(s["home_id"]),
                "away_id": str(s["away_id"]),
                "game_class": s.get("game_class"),
            }
            for team, opp, pts, home in (
                (s["home_id"], s["away_id"], s["home_score"], True),
                (s["away_id"], s["home_id"], s["away_score"], False),
            ):
                rows.append(
                    core.TeamGame(
                        event_id=eid,
                        season=season,
                        kickoff=kick,
                        team_id=str(team),
                        opponent_id=str(opp),
                        home_sign=0 if neutral else (1 if home else -1),
                        game_class=s.get("game_class"),
                        values={k: (float(pts) if k == "pts" else None) for k in core.METRICS},
                    )
                )
    rows.sort(key=lambda r: (r.kickoff, r.event_id, -r.home_sign))
    return rows, facts


def load_eval_rows() -> list[dict[str, Any]]:
    """The v5 replay rows (every completed FBS-involved game, v5's PIT raw projection) = the scored game set."""
    if not file_sha256(V5_ROWS).startswith(V5_ROWS_SHA_PREFIX):
        raise SystemExit("v5 replay row cache sha256 mismatch")
    rows = [json.loads(x) for x in V5_ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    return rows


# ================================================================================================ prep
def finals_residual_var(
    rows: list[core.TeamGame], finals: core.RatingState, classes: dict[tuple[str, int], str], k: str
) -> tuple[float, int]:
    m = finals.metrics[k]
    sq, n = 0.0, 0
    for r in rows:
        y = r.values.get(k)
        if y is None or r.season != finals.season:
            continue
        pred = (
            m.mu
            + m.c_off[classes.get((r.team_id, r.season), core.FCS)]
            + m.off[r.team_id]
            + m.c_def[classes.get((r.opponent_id, r.season), core.FCS)]
            + m.deff[r.opponent_id]
            + m.home * r.home_sign
        )
        sq += (y - pred) ** 2
        n += 1
    return sq, n


ARMS: dict[str, dict[str, tuple[str, ...]]] = {
    # covariates entering the team prior centre (offence / defence); "ret_x_prev" = z_ret * last-season level.
    "core": {"off": (), "def": ()},
    "cfbd": {"off": ("ret", "tal", "rec", "ret_x_prev"), "def": ("tal", "rec")},
    "cfbd_portal": {"off": ("ret", "tal", "rec", "ret_x_prev", "pout"), "def": ("tal", "rec", "pout")},
}
CFBD_PRIORS = CFBD_DIR / "team_season_priors.jsonl"
CFBD_LINES = CFBD_DIR / "lines.jsonl"
PRIOR_ARMS_JSON = OUT / "cfb_v7_prior_arms.json"


def load_cfbd_covariates() -> dict[int, dict[str, dict[str, float]]]:
    """season -> ESPN team id -> standardised preseason covariates (missing -> absent, read as 0 = the mean).

    ret = returning_off_ppa_pct (offence only; CFBD has no defensive figure), tal = talent_prev_season (the
    current-season composite has no known publication date), rec = recruiting_points_4yr_weighted, pout =
    portal_out_stars_sum (point-in-time on transferDate; 2021+ only). portal IN is never used: its destination is
    as-of-fetch and leaks post-kickoff commitments. Every CFBD value is current-revised (as_published_verified
    false); 2026 was fetched mid-season. The standardisation is the rating core's (shared with serving)."""
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with CFBD_PRIORS.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                by_season[int(r["season"])].append(r)
    return {season: core.standardise_covariates(rows) for season, rows in by_season.items()}


def estimate_prior(
    rows: list[core.TeamGame],
    classes: dict[tuple[str, int], str],
    finals: dict[int, core.RatingState],
    cov: dict[int, dict[str, dict[str, float]]] | None = None,
    arm: str = "core",
) -> dict[str, Any]:
    """rho (+ CFBD covariate gammas for the CFBD arms) by least squares of this season's class-relative final on
    last season's re-expressed final (and z covariates), through the origin, train seasons only; lambda = sigma^2 /
    tau^2. sigma^2 is the per-game residual variance of the finals fit; tau^2 is the variance of the prior's error
    minus the finals' own sampling variance (sigma^2 / games), floored at 5% of the raw variance."""
    out: dict[str, Any] = {
        "arm": arm,
        "rho_off": {},
        "rho_def": {},
        "lam_off": {},
        "lam_def": {},
        "cov_off": {},
        "cov_def": {},
        "diag": {},
    }
    seasons = [s for s in sorted(finals) if s <= PRIOR_FIT_LAST_SEASON]
    for k in core.METRICS:
        sq = n = 0.0
        for s in seasons:
            a, b = finals_residual_var(rows, finals[s], classes, k)
            sq += a
            n += b
        sigma2 = sq / max(1.0, n)
        for side, attr in (("off", "off"), ("def", "deff")):
            names = ARMS[arm][side]
            xs, ys, gs = [], [], []
            for s in seasons:
                if s - 1 not in finals:
                    continue
                prev, now = finals[s - 1], finals[s]
                mp, mn = prev.metrics[k], now.metrics[k]
                cp = mp.c_off if side == "off" else mp.c_def
                zs = (cov or {}).get(s, {})
                for t, val in getattr(mn, attr).items():
                    if (
                        t not in getattr(mp, attr)
                        or now.games_played.get(t, 0) < MIN_GAMES_FOR_PRIOR_FIT
                        or prev.games_played.get(t, 0) < MIN_GAMES_FOR_PRIOR_FIT
                    ):
                        continue
                    cls_prev = classes.get((t, s - 1), core.FCS)
                    cls_now = classes.get((t, s), cls_prev)
                    level = getattr(mp, attr)[t] + cp[cls_prev] - cp[cls_now]
                    z = zs.get(t, {})
                    xs.append(
                        [level]
                        + [z.get("ret", 0.0) * level if c == "ret_x_prev" else z.get(c, 0.0) for c in names]
                    )
                    ys.append(val)
                    gs.append(now.games_played[t])
            x, y = np.array(xs), np.array(ys)
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            rho = float(beta[0])
            resid = y - x @ beta
            raw = float(resid.var())
            tau2 = max(0.05 * raw, raw - sigma2 / float(np.median(gs)))
            out[f"rho_{side}"][k] = rho
            out[f"lam_{side}"][k] = sigma2 / tau2
            out[f"cov_{side}"][k] = {c: float(b) for c, b in zip(names, beta[1:], strict=True)}
            out["diag"][f"{k}_{side}"] = {
                "n_pairs": len(xs),
                "rho": round(rho, 4),
                "gamma": {c: round(float(b), 6) for c, b in zip(names, beta[1:], strict=True)},
                "sigma2": round(sigma2, 6),
                "tau2_raw": round(raw, 6),
                "tau2": round(tau2, 6),
                "lambda_games": round(sigma2 / tau2, 3),
                "median_games": float(np.median(gs)),
            }
    out["seasons_used"] = seasons
    return out


def cmd_prep_cfbd(rows: list[core.TeamGame], classes: dict[tuple[str, int], str], finals: dict) -> int:
    cov = load_cfbd_covariates()
    arms = {arm: estimate_prior(rows, classes, finals, cov, arm) for arm in ARMS if arm != "core"}
    payload = {
        "arms": arms,
        "inputs": {
            "pbp_sha256": file_sha256(PBP),
            "snapshot_sha256": file_sha256(SNAPSHOT),
            "cfbd_priors_sha256": file_sha256(CFBD_PRIORS),
        },
        "note": (
            "CFBD arms: finals of 2016-2021 and CFBD covariates of those seasons only. Every CFBD value is "
            "current-revised (as_published_verified false); portal_out exists from 2021 only"
        ),
    }
    PRIOR_ARMS_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for arm, pr in arms.items():
        for k, v in pr["diag"].items():
            print(arm, k, v["rho"], v["gamma"], v["lambda_games"])
    return 0


def load_prior(arm: str) -> dict[str, Any]:
    if arm == "core":
        return json.loads(PRIOR_JSON.read_text(encoding="utf-8"))
    return json.loads(PRIOR_ARMS_JSON.read_text(encoding="utf-8"))["arms"][arm]


def cmd_prep(args: argparse.Namespace) -> int:
    t0 = time.time()
    rows, facts = load_team_games()
    classes = core.season_classes(rows)
    seasons = sorted({r.season for r in rows})
    finals = {s: core.fit_finals(rows, s, classes) for s in seasons if s <= PRIOR_FIT_LAST_SEASON}
    if args.cfbd:
        return cmd_prep_cfbd(rows, classes, finals)
    prior = estimate_prior(rows, classes, finals)
    prior["inputs"] = {
        "pbp_sha256": file_sha256(PBP),
        "snapshot_sha256": file_sha256(SNAPSHOT),
        "team_game_rows": len(rows),
        "events": len(facts),
        "events_with_pbp": sum(1 for f in facts.values() if f["pbp"]),
    }
    prior["note"] = (
        "finals of seasons 2016-2021 only (all end before TRAIN_END); no validation/holdout season read"
    )
    OUT.mkdir(parents=True, exist_ok=True)
    PRIOR_JSON.write_text(json.dumps(prior, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(prior["diag"], indent=1))
    print(f"prep done in {time.time() - t0:.0f}s")
    return 0


def prior_params(prior: dict[str, Any], mult: float, half_life: float | None) -> core.PriorParams:
    return core.PriorParams(
        rho_off=prior["rho_off"],
        rho_def=prior["rho_def"],
        lam_off={k: mult * v for k, v in prior["lam_off"].items()},
        lam_def={k: mult * v for k, v in prior["lam_def"].items()},
        half_life_days=half_life,
        cov_off=prior.get("cov_off") or {},
        cov_def=prior.get("cov_def") or {},
    )


# ================================================================================================ walk
def walk_combo(args: tuple[str, float, float | None]) -> dict[str, Any]:
    """PIT features for every scored game under one grid point; train/validation and holdout to separate files."""
    arm, mult, half_life = args
    key = combo_key(mult, half_life, arm)
    params = prior_params(load_prior(arm), mult, half_life)
    covs = load_cfbd_covariates() if arm != "core" else {}
    rows, facts = load_team_games()
    classes = core.season_classes(rows)
    evals = load_eval_rows()
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
                teams,
                covs.get(season),
            )
            for ev in games:
                f = facts[ev["event_id"]]
                rec: dict[str, Any] = {
                    "event_id": ev["event_id"],
                    "date": ev["date"],
                    "season": ev["season"],
                    "week": ev["week"],
                    "window": window_of(ev["date"]),
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
    mults = [float(x) for x in args.mults.split(",")] if args.mults else list(LAMBDA_MULTIPLIERS)
    combos = [(a, m, h) for a in args.arms.split(",") for m in mults for h in HALF_LIVES]
    if args.only:
        combos = [c for c in combos if combo_key(c[1], c[2], c[0]) == args.only]
    WORK.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=min(args.workers, len(combos))) as pool:
        for res in pool.map(walk_combo, combos):
            print(json.dumps(res), flush=True)
    return 0


# ================================================================================================ distributions
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
    edges = np.append(grid - 0.5, grid[-1] + 0.5)
    z = (edges[None, :] - loc[:, None]) / sd[:, None]
    cdf, sf = ndtr(z), ndtr(-z)
    return np.where(z[:, :-1] > 0, sf[:, :-1] - sf[:, 1:], cdf[:, 1:] - cdf[:, :-1])


def margin_pmf(mu: np.ndarray, sd: np.ndarray, g: np.ndarray, *, balanced: bool) -> np.ndarray:
    """v6.2's keyed discretised normal (same Newton recentring): balanced = cover-balanced at mu (a line),
    else mean-centred (a model mean)."""
    loc = mu.astype(float).copy()
    sign = np.sign(M_GRID[None, :] - mu[:, None])
    for _ in range(RECENTRE_ITERATIONS):
        w = _interval_mass(M_GRID, loc, sd) * g[None, :]
        p = w / w.sum(axis=1, keepdims=True)
        mean = p @ M_GRID
        if balanced:
            gap = (p * sign).sum(axis=1)
            slope = ((p * sign * M_GRID[None, :]).sum(axis=1) - gap * mean) / sd**2
            loc = loc - gap / slope
        else:
            var = p @ (M_GRID**2) - mean**2
            loc = loc + (mu - mean) * sd**2 / var
    w = _interval_mass(M_GRID, loc, sd) * g[None, :]
    return w / w.sum(axis=1, keepdims=True)


def total_pmf(mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    w = _interval_mass(T_GRID, mu, sd)
    return w / w.sum(axis=1, keepdims=True)


def sd_design(mu: np.ndarray, tot: np.ndarray, early: np.ndarray, fcs: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [np.ones_like(mu), np.abs(mu) / SD_SCALE, (tot - SD_TOTAL_CENTRE) / SD_SCALE, early, fcs]
    )


def fit_margin_pmf(
    mu: np.ndarray, tot: np.ndarray, early: np.ndarray, fcs: np.ndarray, y: np.ndarray, *, balanced: bool
) -> dict[str, Any]:
    """MLE of the SD model and key multipliers on train (ridge KEY_RIDGE on theta)."""
    idx = (np.clip(y, -MARGIN_SUPPORT, MARGIN_SUPPORT) + MARGIN_SUPPORT).astype(int)
    xd = sd_design(mu, tot, early, fcs)
    n_sd = xd.shape[1]

    def nll(x: np.ndarray) -> float:
        sd = np.exp(xd @ x[:n_sd])
        p = margin_pmf(mu, sd, key_multipliers(x[n_sd:]), balanced=balanced)
        return float(
            -np.log(np.maximum(p[np.arange(len(mu)), idx], 1e-300)).sum() + KEY_RIDGE * (x[n_sd:] ** 2).sum()
        )

    x0 = np.concatenate([[math.log(16.0)], np.zeros(n_sd - 1), np.zeros(KEY_MAX)])
    t0 = time.time()
    res = minimize(nll, x0, method="L-BFGS-B", options={"maxiter": 300})
    return {
        "sd_coef": [float(v) for v in res.x[:n_sd]],
        "sd_terms": ["const", "abs_mu/10", "(total-55)/10", "early", "fbs_vs_fcs"],
        "theta": [float(v) for v in res.x[n_sd:]],
        "neg_ll": float(res.fun),
        "converged": bool(res.success),
        "seconds": round(time.time() - t0, 1),
    }


def fit_total_sds(mu: np.ndarray, y: np.ndarray) -> list[float]:
    out = []
    for lo, hi in TOTAL_BUCKETS:
        m = (mu >= lo) & (mu < hi)
        r = y[m] - mu[m]
        out.append(
            float(np.sqrt((r * r).mean())) if m.sum() >= 30 else float(np.sqrt(((y - mu) ** 2).mean()))
        )
    return out


def total_sd_of(mu: np.ndarray, sds: list[float]) -> np.ndarray:
    out = np.full(len(mu), sds[-1])
    for (lo, hi), s in zip(TOTAL_BUCKETS, sds, strict=True):
        out[(mu >= lo) & (mu < hi)] = s
    return out


# ================================================================================================ fit / select
def load_walk(key: str, *, holdout: bool = False) -> list[dict[str, Any]]:
    path = WORK / (f"walk_{key}.holdout.jsonl" if holdout else f"walk_{key}.jsonl")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def side_matrix(rows: list[dict[str, Any]], names: tuple[str, ...], side: str) -> np.ndarray:
    return np.array([[r[f"{side}_e"][n.removeprefix("e_")] for n in names] for r in rows], dtype=float)


def fit_stage2(train: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    """OLS team points on the side's expected metrics (both sides of every train game), then a margin and a total
    linear calibration on train."""
    xh, xa = side_matrix(train, names, "home"), side_matrix(train, names, "away")
    ph = np.array([(r["total"] + r["margin"]) / 2.0 for r in train])
    pa = np.array([(r["total"] - r["margin"]) / 2.0 for r in train])
    x = np.vstack([xh, xa])
    x1 = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(x1, np.concatenate([ph, pa]), rcond=None)
    raw_m = (xh - xa) @ beta[1:]
    raw_t = 2 * beta[0] + (xh + xa) @ beta[1:]
    mcal = np.polyfit(raw_m, np.array([r["margin"] for r in train]), 1)
    tcal = np.polyfit(raw_t, np.array([r["total"] for r in train]), 1)
    return {
        "features": list(names),
        "intercept": float(beta[0]),
        "coef": [float(v) for v in beta[1:]],
        "margin_calibration": {"intercept": float(mcal[1]), "slope": float(mcal[0])},
        "total_calibration": {"intercept": float(tcal[1]), "slope": float(tcal[0])},
    }


def project(rows: list[dict[str, Any]], s2: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    names = tuple(s2["features"])
    c = np.array(s2["coef"])
    xh, xa = side_matrix(rows, names, "home"), side_matrix(rows, names, "away")
    raw_m = (xh - xa) @ c
    raw_t = 2 * s2["intercept"] + (xh + xa) @ c
    mc, tc = s2["margin_calibration"], s2["total_calibration"]
    return mc["intercept"] + mc["slope"] * raw_m, tc["intercept"] + tc["slope"] * raw_t


def called(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if "no_call" not in r]


def fit_pieces(cand: dict[str, Any]) -> dict[str, Any]:
    """Margin pmf (SD model + key multipliers) and total bucket SDs on train for one candidate; scores."""
    rows = load_walk(cand["combo"])
    tr = called([r for r in rows if r["window"] == "train"])
    va = called([r for r in rows if r["window"] == "validation"])
    s2 = cand["stage2"]
    m_tr, t_tr = project(tr, s2)
    early_tr = np.array([float(min(r["home_games"], r["away_games"]) < EARLY_GAMES) for r in tr])
    fcs_tr = np.array([float(r["is_fcs"]) for r in tr])
    pmf = fit_margin_pmf(m_tr, t_tr, early_tr, fcs_tr, np.array([r["margin"] for r in tr]), balanced=False)
    tsd = fit_total_sds(t_tr, np.array([r["total"] for r in tr]))
    return {
        "candidate": {
            k: cand[k] for k in ("arm", "combo", "lambda_multiplier", "half_life_days", "feature_set")
        },
        "stage2": s2,
        "margin_pmf": pmf,
        "total_sds": tsd,
        "validation_scores": score_v7(va, s2, pmf, tsd),
        "train_scores": score_v7(tr, s2, pmf, tsd),
    }


def cmd_fit(_: argparse.Namespace) -> int:
    cands = []
    arms = [a for a in ARMS if (WORK / f"walk_{combo_key(1.0, None, a)}.jsonl").exists()]
    for arm in arms:
        for mult, hl in [(m, h) for m in LAMBDA_MULTIPLIERS for h in HALF_LIVES]:
            key = combo_key(mult, hl, arm)
            rows = load_walk(key)
            if any(r["window"] == "holdout" for r in rows):
                raise SystemExit("holdout row in a train/validation walk file")
            tr = called([r for r in rows if r["window"] == "train"])
            va = called([r for r in rows if r["window"] == "validation"])
            for fs, names in FEATURE_SETS.items():
                s2 = fit_stage2(tr, names)
                m_tr, t_tr = project(tr, s2)
                m_va, t_va = project(va, s2)
                y_tr = np.array([r["margin"] for r in tr])
                y_va = np.array([r["margin"] for r in va])
                yt_va = np.array([r["total"] for r in va])
                yt_tr = np.array([r["total"] for r in tr])
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
                        "train_total_mae": float(np.abs(yt_tr - t_tr).mean()),
                        "validation_margin_mae": float(np.abs(y_va - m_va).mean()),
                        "validation_total_mae": float(np.abs(yt_va - t_va).mean()),
                        "validation_margin_rmse": float(np.sqrt(((y_va - m_va) ** 2).mean())),
                    }
                )
                print(
                    f"{key:14s} {fs:5s} train MAE {cands[-1]['train_margin_mae']:.3f} val margin MAE "
                    f"{cands[-1]['validation_margin_mae']:.3f} total MAE {cands[-1]['validation_total_mae']:.3f}",
                    flush=True,
                )

    # Pinned selection: lowest validation margin MAE; ties (< 0.005) -> lower validation total MAE. The best
    # candidate of the other family (core vs CFBD) is the "alt" arm, fit the same way and scored alongside.
    def rank(c: dict[str, Any]) -> tuple[int, float]:
        return (round(c["validation_margin_mae"] / 0.005), c["validation_total_mae"])

    best = min(cands, key=rank)
    other = [c for c in cands if (c["arm"] == "core") != (best["arm"] == "core")]
    alt = min(other, key=rank) if other else None
    todo = [best] + ([alt] if alt is not None else [])
    with ProcessPoolExecutor(max_workers=len(todo)) as pool:
        fitted = list(pool.map(fit_pieces, todo))
    sel = fitted[0]
    spec = {
        "as_of_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head": _git("rev-parse", "HEAD"),
        "prior_sha256": file_sha256(PRIOR_JSON),
        "prior_arms_sha256": file_sha256(PRIOR_ARMS_JSON) if PRIOR_ARMS_JSON.exists() else None,
        "candidates": cands,
        "selected": sel["candidate"],
        "stage2": sel["stage2"],
        "margin_pmf": sel["margin_pmf"],
        "total_sds": sel["total_sds"],
        "validation_scores": sel["validation_scores"],
        "train_scores": sel["train_scores"],
        "alt": fitted[1] if len(fitted) > 1 else None,
    }
    SPEC.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("selected", spec["selected"], json.dumps(spec["validation_scores"]))
    if spec["alt"]:
        print("alt", spec["alt"]["candidate"], json.dumps(spec["alt"]["validation_scores"]))
    return 0


def v7_probabilities(
    rows: list[dict[str, Any]], s2: dict[str, Any], pmf: dict[str, Any], tsd: list[float]
) -> dict[str, np.ndarray]:
    mu, tot = project(rows, s2)
    early = np.array([float(min(r["home_games"], r["away_games"]) < EARLY_GAMES) for r in rows])
    fcs = np.array([float(r["is_fcs"]) for r in rows])
    sd = np.exp(sd_design(mu, tot, early, fcs) @ np.array(pmf["sd_coef"]))
    pm = margin_pmf(mu, sd, key_multipliers(np.array(pmf["theta"])), balanced=False)
    return {
        "mu": mu,
        "tot": tot,
        "sd": sd,
        "p_home": pm[:, M_GRID > 0].sum(axis=1),
        "pmf": pm,
        "tsd": total_sd_of(tot, tsd),
    }


def score_v7(
    rows: list[dict[str, Any]], s2: dict[str, Any], pmf: dict[str, Any], tsd: list[float]
) -> dict[str, Any]:
    pr = v7_probabilities(rows, s2, pmf, tsd)
    y = np.array([r["margin"] for r in rows])
    yt = np.array([r["total"] for r in rows])
    win = (y > 0).astype(float)
    p = np.clip(pr["p_home"], 1e-9, 1 - 1e-9)
    idx = (np.clip(y, -MARGIN_SUPPORT, MARGIN_SUPPORT) + MARGIN_SUPPORT).astype(int)
    return {
        "n": len(rows),
        "margin_mae": round(float(np.abs(y - pr["mu"]).mean()), 4),
        "total_mae": round(float(np.abs(yt - pr["tot"]).mean()), 4),
        "ml_brier": round(float(((p - win) ** 2).mean()), 5),
        "ml_log_loss": round(float(-(win * np.log(p) + (1 - win) * np.log(1 - p)).mean()), 5),
        "margin_log_score": round(
            float(np.log(np.maximum(pr["pmf"][np.arange(len(y)), idx], 1e-300)).mean()), 4
        ),
    }


# ================================================================================================ pinned rule
# Written to outputs/ncaaf/cfb_v7_rule.json by `pin` and committed BEFORE `holdout` runs; `holdout` recomputes the
# sha256 of this dict, compares it to the file and to --rule-sha, and checks the file is committed and unmodified.
RULE: dict[str, Any] = {
    "id": "cfb-v7-holdout-rule-1",
    "pinned_before": "the first and only v7 holdout read; validation numbers were known, holdout numbers were not",
    "windows": {
        "train": f"date <= {TRAIN_END}",
        "validation": f"{TRAIN_END} < date <= {VAL_END}",
        "holdout": f"date > {VAL_END} (2024-11-15 onwards, through the 2026-11-19 end of the v5 replay rows)",
    },
    "point_in_time": (
        "decision = min(kickoff, 08:00 America/New_York on the kickoff's ET date); a game feeds ratings only if "
        "kickoff + 5 h <= decision. v5's replay admits games with start < kickoff (no buffer, same-day results): a "
        "small information advantage to v5 that is left in place."
    ),
    "selection": (
        f"prior arm {list(ARMS)} x lambda multiplier {list(LAMBDA_MULTIPLIERS)} x recency half-life "
        f"{list(HALF_LIVES)} x stage-2 feature set {list(FEATURE_SETS)}: lowest validation margin MAE (0.005-point "
        "ties -> lower validation total MAE). Prior rho / lambda / CFBD gammas from 2016-2021 finals (and those "
        "seasons' CFBD values) only. Margin pmf (SD model + key multipliers, ridge "
        f"{KEY_RIDGE}) and total bucket SDs fit on train for the selected candidate and for the best candidate of "
        "the other family (core vs CFBD), the 'alt' arm."
    ),
    "scored_set": (
        "v5 replay rows (every completed FBS-involved game) in the holdout window on which v7 makes a call; v5 and "
        "the consensus baseline are scored on exactly those games (consensus on the lined subset); no-calls and "
        "rows without facts are counted and reported, never scored."
    ),
    "arms": {
        "v5": (
            "artifact as served: intercept + slope * raw replay margin, slice SD table x conference scale x blowout "
            "widening, negative-binomial engine 200k sims, common random numbers per event (the recheck's arithmetic)"
        ),
        "v7": "selected candidate, mean-centred keyed margin pmf; moneyline = P(margin > 0). The gate uses this arm.",
        "v7_alt": (
            "best validation candidate of the other prior family (with vs without CFBD), reported, never gating"
        ),
        "cfbd_close": (
            "CFBD multi-book close: median over providers of spread_espn_home_close / total_close, rows flagged "
            "ml_spread_sign_conflict or open_close_sign_conflict excluded; moneyline from the consensus pmf"
        ),
        "opener": "CFBD multi-book opener (median spread_espn_home_open / total_open, 2021+), same exclusions",
        "consensus": (
            "ESPN pinned median closing line (espn-lines-rule-1) as the margin / total mean; moneyline from a "
            "cover-balanced keyed pmf whose SD model is fit on TRAIN lined games (a baseline fit, not a v7 fit)"
        ),
    },
    "v5_reproduction_required": {
        "n": 1949,
        "margin_mae": [13.167, 0.002],
        "ml_brier": [0.17197, 0.00005],
        "note": "published in outputs/ncaaf/cfb_v5_margin_slope_recheck.md; checked on all v5 holdout rows first",
    },
    "bootstrap": "paired v7 - v5 deltas, resampling game dates (2000 resamples, seed 11), 95% percentile CI",
    "gate_beats_v5_on_outcomes": {
        "applies_to": "each scored v7 arm (selected and alt) separately, v7 minus v5 on the same games",
        "coverage": "v7 calls >= 97% of the holdout v5 rows",
        "significant_improvement": "margin MAE delta CI high < 0, or moneyline log loss delta CI high < 0",
        "no_point_regression": {
            "margin_mae_at_most": 0.0,
            "ml_brier_at_most": 0.0005,
            "ml_log_loss_at_most": 0.0015,
        },
        "verdict": {
            "PASS -> WIRE AS CHALLENGER": (
                "the CORE arm (no CFBD) passes coverage, significant_improvement and no_point_regression; the "
                "CFBD arm is packaged only if it passes as well, otherwise the core arm is packaged"
            ),
            "FAILED": "core arm: margin MAE delta CI low > 0, or moneyline Brier delta CI low > 0 (significantly worse)",
            "RESEARCH ONLY": (
                "otherwise; if only the CFBD arm passes, RESEARCH ONLY (its gain cannot be separated from "
                "current-revised CFBD values, which are also current-revised in the validation seasons)"
            ),
        },
    },
    "secondary_reported_not_gating": [
        "weeks 0-4 (v5 replay week <= 4) vs 5-14 vs postseason: margin MAE, Brier, log loss deltas with CIs",
        "FBS-FBS vs FBS-FCS",
        "calibration by favourite size (0-3/3-7/7-14/14-21/21+ projected margin)",
        "total MAE (v7 vs v5 vs consensus)",
        "v7 and v5 vs the consensus baseline on outcomes",
    ],
    "market_relative_reported_not_gating": {
        "lines": "ESPN pinned median closing spread and total (espn-lines-rule-1); DESIGN-INFORMED (read before)",
        "corr": "corr(model - line, result - line), date-clustered bootstrap CI",
        "slope_plain": "OLS slope of (result - line) on (model - line)",
        "slope_orthogonalised": "coefficient on (model - line) in OLS of (result - line) on [1, line, model - line]",
        "ats": "disagreement >= 3 and >= 7 points: wins / losses / pushes, Wilson CI, ROI at a 52.4% break-even",
        "totals": "same four on totals",
        "MARKET_SKILL_if": "orthogonalised slope CI low > 0 AND corr CI low > 0 (per market); else NO_MARKET_SKILL",
    },
    "grid_history": (
        "lambda multipliers started at (0.5, 1, 2); 0.5 won on validation at the grid edge, so 0.25 was added "
        "before this pin (0.5 still won). The half-life grid (none, 45 days) was NOT extended although 45 won: the "
        "validation difference between them is about 0.015 points of margin MAE. Every grid change happened on "
        "train/validation numbers only; the holdout was unread."
    ),
    "pre_read_steps": (
        "v5 reproduction (`v5repro`, no v7 file opened) must pass; the consensus pmf is fit on train and stored in "
        "the spec before the pin; the identical scoring path runs once on validation (`dryrun`); script, spec, "
        "prior files, the v5 reproduction and this rule are committed and unmodified; the read marker is written "
        "immediately before the first holdout file is opened."
    ),
    "cfbd_arm": (
        "The CFBD preseason prior landed before this pin, so core and CFBD arms compete on validation and the holdout "
        "is read once for both (selected arm gates; the other family is reported as v7_alt). CFBD values are "
        "current-revised (as_published_verified false; 2026 fetched mid-season): any CFBD-arm gain is an UPPER "
        "BOUND. Used: returning_off_ppa_pct, talent_prev_season, recruiting_points_4yr_weighted, portal_out_stars_sum "
        "(portal arm only). Never used: portal IN (as-of-fetch destination), current-season talent, SP+."
    ),
    "market_relative_cfbd": (
        "the same market block against the CFBD multi-book close; MAE vs the CFBD opener (2021+) for v7, v5, "
        "v7_alt (paired, date-clustered CI). Reported, not gating."
    ),
}


def rule_sha256() -> str:
    return hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cmd_pin(_: argparse.Namespace) -> int:
    sha = rule_sha256()
    payload = {
        "rule": RULE,
        "rule_sha256": sha,
        "pinned_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spec_sha256": file_sha256(SPEC),
        "prior_sha256": file_sha256(PRIOR_JSON),
        "holdout_walk_files_sha256": {
            p.name: file_sha256(p) for p in sorted(WORK.glob("walk_*.holdout.jsonl"))
        },
        "prior_arms_sha256": file_sha256(PRIOR_ARMS_JSON) if PRIOR_ARMS_JSON.exists() else None,
        "cfbd_priors_sha256": file_sha256(CFBD_PRIORS) if CFBD_PRIORS.exists() else None,
        "cfbd_lines_sha256": file_sha256(CFBD_LINES) if CFBD_LINES.exists() else None,
        "cfbd_present_at_pin": CFBD_DIR.is_dir() and any(CFBD_DIR.iterdir()),
    }
    RULE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(sha)
    return 0


# ================================================================================================ v5 arm
def v5_arm(rows: list[dict[str, Any]], lines: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """v5 as served on replay rows (the recheck's price_rows(engine=True) arithmetic for the artifact arm)."""
    from model_prediction.models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine
    from model_prediction.models.cfb_structural_v4 import (
        CFB_BLOWOUT_MARGIN_SCALE,
        CFB_BLOWOUT_SPREAD_THRESHOLD,
    )
    from model_prediction.models.cfb_structural_v5 import (
        cfb_structural_v5_model,
        load_cfb_structural_v5_artifact,
    )

    art = load_cfb_structural_v5_artifact()
    model = cfb_structural_v5_model()
    mc, tc, table = art["margin_calibration"], art["total_calibration"], art["margin_sd_table"]
    k_min, conf_scale = int(art["established_min_sample"]), float(art["conference_margin_scale"])
    eng = CFBJointDistributionEngine(
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL, n_simulations=N_SIM, random_seed=42
    )
    out = []
    for r in rows:
        mu = mc["intercept"] + mc["slope"] * r["raw_margin"]
        pricing_line = round(-r["raw_margin"] * 2.0) / 2.0
        cal_total = max(24.0, tc["intercept"] + tc["slope"] * r["raw_total"])
        slc = (
            "fbs_vs_fcs" if r["is_fcs"] else ("established" if r["sample_games"] >= k_min else "thin_sample")
        )
        mult = conf_scale if (r["is_conf"] and slc == "established") else 1.0
        if abs(pricing_line) > CFB_BLOWOUT_SPREAD_THRESHOLD:
            mult *= 1.0 + CFB_BLOWOUT_MARGIN_SCALE * min(
                15.0, abs(pricing_line) - CFB_BLOWOUT_SPREAD_THRESHOLD
            )
        sd = round(table[slc] * mult, 3)
        p = None
        if r["margin"] != 0:
            eng.margin_sd = sd
            eng.total_sd = model.compute_total_sd(projected_total=cal_total, total_line=r["raw_total"])
            eng.rng = np.random.default_rng(int(r["event_id"]) % (2**32))
            p = eng.compute_market_probabilities(
                mu_home=max(3.0, (cal_total + mu) / 2.0),
                mu_away=max(3.0, (cal_total - mu) / 2.0),
                spread_home_line=pricing_line,
                total_line=r["raw_total"],
            ).p_home_win
        ln = lines.get(r["event_id"]) or {}
        out.append(
            {
                "mu": mu,
                "tot": cal_total,
                "p": p,
                "line_margin": ln.get("margin"),
                "line_total": ln.get("total"),
            }
        )
    return out


def load_cfbd_lines() -> dict[str, dict[str, Any]]:
    """ESPN event id -> CFBD multi-book median close and open (home-margin / total), flagged rows excluded."""
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    with CFBD_LINES.open(encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            eid = r.get("espn_event_id")
            if not eid or str(r.get("espn_match_status", "")).startswith("NO_CALL"):
                continue
            if r.get("ml_spread_sign_conflict") or r.get("open_close_sign_conflict"):
                continue
            a = acc[str(eid)]
            for src, dst in (
                ("spread_espn_home_close", "margin"),
                ("spread_espn_home_open", "open_margin"),
            ):
                if r.get(src) is not None:
                    a[dst].append(-float(r[src]))
            for src, dst in (("total_close", "total"), ("total_open", "open_total")):
                if r.get(src) is not None:
                    a[dst].append(float(r[src]))
    return {
        eid: {k: (statistics.median(v) if v else None) for k, v in a.items()}
        | {"n_books": max((len(v) for v in a.values()), default=0)}
        for eid, a in acc.items()
    }


def line_arm(margin: float | None, total: float | None, row: dict[str, Any], fit: dict[str, Any]) -> Any:
    """A market line as an arm: mean = the line, moneyline from the train-fit cover-balanced keyed pmf."""
    if margin is None:
        return None
    early = float(min(row["home_games"], row["away_games"]) < EARLY_GAMES)
    t_sd = SD_TOTAL_CENTRE if total is None else total
    sd = math.exp(
        float(
            sd_design(
                np.array([margin]), np.array([t_sd]), np.array([early]), np.array([float(row["is_fcs"])])
            )[0]
            @ np.array(fit["sd_coef"])
        )
    )
    pm = margin_pmf(
        np.array([margin]), np.array([sd]), key_multipliers(np.array(fit["theta"])), balanced=True
    )
    return {
        "mu": float(margin),
        "tot": None if total is None else float(total),
        "p": float(pm[0, M_GRID > 0].sum()),
    }


def load_lines() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ln in ESPN_LINES.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln)
        sel = rec.get("selected") or {}
        sp, tt = sel.get("home_spread_line"), sel.get("total_line")
        out[str(rec["event_id"])] = {
            "margin": None if sp is None else -float(sp),
            "total": None if tt is None else float(tt),
        }
    return out


# ================================================================================================ scoring helpers
def _ll(p: float, y: int) -> float:
    q = min(1 - 1e-9, max(1e-9, p if y else 1 - p))
    return -math.log(q)


def arm_summary(games: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    """MAE / Brier / log loss / ECE for one arm over the given game dicts (each carries arm -> {mu, tot, p})."""
    from model_prediction.calibration import calibration_metrics

    g = [x for x in games if x[arm] is not None]
    sc = [x for x in g if x["margin"] != 0 and x[arm]["p"] is not None]
    gt = [x for x in g if x[arm]["tot"] is not None]
    probs = [x[arm]["p"] for x in sc]
    ys = [int(x["margin"] > 0) for x in sc]
    m = calibration_metrics(probs, ys) if len(probs) >= 30 else {}
    return {
        "n": len(g),
        "margin_mae": round(statistics.fmean(abs(x["margin"] - x[arm]["mu"]) for x in g), 4) if g else None,
        "margin_bias_actual_minus_model": round(statistics.fmean(x["margin"] - x[arm]["mu"] for x in g), 3)
        if g
        else None,
        "total_mae": round(statistics.fmean(abs(x["total"] - x[arm]["tot"]) for x in gt), 4) if gt else None,
        "ml_n": len(sc),
        "ml_brier": round(statistics.fmean((p - y) ** 2 for p, y in zip(probs, ys, strict=True)), 5)
        if sc
        else None,
        "ml_log_loss": round(statistics.fmean(_ll(p, y) for p, y in zip(probs, ys, strict=True)), 5)
        if sc
        else None,
        "ml_ece": m.get("expected_calibration_error"),
    }


def paired(games: list[dict[str, Any]], a: str, b: str, *, n_boot: int = 2000) -> dict[str, Any]:
    """b - a over games both arms price; date-clustered bootstrap (seed 11). Lower is better for every metric."""
    by_date: dict[str, list[tuple[float, float, float, float, int, int]]] = defaultdict(list)
    for x in games:
        if x[a] is None or x[b] is None:
            continue
        dm = abs(x["margin"] - x[b]["mu"]) - abs(x["margin"] - x[a]["mu"])
        has_t = x[a]["tot"] is not None and x[b]["tot"] is not None
        dt = abs(x["total"] - x[b]["tot"]) - abs(x["total"] - x[a]["tot"]) if has_t else 0.0
        if x["margin"] != 0 and x[a]["p"] is not None and x[b]["p"] is not None:
            y = int(x["margin"] > 0)
            db = (x[b]["p"] - y) ** 2 - (x[a]["p"] - y) ** 2
            dl = _ll(x[b]["p"], y) - _ll(x[a]["p"], y)
            w = 1
        else:
            db = dl = 0.0
            w = 0
        by_date[x["date"]].append((dm, dt, db, dl, w, int(has_t)))
    dates = sorted(by_date)
    if not dates:
        return {"n": 0}

    def stat(days: list[str]) -> tuple[float, ...]:
        vals = [v for d in days for v in by_date[d]]
        nw = sum(v[4] for v in vals) or 1
        return (
            sum(v[0] for v in vals) / len(vals),
            sum(v[1] for v in vals) / (sum(v[5] for v in vals) or 1),
            sum(v[2] for v in vals) / nw,
            sum(v[3] for v in vals) / nw,
        )

    point = stat(dates)
    rng = random.Random(11)
    samples = [stat([rng.choice(dates) for _ in dates]) for _ in range(n_boot)]
    out: dict[str, Any] = {"n": sum(len(v) for v in by_date.values()), "dates": len(dates)}
    for i, name in enumerate(("margin_mae", "total_mae", "ml_brier", "ml_log_loss")):
        col = sorted(s[i] for s in samples)
        out[name] = {
            "delta": round(point[i], 6),
            "ci_95": [round(col[int(0.025 * (n_boot - 1))], 6), round(col[int(0.975 * (n_boot - 1))], 6)],
        }
    return out


def fav_buckets(games: list[dict[str, Any]], arm: str) -> list[dict[str, Any]]:
    out = []
    for lo, hi, label in FAV_BUCKETS:
        g = [x for x in games if x[arm] is not None and lo <= abs(x[arm]["mu"]) < hi]
        if not g:
            continue
        sg = [1.0 if x[arm]["mu"] >= 0 else -1.0 for x in g]
        sc = [(x, s) for x, s in zip(g, sg, strict=True) if x["margin"] != 0 and x[arm]["p"] is not None]
        out.append(
            {
                "bucket": label,
                "n": len(g),
                "fav_projected_margin": round(statistics.fmean(abs(x[arm]["mu"]) for x in g), 2),
                "fav_actual_margin": round(
                    statistics.fmean(s * x["margin"] for x, s in zip(g, sg, strict=True)), 2
                ),
                "fav_model_win_pct": round(
                    100 * statistics.fmean(x[arm]["p"] if s > 0 else 1 - x[arm]["p"] for x, s in sc), 1
                )
                if sc
                else None,
                "fav_realised_win_pct": round(
                    100 * statistics.fmean(float(s * x["margin"] > 0) for x, s in sc), 1
                )
                if sc
                else None,
            }
        )
    return out


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


def _market_stats(xs: np.ndarray, ys: np.ndarray, ls: np.ndarray) -> tuple[float, float, float]:
    corr = float(np.corrcoef(xs, ys)[0, 1]) if xs.std() > 0 and ys.std() > 0 else float("nan")
    plain = float(np.polyfit(xs, ys, 1)[0]) if xs.std() > 0 else float("nan")
    xm = np.column_stack([np.ones_like(xs), ls, xs])
    beta, *_ = np.linalg.lstsq(xm, ys, rcond=None)
    return corr, plain, float(beta[2])


def market_block(
    games: list[dict[str, Any]], arm: str, market: str, *, source: str = "espn", n_boot: int = 1000
) -> dict[str, Any]:
    """Model vs closing line on lined games: corr, plain and orthogonalised slopes (date-clustered CIs), ATS."""
    prefix = "line" if source == "espn" else "cfbd"
    key_line, key_y, key_m = (
        (f"{prefix}_margin", "margin", "mu") if market == "spread" else (f"{prefix}_total", "total", "tot")
    )
    g = [x for x in games if x[arm] is not None and x.get(key_line) is not None]
    if len(g) < 30:
        return {"n": len(g)}
    ls = np.array([x[key_line] for x in g])
    xs = np.array([x[arm][key_m] for x in g]) - ls
    ys = np.array([x[key_y] for x in g]) - ls
    corr, plain, orth = _market_stats(xs, ys, ls)
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, x in enumerate(g):
        by_date[x["date"]].append(i)
    keys = sorted(by_date)
    rng = random.Random(17)
    boots = []
    for _ in range(n_boot):
        idx = np.array([i for d in (rng.choice(keys) for _ in keys) for i in by_date[d]])
        boots.append(_market_stats(xs[idx], ys[idx], ls[idx]))
    arr = np.array(boots)

    def ci(col: int) -> list[float]:
        c = np.sort(arr[:, col][~np.isnan(arr[:, col])])
        return [round(float(c[int(0.025 * (len(c) - 1))]), 4), round(float(c[int(0.975 * (len(c) - 1))]), 4)]

    out: dict[str, Any] = {
        "n": len(g),
        "corr_model_minus_line_vs_result_minus_line": round(corr, 4),
        "corr_ci95": ci(0),
        "slope_plain": round(plain, 4),
        "slope_plain_ci95": ci(1),
        "slope_orthogonalised": round(orth, 4),
        "slope_orthogonalised_ci95": ci(2),
        "mean_abs_disagreement": round(float(np.abs(xs).mean()), 3),
        "mae_model": round(float(np.abs(ys - xs).mean()), 3),
        "mae_line": round(float(np.abs(ys).mean()), 3),
    }
    pairs = list(zip(xs.tolist(), ys.tolist(), strict=True))
    for thr in (3.0, 7.0):
        out[f"ats_disagree_ge_{int(thr)}"] = _ats([(x, y) for x, y in pairs if abs(x) >= thr])
    ci_o, ci_c = out["slope_orthogonalised_ci95"], out["corr_ci95"]
    out["verdict"] = "MARKET_SKILL" if ci_o[0] > 0 and ci_c[0] > 0 else "NO_MARKET_SKILL"
    return out


# ================================================================================================ holdout
def _committed_unmodified(path: Path) -> bool:
    rel = str(path.relative_to(REPO)).replace("\\", "/")
    tracked = bool(_git("ls-files", rel))
    dirty = bool(_git("status", "--porcelain", "--", rel))
    return tracked and not dirty


def consensus_fit(train_called: list[dict[str, Any]], lines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lined = [r for r in train_called if (lines.get(r["event_id"]) or {}).get("margin") is not None]
    lined = [r for r in lined if lines[r["event_id"]]["total"] is not None]
    mu = np.array([lines[r["event_id"]]["margin"] for r in lined])
    tot = np.array([lines[r["event_id"]]["total"] for r in lined])
    early = np.array([float(min(r["home_games"], r["away_games"]) < EARLY_GAMES) for r in lined])
    fcs = np.array([float(r["is_fcs"]) for r in lined])
    fit = fit_margin_pmf(mu, tot, early, fcs, np.array([r["margin"] for r in lined]), balanced=True)
    fit["train_lined_n"] = len(lined)
    return fit


def consensus_probs(
    rows: list[dict[str, Any]], lines: dict[str, dict[str, Any]], fit: dict[str, Any]
) -> list[Any]:
    out: list[Any] = [None] * len(rows)
    idx = [
        i
        for i, r in enumerate(rows)
        if (lines.get(r["event_id"]) or {}).get("margin") is not None
        and lines[r["event_id"]]["total"] is not None
    ]
    if not idx:
        return out
    mu = np.array([lines[rows[i]["event_id"]]["margin"] for i in idx])
    tot = np.array([lines[rows[i]["event_id"]]["total"] for i in idx])
    early = np.array([float(min(rows[i]["home_games"], rows[i]["away_games"]) < EARLY_GAMES) for i in idx])
    fcs = np.array([float(rows[i]["is_fcs"]) for i in idx])
    sd = np.exp(sd_design(mu, tot, early, fcs) @ np.array(fit["sd_coef"]))
    pm = margin_pmf(mu, sd, key_multipliers(np.array(fit["theta"])), balanced=True)
    ph = pm[:, M_GRID > 0].sum(axis=1)
    for j, i in enumerate(idx):
        out[i] = {"mu": float(mu[j]), "tot": float(tot[j]), "p": float(ph[j])}
    return out


V5_REPRO = OUT / "cfb_v7_v5_repro.json"
DRYRUN_JSON = WORK / "dryrun_validation_report.json"
DRYRUN_MD = WORK / "dryrun_validation_report.md"


def cmd_v5repro(_: argparse.Namespace) -> int:
    """v5 on every holdout replay row (no v7 file is opened). Its holdout numbers are already published in the v5
    recheck, so this is a reproduction, not a v7 read; ``holdout`` refuses to run unless it passed."""
    lines = load_lines()
    v5_hold = sorted(
        (r for r in load_eval_rows() if window_of(r["date"]) == "holdout"),
        key=lambda r: (r["start_utc"], r["event_id"]),
    )
    vals = v5_arm(v5_hold, lines)
    games = [
        {"date": r["date"], "margin": r["margin"], "total": r["total"], "v5": v}
        for r, v in zip(v5_hold, vals, strict=True)
    ]
    got = arm_summary(games, "v5")
    req = RULE["v5_reproduction_required"]
    ok = (
        got["n"] == req["n"]
        and abs(got["margin_mae"] - req["margin_mae"][0]) <= req["margin_mae"][1]
        and abs(got["ml_brier"] - req["ml_brier"][0]) <= req["ml_brier"][1]
    )
    V5_REPRO.write_text(
        json.dumps(
            {
                "got": got,
                "required": req,
                "pass": ok,
                "per_event": {r["event_id"]: v for r, v in zip(v5_hold, vals, strict=True)},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print("v5 reproduction", "PASS" if ok else "FAIL", got)
    return 0 if ok else 1


def cmd_consensus(_: argparse.Namespace) -> int:
    """Train-only fit of the consensus baseline's cover-balanced keyed pmf, stored in the spec before the pin."""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    tr = called([r for r in load_walk(spec["selected"]["combo"]) if r["window"] == "train"])
    spec["consensus_pmf"] = consensus_fit(tr, load_lines())
    SPEC.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("consensus pmf", spec["consensus_pmf"]["sd_coef"], spec["consensus_pmf"]["train_lined_n"])
    return 0


def gate_for(pv: dict[str, Any], coverage: float) -> dict[str, Any]:
    g = RULE["gate_beats_v5_on_outcomes"]["no_point_regression"]
    checks = {
        "coverage": coverage >= 0.97,
        "significant_improvement": pv["margin_mae"]["ci_95"][1] < 0 or pv["ml_log_loss"]["ci_95"][1] < 0,
        "no_point_regression": (
            pv["margin_mae"]["delta"] <= g["margin_mae_at_most"]
            and pv["ml_brier"]["delta"] <= g["ml_brier_at_most"]
            and pv["ml_log_loss"]["delta"] <= g["ml_log_loss_at_most"]
        ),
    }
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "significantly_worse": pv["margin_mae"]["ci_95"][0] > 0 or pv["ml_brier"]["ci_95"][0] > 0,
    }


def score_window(window: str, spec: dict[str, Any], v5_by_id: dict[str, Any]) -> dict[str, Any]:
    """Scores every arm on ``window`` (validation for the dry run, holdout for the one read)."""
    lines = load_lines()
    cfbd = load_cfbd_lines()
    evals = {r["event_id"]: r for r in load_eval_rows()}
    n_v5 = sum(1 for r in evals.values() if window_of(r["date"]) == window)

    def rows_of(combo: str) -> list[dict[str, Any]]:
        if window == "holdout":
            return load_walk(combo, holdout=True)
        return [r for r in load_walk(combo) if r["window"] == window]

    rows = rows_of(spec["selected"]["combo"])
    rows_called = called(rows)
    pr = v7_probabilities(rows_called, spec["stage2"], spec["margin_pmf"], spec["total_sds"])
    alt_by_id: dict[str, Any] = {}
    alt = spec.get("alt")
    if alt:
        alt_rows = called(rows_of(alt["candidate"]["combo"]))
        pa = v7_probabilities(alt_rows, alt["stage2"], alt["margin_pmf"], alt["total_sds"])
        for i, r in enumerate(alt_rows):
            alt_by_id[r["event_id"]] = {
                "mu": float(pa["mu"][i]),
                "tot": float(pa["tot"][i]),
                "p": float(pa["p_home"][i]),
            }
    cfit = spec["consensus_pmf"]
    cons = consensus_probs(rows_called, lines, cfit)
    games = []
    for i, r in enumerate(rows_called):
        ev = evals[r["event_id"]]
        ln = lines.get(r["event_id"]) or {}
        cb = cfbd.get(r["event_id"]) or {}
        games.append(
            {
                "event_id": r["event_id"],
                "date": r["date"],
                "season": r["season"],
                "week": ev["week"],
                "band": week_band(int(ev["week"])),
                "fcs": bool(r["is_fcs"]),
                "margin": r["margin"],
                "total": r["total"],
                "line_margin": ln.get("margin"),
                "line_total": ln.get("total"),
                "cfbd_margin": cb.get("margin"),
                "cfbd_total": cb.get("total"),
                "v7": {"mu": float(pr["mu"][i]), "tot": float(pr["tot"][i]), "p": float(pr["p_home"][i])},
                "v7_alt": alt_by_id.get(r["event_id"]),
                "v5": v5_by_id[r["event_id"]],
                "consensus": cons[i],
                "cfbd_close": line_arm(cb.get("margin"), cb.get("total"), r, cfit),
                "opener": line_arm(cb.get("open_margin"), cb.get("open_total"), r, cfit),
            }
        )
    arms_present = ["v7", "v7_alt", "v5"] if alt_by_id else ["v7", "v5"]
    subsets = {
        "all": games,
        "weeks_0_4": [g for g in games if g["band"] == "weeks_0_4"],
        "weeks_5_14": [g for g in games if g["band"] == "weeks_5_14"],
        "postseason": [g for g in games if g["band"] == "postseason"],
        "fbs_vs_fbs": [g for g in games if not g["fcs"]],
        "fbs_vs_fcs": [g for g in games if g["fcs"]],
        "lined_all": [g for g in games if g["consensus"] is not None],
        "lined_weeks_0_4": [g for g in games if g["consensus"] is not None and g["band"] == "weeks_0_4"],
        "cfbd_lined": [g for g in games if g["cfbd_close"] is not None],
        "opener_lined": [g for g in games if g["opener"] is not None],
    }
    for season in sorted({g["season"] for g in games}):
        subsets[f"season_{season}"] = [g for g in games if g["season"] == season]
    outcomes: dict[str, Any] = {}
    for name, sub in subsets.items():
        blk: dict[str, Any] = {
            arm: arm_summary(sub, arm) for arm in arms_present + ["consensus", "cfbd_close", "opener"]
        }
        blk["paired_v7_minus_v5"] = paired(sub, "v5", "v7")
        if alt_by_id:
            blk["paired_v7_alt_minus_v5"] = paired(sub, "v5", "v7_alt")
            blk["paired_v7_minus_v7_alt"] = paired(sub, "v7_alt", "v7")
        if name.startswith("lined"):
            blk["paired_v7_minus_consensus"] = paired(sub, "consensus", "v7")
            blk["paired_v5_minus_consensus"] = paired(sub, "consensus", "v5")
        if name == "cfbd_lined":
            blk["paired_v7_minus_cfbd_close"] = paired(sub, "cfbd_close", "v7")
            blk["paired_v5_minus_cfbd_close"] = paired(sub, "cfbd_close", "v5")
        if name == "opener_lined":
            for arm in arms_present:
                blk[f"paired_{arm}_minus_opener"] = paired(sub, "opener", arm)
        outcomes[name] = blk
    fav = {arm: fav_buckets(games, arm) for arm in arms_present}
    market = {arm: {m: market_block(games, arm, m) for m in ("spread", "total")} for arm in arms_present}
    market_early = {
        arm: {m: market_block(subsets["weeks_0_4"], arm, m) for m in ("spread", "total")}
        for arm in arms_present
    }
    market_cfbd = {
        arm: {m: market_block(games, arm, m, source="cfbd") for m in ("spread", "total")}
        for arm in arms_present
    }
    # ---- pinned gate: core must pass; the CFBD arm is packaged only if it passes too
    coverage = len(rows_called) / max(1, n_v5)
    gates = {"v7": gate_for(outcomes["all"]["paired_v7_minus_v5"], coverage)}
    if alt_by_id:
        gates["v7_alt"] = gate_for(outcomes["all"]["paired_v7_alt_minus_v5"], coverage)
    arm_of = {"v7": spec["selected"]["arm"], "v7_alt": (alt or {}).get("candidate", {}).get("arm")}
    core_key = next((k for k in gates if arm_of[k] == "core"), None)
    cfbd_key = next((k for k in gates if arm_of[k] not in (None, "core")), None)
    package = None
    if core_key is None:
        verdict = "INVALID (no core arm scored)"
    elif gates[core_key]["pass"]:
        verdict = "PASS -> WIRE AS CHALLENGER"
        package = cfbd_key if cfbd_key is not None and gates[cfbd_key]["pass"] else core_key
    elif gates[core_key]["significantly_worse"]:
        verdict = "FAILED"
    else:
        verdict = "RESEARCH ONLY"
        if cfbd_key is not None and gates[cfbd_key]["pass"]:
            verdict += (
                " (only the CFBD arm passed; its gain cannot be separated from current-revised CFBD values)"
            )
    return {
        "window": window,
        "spec_selected": spec["selected"],
        "alt_candidate": (alt or {}).get("candidate"),
        "counts": {
            "v5_rows": n_v5,
            "v7_rows": len(rows),
            "v7_called": len(rows_called),
            "v7_no_call": len(rows) - len(rows_called),
            "no_facts": n_v5 - len(rows),
            "lined_spread_and_total": len(subsets["lined_all"]),
            "cfbd_lined": len(subsets["cfbd_lined"]),
            "opener_lined": len(subsets["opener_lined"]),
        },
        "consensus_fit_train": cfit,
        "outcomes": outcomes,
        "favourite_buckets": fav,
        "market": market,
        "market_weeks_0_4": market_early,
        "market_vs_cfbd_close": market_cfbd,
        "gate": {
            "per_arm": gates,
            "arm_of": arm_of,
            "core_arm": core_key,
            "cfbd_arm": cfbd_key,
            "coverage": round(coverage, 4),
            "verdict": verdict,
            "package_arm": package,
            "package_arm_prior": arm_of.get(package) if package else None,
        },
    }


def cmd_dryrun(_: argparse.Namespace) -> int:
    """The holdout scoring and report path run on VALIDATION rows (no mark, no holdout file opened)."""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    lines = load_lines()
    v5_rows = [r for r in load_eval_rows() if window_of(r["date"]) == "validation"]
    v5_by_id = {r["event_id"]: v for r, v in zip(v5_rows, v5_arm(v5_rows, lines), strict=True)}
    rep = score_window("validation", spec, v5_by_id)
    rep["holdout_read"] = {
        "read_at_utc": "DRY RUN (validation)",
        "head": _git("rev-parse", "HEAD"),
        "cfbd_present_at_read": True,
    }
    rep["rule_sha256"] = rule_sha256()
    rep["v5_reproduction"] = {"got": {"n": "-", "margin_mae": "-", "ml_brier": "-"}, "pass": True}
    DRYRUN_JSON.write_text(json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    DRYRUN_MD.write_text(render_md(rep, spec), encoding="utf-8")
    print("dry run verdict on validation:", rep["gate"]["verdict"])
    return 0


def cmd_holdout(args: argparse.Namespace) -> int:
    if HOLDOUT_MARK.exists():
        raise SystemExit(f"holdout already read: {HOLDOUT_MARK.read_text(encoding='utf-8')}")
    pinned = json.loads(RULE_PATH.read_text(encoding="utf-8"))
    sha = rule_sha256()
    if pinned["rule_sha256"] != sha or args.rule_sha != sha or pinned["rule"] != RULE:
        raise SystemExit("pinned rule sha256 mismatch")
    for path in (RULE_PATH, SPEC, Path(__file__).resolve(), PRIOR_JSON, PRIOR_ARMS_JSON, V5_REPRO):
        if not _committed_unmodified(path):
            raise SystemExit(f"{path.name} is not committed or has local changes")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if pinned["spec_sha256"] != file_sha256(SPEC) or "consensus_pmf" not in spec:
        raise SystemExit("spec changed after the pin, or has no train-fit consensus pmf")
    repro = json.loads(V5_REPRO.read_text(encoding="utf-8"))
    if not repro["pass"]:
        raise SystemExit("v5 reproduction did not pass")
    for p in sorted(WORK.glob("walk_*.holdout.jsonl")):
        if pinned["holdout_walk_files_sha256"].get(p.name) != file_sha256(p):
            raise SystemExit(f"holdout walk file {p.name} changed after the pin")
    if CFBD_LINES.exists() and pinned["cfbd_lines_sha256"] != file_sha256(CFBD_LINES):
        raise SystemExit("CFBD lines changed after the pin")
    if not DRYRUN_JSON.exists():
        raise SystemExit("run the validation dry run first")
    mark = {
        "read_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule_sha256": sha,
        "head": _git("rev-parse", "HEAD"),
        "cfbd_present_at_read": CFBD_DIR.is_dir() and any(CFBD_DIR.iterdir()),
    }
    HOLDOUT_MARK.write_text(json.dumps(mark, indent=2) + "\n", encoding="utf-8")  # just before the first read
    rep = score_window("holdout", spec, repro["per_event"])
    report = {
        "as_of_utc": mark["read_at_utc"],
        "holdout_read": mark,
        "rule_sha256": sha,
        "v5_reproduction": {k: repro[k] for k in ("got", "required", "pass")},
        **rep,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    REPORT_MD.write_text(render_md(report, spec), encoding="utf-8")
    print(report["gate"]["verdict"], "package:", report["gate"]["package_arm"])
    return 0


# ================================================================================================ report
def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _ci(d: dict[str, Any], nd: int = 4) -> str:
    return f"{d['delta']:+.{nd}f} [{d['ci_95'][0]:+.{nd}f}, {d['ci_95'][1]:+.{nd}f}]"


def render_md(rep: dict[str, Any], spec: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# cfb-structural-v7 holdout report (quick-261121-v7)")
    L.append("")
    L.append(
        f"Verdict: **{rep['gate']['verdict']}** under rule `{RULE['id']}` (sha256 `{rep['rule_sha256'][:16]}...`, "
        f"pinned and committed before this read). Package arm: {rep['gate']['package_arm']} "
        f"({rep['gate']['package_arm_prior']}). Arms: {json.dumps(rep['gate']['arm_of'])}. Per-arm gate: "
        + json.dumps(rep["gate"]["per_arm"])
        + ". CFBD values are current-revised, so any CFBD-arm gain is an upper bound."
    )
    L.append("")
    L.append(
        f"Read {rep['holdout_read']['read_at_utc']} at HEAD `{rep['holdout_read']['head'][:8]}`; this holdout was "
        "read before for v5 / v6 / v6.1 / v6.2 (it is unread only for v7), and every ESPN lined window was read "
        "before, so the market numbers are DESIGN-INFORMED. CFBD data present at read: "
        f"{rep['holdout_read']['cfbd_present_at_read']}."
    )
    L.append(f"Selected candidate: {json.dumps(rep['spec_selected'])}. Counts: {json.dumps(rep['counts'])}.")
    r5 = rep["v5_reproduction"]
    L.append(
        f"v5 reproduction on all holdout replay rows: n {r5['got']['n']}, MAE {r5['got']['margin_mae']}, Brier "
        f"{r5['got']['ml_brier']} (required n 1949, 13.167, 0.17197): {'PASS' if r5['pass'] else 'FAIL'}."
    )
    L.append("")
    L.append("## Outcomes (same games; moneyline probabilities as %, margins in points)")
    L.append("")
    L.append("| subset | arm | n | margin MAE | bias | total MAE | Brier | log loss | ECE |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for name, blk in rep["outcomes"].items():
        for arm in ("v7", "v7_alt", "v5", "consensus", "cfbd_close", "opener"):
            s = blk.get(arm)
            if not s or not s["n"]:
                continue
            L.append(
                f"| {name} | {arm} | {s['n']} | {_fmt(s['margin_mae'], 3)} | {_fmt(s['margin_bias_actual_minus_model'], 2)} "
                f"| {_fmt(s['total_mae'], 3)} | {_fmt(s['ml_brier'], 5)} | {_fmt(s['ml_log_loss'], 5)} | {_fmt(s['ml_ece'], 4)} |"
            )
    L.append("")
    L.append("Paired deltas (date-clustered 95% CI; negative = first arm better):")
    L.append("")
    L.append("| subset | comparison | n | margin MAE | total MAE | Brier | log loss |")
    L.append("|---|---|---|---|---|---|---|")
    for name, blk in rep["outcomes"].items():
        for key in [k for k in blk if k.startswith("paired_")]:
            p = blk.get(key)
            if not p or not p.get("n"):
                continue
            L.append(
                f"| {name} | {key.removeprefix('paired_').replace('_minus_', ' - ')} | {p['n']} | {_ci(p['margin_mae'], 3)} "
                f"| {_ci(p['total_mae'], 3)} | {_ci(p['ml_brier'], 5)} | {_ci(p['ml_log_loss'], 4)} |"
            )
    L.append("")
    L.append("## Calibration by favourite size (served favourite; model vs realised win %)")
    L.append("")
    L.append("| arm | bucket | n | proj fav margin | actual fav margin | model win % | realised win % |")
    L.append("|---|---|---|---|---|---|---|")
    for arm, rows in rep["favourite_buckets"].items():
        for b in rows:
            L.append(
                f"| {arm} | {b['bucket']} | {b['n']} | {b['fav_projected_margin']} | {b['fav_actual_margin']} | "
                f"{b['fav_model_win_pct']}% | {b['fav_realised_win_pct']}% |"
            )
    L.append("")
    for title, blk in (
        ("ESPN pinned median close, all lined holdout games", rep["market"]),
        ("ESPN pinned median close, weeks 0-4", rep["market_weeks_0_4"]),
        ("CFBD multi-book close, all holdout games with one", rep["market_vs_cfbd_close"]),
    ):
        L.append(f"## Market-relative vs ESPN closing lines, {title} (DESIGN-INFORMED)")
        L.append("")
        L.append(
            "| arm | market | n | corr [CI] | plain slope [CI] | orthogonalised slope [CI] | MAE model / line | "
            "ATS 3+ (win %, CI, ROI at 52.4%) | ATS 7+ | verdict |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for arm, mk in blk.items():
            for m, s in mk.items():
                if s.get("n", 0) < 30 or "corr_ci95" not in s:
                    L.append(f"| {arm} | {m} | {s.get('n')} | - | - | - | - | - | - | - |")
                    continue

                def ats(a: dict[str, Any]) -> str:
                    return (
                        f"{a['wins']}-{a['losses']}-{a['pushes']} ({_fmt(a['win_pct'], 1)}%, {a['win_pct_ci95']}, "
                        f"{_fmt(a['roi_pct_at_52_4'], 1)}%)"
                    )

                L.append(
                    f"| {arm} | {m} | {s['n']} | {s['corr_model_minus_line_vs_result_minus_line']:+.3f} {s['corr_ci95']} | "
                    f"{s['slope_plain']:+.3f} {s['slope_plain_ci95']} | {s['slope_orthogonalised']:+.3f} "
                    f"{s['slope_orthogonalised_ci95']} | {s['mae_model']} / {s['mae_line']} | "
                    f"{ats(s['ats_disagree_ge_3'])} | {ats(s['ats_disagree_ge_7'])} | {s['verdict']} |"
                )
        L.append("")
    L.append("## Validation (selection) and fitted pieces")
    L.append("")
    L.append("| combo | feature set | validation margin MAE | validation total MAE | train margin MAE |")
    L.append("|---|---|---|---|---|")
    for c in sorted(spec["candidates"], key=lambda c: c["validation_margin_mae"]):
        L.append(
            f"| {c['combo']} | {c['feature_set']} | {c['validation_margin_mae']:.3f} | {c['validation_total_mae']:.3f} "
            f"| {c['train_margin_mae']:.3f} |"
        )
    L.append("")
    L.append(f"Validation scores (selected): {json.dumps(spec['validation_scores'])}")
    L.append(f"Stage 2: {json.dumps(spec['stage2'])}")
    L.append(
        f"Margin pmf SD coefficients {spec['margin_pmf']['sd_coef']} on {spec['margin_pmf']['sd_terms']}; total SDs {spec['total_sds']}."
    )
    L.append("")
    return "\n".join(L) + "\n"


# ================================================================================================ artifact
FIXTURE_SEASON = 2023  # validation window; the fixture never touches holdout rows
FIXTURE_TEAMS = 30
FIXTURE_FIELDS = (
    "event_id",
    "season",
    "kickoff_utc",
    "team_id",
    "opponent_id",
    "is_home",
    "neutral_site",
    "game_class",
    "points_for",
    "off_plays",
    "off_plays_ng",
    "off_success_rate_ng",
    "off_yards_per_play_ng",
    "off_explosive_rate_ng",
    "off_points_per_drive_ng",
    "off_turnovers",
)


def artifact_payload(cand: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from model_prediction.production_registry import compute_artifact_hash

    sel = cand["candidate"]
    prior = load_prior(sel["arm"])
    mult = float(sel["lambda_multiplier"])
    payload: dict[str, Any] = {
        "model_version": "cfb-structural-v7",
        "league": "NCAAF",
        "sport": "ncaaf",
        "schema_version": "1",
        "method": "cfb_v7_pit_pbp_ridge_ratings_linear_points_keyed_margin_pmf",
        "note": (
            "RESEARCH CHALLENGER (quick-261121-v7), never a champion, never Main, never the auto-buyer. Registered, "
            "not routed (challengers.NCAAF holds one model per market). Fit on train only, selected on validation, "
            "holdout read once under the pinned rule; market-relative numbers are design-informed."
        ),
        "point_in_time": {
            "result_buffer_hours": core.RESULT_BUFFER_HOURS,
            "research_decision_time": "min(kickoff, 08:00 America/New_York on the kickoff's ET date)",
            "rule": "a game feeds ratings only if kickoff + result_buffer_hours <= decision time",
        },
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
            "cfbd_values": "current-revised (as_published_verified false); preseason rows only",
        },
        "stage2": cand["stage2"],
        "margin_distribution": {
            "support": MARGIN_SUPPORT,
            "key_max": KEY_MAX,
            "recentre_iterations": RECENTRE_ITERATIONS,
            "centring": "mean",
            "key_log_multipliers": cand["margin_pmf"]["theta"],
            "sd_coef": cand["margin_pmf"]["sd_coef"],
            "sd_terms": cand["margin_pmf"]["sd_terms"],
            "sd_scale": SD_SCALE,
            "sd_total_centre": SD_TOTAL_CENTRE,
            "early_games": EARLY_GAMES,
        },
        "total_distribution": {
            "support": TOTAL_SUPPORT,
            "buckets": [list(b) for b in TOTAL_BUCKETS],
            "bucket_sds": cand["total_sds"],
        },
        "training": {
            "spec_sha256": file_sha256(SPEC),
            "rule_sha256": rule_sha256(),
            "report_sha256": file_sha256(REPORT_JSON),
            "train_end": TRAIN_END,
            "validation_end": VAL_END,
            "selected": sel,
            "holdout_verdict": report["gate"]["verdict"],
        },
        "qualification": {
            "status": "challenger",
            "qualified": False,
            "note": "Research only; the 2027 forward gate (docs/CFB_V7_2027_GATE.md) decides; promotion only via "
            "model_promotion promote.",
        },
        "frozen_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "promotion_protocol_hash": rule_sha256(),
    }
    payload["artifact_hash"] = compute_artifact_hash(payload)
    return payload


def parity_fixture(cand: dict[str, Any]) -> dict[str, Any]:
    """A small real universe (30 FBS teams, their mutual 2022-2023 games) priced by this script's pipeline."""
    from model_prediction.domain import parse_utc

    raw = []
    with PBP.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                if r["season"] in (FIXTURE_SEASON - 1, FIXTURE_SEASON):
                    raw.append(r)
    counts: dict[str, int] = defaultdict(int)
    for r in raw:
        if r["game_class"] == "FBS-FBS" and r["season"] == FIXTURE_SEASON:
            counts[r["team_id"]] += 1
    teams = set(sorted(counts, key=lambda t: (-counts[t], int(t)))[:FIXTURE_TEAMS])
    sub = [
        {k: r.get(k) for k in FIXTURE_FIELDS}
        for r in raw
        if r["team_id"] in teams and r["opponent_id"] in teams
    ]
    rows = sorted(
        (core.team_game_from_row(r) for r in sub), key=lambda r: (r.kickoff, r.event_id, -r.home_sign)
    )
    classes = core.season_classes(rows)
    sel = cand["candidate"]
    params = prior_params(load_prior(sel["arm"]), float(sel["lambda_multiplier"]), sel["half_life_days"])
    covs = load_cfbd_covariates().get(FIXTURE_SEASON, {}) if sel["arm"] != "core" else {}
    covs = {t: z for t, z in covs.items() if t in teams}
    finals = core.fit_finals([r for r in rows if r.season == FIXTURE_SEASON - 1], FIXTURE_SEASON - 1, classes)
    ordered = sorted(teams, key=int)
    cases = []
    for i, day in enumerate(("2023-08-26", "2023-09-16", "2023-10-14", "2023-11-11")):
        decision = parse_utc(f"{day}T12:00:00Z")
        state = core.fit_state(rows, FIXTURE_SEASON, decision, classes, finals, params, ordered, covs)
        for j in range(3):
            home, away = ordered[(3 * i + 2 * j) % len(ordered)], ordered[(3 * i + 2 * j + 7) % len(ordered)]
            neutral = j == 2
            mf = core.matchup_features(state, home, away, neutral=neutral, classes=classes)
            walk_row = {
                "home_e": mf.home.values,
                "away_e": mf.away.values,
                "home_games": mf.home_games,
                "away_games": mf.away_games,
                "is_fcs": mf.is_fbs_vs_fcs,
            }
            pr = v7_probabilities([walk_row], cand["stage2"], cand["margin_pmf"], cand["total_sds"])
            pt = total_pmf(pr["tot"], pr["tsd"])[0]
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
                        "home_cover": float(pm[M_GRID > 3.5].sum()),
                        "over": float(pt[T_GRID > 50.5].sum()),
                    },
                }
            )
    return {
        "season": FIXTURE_SEASON,
        "spec_sha256": file_sha256(SPEC),
        "rows": sub,
        "covariates": covs,
        "cases": cases,
    }


def cmd_artifact(_: argparse.Namespace) -> int:
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    if not str(report["gate"]["verdict"]).startswith("PASS"):
        raise SystemExit(f"verdict {report['gate']['verdict']}: no artifact is written")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    pkg = report["gate"]["package_arm"]
    cand = (
        {k: spec[k] for k in ("stage2", "margin_pmf", "total_sds")} | {"candidate": spec["selected"]}
        if pkg == "v7"
        else spec["alt"]
    )
    payload = artifact_payload(cand, report)
    ARTIFACT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FIXTURE.write_text(json.dumps(parity_fixture(cand), sort_keys=True) + "\n", encoding="utf-8")
    print(ARTIFACT, payload["artifact_hash"])
    return 0


# ================================================================================================ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prep")
    pp.add_argument("--cfbd", action="store_true")
    pp.set_defaults(func=cmd_prep)
    w = sub.add_parser("walk")
    w.add_argument("--workers", type=int, default=6)
    w.add_argument("--only", default=None)
    w.add_argument("--arms", default="core")
    w.add_argument("--mults", default=None)
    w.set_defaults(func=cmd_walk)
    sub.add_parser("fit").set_defaults(func=cmd_fit)
    sub.add_parser("pin").set_defaults(func=cmd_pin)
    h = sub.add_parser("holdout")
    h.add_argument("--rule-sha", required=True)
    h.set_defaults(func=cmd_holdout)
    sub.add_parser("v5repro").set_defaults(func=cmd_v5repro)
    sub.add_parser("consensus").set_defaults(func=cmd_consensus)
    sub.add_parser("dryrun").set_defaults(func=cmd_dryrun)
    sub.add_parser("artifact").set_defaults(func=cmd_artifact)
    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
