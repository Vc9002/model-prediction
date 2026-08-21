"""Permanent quantitative baseline models for sports prediction.

Includes classic sabermetric and statistical baselines (Forrest31/Bill James):
1. Constant Home Win Rate (naive baseline)
2. Pythagorean Expectation Matchup Probability (Bill James run-differential model with exponent 1.83)
3. Log5 Matchup Probability (Bill James head-to-head win rate model with Laplace smoothing)
4. Combined Elo + Log5 + Pythagorean baseline
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PYTHAGOREAN_EXPONENT_MLB: float = 1.83


def pythagorean_win_rate(
    runs_scored: float, runs_allowed: float, exponent: float = PYTHAGOREAN_EXPONENT_MLB
) -> float:
    """Compute Pythagorean win expectation from runs scored and allowed."""
    if runs_scored <= 0 and runs_allowed <= 0:
        return 0.50
    if runs_scored <= 0:
        return 0.01
    if runs_allowed <= 0:
        return 0.99
    rs_exp = math.pow(runs_scored, exponent)
    ra_exp = math.pow(runs_allowed, exponent)
    denom = rs_exp + ra_exp
    if denom <= 0:
        return 0.50
    return rs_exp / denom


def log5_matchup_probability(win_rate_a: float, win_rate_b: float) -> float:
    """Compute Log5 probability that team A beats team B in a neutral matchup.

    Formula:
        P(A beats B) = (w_A - w_A * w_B) / (w_A + w_B - 2 * w_A * w_B)
    """
    w_a = max(0.01, min(0.99, win_rate_a))
    w_b = max(0.01, min(0.99, win_rate_b))
    num = w_a - (w_a * w_b)
    denom = w_a + w_b - (2.0 * w_a * w_b)
    if denom <= 0:
        return 0.50
    return max(0.01, min(0.99, num / denom))


@dataclass(slots=True)
class TeamRecordAccumulator:
    wins: int = 0
    losses: int = 0
    runs_scored: int = 0
    runs_allowed: int = 0

    @property
    def total_games(self) -> int:
        return self.wins + self.losses

    def win_rate(self, prior_games: int = 20, prior_win_rate: float = 0.50) -> float:
        """Laplace/Bayes smoothed win rate."""
        return (self.wins + prior_games * prior_win_rate) / (self.total_games + prior_games)

    def pythagorean_expectation(self, prior_runs: int = 100) -> float:
        """Smoothed Pythagorean win rate."""
        smoothed_rs = self.runs_scored + prior_runs * 0.5
        smoothed_ra = self.runs_allowed + prior_runs * 0.5
        return pythagorean_win_rate(smoothed_rs, smoothed_ra)
