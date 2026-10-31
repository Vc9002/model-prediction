"""Dedicated NFL Game Total (Over/Under) Model v6.

Completely independent from the point spread engine. Focuses strictly on combined game scoring:
1. Offensive and Defensive Pace (projected combined plays per game: +0.35 pts/play).
2. Decomposed Passing and Rushing EPA contributions.
3. Dome Scoring Boost (+3.5 pts in climate-controlled venues).
4. Non-Linear Wind Drag (wind > 12 mph suppresses deep passing efficiency).
5. Bayesian Market Anchoring (w_mkt = 0.80) to calibrate with closing market totals.
6. Regularized Key Totals Push Kernel (41, 44, 47, 51).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

NFL_TOTAL_V6_VERSION = "nfl-total-structural-v6"
NFL_EMPIRICAL_TOTAL_SD = 13.50
NFL_LEAGUE_AVG_TOTAL = 45.2
NFL_DEFAULT_MARKET_WEIGHT = 0.80

NFL_KEY_TOTALS: dict[int, float] = {
    41: 0.038,
    44: 0.039,
    47: 0.035,
    51: 0.040,
    40: 0.034,
    43: 0.034,
    37: 0.033,
    33: 0.028,
}


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if math.isnan(x) or math.isnan(mean) or math.isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))
    return max(0.0001, min(0.9999, raw))


@dataclass(frozen=True)
class NFLTotalForecast:
    home_team: str
    away_team: str
    projected_total: float
    prob_over: float
    prob_under: float
    total_line: float
    effective_sd: float
    weather_total_penalty: float
    is_dome: bool


class NFLTotalModelV6:
    """Overhauled NFL Game Total prediction engine."""

    def __init__(
        self,
        total_sd: float = NFL_EMPIRICAL_TOTAL_SD,
        league_avg_total: float = NFL_LEAGUE_AVG_TOTAL,
        market_weight: float = NFL_DEFAULT_MARKET_WEIGHT,
        key_number_weight: float = 0.15,
        max_total_prob: float = 0.65,
        min_total_prob: float = 0.35,
        version: str = NFL_TOTAL_V6_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.league_avg_total = league_avg_total
        self.market_weight = market_weight
        self.key_number_weight = key_number_weight
        self.max_total_prob = max_total_prob
        self.min_total_prob = min_total_prob
        self.version = version
        self.sport = "nfl"
        self.league = "NFL"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NFLTotalModelV6:
        return cls(
            total_sd=float(artifact.get("total_sd", NFL_EMPIRICAL_TOTAL_SD)),
            league_avg_total=float(artifact.get("league_avg_total", NFL_LEAGUE_AVG_TOTAL)),
            market_weight=float(artifact.get("market_weight", NFL_DEFAULT_MARKET_WEIGHT)),
            key_number_weight=float(artifact.get("key_number_weight", 0.15)),
            max_total_prob=float(artifact.get("max_total_prob", 0.65)),
            min_total_prob=float(artifact.get("min_total_prob", 0.35)),
            version=str(artifact.get("model_version", NFL_TOTAL_V6_VERSION)),
        )

    def forecast_total(
        self,
        home_team: str,
        away_team: str,
        home_off_epa: float = 0.00,
        away_off_epa: float = 0.00,
        home_def_epa: float = 0.00,
        away_def_epa: float = 0.00,
        home_pace: float = 62.5,
        away_pace: float = 62.5,
        total_pass_epa: float | None = None,
        total_rush_epa: float | None = None,
        wind_mph: float = 0.0,
        temperature_f: float = 65.0,
        is_dome: bool = False,
        total_line: float = 45.5,
        market_weight: float | None = None,
    ) -> NFLTotalForecast:
        w_mkt = self.market_weight if market_weight is None else market_weight

        # 1. Environmental Adjustments
        weather_penalty = 0.0
        dome_boost = 0.0
        if is_dome:
            dome_boost = 3.5  # empirical dome scoring boost
        else:
            if wind_mph > 20.0:
                weather_penalty += 5.0
            elif wind_mph > 14.0:
                weather_penalty += 2.5
            elif wind_mph > 12.0:
                weather_penalty += 1.0

            if temperature_f < 25.0:
                weather_penalty += 2.0
            elif temperature_f < 32.0:
                weather_penalty += 1.0

        # 2. Pace / Combined Plays per Game adjustment
        combined_pace = (home_pace + away_pace) / 2.0
        pace_adj = (combined_pace - 62.5) * 0.35

        # 3. Efficiency Contributions
        if total_pass_epa is not None and total_rush_epa is not None:
            eff_pts = total_pass_epa * 3.5 + total_rush_epa * 4.5
        else:
            eff_pts = (home_off_epa + away_off_epa + home_def_epa + away_def_epa) * 14.0

        # 4. Structural Total Projection
        structural_total = max(
            28.0,
            self.league_avg_total + dome_boost - weather_penalty + pace_adj + eff_pts,
        )

        # 5. Bayesian Market Anchoring (w_mkt = 0.80):
        exp_total = round(w_mkt * total_line + (1.0 - w_mkt) * structural_total, 2)

        # 6. Key Totals Push Probability:
        # P(Over) = 1 - CDF(total_line, exp_total, total_sd)
        p_raw_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)

        key_bump = 0.0
        for kt, kw in NFL_KEY_TOTALS.items():
            if total_line < kt <= total_line + 1.0:
                key_bump += kw * 0.30
            elif total_line - 1.0 < kt <= total_line:
                key_bump -= kw * 0.30

        p_over = (1.0 - self.key_number_weight) * p_raw_over + self.key_number_weight * (
            p_raw_over + key_bump
        )
        p_over = max(self.min_total_prob, min(self.max_total_prob, p_over))
        p_under = 1.0 - p_over

        return NFLTotalForecast(
            home_team=home_team,
            away_team=away_team,
            projected_total=exp_total,
            prob_over=round(p_over, 6),
            prob_under=round(p_under, 6),
            total_line=total_line,
            effective_sd=self.total_sd,
            weather_total_penalty=weather_penalty,
            is_dome=is_dome,
        )
