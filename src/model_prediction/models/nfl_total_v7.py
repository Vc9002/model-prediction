"""Dedicated NFL Game Total (Over/Under) Model v7 (nfl-total-structural-v7).

State-of-the-art quantitative NFL game scoring engine:
1. Two-Sided Team Scoring & Market Delta Anchoring: preserves closing total line as true baseline.
2. Explosive Play Rate Edge: passes >= 20 yds + rushes >= 12 yds drive fast scores and avoid clock bleed.
3. Red Zone Touchdown Conversion Rate: converting red zone trips into 7 points (TD) vs 3 points (FG).
4. Decomposed Passing & Rushing EPA contributions (passing weighted 3x over rushing).
5. Non-Linear Stadium Weather Suppression (sustained wind >= 14/18 mph, sub-freezing temperatures).
6. Indoor Climate Dome Scoring Boost (+0.80 pts).
7. Calibrated Continuous Gaussian CDF without discrete direction inversion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

NFL_TOTAL_V7_VERSION = "nfl-total-structural-v7"
NFL_EMPIRICAL_TOTAL_SD = 13.80
NFL_LEAGUE_AVG_TOTAL = 45.5
NFL_DEFAULT_MARKET_WEIGHT = 0.60


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


class NFLTotalModelV7:
    """Overhauled NFL Game Total prediction engine v7."""

    def __init__(
        self,
        total_sd: float = NFL_EMPIRICAL_TOTAL_SD,
        league_avg_total: float = NFL_LEAGUE_AVG_TOTAL,
        market_weight: float = NFL_DEFAULT_MARKET_WEIGHT,
        max_total_prob: float = 0.65,
        min_total_prob: float = 0.35,
        version: str = NFL_TOTAL_V7_VERSION,
        explosive_weight: float = 10.0,
        red_zone_weight: float = 3.0,
        pass_weight: float = 1.5,
        rush_weight: float = 0.5,
        pace_weight: float = 0.15,
        dome_boost: float = 0.80,
        bias: float = 0.80,
    ) -> None:
        self.total_sd = total_sd
        self.league_avg_total = league_avg_total
        self.market_weight = market_weight
        self.max_total_prob = max_total_prob
        self.min_total_prob = min_total_prob
        self.version = version
        self.explosive_weight = explosive_weight
        self.red_zone_weight = red_zone_weight
        self.pass_weight = pass_weight
        self.rush_weight = rush_weight
        self.pace_weight = pace_weight
        self.dome_boost = dome_boost
        self.bias = bias
        self.sport = "nfl"
        self.league = "NFL"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NFLTotalModelV7:
        return cls(
            total_sd=float(artifact.get("total_sd", NFL_EMPIRICAL_TOTAL_SD)),
            league_avg_total=float(artifact.get("league_avg_total", NFL_LEAGUE_AVG_TOTAL)),
            market_weight=float(artifact.get("market_weight", NFL_DEFAULT_MARKET_WEIGHT)),
            max_total_prob=float(artifact.get("max_total_prob", 0.65)),
            min_total_prob=float(artifact.get("min_total_prob", 0.35)),
            version=str(artifact.get("model_version", NFL_TOTAL_V7_VERSION)),
            explosive_weight=float(artifact.get("explosive_weight", 10.0)),
            red_zone_weight=float(artifact.get("red_zone_weight", 3.0)),
            pass_weight=float(artifact.get("pass_weight", 1.5)),
            rush_weight=float(artifact.get("rush_weight", 0.5)),
            pace_weight=float(artifact.get("pace_weight", 0.15)),
            dome_boost=float(artifact.get("dome_boost", 0.80)),
            bias=float(artifact.get("bias", 0.80)),
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
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        is_neutral_site: bool = False,
        home_explosive_rate: float = 0.08,
        away_explosive_rate: float = 0.08,
        home_def_explosive_rate: float = 0.08,
        away_def_explosive_rate: float = 0.08,
        home_rz_td_rate: float = 0.60,
        away_rz_td_rate: float = 0.60,
        home_def_rz_td_rate: float = 0.60,
        away_def_rz_td_rate: float = 0.60,
    ) -> NFLTotalForecast:
        # 1. Environmental Adjustments
        weather_penalty = 0.0
        dome_boost = 0.0
        if is_dome:
            dome_boost = self.dome_boost
        else:
            if wind_mph >= 18.0:
                weather_penalty += 2.5
            elif wind_mph >= 14.0:
                weather_penalty += 1.2
            elif wind_mph >= 12.0:
                weather_penalty += 0.5

            if temperature_f < 25.0:
                weather_penalty += 1.2
            elif temperature_f < 32.0:
                weather_penalty += 0.5

        # 2. Matchup Feature Edges
        if total_pass_epa is not None:
            pass_edge = total_pass_epa
        else:
            pass_edge = (home_off_epa + away_def_epa) + (away_off_epa + home_def_epa)

        if total_rush_epa is not None:
            rush_edge = total_rush_epa
        else:
            rush_edge = 0.0

        # Red Zone TD conversion edge vs 0.60 baseline
        rz_edge = ((home_rz_td_rate + away_def_rz_td_rate) / 2.0 - 0.60) + (
            (away_rz_td_rate + home_def_rz_td_rate) / 2.0 - 0.60
        )

        # Explosive play rate edge vs 0.08 baseline
        exp_edge = ((home_explosive_rate + away_def_explosive_rate) / 2.0 - 0.08) + (
            (away_explosive_rate + home_def_explosive_rate) / 2.0 - 0.08
        )

        # Pace edge vs 62.5 plays baseline
        pace_edge = ((home_pace + away_pace) / 2.0 - 62.5) * self.pace_weight

        # QB impact on scoring
        qb_pts = (home_qb_tier_adj + away_qb_tier_adj) * 0.4

        # 3. Market Delta Total Projection
        tot_delta = (
            self.pass_weight * pass_edge
            + self.rush_weight * rush_edge
            + self.red_zone_weight * rz_edge
            + self.explosive_weight * exp_edge
            + dome_boost
            - weather_penalty
            + pace_edge
            + qb_pts
            + self.bias
        )

        exp_total = round(total_line + tot_delta, 2)

        # 4. Continuous Calibrated Gaussian CDF:
        p_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)
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
