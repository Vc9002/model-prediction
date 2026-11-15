"""NCAAB v7 SHADOW challenger research model (inert).

Not registered anywhere: no entry in ``config/production.yaml`` / ``config/model.yaml``, no
artifact under ``config/models/``, no champion route. ``ncaab-spread-v6`` / ``ncaab-total-v6``
are untouched; this module only *reads* them. It exists so that every v7 feature idea of
quick task 261115-1m7 (docs: ``outputs/ncaab/ncaab_v7_research.md``) is a pure function that the
research runner (``scripts/ncaab_v7_research.py``) and any future serving path call identically.

Train/serve parity by construction: the runner never re-implements this math. It calls
``NCAABV7ShadowModel.components`` day by day over point-in-time history (the *feature function*)
and ``margin_from_components`` / ``total_from_components`` (the *combiner*) with a config fitted
on the train window. Serving would call the same two functions.

Point-in-time contract: ``components`` only reads ``history`` (games the caller may see) and
``upcoming`` (identity, start time, neutral flag). Archived scoreboards / box scores are keyed by
event id and only consulted for ids present in ``history``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from scipy import stats

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.ncaab_possessions import NCAABPossessionStore
from ..features.ncaab_scoreboard_stats import NCAABScoreboardStats
from .basketball import UpcomingGame
from .ncaab_spread_v6 import (
    ARTIFACT_PATH as SPREAD_V6_ARTIFACT,
)
from .ncaab_spread_v6 import (
    ELO_POINTS_PER_MARGIN_POINT,
    NCAABSpreadModelV6,
    load_verified_artifact,
)
from .ncaab_total_v6 import ncaab_total_v6_model

NCAAB_V7_SHADOW_VERSION = "ncaab-v7-shadow"
OFFSEASON_GAP_DAYS = 150.0
HCA_PRIOR_POINTS = 3.5  # late-season D1 home edge; only a ridge target, never a served constant
THIN_SAMPLE_GAMES = 8.0  # same horizon v6's dynamic volatility uses
TAIL_KNOT_POINTS = 12.0  # |predicted margin| beyond which the tail term adds slope

# --------------------------------------------------------------------------- pure functions


def ridge_team_strengths(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    margin: np.ndarray,
    weights: np.ndarray,
    neutral: np.ndarray,
    n_teams: int,
    prior: np.ndarray,
    lam: float,
    hca_prior: float = HCA_PRIOR_POINTS,
    hca_lam: float = 20.0,
) -> tuple[np.ndarray, float]:
    """Massey ratings with a per-team prior: min sum w (m - (r_h - r_a + hca*venue))^2 + lam |r - prior|^2.

    ``prior`` is in margin points (a Tier-1 team sits above a Tier-5 team before any game is
    played). ``hca`` is one shared home edge, shrunk toward ``hca_prior``; neutral games carry no
    home indicator. With no games the ratings equal the prior.
    """
    n = len(margin)
    if n == 0:
        return prior.copy(), hca_prior
    x = np.zeros((n, n_teams + 1))
    rows = np.arange(n)
    x[rows, home_idx] = 1.0
    x[rows, away_idx] = -1.0
    x[:, n_teams] = (~neutral).astype(float)
    gram = x.T @ (x * weights[:, None])
    reg = np.eye(n_teams + 1) * lam
    reg[n_teams, n_teams] = hca_lam
    target = np.concatenate([prior, [hca_prior]])
    sol = np.linalg.solve(gram + reg, x.T @ (weights * margin) + reg @ target)
    return sol[:n_teams], float(sol[n_teams])


def season_weights(seasons: np.ndarray, current_season: int, carry: float) -> np.ndarray:
    """1.0 for the current season, ``carry`` for earlier ones (0 drops them entirely)."""
    return np.where(seasons == current_season, 1.0, carry)


def shrunk_mean(total: float, count: float, k: float) -> float:
    """Empirical-Bayes mean toward zero: total / (count + k)."""
    return total / (count + k) if (count + k) > 0 else 0.0


def thin_factor(sample: float) -> float:
    """1 at zero current-season games, 0 once ``THIN_SAMPLE_GAMES`` are played."""
    return max(0.0, min(1.0, 1.0 - float(sample) / THIN_SAMPLE_GAMES))


def student_t_scale_mle(residuals: Sequence[float], df: float = 8.0) -> float:
    """Maximum-likelihood Student-t scale (location fixed at 0) for the given residuals."""
    r = np.asarray(residuals, dtype=float)
    if r.size < 5:
        raise ValueError("need at least 5 residuals to fit a scale")
    lo, hi = 0.5, 4.0 * float(np.std(r) + 1e-9)
    for _ in range(80):  # golden-section search on the negative log-likelihood
        m1 = lo + (hi - lo) * 0.381966
        m2 = lo + (hi - lo) * 0.618034
        f1 = -float(np.sum(stats.t.logpdf(r / m1, df) - math.log(m1)))
        f2 = -float(np.sum(stats.t.logpdf(r / m2, df) - math.log(m2)))
        if f1 < f2:
            hi = m2
        else:
            lo = m1
    return 0.5 * (lo + hi)


def fit_least_squares(x: np.ndarray, y: np.ndarray, ridge: float = 1e-6) -> np.ndarray:
    """Plain (barely regularised) least squares; the stacking layers have a handful of columns."""
    gram = x.T @ x + ridge * np.eye(x.shape[1])
    return np.linalg.solve(gram, x.T @ y)


def win_probability(mean: float, scale: float, df: float = 8.0) -> float:
    """P(home margin > 0) with integer margins: 1 - T((0.5 - mean) / scale)."""
    return float(min(0.9999, max(0.0001, 1.0 - stats.t.cdf((0.5 - mean) / scale, df=df))))


def fit_efficiency_pace(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    neutral: np.ndarray,
    possessions: np.ndarray,
    home_points: np.ndarray,
    away_points: np.ndarray,
    n_teams: int,
    lam_pace: float = 3.0,
    lam_eff: float = 8.0,
) -> dict[str, Any]:
    """Ridge pace and efficiency decomposition: total = poss * (ppp_home + ppp_away).

    possessions   = mu_p + pace_home + pace_away
    ppp_home      = mu_e + off_home + def_away + hca_e * (not neutral)
    ppp_away      = mu_e + off_away + def_home
    All team effects are shrunk to zero (league average).
    """
    n = len(possessions)
    rows = np.arange(n)
    xp = np.zeros((n, n_teams + 1))
    xp[rows, home_idx] = 1.0
    xp[rows, away_idx] += 1.0
    xp[:, n_teams] = 1.0
    reg_p = np.eye(n_teams + 1) * lam_pace
    reg_p[n_teams, n_teams] = 1e-6
    pace = np.linalg.solve(xp.T @ xp + reg_p, xp.T @ possessions)

    ppp_h = home_points / possessions
    ppp_a = away_points / possessions
    # parameters: off[T], def[T], mu_e, hca_e
    xe = np.zeros((2 * n, 2 * n_teams + 2))
    xe[rows, home_idx] = 1.0
    xe[rows, n_teams + away_idx] = 1.0
    xe[rows, 2 * n_teams] = 1.0
    xe[rows, 2 * n_teams + 1] = (~neutral).astype(float)
    xe[n + rows, away_idx] = 1.0
    xe[n + rows, n_teams + home_idx] = 1.0
    xe[n + rows, 2 * n_teams] = 1.0
    y = np.concatenate([ppp_h, ppp_a])
    reg_e = np.eye(2 * n_teams + 2) * lam_eff
    reg_e[2 * n_teams, 2 * n_teams] = 1e-6
    reg_e[2 * n_teams + 1, 2 * n_teams + 1] = 1e-6
    eff = np.linalg.solve(xe.T @ xe + reg_e, xe.T @ y)
    return {
        "mu_p": float(pace[n_teams]),
        "pace": pace[:n_teams],
        "off": eff[:n_teams],
        "def": eff[n_teams : 2 * n_teams],
        "mu_e": float(eff[2 * n_teams]),
        "hca_e": float(eff[2 * n_teams + 1]),
    }


def predict_efficiency_pace_total(fit: Mapping[str, Any], h: int, a: int, neutral: bool) -> float:
    poss = fit["mu_p"] + fit["pace"][h] + fit["pace"][a]
    hca = 0.0 if neutral else fit["hca_e"]
    ppp_h = fit["mu_e"] + fit["off"][h] + fit["def"][a] + hca
    ppp_a = fit["mu_e"] + fit["off"][a] + fit["def"][h]
    return float(poss * (ppp_h + ppp_a))


# --------------------------------------------------------------------------- components


@dataclass
class V7Components:
    """Every point-in-time input the v7 combiner may use, one row per upcoming game."""

    event_id: str
    home: str
    away: str
    neutral: bool
    d1_home: bool
    d1_away: bool
    games_home: int
    games_away: int
    v6_mean: float
    v6_scale: float
    v6_sd: float
    v6_p_home: float
    elo_margin: float
    trend_margin: float
    rest_diff: float
    sample: int
    ridge_c0: float
    ridge_c05: float
    ridge_c10: float
    ridge_hca: float
    team_hca_sum_home: float
    team_hca_n_home: int
    rest_days_home: float
    rest_days_away: float
    games4_home: int
    games4_away: int
    v6_total: float | None = None
    v6_total_sd: float | None = None
    effpace_total: float | None = None
    effpace_poss: float | None = None
    extras: dict[str, float] = field(default_factory=dict)

    @property
    def both_d1(self) -> bool:
        return self.d1_home and self.d1_away

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.update(row.pop("extras"))
        return row


def history_neutral(game: GameRecord, stats_table: NCAABScoreboardStats) -> bool:
    """Archived ESPN ``neutralSite`` when the scoreboard is on disk, else the TRNMNT proxy."""
    record = stats_table.get(game.event_id)
    if record is not None:
        return bool(record.neutral_site)
    return game.competition_type == "TRNMNT"


class NCAABV7ShadowModel:
    """Computes the v7 feature components; the combiner functions below turn them into forecasts."""

    def __init__(
        self,
        ridge_lam: float = 5.0,
        rank_prior_per_slot: float = 8.0,
        min_fit_games: int = 30,
        with_totals: bool = True,
        v6_spread: NCAABSpreadModelV6 | None = None,
    ) -> None:
        self.ridge_lam = ridge_lam
        self.rank_prior_per_slot = rank_prior_per_slot
        self.min_fit_games = min_fit_games
        self.with_totals = with_totals
        self.version = NCAAB_V7_SHADOW_VERSION
        self._v6 = v6_spread or NCAABSpreadModelV6.from_artifact(load_verified_artifact(SPREAD_V6_ARTIFACT))
        self._v6_total = ncaab_total_v6_model() if with_totals else None
        self._stats = NCAABScoreboardStats()
        self._poss = NCAABPossessionStore() if with_totals else None
        self._d1 = self._v6.team_priors

    # -- helpers
    def _current_season(self, history: Sequence[GameRecord], start: datetime) -> int:
        last = max(history, key=lambda g: g.start)
        season = int(last.season_year or 0)
        if (start - last.start).total_seconds() / 86400.0 > OFFSEASON_GAP_DAYS:
            season += 1
        return season

    def components(
        self, history: Sequence[GameRecord], upcoming: Sequence[UpcomingGame]
    ) -> list[V7Components]:
        if not upcoming:
            return []
        start = parse_utc(upcoming[0].event_start_utc)
        season_now = self._current_season(history, start)
        v6_preds = {
            p.event_id: p for p in self._v6.predict_games(history, upcoming) if p.market_type == "moneyline"
        }
        v6_totals: dict[str, Any] = {}
        if self._v6_total is not None:
            v6_totals = {
                p.event_id: p
                for p in self._v6_total.predict_games(history, upcoming)
                if p.market_type == "total"
            }

        teams = sorted(
            {g.home_team for g in history}
            | {g.away_team for g in history}
            | {u.home_team for u in upcoming}
            | {u.away_team for u in upcoming}
        )
        index = {t: i for i, t in enumerate(teams)}
        n_teams = len(teams)
        ranks = self._stats.last_known_ranks(history) if self.rank_prior_per_slot > 0 else {}
        prior = np.zeros(n_teams)
        for t, i in index.items():
            if t in self._d1:
                tier = self._d1[t] + self.rank_prior_per_slot * (26 - ranks[t]) if t in ranks else self._d1[t]
                prior[i] = (tier - 1500.0) / ELO_POINTS_PER_MARGIN_POINT

        d1_games = [g for g in history if g.home_team in self._d1 and g.away_team in self._d1]
        hidx = np.array([index[g.home_team] for g in d1_games], dtype=int)
        aidx = np.array([index[g.away_team] for g in d1_games], dtype=int)
        margin = np.array([g.margin for g in d1_games], dtype=float)
        seasons = np.array([int(g.season_year or 0) for g in d1_games])
        neut = np.array([history_neutral(g, self._stats) for g in d1_games], dtype=bool)

        fits: dict[float, tuple[np.ndarray, float]] = {}
        for carry in (0.0, 0.5, 1.0):
            w = season_weights(seasons, season_now, carry)
            keep = w > 0
            if int(np.sum(seasons == season_now)) < self.min_fit_games:
                fits[carry] = (prior.copy(), HCA_PRIOR_POINTS)
                continue
            fits[carry] = ridge_team_strengths(
                hidx[keep], aidx[keep], margin[keep], w[keep], neut[keep], n_teams, prior, self.ridge_lam
            )

        # per-team home residual sums under the current-season, no-carry fit
        r0, hca0 = fits[0.0]
        cur = (seasons == season_now) & (~neut)
        team_sum: dict[int, float] = {}
        team_n: dict[int, int] = {}
        for h, a, m in zip(hidx[cur], aidx[cur], margin[cur], strict=True):
            team_sum[h] = team_sum.get(h, 0.0) + float(m - (r0[h] - r0[a]) - hca0)
            team_n[h] = team_n.get(h, 0) + 1

        # schedule: per-team game start list for rest / density
        by_team: dict[str, list[datetime]] = {}
        games_cur: dict[str, int] = {}
        for g in history:
            by_team.setdefault(g.home_team, []).append(g.start)
            by_team.setdefault(g.away_team, []).append(g.start)
            if int(g.season_year or 0) == season_now:
                games_cur[g.home_team] = games_cur.get(g.home_team, 0) + 1
                games_cur[g.away_team] = games_cur.get(g.away_team, 0) + 1

        total_fit = (
            self._fit_total_model(history, teams, index, season_now) if self._poss is not None else None
        )

        out: list[V7Components] = []
        for u in upcoming:
            p6 = v6_preds[u.event_id]
            fb = p6.feature_basis
            h, a = index[u.home_team], index[u.away_team]
            gstart = parse_utc(u.event_start_utc)

            def rest_and_density(team: str, gstart: datetime = gstart) -> tuple[float, int]:
                prev = [s for s in by_team.get(team, []) if s < gstart]
                if not prev:
                    return 14.0, 0
                last = max(prev)
                return min(14.0, (gstart - last).total_seconds() / 86400.0), sum(
                    1 for s in prev if (gstart - s).total_seconds() <= 345600
                )

            rh, g4h = rest_and_density(u.home_team)
            ra, g4a = rest_and_density(u.away_team)
            ridge_c = {
                c: float(fits[c][0][h] - fits[c][0][a] + (0.0 if u.neutral_site else fits[c][1]))
                for c in fits
            }
            comp = V7Components(
                event_id=u.event_id,
                home=u.home_team,
                away=u.away_team,
                neutral=bool(u.neutral_site),
                d1_home=u.home_team in self._d1,
                d1_away=u.away_team in self._d1,
                games_home=games_cur.get(u.home_team, 0),
                games_away=games_cur.get(u.away_team, 0),
                v6_mean=float(p6.inference_inputs["mean"]),
                v6_scale=float(p6.inference_inputs["scale"]),
                v6_sd=float(p6.inference_inputs["sd"]),
                v6_p_home=float(p6.probabilities["home"]),
                elo_margin=float(fb["elo_margin"]),
                trend_margin=float(fb["trend_margin"]),
                rest_diff=float(fb["rest_diff"]),
                sample=int(fb["current_season_sample"]),
                ridge_c0=ridge_c[0.0],
                ridge_c05=ridge_c[0.5],
                ridge_c10=ridge_c[1.0],
                ridge_hca=float(fits[0.0][1]),
                team_hca_sum_home=float(team_sum.get(h, 0.0)),
                team_hca_n_home=int(team_n.get(h, 0)),
                rest_days_home=rh,
                rest_days_away=ra,
                games4_home=g4h,
                games4_away=g4a,
            )
            t6 = v6_totals.get(u.event_id)
            if t6 is not None:
                comp.v6_total = float(t6.inference_inputs["mean"])
                comp.v6_total_sd = float(t6.inference_inputs["sd"])
            if total_fit is not None:
                comp.effpace_total = predict_efficiency_pace_total(total_fit, h, a, bool(u.neutral_site))
                comp.effpace_poss = float(total_fit["mu_p"] + total_fit["pace"][h] + total_fit["pace"][a])
            out.append(comp)
        return out

    def _fit_total_model(
        self, history: Sequence[GameRecord], teams: list[str], index: dict[str, int], season_now: int
    ) -> dict[str, Any] | None:
        assert self._poss is not None
        pairs = [(g, p) for g, p in self._poss.for_history(history) if int(g.season_year or 0) == season_now]
        if len(pairs) < self.min_fit_games:
            return None
        hidx = np.array([index[g.home_team] for g, _ in pairs], dtype=int)
        aidx = np.array([index[g.away_team] for g, _ in pairs], dtype=int)
        neut = np.array([history_neutral(g, self._stats) for g, _ in pairs], dtype=bool)
        poss = np.array([p.game_possessions for _, p in pairs], dtype=float)
        hp = np.array([p.home.points for _, p in pairs], dtype=float)
        ap = np.array([p.away.points for _, p in pairs], dtype=float)
        return fit_efficiency_pace(hidx, aidx, neut, poss, hp, ap, len(teams))


# --------------------------------------------------------------------------- combiner


MARGIN_FEATURES = (
    "v6",
    "elo",
    "trend",
    "rest",
    "ridge_c0",
    "ridge_c05",
    "ridge_c10",
    "v6_thin",
    "ridge_thin",
    "neutral",
    "afav",
    "v6_tail",
    "b2b",
    "dens",
    "team_hca_k10",
    "team_hca_k20",
    "const",
)


def margin_feature(comp: V7Components, name: str) -> float:
    """One named regressor of the margin stack (the single definition train and serve share)."""
    thin = thin_factor(comp.sample)
    if name == "v6":
        return comp.v6_mean
    if name == "elo":
        return comp.elo_margin
    if name == "trend":
        return comp.trend_margin
    if name == "rest":
        return comp.rest_diff
    if name == "ridge_c0":
        return comp.ridge_c0
    if name == "ridge_c05":
        return comp.ridge_c05
    if name == "ridge_c10":
        return comp.ridge_c10
    if name == "v6_thin":
        return comp.v6_mean * thin
    if name == "ridge_thin":
        return comp.ridge_c0 * thin
    if name == "neutral":
        return 1.0 if comp.neutral else 0.0
    if name == "afav":
        return min(comp.v6_mean, 0.0)
    if name == "v6_tail":
        return math.copysign(max(abs(comp.v6_mean) - TAIL_KNOT_POINTS, 0.0), comp.v6_mean)
    if name == "b2b":  # away team on a back-to-back minus home team on one (home-positive)
        return float(comp.rest_days_away <= 1.2) - float(comp.rest_days_home <= 1.2)
    if name == "dens":  # 3-in-4 density, same sign convention
        return float(comp.games4_away >= 2) - float(comp.games4_home >= 2)
    if name == "team_hca_k10":
        return 0.0 if comp.neutral else shrunk_mean(comp.team_hca_sum_home, comp.team_hca_n_home, 10.0)
    if name == "team_hca_k20":
        return 0.0 if comp.neutral else shrunk_mean(comp.team_hca_sum_home, comp.team_hca_n_home, 20.0)
    if name == "const":
        return 1.0
    raise KeyError(f"unknown margin feature {name!r}")


def margin_design(comps: Sequence[V7Components], names: Sequence[str]) -> np.ndarray:
    return np.array([[margin_feature(c, n) for n in names] for c in comps], dtype=float)


@dataclass(frozen=True)
class V7Config:
    """Fitted combiner. ``margin_names``/``margin_coef`` apply to D1-vs-D1 games; ``nond1_*`` to the rest."""

    margin_names: tuple[str, ...]
    margin_coef: tuple[float, ...]
    nond1_rule: str = "v6"  # "v6" | "linear" | "decline"
    nond1_names: tuple[str, ...] = ("v6",)
    nond1_coef: tuple[float, ...] = (1.0,)
    min_games_decline: int = 0  # decline when either team has fewer current-season games
    scale_edges: tuple[int, ...] = (0, 1, 3, 8)  # lower bounds of the sample buckets
    scale_values: tuple[float, ...] = (10.5, 10.5, 10.5, 10.5)
    nond1_scale: float = 18.0  # Student-t scale for games with a non-D1 team
    df: float = 8.0
    total_names: tuple[str, ...] = ("v6_total",)
    total_coef: tuple[float, ...] = (1.0,)
    total_sd: float = 16.5
    notes: str = ""


def scale_bucket(sample: float, edges: Sequence[int]) -> int:
    idx = 0
    for i, edge in enumerate(edges):
        if sample >= edge:
            idx = i
    return idx


def decline_reason(comp: V7Components, config: V7Config) -> str | None:
    if config.nond1_rule == "decline" and not comp.both_d1:
        return "NO_CALL_NON_D1"
    if config.min_games_decline > 0 and min(comp.games_home, comp.games_away) < config.min_games_decline:
        return "NO_CALL_THIN_SAMPLE"
    return None


def margin_from_components(comp: V7Components, config: V7Config) -> float:
    if comp.both_d1:
        names, coef = config.margin_names, config.margin_coef
    elif config.nond1_rule == "linear":
        names, coef = config.nond1_names, config.nond1_coef
    else:  # "v6", or "decline" (callers still get the v6 number so declined games can be scored)
        return comp.v6_mean
    return float(sum(c * margin_feature(comp, n) for n, c in zip(names, coef, strict=True)))


def total_feature(comp: V7Components, name: str) -> float:
    if name == "v6_total":
        return comp.v6_total if comp.v6_total is not None else float("nan")
    if name == "effpace_total":
        return comp.effpace_total if comp.effpace_total is not None else float("nan")
    if name == "const":
        return 1.0
    raise KeyError(f"unknown total feature {name!r}")


def total_from_components(comp: V7Components, config: V7Config) -> float | None:
    if comp.v6_total is None:
        return None
    if comp.effpace_total is None and "effpace_total" in config.total_names:
        return comp.v6_total  # no fit yet (thin season): stay on v6
    return float(
        sum(c * total_feature(comp, n) for n, c in zip(config.total_names, config.total_coef, strict=True))
    )


def spread_distribution(comp: V7Components, config: V7Config) -> tuple[float, float, float]:
    """(mean, t-scale, p_home) for one game under the fitted config."""
    mean = margin_from_components(comp, config)
    if comp.both_d1:
        scale = config.scale_values[scale_bucket(comp.sample, config.scale_edges)]
    else:
        scale = config.nond1_scale
    return mean, scale, win_probability(mean, scale, config.df)
