"""NCAAF v7 rating core: point-in-time opponent-adjusted play-by-play ratings (research challenger).

One module computes the ratings for both the fit script (``scripts/cfb_v7_fit.py``) and the serving module
(``models/cfb_structural_v7.py``), so training and serving share the arithmetic by construction.

Inputs are team-game rows in the ``scripts/cfb_pbp_features.py`` format (one row per team per game, keyed by ESPN
team id; offence metrics garbage-time filtered with the 38/28/22 rule). A row without play-by-play (score only)
feeds the points metric alone.

Model, per metric k (success rate, yards per play, explosive rate, points per drive, points, plays, turnover rate):

    y(offence t vs defence u) = mu + cO[class t] + o_t + cD[class u] + d_u + h * s + noise,   s = +1 home, -1 away,
                                                                                                0 neutral

refit at each decision time on the current season's admitted games, as a ridge regression whose team terms shrink
toward a preseason prior centre ``rho_k * (last season's final rating)`` with strength ``lambda_k`` (in game
units; the prior's weight decays as games accumulate, n / (n + lambda)). League terms (mu, h, class offsets) shrink
toward last season's values. Last season's finals are an independent full-season fit of that season, so a season's
prior never depends on a later season. Within a season an optional recency half-life down-weights old games.

Point in time: a game feeds ratings at decision time T only if ``kickoff + RESULT_BUFFER_HOURS <= T`` (v6's
D-RES-BUFFER), so an unfinished game is never read. The research decision time for a game is
``min(kickoff, 08:00 America/New_York on the kickoff's ET date)``: the morning slate run.

Fail closed: a team with neither a previous-season final nor an admitted current-season row has no rating;
``predict`` raises ``V7NoCall`` with a ``NO_CALL_*`` reason code instead of guessing.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from ..domain import parse_utc

RESULT_BUFFER_HOURS = 5.0  # v6's D-RES-BUFFER: longer than a 3.5 h game plus overtime / weather delay
DECISION_LOCAL_TIME = time(8, 0)  # the morning slate run, America/New_York
_ET = ZoneInfo("America/New_York")
FBS, FCS = "FBS", "FCS"
METRICS: tuple[str, ...] = ("sr", "ypp", "exp", "ppd", "pts", "plays", "to")
PBP_METRICS: tuple[str, ...] = ("sr", "ypp", "exp", "ppd", "plays", "to")
# Finals (prior source) are fit with this weak team ridge: enough to keep a 1-2 game team finite.
FINALS_TEAM_LAMBDA = 1.0
# League-level terms (mu, home, class offsets) shrink toward last season's values with this strength (games).
LEAGUE_LAMBDA = 20.0
_TINY = 1e-6

NO_CALL_NO_RATING = "NO_CALL_V7_NO_RATING_HISTORY"
NO_CALL_UNKNOWN_TEAM = "NO_CALL_V7_UNKNOWN_TEAM_ID"


class V7NoCall(Exception):
    """A v7 rating cannot be formed for this game; ``reason`` is a NO_CALL_* code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


# ------------------------------------------------------------------------------------------------ rows
@dataclass(frozen=True)
class TeamGame:
    """One offence row: team ``team_id`` on offence against ``opponent_id``'s defence."""

    event_id: str
    season: int
    kickoff: datetime
    team_id: str
    opponent_id: str
    home_sign: int  # +1 home, -1 away, 0 neutral site
    game_class: str | None
    values: Mapping[str, float | None]


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def metric_values(row: Mapping[str, Any]) -> dict[str, float | None]:
    """Metric values of one pbp-format row; pbp metrics are None when the row has no scrimmage plays."""
    plays_ng = _f(row.get("off_plays_ng")) or 0.0
    plays = _f(row.get("off_plays")) or 0.0
    has_pbp = plays_ng > 0 and plays > 0
    turnovers = _f(row.get("off_turnovers"))
    return {
        "sr": _f(row.get("off_success_rate_ng")) if has_pbp else None,
        "ypp": _f(row.get("off_yards_per_play_ng")) if has_pbp else None,
        "exp": _f(row.get("off_explosive_rate_ng")) if has_pbp else None,
        "ppd": _f(row.get("off_points_per_drive_ng")) if has_pbp else None,
        "pts": _f(row.get("points_for")),
        "plays": plays if has_pbp else None,
        "to": (turnovers / plays) if has_pbp and turnovers is not None else None,
    }


