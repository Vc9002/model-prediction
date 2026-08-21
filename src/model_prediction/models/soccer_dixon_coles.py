"""Soccer Joint Bivariate Poisson Dixon-Coles model and parameter optimization engine.

Inspired by Dixon & Coles (1997) "Modelling Association Football Scores and
Inefficiencies in the Football Betting Market", penaltyblog, and football-mle.

Key capabilities:
1. Low-score correlation adjustment tau(x, y, lambda_h, lambda_a, rho) for (0,0), (1,0), (0,1), (1,1).
2. Exponential time decay weighting w_k = exp(-xi * (t_now - t_k)).
3. DixonColesEngine: Maximum Likelihood Estimation (MLE) of attack (alpha), defense (beta),
   home advantage (gamma), and correlation (rho) subject to sum(alpha) = 1.0 constraint.
4. BivariateScoreGrid (0..10 goals) generating:
   - 1X2 probabilities: P(Home Win), P(Draw), P(Away Win)
   - Both Teams To Score (BTTS): P(BTTS Yes), P(BTTS No)
   - Over/Under totals: P(Over L), P(Under L) for arbitrary lines (0.5, 1.5, 2.5, 3.5, 4.5, etc.)
   - Asian Handicap matrices: P(Home Win), P(Draw/Push), P(Away Win), Cover probabilities across lines.
5. Temporal cross-validation helper to search for optimal decay parameter xi over historical matches.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

MAX_GOALS: int = 10
DEFAULT_XI_GRID: tuple[float, ...] = (0.0, 0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005)


def dixon_coles_tau(
    x: int,
    y: int,
    lambda_h: float,
    lambda_a: float,
    rho: float,
) -> float:
    """Dixon-Coles low-score dependence adjustment factor tau(x, y).

    In the classic Dixon & Coles (1997) bivariate formulation:
    - tau(0, 0) = 1 - lambda_h * lambda_a * rho
    - tau(1, 0) = 1 + lambda_a * rho
    - tau(0, 1) = 1 + lambda_h * rho
    - tau(1, 1) = 1 - rho
    - tau(x, y) = 1 for all x >= 2 or y >= 2

    This formulation guarantees that the low-score adjustment terms (+/- rho * lambda_h * lambda_a)
    cancel out when summed across the four low-score cells, preserving marginal Poisson distributions.
    """
    if x == 0 and y == 0:
        return 1.0 - lambda_h * lambda_a * rho
    if x == 1 and y == 0:
        return 1.0 + lambda_a * rho
    if x == 0 and y == 1:
        return 1.0 + lambda_h * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


# Alias for concise calling
tau = dixon_coles_tau


def _to_timestamp_days(value: datetime | date | float | str) -> float:
    """Convert various timestamp formats to float days relative to Unix epoch."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        clean_str = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean_str)
        except ValueError:
            dt = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp() / 86400.0
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.timestamp() / 86400.0
    if isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=UTC)
        return dt.timestamp() / 86400.0
    raise TypeError(f"Unsupported timestamp type: {type(value)}")


def time_decay_weight(
    t_k: datetime | date | float | str,
    t_now: datetime | date | float | str,
    xi: float,
) -> float:
    """Calculate exponential time-decay weight w_k = exp(-xi * (t_now - t_k)).

    Parameters
    ----------
    t_k : datetime | date | float | str
        Timestamp or date of match k.
    t_now : datetime | date | float | str
        Reference timestamp or date (decision time).
    xi : float
        Decay parameter in 1/days. If xi <= 0, returns 1.0.

    Returns
    -------
    float
        Weight in (0, 1].
    """
    if xi <= 0.0:
        return 1.0
    k_days = _to_timestamp_days(t_k)
    now_days = _to_timestamp_days(t_now)
    delta_days = max(0.0, now_days - k_days)
    return float(math.exp(-xi * delta_days))


def compute_match_weights(
    match_dates: Sequence[datetime | date | float | str],
    t_now: datetime | date | float | str | None = None,
    xi: float = 0.0,
) -> np.ndarray:
    """Compute vector of exponential decay weights for matches."""
    n = len(match_dates)
    if xi <= 0.0 or n == 0:
        return np.ones(n, dtype=np.float64)

    days_arr = np.array([_to_timestamp_days(d) for d in match_dates], dtype=np.float64)
    if t_now is None:
        ref_day = float(np.max(days_arr))
    else:
        ref_day = _to_timestamp_days(t_now)

    delta_days = np.maximum(0.0, ref_day - days_arr)
    return np.exp(-xi * delta_days)


