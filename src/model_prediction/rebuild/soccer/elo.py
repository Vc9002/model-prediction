"""Dixon-Coles Poisson model for soccer match outcome prediction.

Named `elo.py` for consistency with other sport model modules, but implements
a Dixon-Coles bivariate Poisson model (not Elo). The model estimates:
  - Team attack/defense strengths (constrained to sum to zero)
  - League baseline scoring rates
  - Home advantage
  - Dixon-Coles rho (low-score draw correlation)

Prediction: P(HOME), P(DRAW), P(AWAY) from the bivariate Poisson score matrix
with Dixon-Coles tau correction.

Reference: Dixon & Coles (1997), "Modelling Association Football Scores
and Inefficiencies in the Football Betting Market."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.special import gammaln


@dataclass
class DixonColesParams:
    """Fitted Dixon-Coles model parameters."""

    team_attack: dict[str, float] = field(default_factory=dict)
    team_defense: dict[str, float] = field(default_factory=dict)
    league_baseline: dict[str, float] = field(default_factory=dict)
    home_advantage: float = 0.0
    rho: float = 0.0  # Dixon-Coles low-score correlation, typically in [-0.1, 0.1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_attack": self.team_attack,
            "team_defense": self.team_defense,
            "league_baseline": self.league_baseline,
            "home_advantage": self.home_advantage,
            "rho": self.rho,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DixonColesParams:
        return cls(
            team_attack=data.get("team_attack", {}),
            team_defense=data.get("team_defense", {}),
            league_baseline=data.get("league_baseline", {}),
            home_advantage=float(data.get("home_advantage", 0.0)),
            rho=float(data.get("rho", 0.0)),
        )


def _tau(h: int, a: int, lambda_h: float, lambda_a: float, rho: float) -> float:
    """Dixon-Coles tau correction for low-scoring draws.

    Returns a multiplicative correction factor for the independent
    bivariate Poisson probability. tau = 1 for most scorelines.
    """
    if h == 0 and a == 0:
        return 1.0 - lambda_h * lambda_a * rho
    if h == 1 and a == 0:
        return 1.0 + lambda_a * rho
    if h == 0 and a == 1:
        return 1.0 + lambda_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _bivariate_poisson_logpmf(h: int, a: int, lambda_h: float, lambda_a: float, rho: float) -> float:
    """Log-probability of (h, a) goals under Dixon-Coles bivariate Poisson."""
    tau_val = _tau(h, a, lambda_h, lambda_a, rho)
    if tau_val <= 0:
        return -np.inf
    log_prob = (
        np.log(tau_val)
        + h * np.log(max(lambda_h, 1e-15))
        + a * np.log(max(lambda_a, 1e-15))
        - lambda_h
        - lambda_a
        - gammaln(h + 1)
        - gammaln(a + 1)
    )
    return float(log_prob)


def _score_matrix_probability(
    lambda_h: float, lambda_a: float, rho: float, max_goals: int = 15
) -> tuple[float, float, float]:
    """Compute P(HOME), P(DRAW), P(AWAY) from the score probability matrix.

    Sums over all (h, a) up to max_goals (truncation is negligible for
    realistic scoring rates).
    """
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            tau_val = _tau(h, a, lambda_h, lambda_a, rho)
            if tau_val <= 0:
                continue
            # Poisson independent part
            p = (
                tau_val
                * np.exp(-lambda_h + h * np.log(max(lambda_h, 1e-15)) - gammaln(h + 1))
                * np.exp(-lambda_a + a * np.log(max(lambda_a, 1e-15)) - gammaln(a + 1))
            )
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    if total > 0:
        p_home /= total
        p_draw /= total
        p_away /= total
    return p_home, p_draw, p_away


class DixonColesModel:
    """Dixon-Coles bivariate Poisson model for soccer."""

    MAX_GOALS = 15

    def __init__(self) -> None:
        self.params: DixonColesParams | None = None

    def fit(
        self,
        matches: pl.DataFrame,
        *,
        verbose: bool = False,
        max_iterations: int = 5000,
    ) -> DixonColesParams:
        """Fit Dixon-Coles parameters via maximum likelihood.

        Args:
            matches: DataFrame with columns:
                home_team_name, away_team_name, home_score, away_score,
                competition_id (league code)
            verbose: Print optimization progress
            max_iterations: Maximum iterations for L-BFGS-B

        Returns:
            Fitted DixonColesParams
        """
        required = {"home_team_name", "away_team_name", "home_score", "away_score"}
        missing = sorted(required - set(matches.columns))
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Collect unique teams and leagues
        teams = sorted(
            set(matches["home_team_name"].unique().to_list())
            | set(matches["away_team_name"].unique().to_list())
        )
        leagues = (
            sorted(matches["competition_id"].unique().to_list())
            if "competition_id" in matches.columns
            else ["default"]
        )

        team_to_idx = {t: i for i, t in enumerate(teams)}
        league_to_idx = {l: i for i, l in enumerate(leagues)}

        n_teams = len(teams)
        n_leagues = len(leagues)

        # Build match arrays
        home_idx = np.array([team_to_idx[m["home_team_name"]] for m in matches.iter_rows(named=True)])
        away_idx = np.array([team_to_idx[m["away_team_name"]] for m in matches.iter_rows(named=True)])
        home_goals = np.array(matches["home_score"].to_list(), dtype=np.int64)
        away_goals = np.array(matches["away_score"].to_list(), dtype=np.int64)

        if "competition_id" in matches.columns:
            league_idx = np.array(
                [league_to_idx.get(m["competition_id"], 0) for m in matches.iter_rows(named=True)]
            )
        else:
            league_idx = np.zeros(len(home_idx), dtype=np.int64)

        n_matches = len(home_idx)
        assert n_matches > 0, "No matches to fit"

        # Parameter layout:
        # [0 : n_teams)                    attack_i
        # [n_teams : 2*n_teams)            defense_i
        # [2*n_teams : 2*n_teams + n_leagues) league_baseline_k
        # [2*n_teams + n_leagues]          home_advantage
        # [2*n_teams + n_leagues + 1]      rho (logit-transformed)
        n_params = 2 * n_teams + n_leagues + 2

        # Initial guess
        x0 = np.zeros(n_params)
        x0[2 * n_teams : 2 * n_teams + n_leagues] = 0.3  # league baseline
        x0[2 * n_teams + n_leagues] = 0.2  # home advantage
        x0[-1] = 0.0  # logit(rho) -> rho ≈ 0

        def unpack(x: np.ndarray) -> tuple:
            attack = x[:n_teams]
            defense = x[n_teams : 2 * n_teams]
            league_base = x[2 * n_teams : 2 * n_teams + n_leagues]
            home_adv = x[2 * n_teams + n_leagues]
            rho_logit = x[-1]
            rho = np.tanh(rho_logit) * 0.3  # soft-clamp to [-0.3, 0.3]
            return attack, defense, league_base, home_adv, rho

        # Pre-compute gammaln for score lookup (0..20 goals covers all real data)
        _gammaln_lookup = np.array([gammaln(k + 1) for k in range(21)])

        def _vec_logpmf(
            h: np.ndarray,
            a: np.ndarray,
            lambda_h: np.ndarray,
            lambda_a: np.ndarray,
            rho: float,
        ) -> np.ndarray:
            """Vectorized bivariate Poisson log-PMF with Dixon-Coles tau."""
            small = 1e-15
            lh = np.maximum(lambda_h, small)
            la = np.maximum(lambda_a, small)

            # Base Poisson log-prob
            log_p = (
                h * np.log(lh)
                + a * np.log(la)
                - lh
                - la
                - _gammaln_lookup[h.clip(0, 20)]
                - _gammaln_lookup[a.clip(0, 20)]
            )

            # Tau correction
            tau = np.ones_like(h, dtype=float)
            mask_00 = (h == 0) & (a == 0)
            mask_10 = (h == 1) & (a == 0)
            mask_01 = (h == 0) & (a == 1)
            mask_11 = (h == 1) & (a == 1)
            tau[mask_00] = 1.0 - lambda_h[mask_00] * lambda_a[mask_00] * rho
            tau[mask_10] = 1.0 + lambda_a[mask_10] * rho
            tau[mask_01] = 1.0 + lambda_h[mask_01] * rho
            tau[mask_11] = 1.0 - rho

            valid = tau > 0
            result = np.full_like(log_p, -np.inf)
            result[valid] = log_p[valid] + np.log(tau[valid])
            return result

        def neg_log_likelihood(x: np.ndarray) -> float:
            attack, defense, league_base, home_adv, rho = unpack(x)
            lambda_h = np.exp(league_base[league_idx] + attack[home_idx] + defense[away_idx] + home_adv)
            lambda_a = np.exp(league_base[league_idx] + attack[away_idx] + defense[home_idx])
            ll = _vec_logpmf(home_goals, away_goals, lambda_h, lambda_a, rho)
            return float(-ll.sum())

        if verbose:
            print(
                f"Fitting Dixon-Coles: {n_teams} teams, {n_leagues} leagues, "
                f"{n_matches} matches, {n_params} parameters"
            )

        result = minimize(
            neg_log_likelihood,
            x0,
            method="L-BFGS-B",
            options={"maxiter": max_iterations, "maxcor": 20},
        )

        attack, defense, league_base, home_adv, rho = unpack(result.x)

        # Center attack and defense to sum to zero
        attack_mean = np.mean(attack)
        defense_mean = np.mean(defense)
        attack_centered = attack - attack_mean
        defense_centered = defense - defense_mean
        # Adjust league baselines to absorb the centering
        league_base_adjusted = league_base + attack_mean + defense_mean

        params = DixonColesParams(
            team_attack={teams[i]: float(attack_centered[i]) for i in range(n_teams)},
            team_defense={teams[i]: float(defense_centered[i]) for i in range(n_teams)},
            league_baseline={leagues[i]: float(league_base_adjusted[i]) for i in range(n_leagues)},
            home_advantage=float(home_adv),
            rho=float(rho),
        )
        self.params = params
        return params

    def predict(
        self,
        home_team: str,
        away_team: str,
        league: str | None = None,
    ) -> tuple[float, float, float]:
        """Predict P(HOME), P(DRAW), P(AWAY) for a single match.

        Returns probabilities that sum to 1.
        """
        if self.params is None:
            raise RuntimeError("Model not fitted. Call fit() first.")

        attack_h = self.params.team_attack.get(home_team, 0.0)
        defense_h = self.params.team_defense.get(home_team, 0.0)
        attack_a = self.params.team_attack.get(away_team, 0.0)
        defense_a = self.params.team_defense.get(away_team, 0.0)

        league_base = self.params.league_baseline.get(
            league or "default",
            np.mean(list(self.params.league_baseline.values())) if self.params.league_baseline else 0.3,
        )

        lambda_h = np.exp(league_base + attack_h + defense_a + self.params.home_advantage)
        lambda_a = np.exp(league_base + attack_a + defense_h)

        return _score_matrix_probability(lambda_h, lambda_a, self.params.rho)

    def predict_batch(self, matches: pl.DataFrame) -> pl.DataFrame:
        """Predict probabilities for a batch of matches.

        Args:
            matches: DataFrame with home_team_name, away_team_name,
                     and optionally competition_id columns.

        Returns:
            DataFrame with added p_home, p_draw, p_away columns.
        """
        if self.params is None:
            raise RuntimeError("Model not fitted. Call fit() first.")

        results = []
        for row in matches.iter_rows(named=True):
            league = row.get("competition_id")
            p_h, p_d, p_a = self.predict(
                str(row["home_team_name"]),
                str(row["away_team_name"]),
                league=str(league) if league else None,
            )
            results.append({"p_home": p_h, "p_draw": p_d, "p_away": p_a})

        preds = pl.DataFrame(results)
        return pl.concat([matches, preds], how="horizontal_extend")