def team_game_from_row(row: Mapping[str, Any]) -> TeamGame:
    kickoff = row["kickoff_utc"]
    kick = parse_utc(kickoff) if isinstance(kickoff, str) else kickoff
    neutral = bool(row.get("neutral_site"))
    sign = 0 if neutral else (1 if row.get("is_home") else -1)
    return TeamGame(
        event_id=str(row["event_id"]),
        season=int(row["season"]),
        kickoff=kick,
        team_id=str(row["team_id"]),
        opponent_id=str(row["opponent_id"]),
        home_sign=sign,
        game_class=row.get("game_class"),
        values=metric_values(row),
    )


def season_classes(rows: Iterable[TeamGame]) -> dict[tuple[str, int], str]:
    """(team, season) -> FBS when the team plays any FBS-FBS game that season (schedule metadata), else FCS."""
    seen: dict[tuple[str, int], str] = {}
    for r in rows:
        for team in (r.team_id, r.opponent_id):
            key = (team, r.season)
            if r.game_class == "FBS-FBS":
                seen[key] = FBS
            else:
                seen.setdefault(key, FCS)
    return seen


def decision_time_for(kickoff: datetime) -> datetime:
    """Research decision time: 08:00 ET on the kickoff's ET date, or the kickoff itself if that is earlier."""
    local = kickoff.astimezone(_ET)
    morning = datetime.combine(local.date(), DECISION_LOCAL_TIME, tzinfo=_ET)
    return min(kickoff, morning.astimezone(kickoff.tzinfo))


def admitted(kickoff: datetime, decision: datetime) -> bool:
    return kickoff + timedelta(hours=RESULT_BUFFER_HOURS) <= decision


# ------------------------------------------------------------------------------------------------ parameters
@dataclass(frozen=True)
class PriorParams:
    """Per-metric prior regression (rho) and ridge strength (lambda, game units) for offence and defence."""

    rho_off: Mapping[str, float]
    rho_def: Mapping[str, float]
    lam_off: Mapping[str, float]
    lam_def: Mapping[str, float]
    half_life_days: float | None = None
    # Preseason covariate coefficients (CFBD arm): metric -> covariate -> gamma, added to the team prior centre as
    # gamma * z (z standardised within season). The covariate "ret_x_prev" is z_ret times the team's re-expressed
    # last-season level, so returning production scales how much of last season carries over.
    cov_off: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    cov_def: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


@dataclass
class MetricState:
    """Ratings for one metric at one decision time."""

    mu: float
    home: float
    c_off: dict[str, float]
    c_def: dict[str, float]
    off: dict[str, float]
    deff: dict[str, float]


@dataclass
class RatingState:
    season: int
    decision: datetime | None
    metrics: dict[str, MetricState]
    games_played: dict[str, int] = field(default_factory=dict)  # admitted current-season rows per team
    prior_teams: frozenset[str] = frozenset()  # teams with a previous-season final


# ------------------------------------------------------------------------------------------------ solver
def _solve_metric(
    obs: Sequence[TeamGame],
    metric: str,
    teams: Sequence[str],
    classes: Mapping[tuple[str, int], str],
    season: int,
    weights: np.ndarray,
    *,
    lam_off: float,
    lam_def: float,
    centre_off: Mapping[str, float],
    centre_def: Mapping[str, float],
    league_centre: Mapping[str, float] | None,
) -> MetricState:
    """Weighted ridge with prior centres. Parameter layout: o (n), d (n), mu, h, cO_FCS, cD_FCS."""
    n = len(teams)
    index = {t: i for i, t in enumerate(teams)}
    p = 2 * n + 4
    i_mu, i_h, i_co, i_cd = 2 * n, 2 * n + 1, 2 * n + 2, 2 * n + 3
    a = np.zeros(p * p)
    b = np.zeros(p)
    rows_idx: list[list[int]] = []
    rows_val: list[list[float]] = []
    ys: list[float] = []
    ws: list[float] = []
    for r, w in zip(obs, weights, strict=True):
        y = r.values.get(metric)
        if y is None or w <= 0:
            continue
        it, iu = index[r.team_id], n + index[r.opponent_id]
        idx = [it, iu, i_mu, i_h, i_co, i_cd]
        val = [
            1.0,
            1.0,
            1.0,
            float(r.home_sign),
            1.0 if classes.get((r.team_id, season), FCS) == FCS else 0.0,
            1.0 if classes.get((r.opponent_id, season), FCS) == FCS else 0.0,
        ]
        rows_idx.append(idx)
        rows_val.append(val)
        ys.append(float(y))
        ws.append(float(w))
    if ys:
        ii = np.asarray(rows_idx)
        vv = np.asarray(rows_val)
        yy = np.asarray(ys)
        ww = np.asarray(ws)
        flat = (ii[:, :, None] * p + ii[:, None, :]).ravel()
        np.add.at(a, flat, (ww[:, None, None] * vv[:, :, None] * vv[:, None, :]).ravel())
        np.add.at(b, ii.ravel(), (ww[:, None] * yy[:, None] * vv).ravel())
    a = a.reshape(p, p)
    centre = np.zeros(p)
    pen = np.zeros(p)
    for t, i in index.items():
        centre[i] = centre_off.get(t, 0.0)
        centre[n + i] = centre_def.get(t, 0.0)
    pen[:n] = lam_off
    pen[n : 2 * n] = lam_def
    if league_centre is None:
        # Finals: league terms are free (a tiny ridge keeps an absent class offset at 0); mu starts at the mean.
        pen[[i_mu, i_h, i_co, i_cd]] = _TINY
        centre[i_mu] = float(np.average(ys, weights=ws)) if ys else 0.0
    else:
        pen[[i_mu, i_h, i_co, i_cd]] = LEAGUE_LAMBDA
        centre[i_mu] = league_centre["mu"]
        centre[i_h] = league_centre["home"]
        centre[i_co] = league_centre["c_off"]
        centre[i_cd] = league_centre["c_def"]
    beta = np.linalg.solve(a + np.diag(pen), b + pen * centre)
    return MetricState(
        mu=float(beta[i_mu]),
        home=float(beta[i_h]),
        c_off={FBS: 0.0, FCS: float(beta[i_co])},
        c_def={FBS: 0.0, FCS: float(beta[i_cd])},
        off={t: float(beta[i]) for t, i in index.items()},
        deff={t: float(beta[n + i]) for t, i in index.items()},
    )