@dataclass(frozen=True)
class AsianHandicapLineResult:
    """Asian Handicap probability decomposition for a specific line."""

    line: float
    home_win: float
    draw: float
    away_win: float
    home_cover: float
    away_cover: float

    def as_dict(self) -> dict[str, float]:
        return {
            "line": self.line,
            "home_win": round(self.home_win, 6),
            "draw": round(self.draw, 6),
            "away_win": round(self.away_win, 6),
            "home_cover": round(self.home_cover, 6),
            "away_cover": round(self.away_cover, 6),
        }


@dataclass
class BivariateScoreGrid:
    """Joint bivariate Poisson score grid with Dixon-Coles adjustment.

    Contains an (max_goals+1, max_goals+1) matrix representing P(Home=x, Away=y).
    """

    grid: np.ndarray
    lambda_h: float
    lambda_a: float
    rho: float
    max_goals: int = MAX_GOALS

    def __post_init__(self) -> None:
        expected_dim = self.max_goals + 1
        if self.grid.shape != (expected_dim, expected_dim):
            raise ValueError(
                f"Grid shape {self.grid.shape} does not match expected ({expected_dim}, {expected_dim})"
            )
        # Ensure exact normalization to 1.0
        total = float(np.sum(self.grid))
        if total > 0 and not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            self.grid = self.grid / total

    def exact_score(self, home_goals: int, away_goals: int) -> float:
        """Probability of exact score (home_goals, away_goals)."""
        if 0 <= home_goals <= self.max_goals and 0 <= away_goals <= self.max_goals:
            return float(self.grid[home_goals, away_goals])
        return 0.0

    def prob_home_win(self) -> float:
        """Probability of Home Win (x > y)."""
        return float(np.sum(np.tril(self.grid, -1)))

    def prob_draw(self) -> float:
        """Probability of Draw (x == y)."""
        return float(np.sum(np.diag(self.grid)))

    def prob_away_win(self) -> float:
        """Probability of Away Win (x < y)."""
        return float(np.sum(np.triu(self.grid, 1)))

    def prob_1x2(self) -> dict[str, float]:
        """Three-way match result probabilities."""
        p_home = self.prob_home_win()
        p_away = self.prob_away_win()
        p_draw = self.prob_draw()
        return {
            "home": p_home,
            "draw": p_draw,
            "away": p_away,
        }

    def prob_btts(self) -> dict[str, float]:
        """Both Teams To Score (BTTS) probabilities."""
        # BTTS Yes: Home >= 1 and Away >= 1
        p_yes = float(np.sum(self.grid[1:, 1:]))
        p_no = 1.0 - p_yes
        return {
            "yes": p_yes,
            "no": max(0.0, p_no),
        }

    def prob_over_under(self, line: float) -> dict[str, float]:
        """Over / Under total goals probability for an arbitrary line."""
        h_idx, a_idx = np.indices(self.grid.shape)
        totals = h_idx + a_idx
        p_over = float(np.sum(self.grid[totals > line]))
        p_under = float(np.sum(self.grid[totals < line]))
        p_push = float(np.sum(self.grid[totals == line]))
        return {
            "over": p_over,
            "under": p_under,
            "push": p_push,
        }

    def prob_over_under_table(
        self,
        lines: Sequence[float] = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
    ) -> dict[float, dict[str, float]]:
        """Over / Under probabilities across standard goal lines."""
        return {line: self.prob_over_under(line) for line in lines}

    def asian_handicap(self, line: float) -> AsianHandicapLineResult:
        """Asian Handicap calculation from Home team perspective.

        Supports:
        - Full lines (e.g. -2.0, -1.0, 0.0, +1.0, +2.0): Win / Push / Loss
        - Half lines (e.g. -1.5, -0.5, +0.5, +1.5): Win / Loss (Push = 0)
        - Quarter lines (e.g. -0.75, -0.25, +0.25, +0.75): Split stake over line +/- 0.25
        """
        # Check if line is a quarter line (fraction is 0.25 or 0.75)
        rem = abs(line * 4) % 2
        is_quarter = math.isclose(rem, 1.0, abs_tol=1e-5)

        if is_quarter:
            # Quarter handicap splits into two adjacent quarter/half lines
            lower_line = line - 0.25
            upper_line = line + 0.25
            res_lower = self.asian_handicap(lower_line)
            res_upper = self.asian_handicap(upper_line)

            home_win = 0.5 * (res_lower.home_win + res_upper.home_win)
            draw = 0.5 * (res_lower.draw + res_upper.draw)
            away_win = 0.5 * (res_lower.away_win + res_upper.away_win)
            home_cover = 0.5 * (res_lower.home_cover + res_upper.home_cover)
            away_cover = 0.5 * (res_lower.away_cover + res_upper.away_cover)

            return AsianHandicapLineResult(
                line=line,
                home_win=home_win,
                draw=draw,
                away_win=away_win,
                home_cover=home_cover,
                away_cover=away_cover,
            )

        h_idx, a_idx = np.indices(self.grid.shape)
        margins = h_idx - a_idx
        effective = margins + line

        home_win = float(np.sum(self.grid[effective > 1e-9]))
        draw = float(np.sum(self.grid[np.abs(effective) <= 1e-9]))
        away_win = float(np.sum(self.grid[effective < -1e-9]))
        home_cover = home_win + 0.5 * draw
        away_cover = away_win + 0.5 * draw

        return AsianHandicapLineResult(
            line=line,
            home_win=home_win,
            draw=draw,
            away_win=away_win,
            home_cover=home_cover,
            away_cover=away_cover,
        )

    def asian_handicap_matrix(
        self,
        lines: Sequence[float] = (-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5),
    ) -> dict[float, dict[str, float]]:
        """Asian handicap decomposition across multiple spread lines."""
        return {line: self.asian_handicap(line).as_dict() for line in lines}


