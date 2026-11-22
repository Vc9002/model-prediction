"""D1_prior_regression: v7 with a regressed prior-season rating as the ridge prior (protocol 261120 grid entry).

Pinned spec (protocol_261120.json, candidate_grid.D1_prior_regression): "Replace the v7 conference-tier prior
with a preseason prior = prior-season final ridge rating regressed toward the conference mean by a fitted
factor (plus AP rank as in v7)." Free parameters: regression factor, combiner, bucket scales. Nothing else
moves. Carry 0.1, lambda 2, kappa 1.4, the F1 form and the tail knot are v7's served values. No fade or floor
parameter is added, because the protocol lists none. The ridge's lambda is the implicit fade: the prior weighs
as much as two current-season games.

Prior for team t on a day of season s (margin points, the v7 ridge target):

    final_prev[t] = v7 served ridge (rg_c010_l02_k14) fit on every game of seasons <= s-1, as of season s-1
    cm[t]         = mean(final_prev) over t's season-s conference members that have a previous rating
    prior[t]      = cm[t] + (1 - r) * (final_prev[t] - cm[t]) + rank_bonus[t]
    rank_bonus[t] = 1.4 * 8 * (26 - AP rank) / 28 for ranked teams (v7's rank term, last known rank)

A team without a previous rating (fewer than MIN_PREV_GAMES D1 games in s-1, an independent, or a conference
with fewer than MIN_CONF_TEAMS rated members) keeps v7's tier prior. So does every team when r is "off".

Point in time: a day's history is every game that tipped before Eastern midnight of that day. final_prev is
built from that history's games of seasons <= s-1 only. Conference membership for season s is the as-of map,
which is public before tip-off, the same rule v7 uses. r, the F1 coefficients and the bucket scales are fit per
fold on the three other development seasons and never on the predicted season.

Subcommands (run from the repo root, OPENBLAS_NUM_THREADS=1):
  components  day-by-day ridge for every r in R_GRID plus the v7 tier-prior arm (outcome-free) -> components
  repro       tier-prior arm through the SERVED v7 scheme, compared with `grader.py export --model v7_served`
  build       per-fold fit of r / F1 / scales on training seasons -> predictions + sidecar + fit report
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "src", REPO / "scripts", REPO / "scripts" / "ncaab_research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import grader
import ncaab_residual as nr

from model_prediction.models.ncaab_spread_v6 import ELO_POINTS_PER_MARGIN_POINT
from model_prediction.models.ncaab_v7_multiseason import (
    HCA_LAMBDA,
    MIN_FIT_GAMES,
    UNMAPPED_D1_IDS,
    accumulate_normal_equations,
    geometric_season_weights,
    ridge_by_accumulation,
)
from model_prediction.models.ncaab_v7_serving import (
    BUCKET_CUTS,
    TAIL_KNOT,
    V7_VARIANT,
    combine_f1,
)
from model_prediction.models.ncaab_v7_shadow import (
    HCA_PRIOR_POINTS,
    history_neutral,
    student_t_scale_mle,
)

GRID_NAME = "D1_prior_regression"
CANDIDATE = "D1 prior-season ridge regressed to conference mean (fitted r) as v7 ridge prior, F1 refit v1"
OUT = REPO / "outputs" / "ncaab" / "research_261120" / GRID_NAME
COMPONENTS_OUT = OUT / "d1_components.jsonl"
PRED_OUT = OUT / "d1_prior_regression.jsonl"
DEV_SEASONS = (2023, 2024, 2025, 2026)

# v7 served ridge variant rg_c010_l02_k14 (frozen config, pin 2)
CARRY = 0.10
LAM = 2.0
KAPPA = 1.4
RANK_PER_SLOT = 8.0  # v7 rank_prior_per_slot (Elo points per AP slot above 26)
# Pinned before any outcome was joined; never tuned after a read.
R_GRID = tuple(round(0.05 * i, 2) for i in range(21))  # regression fraction toward the conference mean
MIN_PREV_GAMES = 10  # D1 games in season s-1 for a team to carry a previous rating
MIN_CONF_TEAMS = 3  # rated members needed for a conference mean
MIN_HISTORY_GAMES = 20  # v7 component runner skips days with fewer history games
DF_T = 8.0
N_BUCKETS = 4


def r_key(r: float) -> str:
    return f"d1_r{round(r * 100):03d}"


# ----------------------------------------------------------------------------------------------- prior maths
def conference_targets(
    final_prev: np.ndarray,
    has_prev: np.ndarray,
    conf_of: Sequence[str | None],
    min_conf_teams: int = MIN_CONF_TEAMS,
) -> tuple[np.ndarray, np.ndarray]:
    """Per team: the conference mean of previous ratings, and whether the team gets a regressed prior.

    ``conf_of[i]`` is team i's conference id in the CURRENT season (None when not D1 this season).
    """
    n = len(final_prev)
    cm = np.full(n, np.nan)
    valid = np.zeros(n, dtype=bool)
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(conf_of):
        if c is None or c in UNMAPPED_D1_IDS or not has_prev[i]:
            continue
        groups.setdefault(c, []).append(i)
    for members in groups.values():
        if len(members) < min_conf_teams:
            continue
        idx = np.array(members)
        cm[idx] = float(np.mean(final_prev[idx]))
        valid[idx] = True
    return cm, valid


def regressed_prior(
    final_prev: np.ndarray,
    cm: np.ndarray,
    valid: np.ndarray,
    rank_bonus: np.ndarray,
    fallback: np.ndarray,
    r: float,
) -> np.ndarray:
    """cm + (1 - r) * (final_prev - cm) + rank bonus where a target exists, else v7's tier prior."""
    reg = np.where(valid, np.nan_to_num(cm) + (1.0 - r) * (final_prev - np.nan_to_num(cm)), 0.0)
    return np.where(valid, reg + rank_bonus, fallback)


