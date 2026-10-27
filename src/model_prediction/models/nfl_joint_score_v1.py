"""Research challenger: one coherent NFL score lattice for all three markets.

The incumbent learned moneyline probability anchors the win marginal. The
incumbent structural margin and total distributions provide score shape.
No historical outcome is used in constructing a game forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .nfl_structural_v5 import (
    NFL_LEAGUE_AVG_TOTAL,
    discrete_spread_distribution,
    discrete_total_distribution,
)

MODEL_VERSION = "nfl-joint-score-v1"


@dataclass(frozen=True)
class NFLJointScoreForecast:
    scores: dict[tuple[int, int], float]

    def home_win_probability(self) -> float:
        return sum(p for (home, away), p in self.scores.items() if home > away) + 0.5 * sum(
            p for (home, away), p in self.scores.items() if home == away
        )

    def home_cover_probability(self, home_line: float) -> float:
        return sum(p for (home, away), p in self.scores.items() if home - away + home_line > 0)

    def away_cover_probability(self, away_line: float) -> float:
        return sum(p for (home, away), p in self.scores.items() if away - home + away_line > 0)

    def over_probability(self, total_line: float) -> float:
        return sum(p for (home, away), p in self.scores.items() if home + away > total_line)

    def under_probability(self, total_line: float) -> float:
        return sum(p for (home, away), p in self.scores.items() if home + away < total_line)


def joint_score_forecast(
    home_win_probability: float,
    margin_mean: float,
    margin_sd: float,
    total_mean: float,
    total_sd: float,
    league_avg_total: float = NFL_LEAGUE_AVG_TOTAL,
) -> NFLJointScoreForecast:
    """Match the learned win marginal while retaining structural score shapes.

    Each integer margin gets a conditional total distribution restricted to
    feasible nonnegative integer scores. Positive and negative margins are
    scaled to match the learned home win probability; tie mass is unchanged.
    """
    values = (home_win_probability, margin_mean, margin_sd, total_mean, total_sd, league_avg_total)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("nonfinite_joint_score_input")
    if not 0 < home_win_probability < 1 or margin_sd <= 0 or total_sd <= 0:
        raise ValueError("invalid_joint_score_parameter")
    margins = discrete_spread_distribution(margin_mean, margin_sd)
    totals = discrete_total_distribution(total_mean, total_sd, league_avg_total)
    tie_mass = margins.get(0, 0.0)
    positive_mass = sum(prob for margin, prob in margins.items() if margin > 0)
    negative_mass = sum(prob for margin, prob in margins.items() if margin < 0)
    target_positive = home_win_probability - tie_mass / 2
    target_negative = 1 - home_win_probability - tie_mass / 2
    if min(target_positive, target_negative, positive_mass, negative_mass) <= 0:
        raise ValueError("unattainable_joint_win_probability")
    scores: dict[tuple[int, int], float] = {}
    for margin, margin_prob in margins.items():
        feasible = [
            (total, prob)
            for total, prob in totals.items()
            if total >= abs(margin) and (total - margin) % 2 == 0
        ]
        conditional_mass = sum(prob for _, prob in feasible)
        if conditional_mass <= 0:
            raise ValueError("no_feasible_scores_for_margin")
        scale = (
            target_positive / positive_mass
            if margin > 0
            else (target_negative / negative_mass if margin < 0 else 1.0)
        )
        for total, total_prob in feasible:
            home = (total + margin) // 2
            away = (total - margin) // 2
            scores[(home, away)] = margin_prob * scale * total_prob / conditional_mass
    return NFLJointScoreForecast(scores)