def build_score_grid(
    lambda_h: float,
    lambda_a: float,
    rho: float = 0.0,
    max_goals: int = MAX_GOALS,
) -> BivariateScoreGrid:
    """Build a normalized bivariate Poisson score grid with Dixon-Coles adjustment."""
    lh = max(1e-6, float(lambda_h))
    la = max(1e-6, float(lambda_a))
    dim = max_goals + 1

    h_arr = np.arange(dim, dtype=np.float64)
    a_arr = np.arange(dim, dtype=np.float64)

    # Compute marginal Poisson PMFs in log-space for numerical stability
    log_p_h = -lh + h_arr * math.log(lh) - gammaln(h_arr + 1)
    log_p_a = -la + a_arr * math.log(la) - gammaln(a_arr + 1)

    p_h = np.exp(log_p_h)
    p_a = np.exp(log_p_a)

    # Outer product for independent joint probability
    joint = np.outer(p_h, p_a)

    # Apply Dixon-Coles tau adjustment to low-score cells
    if dim > 0:
        joint[0, 0] *= max(0.0, dixon_coles_tau(0, 0, lh, la, rho))
    if dim > 1:
        joint[1, 0] *= max(0.0, dixon_coles_tau(1, 0, lh, la, rho))
        joint[0, 1] *= max(0.0, dixon_coles_tau(0, 1, lh, la, rho))
        joint[1, 1] *= max(0.0, dixon_coles_tau(1, 1, lh, la, rho))

    # Normalize
    total = np.sum(joint)
    if total > 0:
        joint /= total

    return BivariateScoreGrid(
        grid=joint,
        lambda_h=lh,
        lambda_a=la,
        rho=rho,
        max_goals=max_goals,
    )


@dataclass(frozen=True)
class DixonColesMatchPrediction:
    """Consolidated prediction for a soccer match."""

    home_team: str
    away_team: str
    lambda_home: float
    lambda_away: float
    rho: float
    prob_home: float
    prob_draw: float
    prob_away: float
    btts_yes: float
    btts_no: float
    over_under: dict[float, dict[str, float]]
    asian_handicap: dict[float, dict[str, float]]
    score_grid: BivariateScoreGrid

    def as_dict(self) -> dict[str, Any]:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "lambda_home": round(self.lambda_home, 4),
            "lambda_away": round(self.lambda_away, 4),
            "rho": round(self.rho, 4),
            "prob_home": round(self.prob_home, 6),
            "prob_draw": round(self.prob_draw, 6),
            "prob_away": round(self.prob_away, 6),
            "btts_yes": round(self.btts_yes, 6),
            "btts_no": round(self.btts_no, 6),
            "over_under": self.over_under,
            "asian_handicap": self.asian_handicap,
        }