def _teams_of(rows: Iterable[TeamGame], extra: Iterable[str] = ()) -> list[str]:
    out = {r.team_id for r in rows} | {r.opponent_id for r in rows} | set(extra)
    return sorted(out)


def fit_finals(rows: Sequence[TeamGame], season: int, classes: Mapping[tuple[str, int], str]) -> RatingState:
    """Independent full-season ratings of ``season`` (every row of that season, weak team ridge, no prior)."""
    obs = [r for r in rows if r.season == season]
    teams = _teams_of(obs)
    w = np.ones(len(obs))
    metrics = {
        k: _solve_metric(
            obs,
            k,
            teams,
            classes,
            season,
            w,
            lam_off=FINALS_TEAM_LAMBDA,
            lam_def=FINALS_TEAM_LAMBDA,
            centre_off={},
            centre_def={},
            league_centre=None,
        )
        for k in METRICS
    }
    played: dict[str, int] = defaultdict(int)
    for r in obs:
        played[r.team_id] += 1
    return RatingState(season=season, decision=None, metrics=metrics, games_played=dict(played))


def prior_centres(
    finals: RatingState | None,
    metric: str,
    params: PriorParams,
    classes: Mapping[tuple[str, int], str],
    season: int,
    teams: Iterable[str],
    covariates: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Team prior centres for ``season``: rho * last season's absolute level minus this season's class offset,
    plus the preseason covariate terms when the parameters carry them.

    A team that changed class keeps its absolute strength (it is re-expressed against its new class offset)."""
    co: dict[str, float] = {}
    cd: dict[str, float] = {}
    cov = covariates or {}
    g_off = params.cov_off.get(metric, {})
    g_def = params.cov_def.get(metric, {})
    m = finals.metrics[metric] if finals is not None else None
    for t in teams:
        z = cov.get(t, {})
        x_off = x_def = 0.0
        has_prev = m is not None and t in m.off
        if has_prev:
            assert m is not None and finals is not None
            cls_prev = classes.get((t, finals.season), FCS)
            cls_now = classes.get((t, season), cls_prev)
            x_off = m.off[t] + m.c_off[cls_prev] - m.c_off[cls_now]
            x_def = m.deff[t] + m.c_def[cls_prev] - m.c_def[cls_now]
        if not has_prev and not (z and (g_off or g_def)):
            continue
        co[t] = params.rho_off[metric] * x_off + _cov_term(g_off, z, x_off)
        cd[t] = params.rho_def[metric] * x_def + _cov_term(g_def, z, x_def)
    return co, cd


def _cov_term(gamma: Mapping[str, float], z: Mapping[str, float], prev_level: float) -> float:
    out = 0.0
    for name, g in gamma.items():
        if name == "ret_x_prev":
            out += g * z.get("ret", 0.0) * prev_level
        else:
            out += g * z.get(name, 0.0)
    return out


def fit_state(
    rows: Sequence[TeamGame],
    season: int,
    decision: datetime,
    classes: Mapping[tuple[str, int], str],
    finals_prev: RatingState | None,
    params: PriorParams,
    extra_teams: Iterable[str] = (),
    covariates: Mapping[str, Mapping[str, float]] | None = None,
) -> RatingState:
    """Ratings for ``season`` at ``decision`` from the admitted current-season rows and last season's finals
    (plus ``covariates``: team -> standardised preseason values, used only when ``params`` carries coefficients)."""
    obs = [r for r in rows if r.season == season and admitted(r.kickoff, decision)]
    prior_teams = frozenset(finals_prev.metrics[METRICS[0]].off) if finals_prev is not None else frozenset()
    teams = _teams_of(obs, list(extra_teams) + sorted(prior_teams))
    if params.half_life_days:
        age = np.array([(decision - r.kickoff).total_seconds() / 86400.0 for r in obs])
        w = np.power(0.5, age / params.half_life_days)
    else:
        w = np.ones(len(obs))
    metrics: dict[str, MetricState] = {}
    for k in METRICS:
        co, cd = prior_centres(finals_prev, k, params, classes, season, teams, covariates)
        league = None
        if finals_prev is not None:
            fm = finals_prev.metrics[k]
            league = {"mu": fm.mu, "home": fm.home, "c_off": fm.c_off[FCS], "c_def": fm.c_def[FCS]}
        else:
            league = {"mu": 0.0, "home": 0.0, "c_off": 0.0, "c_def": 0.0}
            if obs:
                vals = [float(v) for r in obs if (v := r.values.get(k)) is not None]
                league["mu"] = sum(vals) / len(vals) if vals else 0.0
        metrics[k] = _solve_metric(
            obs,
            k,
            teams,
            classes,
            season,
            w,
            lam_off=params.lam_off[k],
            lam_def=params.lam_def[k],
            centre_off=co,
            centre_def=cd,
            league_centre=league,
        )
    played: dict[str, int] = defaultdict(int)
    for r in obs:
        played[r.team_id] += 1
    return RatingState(
        season=season, decision=decision, metrics=metrics, games_played=dict(played), prior_teams=prior_teams
    )


# ------------------------------------------------------------------------------------------------ features
FEATURE_NAMES: tuple[str, ...] = tuple(f"e_{k}" for k in METRICS)


@dataclass(frozen=True)
class SideFeatures:
    """Expected per-metric offence values for one side of a matchup at the decision time."""

    team_id: str
    values: dict[str, float]


@dataclass(frozen=True)
class MatchupFeatures:
    home: SideFeatures
    away: SideFeatures
    home_games: int
    away_games: int
    is_fbs_vs_fcs: bool


def _side(state: RatingState, k: str, off: str, deff: str, sign: int, c_o: str, c_d: str) -> float:
    m = state.metrics[k]
    return m.mu + m.c_off[c_o] + m.off.get(off, 0.0) + m.c_def[c_d] + m.deff.get(deff, 0.0) + m.home * sign


def matchup_features(
    state: RatingState,
    home_id: str,
    away_id: str,
    *,
    neutral: bool,
    classes: Mapping[tuple[str, int], str],
) -> MatchupFeatures:
    """Expected metric values for both offences. Fails closed when a team has no rating history at all."""
    for t in (home_id, away_id):
        if not t or t == "None":
            raise V7NoCall(NO_CALL_UNKNOWN_TEAM, str(t))
        if t not in state.prior_teams and state.games_played.get(t, 0) == 0:
            raise V7NoCall(NO_CALL_NO_RATING, t)
    s = 0 if neutral else 1
    ch = classes.get((home_id, state.season), FCS)
    ca = classes.get((away_id, state.season), FCS)
    hv = {k: _side(state, k, home_id, away_id, s, ch, ca) for k in METRICS}
    av = {k: _side(state, k, away_id, home_id, -s, ca, ch) for k in METRICS}
    return MatchupFeatures(
        home=SideFeatures(home_id, hv),
        away=SideFeatures(away_id, av),
        home_games=state.games_played.get(home_id, 0),
        away_games=state.games_played.get(away_id, 0),
        is_fbs_vs_fcs=(ch == FBS) != (ca == FBS),
    )


def side_vector(values: Mapping[str, float], names: Sequence[str]) -> np.ndarray:
    return np.array([values[n.removeprefix("e_")] for n in names], dtype=float)


def project_points(
    feat: MatchupFeatures, coef: Sequence[float], intercept: float, names: Sequence[str]
) -> tuple[float, float]:
    """Stage-2 team points (home, away) from the fitted linear points model."""
    c = np.asarray(coef, dtype=float)
    ph = float(intercept + side_vector(feat.home.values, names) @ c)
    pa = float(intercept + side_vector(feat.away.values, names) @ c)
    return ph, pa
