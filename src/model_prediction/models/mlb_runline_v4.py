"""MLB Structural Runline v4 (mlb-structural-runline-v4).

Derives point-in-time spread / runline (-1.5 / +1.5) probabilities directly from the
bivariate joint Poisson run distribution P(R_H = h, R_A = a):
- P(Home -1.5) = sum_{h >= a + 2} P(R_H=h, R_A=a)
- P(Away +1.5) = sum_{h <= a + 1} P(R_H=h, R_A=a) = 1 - P(Home -1.5)
- P(Away -1.5) = sum_{a >= h + 2} P(R_H=h, R_A=a)
- P(Home +1.5) = sum_{a <= h + 1} P(R_H=h, R_A=a) = 1 - P(Away -1.5)

Eliminates heuristic margin regressions in favor of mathematically coherent structural runlines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MLB_RUNLINE_V4_MODEL_VERSION = "mlb-structural-runline-v4"


@dataclass(frozen=True)
class MLBRunlineForecast:
    """Complete structural runline forecast for an MLB matchup."""

    home_team: str
    away_team: str
    lambda_home: float
    lambda_away: float
    prob_home_minus_1_5: float
    prob_away_plus_1_5: float
    prob_away_minus_1_5: float
    prob_home_plus_1_5: float
    prob_one_run_game: float
    bivariate_run_matrix: list[list[float]]


def compute_bivariate_run_matrix(
    lambda_home: float,
    lambda_away: float,
    max_runs: int = 15,
) -> np.ndarray:
    """Compute (max_runs+1) x (max_runs+1) joint Poisson run distribution."""
    lh = max(0.2, float(lambda_home))
    la = max(0.2, float(lambda_away))

    p_h = np.array([(math.exp(-lh) * (lh**i)) / math.factorial(i) for i in range(max_runs + 1)])
    p_a = np.array([(math.exp(-la) * (la**j)) / math.factorial(j) for j in range(max_runs + 1)])

    matrix = np.outer(p_h, p_a)
    tot = float(np.sum(matrix))
    if tot > 0:
        matrix /= tot
    return matrix


class MLBStructuralRunlineV4Model:
    """Structural Runline (-1.5 / +1.5) Engine derived from Bivariate Poisson Scoring."""

    version: str = MLB_RUNLINE_V4_MODEL_VERSION

    def __init__(self, max_runs: int = 15) -> None:
        self.max_runs = max_runs

    def forecast_runline(
        self,
        home_team: str,
        away_team: str,
        lambda_home: float,
        lambda_away: float,
    ) -> MLBRunlineForecast:
        """Derive coherent runline cover probabilities from expected runs."""
        matrix = compute_bivariate_run_matrix(lambda_home, lambda_away, max_runs=self.max_runs)

        # Indices: i = Home Runs, j = Away Runs
        # Home -1.5: i - j >= 2 (i >= j + 2)
        grid_i, grid_j = np.indices(matrix.shape)
        home_covers_minus_1_5 = float(np.sum(matrix[grid_i >= grid_j + 2]))
        away_covers_plus_1_5 = 1.0 - home_covers_minus_1_5

        # Away -1.5: j - i >= 2 (j >= i + 2)
        away_covers_minus_1_5 = float(np.sum(matrix[grid_j >= grid_i + 2]))
        home_covers_plus_1_5 = 1.0 - away_covers_minus_1_5

        # Exact 1-run game probability: |i - j| == 1
        one_run_prob = float(np.sum(matrix[np.abs(grid_i - grid_j) == 1]))

        return MLBRunlineForecast(
            home_team=home_team,
            away_team=away_team,
            lambda_home=round(float(lambda_home), 3),
            lambda_away=round(float(lambda_away), 3),
            prob_home_minus_1_5=round(home_covers_minus_1_5, 4),
            prob_away_plus_1_5=round(away_covers_plus_1_5, 4),
            prob_away_minus_1_5=round(away_covers_minus_1_5, 4),
            prob_home_plus_1_5=round(home_covers_plus_1_5, 4),
            prob_one_run_game=round(one_run_prob, 4),
            bivariate_run_matrix=matrix.tolist(),
        )