def _extract_match_arrays(
    matches: Iterable[Any],
    team_indices: dict[str, int] | None = None,
) -> tuple[dict[str, int], np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[Any]]:
    """Extract team indices, goal arrays, and dates from match records."""
    home_teams: list[str] = []
    away_teams: list[str] = []
    home_goals_list: list[int] = []
    away_goals_list: list[int] = []
    dates_list: list[Any] = []

    for m in matches:
        if isinstance(m, dict):
            h_team = str(m.get("home_team", m.get("home", "")))
            a_team = str(m.get("away_team", m.get("away", "")))
            h_g = int(m.get("home_score", m.get("home_goals", 0)))
            a_g = int(m.get("away_score", m.get("away_goals", 0)))
            dt = m.get("event_start_utc", m.get("date", m.get("timestamp", None)))
        elif hasattr(m, "home_team") and hasattr(m, "away_team"):
            h_team = str(m.home_team)
            a_team = str(m.away_team)
            h_g = int(getattr(m, "home_score", getattr(m, "home_goals", 0)))
            a_g = int(getattr(m, "away_score", getattr(m, "away_goals", 0)))
            dt = getattr(m, "event_start_utc", getattr(m, "start", getattr(m, "date", None)))
        else:
            raise TypeError(f"Unrecognized match format: {type(m)}")

        home_teams.append(h_team)
        away_teams.append(a_team)
        home_goals_list.append(h_g)
        away_goals_list.append(a_g)
        dates_list.append(dt)

    if team_indices is None:
        unique_teams = sorted(set(home_teams + away_teams))
        team_indices = {t: i for i, t in enumerate(unique_teams)}

    h_idx = np.array([team_indices[t] for t in home_teams], dtype=np.int32)
    a_idx = np.array([team_indices[t] for t in away_teams], dtype=np.int32)
    h_goals = np.array(home_goals_list, dtype=np.float64)
    a_goals = np.array(away_goals_list, dtype=np.float64)

    return team_indices, h_idx, a_idx, h_goals, a_goals, dates_list


def log_prob_clipping(log_p: np.ndarray, floor_val: float = -50.0) -> np.ndarray:
    """Clip log probabilities to prevent numerical underflow in optimization."""
    return np.maximum(log_p, floor_val)


