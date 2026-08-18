"""Shared Poisson / Dixon-Coles scoring math -- sport-universal infrastructure,
not a per-league predictive assumption (per the binding rule in docs/
RESEARCH_BACKLOG.md: "shared infrastructure != shared model... never
predictive assumptions across different sports/games"). The functional
FORM (independent Poisson rates with a Dixon-Coles low-score correction) is
shared; every number that goes INTO it (baseline, home advantage, rho) is
fit per league in league_model.py's callers, never hardcoded here.

Deliberately byte-for-byte the same formulas as models/soccer.py's
module-level functions -- this module doesn't change the incumbent's
behavior, it lets per-league configs reuse the identical math without
duplicating it six times.
"""

from __future__ import annotations

from math import exp, log

MAX_GOALS = 10


def poisson_pmf(rate: float, k: int) -> float:
    result = exp(-rate)
    for i in range(1, k + 1):
        result *= rate / i
    return result


def dc_adjustment(home_goals: int, away_goals: int, home_rate: float, away_rate: float, rho: float) -> float:
    """Dixon-Coles tau for the four low-score cells."""
    if home_goals == 0 and away_goals == 0:
        return 1 - home_rate * away_rate * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_rate * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_rate * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def score_matrix(
    home_rate: float, away_rate: float, rho: float, max_goals: int = MAX_GOALS
) -> list[list[float]]:
    matrix = []
    for h in range(max_goals + 1):
        row = []
        for a in range(max_goals + 1):
            probability = (
                poisson_pmf(home_rate, h)
                * poisson_pmf(away_rate, a)
                * dc_adjustment(h, a, home_rate, away_rate, rho)
            )
            row.append(max(probability, 0.0))
        matrix.append(row)
    total = sum(sum(row) for row in matrix)
    return [[cell / total for cell in row] for row in matrix]


def matrix_outcomes(matrix: list[list[float]]) -> tuple[float, float, float]:
    """(home_win, away_win, draw) from a score matrix."""
    max_goals = len(matrix) - 1
    home_win = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(h))
    away_win = sum(matrix[h][a] for a in range(max_goals + 1) for h in range(a))
    draw = 1 - home_win - away_win
    return home_win, away_win, draw


def matrix_over_under(matrix: list[list[float]], line: float) -> float:
    max_goals = len(matrix) - 1
    return sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a > line)


def matrix_btts(matrix: list[list[float]]) -> float:
    max_goals = len(matrix) - 1
    return sum(matrix[h][a] for h in range(1, max_goals + 1) for a in range(1, max_goals + 1))


def platt_calibrate(raw_probability: float, intercept: float, slope: float) -> float:
    clipped = min(1 - 1e-9, max(1e-9, raw_probability))
    logit = log(clipped / (1 - clipped))
    calibrated = intercept + slope * logit
    return 1 / (1 + exp(-calibrated))
