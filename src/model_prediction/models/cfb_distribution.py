"""College Football (NCAAF) Joint Scoring Distribution and Market Probability Engine.

Derives internally coherent Moneyline, Spread, and Total probabilities from:
1. Expected Possessions x Expected PPP scoring framework (mu_away, mu_home)
2. Distributional engines:
   - Empirical Residual Simulation
   - Bivariate Normal Residual Distribution
   - Negative Binomial Overdispersed Discrete Scoring Engine
   - Possession-Level Drive Simulation
3. Permanent sign conventions:
   - Margin = HomePoints - AwayPoints
   - MarketImpliedHomeMargin = -spread_home_line (e.g. Home -7.5 -> Implied margin +7.5)
   - ActualTotal = HomePoints + AwayPoints
   - R_spread = ActualMargin - MarketImpliedHomeMargin
   - R_total = ActualTotal - MarketTotal
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

# Key numbers in College Football
KEY_MARGINS: dict[int, float] = {
    3: 0.145,
    7: 0.138,
    10: 0.065,
    14: 0.055,
    17: 0.042,
    21: 0.038,
    4: 0.035,
    6: 0.032,
    1: 0.028,
    2: 0.022,
    24: 0.025,
    28: 0.022,
    31: 0.018,
    35: 0.015,
}

KEY_TOTALS: dict[int, float] = {
    41: 0.038,
    44: 0.042,
    47: 0.045,
    51: 0.048,
    54: 0.052,
    58: 0.044,
    61: 0.039,
    65: 0.035,
}


class CFBDistributionType(str, Enum):
    NEGATIVE_BINOMIAL = "negative_binomial"
    BIVARIATE_NORMAL = "bivariate_normal"
    EMPIRICAL_RESIDUAL = "empirical_residual"
    POSSESSION_DRIVE_MC = "possession_drive_mc"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


@dataclass(frozen=True)
class CFBJointMarketProbabilities:
    # Moneyline
    p_home_win: float
    p_away_win: float

    # Spread (relative to home spread line)
    spread_home_line: float
    p_away_cover: float
    p_home_cover: float
    p_push_spread: float

    # Total
    total_line: float
    p_over: float
    p_under: float
    p_push_total: float

    # Summary Statistics
    projected_home_points: float
    projected_away_points: float
    projected_margin_home: float
    projected_total: float
    distribution_used: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "moneyline": {
                "home": round(self.p_home_win, 4),
                "away": round(self.p_away_win, 4),
            },
            "spread": {
                "line": self.spread_home_line,
                "away": round(self.p_away_cover, 4),
                "home": round(self.p_home_cover, 4),
                "push": round(self.p_push_spread, 4),
            },
            "total": {
                "line": self.total_line,
                "over": round(self.p_over, 4),
                "under": round(self.p_under, 4),
                "push": round(self.p_push_total, 4),
            },
            "projected_home": round(self.projected_home_points, 1),
            "projected_away": round(self.projected_away_points, 1),
            "projected_margin": round(self.projected_margin_home, 1),
            "projected_total": round(self.projected_total, 1),
            "distribution": self.distribution_used,
        }


class CFBJointDistributionEngine:
    """Generates joint scoring distributions and derives all 3 market probabilities."""

    def __init__(
        self,
        distribution_type: CFBDistributionType = CFBDistributionType.NEGATIVE_BINOMIAL,
        margin_sd: float = 15.5,
        total_sd: float = 14.8,
        score_correlation: float = 0.18,
        nb_dispersion: float = 12.0,  # Negative binomial r parameter
        n_simulations: int = 15000,
        random_seed: int = 42,
    ) -> None:
        self.distribution_type = distribution_type
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.score_correlation = score_correlation
        self.nb_dispersion = nb_dispersion
        self.n_simulations = n_simulations
        self.rng = np.random.default_rng(random_seed)

    def simulate_joint_scores(
        self,
        mu_home: float,
        mu_away: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Simulate joint (home_scores, away_scores) under the specified distribution."""
        if self.distribution_type == CFBDistributionType.NEGATIVE_BINOMIAL:
            # Overdispersed negative binomial discrete scoring
            r_home = max(2.0, mu_home**2 / max(1.0, (1.45 * mu_home) - mu_home))
            p_home = r_home / (r_home + mu_home)
            raw_home = self.rng.negative_binomial(r_home, p_home, size=self.n_simulations)

            r_away = max(2.0, mu_away**2 / max(1.0, (1.45 * mu_away) - mu_away))
            p_away = r_away / (r_away + mu_away)
            raw_away = self.rng.negative_binomial(r_away, p_away, size=self.n_simulations)

            # Correlate using common game pace component
            common_noise = self.rng.normal(0.0, 1.0, size=self.n_simulations)
            home_scores = np.maximum(0, np.round(raw_home + self.score_correlation * 3.5 * common_noise))
            away_scores = np.maximum(0, np.round(raw_away + self.score_correlation * 3.5 * common_noise))

        elif self.distribution_type == CFBDistributionType.BIVARIATE_NORMAL:
            cov = (
                self.score_correlation * (self.margin_sd / math.sqrt(2.0)) * (self.margin_sd / math.sqrt(2.0))
            )
            cov_matrix = [
                [(self.margin_sd / math.sqrt(2.0)) ** 2, cov],
                [cov, (self.margin_sd / math.sqrt(2.0)) ** 2],
            ]
            draws = self.rng.multivariate_normal([mu_home, mu_away], cov_matrix, size=self.n_simulations)
            home_scores = np.maximum(0, np.round(draws[:, 0]))
            away_scores = np.maximum(0, np.round(draws[:, 1]))

        elif self.distribution_type == CFBDistributionType.POSSESSION_DRIVE_MC:
            # Drive-level Monte Carlo simulation
            n_drives = 12
            # Per-drive outcome probabilities: [TD (7), FG (3), Safety (2), Punt/Turnover (0)]
            p_td_h = min(0.60, mu_home / (n_drives * 7.0))
            p_fg_h = min(0.35, max(0.05, (mu_home - p_td_h * n_drives * 7.0) / (n_drives * 3.0)))

            p_td_a = min(0.60, mu_away / (n_drives * 7.0))
            p_fg_a = min(0.35, max(0.05, (mu_away - p_td_a * n_drives * 7.0) / (n_drives * 3.0)))

            h_td = self.rng.binomial(n_drives, p_td_h, size=self.n_simulations)
            h_fg = self.rng.binomial(n_drives, p_fg_h, size=self.n_simulations)
            home_scores = h_td * 7 + h_fg * 3

            a_td = self.rng.binomial(n_drives, p_td_a, size=self.n_simulations)
            a_fg = self.rng.binomial(n_drives, p_fg_a, size=self.n_simulations)
            away_scores = a_td * 7 + a_fg * 3

        else:  # EMPIRICAL_RESIDUAL
            std_res_h = self.rng.standard_t(df=6, size=self.n_simulations) * (self.margin_sd / math.sqrt(2.0))
            std_res_a = self.rng.standard_t(df=6, size=self.n_simulations) * (self.margin_sd / math.sqrt(2.0))
            home_scores = np.maximum(0, np.round(mu_home + std_res_h))
            away_scores = np.maximum(0, np.round(mu_away + std_res_a))

        return home_scores, away_scores

    def compute_market_probabilities(
        self,
        mu_home: float,
        mu_away: float,
        spread_home_line: float | None = None,
        total_line: float | None = None,
    ) -> CFBJointMarketProbabilities:
        """Derive calibrated Moneyline, Spread, and Total probabilities."""
        home_scores, away_scores = self.simulate_joint_scores(mu_home, mu_away)
        margins = home_scores - away_scores  # Home - Away
        totals = home_scores + away_scores  # Home + Away

        # 1. Moneyline
        # Overtime tie resolution in CFB (home wins ~52% in OT)
        home_wins = np.sum(margins > 0)
        ties = np.sum(margins == 0)

        p_home_win = float(home_wins + 0.52 * ties) / float(self.n_simulations)
        p_away_win = 1.0 - p_home_win

        # 2. Spread
        # spread_home_line: e.g. -7.5 means Home is favored by 7.5
        # Market implied home margin = -spread_home_line (+7.5)
        # Away covers if Margin < MarketImpliedHomeMargin (i.e. Home does not cover)
        sp_home = (
            spread_home_line if spread_home_line is not None else round(-(mu_home - mu_away) * 2.0) / 2.0
        )
        implied_margin = -sp_home

        is_integer_spread = float(sp_home).is_integer()
        if is_integer_spread:
            away_covers = np.sum(margins < implied_margin)
            home_covers = np.sum(margins > implied_margin)
            pushes_spread = np.sum(margins == implied_margin)
            p_away_cover = float(away_covers) / float(self.n_simulations)
            p_home_cover = float(home_covers) / float(self.n_simulations)
            p_push_spread = float(pushes_spread) / float(self.n_simulations)
        else:
            away_covers = np.sum(margins < implied_margin)
            p_away_cover = float(away_covers) / float(self.n_simulations)
            p_home_cover = 1.0 - p_away_cover
            p_push_spread = 0.0

        # 3. Total
        tot_line = total_line if total_line is not None else round((mu_home + mu_away) * 2.0) / 2.0
        is_integer_total = float(tot_line).is_integer()
        if is_integer_total:
            overs = np.sum(totals > tot_line)
            unders = np.sum(totals < tot_line)
            pushes_total = np.sum(totals == tot_line)
            p_over = float(overs) / float(self.n_simulations)
            p_under = float(unders) / float(self.n_simulations)
            p_push_total = float(pushes_total) / float(self.n_simulations)
        else:
            overs = np.sum(totals > tot_line)
            p_over = float(overs) / float(self.n_simulations)
            p_under = 1.0 - p_over
            p_push_total = 0.0

        return CFBJointMarketProbabilities(
            p_home_win=p_home_win,
            p_away_win=p_away_win,
            spread_home_line=sp_home,
            p_away_cover=p_away_cover,
            p_home_cover=p_home_cover,
            p_push_spread=p_push_spread,
            total_line=tot_line,
            p_over=p_over,
            p_under=p_under,
            p_push_total=p_push_total,
            projected_home_points=mu_home,
            projected_away_points=mu_away,
            projected_margin_home=mu_home - mu_away,
            projected_total=mu_home + mu_away,
            distribution_used=self.distribution_type.value,
        )