@dataclass
class DixonColesEngine:
    """Maximum Likelihood Estimation (MLE) engine for Dixon-Coles Soccer Model.

    Estimates:
    - attack parameters alpha_i for each team i
    - defense parameters beta_j for each team j
    - home ground advantage gamma
    - low-score correlation rho

    Subject to sum(alpha) = 1.0 constraint.
    """

    xi: float = 0.0
    attack_params: dict[str, float] = field(default_factory=dict)
    defense_params: dict[str, float] = field(default_factory=dict)
    home_advantage: float = 1.25
    rho: float = -0.05
    teams: list[str] = field(default_factory=list)
    team_indices: dict[str, int] = field(default_factory=dict)
    is_fitted: bool = False
    log_likelihood: float | None = None

    def fit(
        self,
        matches: Sequence[Any],
        xi: float | None = None,
        t_now: datetime | date | float | str | None = None,
        sum_alpha_constraint: float = 1.0,
        rho_bounds: tuple[float, float] = (-0.5, 0.5),
        max_iter: int = 300,
        tol: float = 1e-6,
    ) -> DixonColesEngine:
        """Fit Dixon-Coles parameters via constrained Maximum Likelihood Estimation.

        Parameters
        ----------
        matches : Sequence[Any]
            Match history records (GameRecord, dict, or similar).
        xi : float | None
            Decay parameter. If None, uses self.xi.
        t_now : datetime | date | float | str | None
            Reference date for exponential time decay weighting.
        sum_alpha_constraint : float
            Constraint target for sum(alpha), default 1.0.
        rho_bounds : tuple[float, float]
            Optimization bounds for low-score correlation parameter rho.
        max_iter : int
            Maximum solver iterations.
        tol : float
            Solver tolerance.

        Returns
        -------
        self : DixonColesEngine
        """
        if xi is not None:
            self.xi = float(xi)

        if not matches:
            raise ValueError("Cannot fit DixonColesEngine on empty match history.")

        team_indices, h_idx, a_idx, h_goals, a_goals, dates = _extract_match_arrays(matches)
        self.team_indices = team_indices
        self.teams = sorted(team_indices.keys())
        n_teams = len(self.teams)
        n_matches = len(h_idx)

        if n_teams < 2:
            raise ValueError(f"Need at least 2 distinct teams to fit, found {n_teams}")

        # Compute weights
        if self.xi > 0.0 and any(d is not None for d in dates):
            weights = compute_match_weights(dates, t_now=t_now, xi=self.xi)
        else:
            weights = np.ones(n_matches, dtype=np.float64)

        # Precompute constants for log-likelihood
        gammaln_h = gammaln(h_goals + 1)
        gammaln_a = gammaln(a_goals + 1)

        m00 = (h_goals == 0) & (a_goals == 0)
        m10 = (h_goals == 1) & (a_goals == 0)
        m01 = (h_goals == 0) & (a_goals == 1)
        m11 = (h_goals == 1) & (a_goals == 1)

        def _nll(params: np.ndarray) -> float:
            alphas = params[:n_teams]
            betas = params[n_teams : 2 * n_teams]
            gamma = params[2 * n_teams]
            rho_val = params[2 * n_teams + 1]

            lh = alphas[h_idx] * betas[a_idx] * gamma
            la = alphas[a_idx] * betas[h_idx]

            lh_safe = np.maximum(lh, 1e-12)
            la_safe = np.maximum(la, 1e-12)

            tau_vals = np.ones(n_matches, dtype=np.float64)
            if np.any(m00):
                tau_vals[m00] = 1.0 - lh[m00] * la[m00] * rho_val
            if np.any(m10):
                tau_vals[m10] = 1.0 + la[m10] * rho_val
            if np.any(m01):
                tau_vals[m01] = 1.0 + lh[m01] * rho_val
            if np.any(m11):
                tau_vals[m11] = 1.0 - rho_val

            tau_safe = np.maximum(tau_vals, 1e-12)

            log_p = (
                np.log(tau_safe)
                - lh
                + h_goals * np.log(lh_safe)
                - gammaln_h
                - la
                + a_goals * np.log(la_safe)
                - gammaln_a
            )
            return float(-np.sum(weights * log_prob_clipping(log_p)))

        # Parameter vector layout: [alpha_0..alpha_{n-1}, beta_0..beta_{n-1}, gamma, rho]
        # Initial guess: sum(alpha) = sum_alpha_constraint
        init_alpha = np.full(n_teams, sum_alpha_constraint / n_teams, dtype=np.float64)
        init_beta = np.ones(n_teams, dtype=np.float64)
        init_gamma = 1.25
        init_rho = -0.05
        init_params = np.concatenate([init_alpha, init_beta, [init_gamma, init_rho]])

        bounds: list[tuple[float | None, float | None]] = []
        for _ in range(n_teams):
            bounds.append((1e-5, 10.0))  # alpha
        for _ in range(n_teams):
            bounds.append((1e-5, 50.0))  # beta
        bounds.append((0.01, 10.0))  # gamma
        bounds.append(rho_bounds)  # rho

        constraints = [
            {
                "type": "eq",
                "fun": lambda p: float(np.sum(p[:n_teams]) - sum_alpha_constraint),
            }
        ]

        res = minimize(
            _nll,
            init_params,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": max_iter, "ftol": tol, "disp": False},
        )

        fitted_params = res.x
        fitted_alphas = fitted_params[:n_teams]
        fitted_betas = fitted_params[n_teams : 2 * n_teams]
        fitted_gamma = float(fitted_params[2 * n_teams])
        fitted_rho = float(fitted_params[2 * n_teams + 1])

        # Enforce exact normalization sum(alpha) = sum_alpha_constraint
        alpha_sum = float(np.sum(fitted_alphas))
        if alpha_sum > 0:
            scale = sum_alpha_constraint / alpha_sum
            fitted_alphas = fitted_alphas * scale
            fitted_betas = fitted_betas / scale

        self.attack_params = {t: float(fitted_alphas[i]) for i, t in enumerate(self.teams)}
        self.defense_params = {t: float(fitted_betas[i]) for i, t in enumerate(self.teams)}
        self.home_advantage = fitted_gamma
        self.rho = fitted_rho
        self.is_fitted = True
        self.log_likelihood = float(-res.fun)

        return self

    def predict_expected_goals(
        self,
        home_team: str,
        away_team: str,
    ) -> tuple[float, float]:
        """Compute expected goals (lambda_home, lambda_away) for a matchup."""
        if not self.is_fitted:
            raise RuntimeError("DixonColesEngine must be fitted before making predictions.")

        default_alpha = 1.0 / max(1, len(self.teams))
        default_beta = 1.0

        alpha_h = self.attack_params.get(home_team, default_alpha)
        beta_h = self.defense_params.get(home_team, default_beta)
        alpha_a = self.attack_params.get(away_team, default_alpha)
        beta_a = self.defense_params.get(away_team, default_beta)

        lambda_h = alpha_h * beta_a * self.home_advantage
        lambda_a = alpha_a * beta_h

        return float(lambda_h), float(lambda_a)

    def predict_score_grid(
        self,
        home_team: str,
        away_team: str,
        max_goals: int = MAX_GOALS,
    ) -> BivariateScoreGrid:
        """Generate full joint bivariate score grid for a matchup."""
        lh, la = self.predict_expected_goals(home_team, away_team)
        return build_score_grid(lh, la, rho=self.rho, max_goals=max_goals)

    def predict_match(
        self,
        home_team: str,
        away_team: str,
        max_goals: int = MAX_GOALS,
        over_under_lines: Sequence[float] = (0.5, 1.5, 2.5, 3.5, 4.5),
        asian_handicap_lines: Sequence[float] = (-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5),
    ) -> DixonColesMatchPrediction:
        """Consolidated prediction for match markets."""
        lh, la = self.predict_expected_goals(home_team, away_team)
        grid = build_score_grid(lh, la, rho=self.rho, max_goals=max_goals)

        p_1x2 = grid.prob_1x2()
        btts = grid.prob_btts()
        ou_table = grid.prob_over_under_table(over_under_lines)
        ah_matrix = grid.asian_handicap_matrix(asian_handicap_lines)

        return DixonColesMatchPrediction(
            home_team=home_team,
            away_team=away_team,
            lambda_home=lh,
            lambda_away=la,
            rho=self.rho,
            prob_home=p_1x2["home"],
            prob_draw=p_1x2["draw"],
            prob_away=p_1x2["away"],
            btts_yes=btts["yes"],
            btts_no=btts["no"],
            over_under=ou_table,
            asian_handicap=ah_matrix,
            score_grid=grid,
        )