def solve_many(
    gram: tuple[np.ndarray, np.ndarray], priors: np.ndarray, lam: float, hca_prior: float = HCA_PRIOR_POINTS
) -> tuple[np.ndarray, np.ndarray]:
    """ridge_by_accumulation for several prior targets at once (columns of ``priors``, shape n x k)."""
    g, b = gram
    n = g.shape[0] - 1
    diag = np.full(n + 1, lam)
    diag[n] = HCA_LAMBDA
    target = np.vstack([priors, np.full((1, priors.shape[1]), hca_prior)])
    sol = np.linalg.solve(g + np.diag(diag), b[:, None] + diag[:, None] * target)
    return sol[:n], sol[n]


# ----------------------------------------------------------------------------------------------- engine
class RidgeEngine:
    """Replays v7's ridge day by day (exactly as the component runner did) with extra prior targets."""

    def __init__(self, events: list[dict[str, Any]], model: Any | None = None) -> None:
        from model_prediction.models.ncaab_v7_multiseason import (
            NCAABV7MultiSeasonModel,
            build_asof_conferences,
        )

        self.events = events
        self.conf_ids = build_asof_conferences(events)
        self.model = model or NCAABV7MultiSeasonModel(self.conf_ids, stats_root=nr.STATS_ROOT, asof_map=True)
        games = nr.to_game_records(events)
        order = sorted(range(len(games)), key=lambda i: games[i].start)
        self.games = [games[i] for i in order]
        self.starts = [g.start for g in self.games]
        self.neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
        ti = self.model.team_index
        self.n = len(ti)
        gs = self.games
        self.season = np.array([int(g.season_year or 0) for g in gs])
        self.d1 = np.array(
            [
                g.home_team in ti
                and g.away_team in ti
                and g.home_team in self.conf_ids.get(int(g.season_year or 0), {})
                and g.away_team in self.conf_ids.get(int(g.season_year or 0), {})
                for g in gs
            ],
            dtype=bool,
        )
        self.hidx = np.array([ti.get(g.home_team, -1) for g in gs], dtype=int)
        self.aidx = np.array([ti.get(g.away_team, -1) for g in gs], dtype=int)
        self.margin = np.array([g.margin for g in gs], dtype=float)
        stats = self.model._stats
        self.venue = np.array([0.0 if history_neutral(g, stats) else 1.0 for g in gs])
        self._prev: dict[int, tuple[np.ndarray, np.ndarray, int]] = {}

    def base_tier(self, season_now: int, hist: Sequence[Any]) -> tuple[np.ndarray, np.ndarray]:
        """v7's (tier + rank) prior in margin points before kappa, and the rank part alone."""
        m = self.model
        tiers_now = m.tier_by_season.get(season_now, {})
        ranks = m._stats.last_known_ranks(hist) if m.rank_prior_per_slot > 0 else {}
        base = np.zeros(self.n)
        rank_part = np.zeros(self.n)
        for t, i in m.team_index.items():
            tier = tiers_now.get(t)
            if tier is None:
                for s in reversed(m.seasons):
                    if s < season_now and t in m.tier_by_season[s]:
                        tier = m.tier_by_season[s][t]
                        break
            if tier is None:
                tier = 1500.0
            if t in ranks:
                bonus = m.rank_prior_per_slot * (26 - ranks[t])
                tier += bonus
                rank_part[i] = bonus / ELO_POINTS_PER_MARGIN_POINT
            base[i] = (tier - 1500.0) / ELO_POINTS_PER_MARGIN_POINT
        return base, rank_part

    def _fit(self, k: int, season_now: int, prior: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
        """v7's ridge on the first k history games (the served arm), plus the kept mask."""
        w = geometric_season_weights(self.season[:k], season_now, CARRY) * self.d1[:k]
        keep = w > 0
        if int(keep.sum()) < MIN_FIT_GAMES:
            return prior.copy(), HCA_PRIOR_POINTS, keep
        h, a = self.hidx[:k][keep], self.aidx[:k][keep]
        r, hca = ridge_by_accumulation(
            h, a, self.margin[:k][keep], w[keep], self.venue[:k][keep], self.n, prior, LAM
        )
        return r, hca, keep

    def prev_final(self, season_now: int, k_hist: int) -> tuple[np.ndarray, np.ndarray]:
        """Previous-season final ratings and the has-previous flag, from history games of seasons < s only."""
        if season_now in self._prev:
            ratings, has, k_prev = self._prev[season_now]
            # every season s-1 game precedes every season s day, so the prefix must be the same set
            assert k_prev <= k_hist
            return ratings, has
        later = np.flatnonzero(self.season[:k_hist] >= season_now)
        k_prev = int(later[0]) if len(later) else k_hist
        if (self.season[:k_prev] >= season_now).any() or (self.season[k_prev:k_hist] < season_now).any():
            raise AssertionError("seasons interleave: the previous-season prefix is not exactly seasons < s")
        hist_prev = self.games[:k_prev]
        base, _ = self.base_tier(season_now - 1, hist_prev)
        ratings, _, _ = self._fit(k_prev, season_now - 1, KAPPA * base)
        cur = (self.season[:k_prev] == season_now - 1) & self.d1[:k_prev]
        counts = np.bincount(self.hidx[:k_prev][cur], minlength=self.n) + np.bincount(
            self.aidx[:k_prev][cur], minlength=self.n
        )
        prev_map = self.conf_ids.get(season_now - 1, {})
        has = np.array(
            [t in prev_map and counts[i] >= MIN_PREV_GAMES for t, i in self.model.team_index.items()]
        )
        order = np.array(list(self.model.team_index.values()))
        has_by_idx = np.zeros(self.n, dtype=bool)
        has_by_idx[order] = has
        self._prev[season_now] = (ratings, has_by_idx, k_prev)
        return ratings, has_by_idx

    def day_rows(self, day: str, todays: Sequence[Any]) -> list[dict[str, Any]]:
        from model_prediction.domain import EASTERN

        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN)
        k = bisect.bisect_left(self.starts, cutoff)
        hist = self.games[:k]
        if len(hist) < MIN_HISTORY_GAMES or not todays:
            return []
        m = self.model
        season_now = m.current_season(hist, todays[0].start)
        tiers_now = m.tier_by_season.get(season_now, {})
        base, rank_part = self.base_tier(season_now, hist)
        fallback = KAPPA * base
        rank_bonus = KAPPA * rank_part
        r_off, hca_off, keep = self._fit(k, season_now, fallback)
        final_prev, has_prev = self.prev_final(season_now, k)
        inv = {i: t for t, i in m.team_index.items()}
        conf_now = self.conf_ids.get(season_now, {})
        cm, valid = conference_targets(final_prev, has_prev, [conf_now.get(inv[i]) for i in range(self.n)])
        priors = np.column_stack(
            [regressed_prior(final_prev, cm, valid, rank_bonus, fallback, r) for r in R_GRID]
        )
        if int(keep.sum()) < MIN_FIT_GAMES:
            rr, hh = priors, np.full(len(R_GRID), HCA_PRIOR_POINTS)
        else:
            w = geometric_season_weights(self.season[:k], season_now, CARRY) * self.d1[:k]
            gram = accumulate_normal_equations(
                self.hidx[:k][keep],
                self.aidx[:k][keep],
                self.margin[:k][keep],
                w[keep],
                self.venue[:k][keep],
                self.n,
            )
            rr, hh = solve_many(gram, priors, LAM)
        rows = []
        for g in todays:
            if g.home_team not in tiers_now or g.away_team not in tiers_now:
                continue
            if g.home_team not in m.team_index or g.away_team not in m.team_index:
                continue
            hi, ai = m.team_index[g.home_team], m.team_index[g.away_team]
            neutral = self.neutral[g.event_id]
            row: dict[str, Any] = {
                "event_id": g.event_id,
                "day": day,
                "season_year": int(g.season_year or 0),
                "season_now": season_now,
                "ridge_off": round(float(r_off[hi] - r_off[ai] + (0.0 if neutral else hca_off)), 4),
                "has_prev_home": bool(valid[hi]),
                "has_prev_away": bool(valid[ai]),
                "prev_minus_cm_home": None if not valid[hi] else round(float(final_prev[hi] - cm[hi]), 4),
                "prev_minus_cm_away": None if not valid[ai] else round(float(final_prev[ai] - cm[ai]), 4),
            }
            for j, r in enumerate(R_GRID):
                row[r_key(r)] = round(float(rr[hi, j] - rr[ai, j] + (0.0 if neutral else hh[j])), 4)
            rows.append(row)
        return rows


