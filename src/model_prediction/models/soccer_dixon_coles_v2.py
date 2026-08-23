"""Hierarchical Dixon-Coles v2 Soccer Model (soccer-dc-v2).

Computes full bivariate score distributions P(H=i, A=j) from competition baselines
and empirical-Bayes shrunk team attack/defense ratings:
    log lambda_H = mu_c + HFA_c + alpha_H - beta_A
    log lambda_A = mu_c + alpha_A - beta_H

Derives consistent 1X2, Both Teams To Score (BTTS), Over/Under totals, and Double Chance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SoccerCompetitionParams:
    """Competition-level baseline parameters."""

    competition_id: str
    baseline_mu: float = 0.25  # log baseline expected goals per side (~ 1.28 goals)
    home_field_advantage: float = 0.20  # log HFA boost (~ 22% expected goal lift)
    rho_low_score_dep: float = -0.05  # Dixon-Coles 0-0 / 1-0 / 0-1 correlation correction
    shrinkage_tau: float = 10.0  # partial pooling sample size


@dataclass(frozen=True)
class SoccerTeamRatings:
    """Empirical Bayes shrunk attack and defense parameters."""

    team_id: str
    attack: float = 0.0  # log attack rating (0 = average)
    defense: float = 0.0  # log defense rating (0 = average)
    matches_played: int = 0


@dataclass(frozen=True)
class SoccerDCV2Forecast:
    """Full probabilistic forecast derived from bivariate score matrix."""

    home_expected_goals: float
    away_expected_goals: float
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    prob_btts_yes: float
    prob_btts_no: float
    prob_over_1_5: float
    prob_under_1_5: float
    prob_over_2_5: float
    prob_under_2_5: float
    prob_over_3_5: float
    prob_under_3_5: float
    prob_double_chance_1x: float
    prob_double_chance_x2: float
    prob_double_chance_12: float
    score_matrix: list[list[float]]


def _dixon_coles_tau(x: int, y: int, lambda_: float, mu_: float, rho: float) -> float:
    """Dixon-Coles bivariate adjustment factor tau(x, y)."""
    if x == 0 and y == 0:
        return 1.0 - (lambda_ * mu_ * rho)
    elif x == 0 and y == 1:
        return 1.0 + (lambda_ * rho)
    elif x == 1 and y == 0:
        return 1.0 + (mu_ * rho)
    elif x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


class SoccerDixonColesV2Model:
    """Hierarchical Dixon-Coles model supporting all Polymarket soccer markets."""

    def __init__(
        self,
        competition_params: dict[str, SoccerCompetitionParams] | None = None,
        max_goals: int = 10,
    ) -> None:
        self.competition_params = competition_params or {}
        self.max_goals = max_goals
        self.team_ratings: dict[str, SoccerTeamRatings] = {}

    def get_competition_params(self, competition_id: str) -> SoccerCompetitionParams:
        return self.competition_params.get(
            competition_id, SoccerCompetitionParams(competition_id=competition_id)
        )

    def fit_team_ratings(
        self,
        match_history: list[dict[str, Any]],
        shrinkage_tau: float = 10.0,
    ) -> None:
        """Estimate shrunk attack and defense parameters from completed match history."""
        goals_for: dict[str, float] = {}
        goals_against: dict[str, float] = {}
        matches: dict[str, int] = {}

        for m in match_history:
            h, a = m["home_team"], m["away_team"]
            hg, ag = float(m["home_score"]), float(m["away_score"])
            goals_for[h] = goals_for.get(h, 0.0) + hg
            goals_against[h] = goals_against.get(h, 0.0) + ag
            matches[h] = matches.get(h, 0) + 1

            goals_for[a] = goals_for.get(a, 0.0) + ag
            goals_against[a] = goals_against.get(a, 0.0) + hg
            matches[a] = matches.get(a, 0) + 1

        for team, n in matches.items():
            if n <= 0:
                continue
            avg_scored = goals_for[team] / n
            avg_conceded = goals_against[team] / n

            # Empirical Bayes partial-pooling shrinkage
            w = n / (n + shrinkage_tau)
            # relative to baseline ~1.30 goals
            raw_att = math.log(max(0.1, avg_scored / 1.30))
            raw_def = math.log(max(0.1, avg_conceded / 1.30))

            shrunk_att = w * raw_att
            shrunk_def = w * raw_def

            self.team_ratings[team] = SoccerTeamRatings(
                team_id=team,
                attack=round(shrunk_att, 4),
                defense=round(shrunk_def, 4),
                matches_played=n,
            )

    def forecast_match(
        self,
        home_team: str,
        away_team: str,
        competition_id: str = "global",
    ) -> SoccerDCV2Forecast:
        """Generate complete joint distribution and derived market probabilities."""
        params = self.get_competition_params(competition_id)
        home_rat = self.team_ratings.get(home_team, SoccerTeamRatings(team_id=home_team))
        away_rat = self.team_ratings.get(away_team, SoccerTeamRatings(team_id=away_team))

        log_lambda_h = params.baseline_mu + params.home_field_advantage + home_rat.attack - away_rat.defense
        log_lambda_a = params.baseline_mu + away_rat.attack - home_rat.defense

        lambda_h = max(0.10, math.exp(log_lambda_h))
        lambda_a = max(0.10, math.exp(log_lambda_a))

        # Compute (max_goals+1) x (max_goals+1) bivariate score matrix
        matrix = np.zeros((self.max_goals + 1, self.max_goals + 1), dtype=float)
        for i in range(self.max_goals + 1):
            p_h = (math.exp(-lambda_h) * (lambda_h**i)) / math.factorial(i)
            for j in range(self.max_goals + 1):
                p_a = (math.exp(-lambda_a) * (lambda_a**j)) / math.factorial(j)
                adj = _dixon_coles_tau(i, j, lambda_h, lambda_a, params.rho_low_score_dep)
                matrix[i, j] = p_h * p_a * adj

        # Normalize matrix
        total_prob = float(np.sum(matrix))
        if total_prob > 0:
            matrix /= total_prob

        # Derived Probabilities
        prob_home = float(np.sum(np.tril(matrix, -1)))
        prob_draw = float(np.sum(np.diag(matrix)))
        prob_away = float(np.sum(np.triu(matrix, 1)))

        # BTTS: Both Teams To Score (i >= 1 and j >= 1)
        prob_btts_yes = float(np.sum(matrix[1:, 1:]))
        prob_btts_no = 1.0 - prob_btts_yes

        # Totals
        goals_grid = np.fromfunction(lambda i, j: i + j, matrix.shape, dtype=int)
        prob_over_1_5 = float(np.sum(matrix[goals_grid > 1.5]))
        prob_under_1_5 = 1.0 - prob_over_1_5

        prob_over_2_5 = float(np.sum(matrix[goals_grid > 2.5]))
        prob_under_2_5 = 1.0 - prob_over_2_5

        prob_over_3_5 = float(np.sum(matrix[goals_grid > 3.5]))
        prob_under_3_5 = 1.0 - prob_over_3_5

        # Double Chance
        prob_dc_1x = prob_home + prob_draw
        prob_dc_x2 = prob_draw + prob_away
        prob_dc_12 = prob_home + prob_away

        return SoccerDCV2Forecast(
            home_expected_goals=round(lambda_h, 3),
            away_expected_goals=round(lambda_a, 3),
            prob_home_win=round(prob_home, 4),
            prob_draw=round(prob_draw, 4),
            prob_away_win=round(prob_away, 4),
            prob_btts_yes=round(prob_btts_yes, 4),
            prob_btts_no=round(prob_btts_no, 4),
            prob_over_1_5=round(prob_over_1_5, 4),
            prob_under_1_5=round(prob_under_1_5, 4),
            prob_over_2_5=round(prob_over_2_5, 4),
            prob_under_2_5=round(prob_under_2_5, 4),
            prob_over_3_5=round(prob_over_3_5, 4),
            prob_under_3_5=round(prob_under_3_5, 4),
            prob_double_chance_1x=round(prob_dc_1x, 4),
            prob_double_chance_x2=round(prob_dc_x2, 4),
            prob_double_chance_12=round(prob_dc_12, 4),
            score_matrix=matrix.tolist(),
        )