def ranked_probability_score(
    p_home: float,
    p_draw: float,
    p_away: float,
    actual_home_goals: int,
    actual_away_goals: int,
) -> float:
    """Compute Ranked Probability Score (RPS) for three-way soccer outcome.

    RPS evaluates probabilistic predictions for ordered multi-category outcomes:
    Outcome order: [Home Win (1), Draw (2), Away Win (3)].
    Lower RPS indicates better probabilistic accuracy (0 = perfect prediction).
    """
    # Normalize probabilities
    s = p_home + p_draw + p_away
    if s > 0:
        ph = p_home / s
        pd = p_draw / s
    else:
        ph, pd = 1.0 / 3.0, 1.0 / 3.0

    # Cumulative probabilities
    p1 = ph
    p2 = ph + pd

    # Actual outcome
    if actual_home_goals > actual_away_goals:
        # Home Win
        o1, o2 = 1.0, 1.0
    elif actual_home_goals == actual_away_goals:
        # Draw
        o1, o2 = 0.0, 1.0
    else:
        # Away Win
        o1, o2 = 0.0, 0.0

    return 0.5 * ((p1 - o1) ** 2 + (p2 - o2) ** 2)


def temporal_cross_validation(
    matches: Sequence[Any],
    xi: float,
    n_splits: int = 5,
    min_train_matches: int = 30,
    metric: str = "rps",
    max_iter: int = 150,
) -> float:
    """Perform temporal walk-forward cross-validation for a specific decay parameter xi.

    Parameters
    ----------
    matches : Sequence[Any]
        Chronologically ordered match records.
    xi : float
        Decay parameter to evaluate.
    n_splits : int
        Number of walk-forward test folds.
    min_train_matches : int
        Minimum number of initial matches in training set.
    metric : str
        Evaluation metric: 'rps' (Ranked Probability Score) or 'log_loss'.
    max_iter : int
        Maximum MLE iterations per fold.

    Returns
    -------
    float
        Average validation loss across all folds (lower is better).
    """
    if len(matches) < min_train_matches + n_splits:
        raise ValueError(
            f"Insufficient matches ({len(matches)}) for min_train={min_train_matches} and n_splits={n_splits}"
        )

    # Sort matches chronologically if possible
    def _get_match_date(m: Any) -> float:
        if isinstance(m, dict):
            dt = m.get("event_start_utc", m.get("date", m.get("timestamp", 0.0)))
        elif hasattr(m, "event_start_utc"):
            dt = m.event_start_utc
        elif hasattr(m, "start"):
            dt = m.start
        elif hasattr(m, "date"):
            dt = m.date
        else:
            dt = 0.0
        if isinstance(dt, (int, float, str, datetime, date)):
            return _to_timestamp_days(dt)
        return 0.0

    sorted_matches = sorted(matches, key=_get_match_date)
    total_matches = len(sorted_matches)
    eval_matches = total_matches - min_train_matches
    fold_size = max(1, eval_matches // n_splits)

    fold_scores: list[float] = []

    for s in range(n_splits):
        train_end = min_train_matches + s * fold_size
        test_end = min(total_matches, train_end + fold_size) if s < n_splits - 1 else total_matches

        train_data = sorted_matches[:train_end]
        test_data = sorted_matches[train_end:test_end]

        if not test_data:
            break

        # Fit model on training fold
        engine = DixonColesEngine(xi=xi)
        try:
            engine.fit(train_data, max_iter=max_iter)
        except (RuntimeError, ValueError):
            continue

        match_losses: list[float] = []
        for m in test_data:
            if isinstance(m, dict):
                h_team = str(m.get("home_team", m.get("home", "")))
                a_team = str(m.get("away_team", m.get("away", "")))
                h_g = int(m.get("home_score", m.get("home_goals", 0)))
                a_g = int(m.get("away_score", m.get("away_goals", 0)))
            else:
                h_team = str(m.home_team)
                a_team = str(m.away_team)
                h_g = int(getattr(m, "home_score", getattr(m, "home_goals", 0)))
                a_g = int(getattr(m, "away_score", getattr(m, "away_goals", 0)))

            pred = engine.predict_match(h_team, a_team)
            if metric == "log_loss":
                # Joint score log likelihood loss
                p_score = pred.score_grid.exact_score(h_g, a_g)
                loss = -math.log(max(1e-12, p_score))
            else:
                # Default: RPS on 1X2 outcomes
                loss = ranked_probability_score(pred.prob_home, pred.prob_draw, pred.prob_away, h_g, a_g)

            match_losses.append(loss)

        if match_losses:
            fold_scores.append(float(np.mean(match_losses)))

    if not fold_scores:
        return float("inf")

    return float(np.mean(fold_scores))


def optimize_decay_xi(
    matches: Sequence[Any],
    xi_candidates: Sequence[float] | None = None,
    n_splits: int = 5,
    min_train_matches: int = 30,
    metric: str = "rps",
) -> tuple[float, dict[float, float]]:
    """Search for the optimal decay parameter xi via temporal cross-validation.

    Parameters
    ----------
    matches : Sequence[Any]
        Historical match dataset.
    xi_candidates : Sequence[float] | None
        Grid of xi values to evaluate. Defaults to DEFAULT_XI_GRID.
    n_splits : int
        Number of cross-validation splits.
    min_train_matches : int
        Minimum matches required for training fold.
    metric : str
        Evaluation metric ('rps' or 'log_loss').

    Returns
    -------
    best_xi : float
        Optimal xi parameter minimizing validation loss.
    results : dict[float, float]
        Mapping of {xi_value: cross_val_loss}.
    """
    candidates = list(xi_candidates) if xi_candidates is not None else list(DEFAULT_XI_GRID)
    results: dict[float, float] = {}

    for cand in candidates:
        score = temporal_cross_validation(
            matches=matches,
            xi=cand,
            n_splits=n_splits,
            min_train_matches=min_train_matches,
            metric=metric,
        )
        results[cand] = score

    best_xi = min(results.keys(), key=lambda k: results[k])
    return best_xi, results