def run_components(seasons: Sequence[int] = DEV_SEASONS, out: Path = COMPONENTS_OUT) -> int:
    events = grader.load_history_events()  # completed, unlocked, sorted by tip time
    eng = RidgeEngine(events)
    from model_prediction.domain import EASTERN

    by_day: dict[str, list[Any]] = {}
    for g in eng.games:
        if int(g.season_year or 0) in seasons:
            by_day.setdefault(g.start.astimezone(EASTERN).date().isoformat(), []).append(g)
    t0 = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for i, day in enumerate(sorted(by_day)):
            grader.assert_unlocked(by_day[day][0].season_year, day)
            for row in eng.day_rows(day, by_day[day]):
                fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                n += 1
            if i % 100 == 0:
                print(f"  day {i}/{len(by_day)} {day} rows {n} {time.time() - t0:.0f}s", flush=True)
    print(f"components {n} rows -> {out} ({time.time() - t0:.0f}s)", flush=True)
    return n


# ----------------------------------------------------------------------------------------------- combiner
def sbucket(sample: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BUCKET_CUTS), np.asarray(sample), side="right")


def tail_term(v6: np.ndarray) -> np.ndarray:
    return np.sign(v6) * np.maximum(np.abs(v6) - TAIL_KNOT, 0.0)


def f1_design(ridge: np.ndarray, v6: np.ndarray) -> np.ndarray:
    return np.column_stack([np.asarray(ridge, float), tail_term(np.asarray(v6, float))])


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.solve(x.T @ x + 1e-6 * np.eye(x.shape[1]), x.T @ y)  # as scripts/ncaab_v7_multiseason


def fit_f1(train: pd.DataFrame, col: str) -> dict[str, Any]:
    """F1 coefficients (no intercept) and per-bucket t8 scales on ``train`` (D1 rows with ``margin``)."""
    x = f1_design(train[col].to_numpy(float), train["v6_mean"].to_numpy(float))
    y = train["margin"].to_numpy(float)
    coef = ols(x, y)
    resid = y - x @ coef
    b = sbucket(train["sample"].to_numpy())
    scales = [student_t_scale_mle(resid[b == k], DF_T) for k in range(N_BUCKETS)]
    return {"col": col, "coef": coef.tolist(), "scales": scales, "rmse": float(np.sqrt(np.mean(resid**2)))}


def apply_f1(df: pd.DataFrame, cfg: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    x = f1_design(df[cfg["col"]].to_numpy(float), df["v6_mean"].to_numpy(float))
    return x @ np.array(cfg["coef"]), np.array(cfg["scales"])[sbucket(df["sample"].to_numpy())]


def fit_fold(train: pd.DataFrame, predicted_season: int) -> dict[str, Any]:
    """Choose r by training RMSE after an F1 refit (training seasons only), then fit F1 and scales."""
    seasons = sorted({int(s) for s in train["season"]})
    if predicted_season in seasons:
        raise ValueError(f"leak: predicted season {predicted_season} in the fit rows {seasons}")
    curve = {r_key(r): fit_f1(train, r_key(r))["rmse"] for r in R_GRID}
    best = min(R_GRID, key=lambda r: (curve[r_key(r)], r))
    cfg = fit_f1(train, r_key(best))
    off = fit_f1(train, "ridge_off")
    early = train["sample"].to_numpy() < 8
    diag = {}
    for name, c in (("tier_prior_refit", off), ("chosen_r", cfg)):
        mu, _ = apply_f1(train, c)
        e = train["margin"].to_numpy(float) - mu
        diag[name] = {
            "rmse_all": round(float(np.sqrt(np.mean(e**2))), 5),
            "rmse_sample_lt8": round(float(np.sqrt(np.mean(e[early] ** 2))), 5),
            "rmse_by_bucket": {
                lab: round(float(np.sqrt(np.mean(e[sbucket(train["sample"].to_numpy()) == k] ** 2))), 5)
                for k, lab in enumerate(("0", "1-2", "3-7", "8+"))
            },
        }
    return {
        "predicted_season": predicted_season,
        "fit_seasons": seasons,
        "n_train": len(train),
        "r_selected": best,
        "r_at_grid_edge": best in (R_GRID[0], R_GRID[-1]),
        "rmse_curve": {k: round(v, 5) for k, v in curve.items()},
        "config": cfg,
        "tier_prior_refit_config": off,
        "train_diagnostics": diag,
    }


# ----------------------------------------------------------------------------------------------- frames
def load_frame() -> pd.DataFrame:
    """Archive identity + v6 fields + sample (+ margin, read ONLY for fit seasons) joined to D1 components."""
    a = grader.load_archive(DEV_SEASONS)
    a = a[a["d1"]].copy()
    comp = pd.read_json(COMPONENTS_OUT, lines=True, dtype={"event_id": str})
    comp["event_id"] = comp["event_id"].astype(str)
    df = a.merge(comp, on="event_id", how="left", validate="one_to_one")
    missing = df[r_key(R_GRID[0])].isna()
    if missing.any():
        raise RuntimeError(f"{int(missing.sum())} D1 archive games have no D1 component row")
    if (df["season"] != df["season_year"]).any() or (df["game_date"] != df["day"]).any():
        raise RuntimeError("component rows disagree with the archive on season or day")
    return df


def submission(df: pd.DataFrame, mu: np.ndarray, scale: np.ndarray) -> pd.DataFrame:
    dist = np.full(len(df), "t8")
    p = grader.served_ml_blend(df["event_id"].tolist(), mu, scale, dist)
    out = df[["event_id", "season", "game_date", "home_team", "away_team", "neutral"]].copy()
    out["mu_margin"] = mu
    out["scale_margin"] = scale
    out["dist"] = "t8"
    out["p_home_ml"] = p
    out["games_played_min"] = df["sample"].astype(int).to_numpy()
    return out.reset_index(drop=True)


def served_scheme(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """The SERVED v7 scheme on ``col``: frozen F1 for 2025/2026, m5r LOSO (other fit season) for 2023/2024."""
    mu = np.full(len(df), np.nan)
    sc = np.full(len(df), np.nan)
    for s in (2025, 2026):
        m = (df["season"] == s).to_numpy()
        for i in np.flatnonzero(m):
            mu[i], sc[i] = combine_f1(
                float(df[col].iat[i]), float(df["v6_mean"].iat[i]), int(df["sample"].iat[i])
            )
    for hold, other in ((2023, 2024), (2024, 2023)):
        cfg = fit_f1(df[df["season"] == other], col)
        m = (df["season"] == hold).to_numpy()
        mu[m], sc[m] = apply_f1(df[m], cfg)
    return submission(df, mu, sc)


def cmd_repro(args: argparse.Namespace) -> int:
    df = load_frame()
    ref_ridge = {}
    for rec in grader._iter_jsonl(grader.COMPONENTS):
        ref_ridge[str(rec["event_id"])] = rec.get(V7_VARIANT)
    ref = df["event_id"].map(ref_ridge).astype(float).to_numpy()
    d_ridge = np.abs(df["ridge_off"].to_numpy(float) - ref)
    mine = served_scheme(df, "ridge_off")
    exp = pd.read_json(args.export, lines=True, dtype={"event_id": str})
    exp["event_id"] = exp["event_id"].astype(str)
    j = mine.merge(exp, on="event_id", suffixes=("", "_v7"), how="outer", indicator=True)
    both = j[j["_merge"] == "both"]
    res = {
        "ridge_vs_archive_rg_c010_l02_k14": {
            "n": int(np.isfinite(d_ridge).sum()),
            "max_abs_diff": float(np.nanmax(d_ridge)),
            "n_abs_diff_gt_1e-4": int((d_ridge > 1e-4).sum()),
        },
        "served_scheme_vs_v7_export": {
            "n_mine": len(mine),
            "n_export": len(exp),
            "n_only_mine": int((j["_merge"] == "left_only").sum()),
            "n_only_export": int((j["_merge"] == "right_only").sum()),
            "mu_max_abs_diff": float(np.max(np.abs(both["mu_margin"] - both["mu_margin_v7"]))),
            "scale_max_abs_diff": float(np.max(np.abs(both["scale_margin"] - both["scale_margin_v7"]))),
            "p_home_ml_max_abs_diff": float(np.max(np.abs(both["p_home_ml"] - both["p_home_ml_v7"]))),
            "by_season_n": {int(s): int(n) for s, n in both.groupby("season").size().items()},
        },
    }
    for s, g in both.groupby("season"):
        res["served_scheme_vs_v7_export"][f"mu_max_abs_diff_{int(s)}"] = float(
            np.max(np.abs(g["mu_margin"] - g["mu_margin_v7"]))
        )
    (OUT / "repro_check.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))
    return 0


def cmd_build(_args: argparse.Namespace) -> int:
    df = load_frame()
    parts, folds = [], {}
    for s in DEV_SEASONS:
        train = df[df["season"] != s]
        fold = fit_fold(train, s)
        test = df[df["season"] == s]
        mu, sc = apply_f1(test, fold["config"])
        parts.append(submission(test, mu, sc))
        folds[str(s)] = fold
        print(
            f"fold {s}: r {fold['r_selected']} coef {np.round(fold['config']['coef'], 4).tolist()} "
            f"scales {np.round(fold['config']['scales'], 3).tolist()} train rmse {fold['config']['rmse']:.4f} "
            f"(tier-prior refit {fold['tier_prior_refit_config']['rmse']:.4f})",
            flush=True,
        )
    pred = pd.concat(parts).sort_values(["game_date", "event_id"]).reset_index(drop=True)
    pred.to_json(PRED_OUT, orient="records", lines=True)
    code_sha = nr.sha256_file(Path(__file__))
    meta = {
        "candidate": CANDIDATE,
        "grid_name": GRID_NAME,
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "code_sha256": code_sha,
        "fit_seasons_by_predicted_season": {k: v["fit_seasons"] for k, v in folds.items()},
        "history_cutoff": "strictly before Eastern midnight of the game day",
        "data_sources": {
            str(grader.COMPONENTS.relative_to(REPO)): nr.sha256_file(grader.COMPONENTS),
            str(nr.SCOREBOARD.relative_to(REPO)): nr.sha256_file(nr.SCOREBOARD),
            str(COMPONENTS_OUT.relative_to(REPO)): nr.sha256_file(COMPONENTS_OUT),
        },
        "pinned_knobs": {
            "R_GRID": list(R_GRID),
            "MIN_PREV_GAMES": MIN_PREV_GAMES,
            "MIN_CONF_TEAMS": MIN_CONF_TEAMS,
            "carry": CARRY,
            "lambda": LAM,
            "kappa": KAPPA,
        },
    }
    PRED_OUT.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = {
        "folds": folds,
        "n_predictions": len(pred),
        "by_season": pred.groupby("season").size().to_dict(),
    }
    (OUT / "fit_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        f"wrote {len(pred)} predictions -> {PRED_OUT}; sha256 {hashlib.sha256(PRED_OUT.read_bytes()).hexdigest()}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("components")
    rp = sub.add_parser("repro")
    rp.add_argument("--export", required=True, help="grader.py export --model v7_served output")
    sub.add_parser("build")
    args = ap.parse_args(argv)
    if args.cmd == "components":
        run_components()
        return 0
    if args.cmd == "repro":
        return cmd_repro(args)
    return cmd_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
