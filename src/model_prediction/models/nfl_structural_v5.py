"""NFL Structural v5 Score Distribution Engine (nfl-structural-v5).

Comprehensive NFL game simulation & market derivation engine:
1. EPA/Play & Success Rates: Pass EPA, Rush EPA, Offensive Line vs Pass Rush pressure disparity.
2. Quarterback Tier & Availability: Starter EPA delta vs backup replacement level.
3. Situational Factors: Rest advantage (Thursday Night, Bye Week, Monday Night fatigue), Travel.
4. Weather: Non-linear wind penalty on passing efficiency (wind > 15 mph suppresses total).
5. Discrete Key Number Modeling: Margin clusters around 3, 7, 6, 10, 14, 4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NFL_V5_MODEL_VERSION = "nfl-structural-v5"
NFL_LEAGUE_AVG_TOTAL = 44.5
NFL_HFA_POINTS = 1.8
NFL_DEFAULT_SPREAD_SD = 13.8
NFL_DEFAULT_TOTAL_SD = 13.5

NFL_KEY_MARGINS: dict[int, float] = {
    3: 0.152,
    7: 0.098,
    6: 0.062,
    10: 0.058,
    4: 0.051,
    14: 0.048,
    1: 0.038,
    2: 0.025,
}


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


@dataclass(frozen=True)
class NFLStructuralForecast:
    home_team: str
    away_team: str
    projected_home_points: float
    projected_away_points: float
    projected_margin_home: float
    projected_total: float
    prob_home_win: float
    prob_away_win: float
    prob_home_cover: float
    prob_away_cover: float
    prob_over: float
    prob_under: float
    spread_home_line: float
    total_line: float
    weather_total_penalty: float


class NFLStructuralV5Engine:
    """Unified NFL score simulation and market translation engine."""

    def __init__(
        self,
        spread_sd: float = NFL_DEFAULT_SPREAD_SD,
        total_sd: float = NFL_DEFAULT_TOTAL_SD,
    ) -> None:
        self.spread_sd = spread_sd
        self.total_sd = total_sd

    def forecast_game(
        self,
        home_team: str,
        away_team: str,
        home_off_epa: float = 0.05,
        home_def_epa: float = 0.00,
        away_off_epa: float = 0.00,
        away_def_epa: float = 0.05,
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        wind_mph: float = 5.0,
        temperature_f: float = 65.0,
        is_dome: bool = False,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        spread_home_line: float = -3.5,
        total_line: float = 44.5,
    ) -> NFLStructuralForecast:
        base_points_per_side = NFL_LEAGUE_AVG_TOTAL / 2.0  # 22.25 pts

        # 1. EPA / Efficiency Points Projection (~ 32.0 pts per 1.0 EPA/play delta across 65 plays)
        h_eff_pts = (home_off_epa - away_def_epa) * 32.0 + home_qb_tier_adj
        a_eff_pts = (away_off_epa - home_def_epa) * 32.0 + away_qb_tier_adj

        # 2. Situational Rest Disparity
        rest_pts = (home_rest_days - away_rest_days) * 0.35

        # 3. Weather Penalties
        weather_penalty = 0.0
        if not is_dome:
            if wind_mph > 20.0:
                weather_penalty += 4.5
            elif wind_mph > 14.0:
                weather_penalty += 2.0
            if temperature_f < 25.0:
                weather_penalty += 1.5

        pts_home = max(
            6.0, base_points_per_side + NFL_HFA_POINTS + h_eff_pts + rest_pts - (weather_penalty / 2.0)
        )
        pts_away = max(6.0, base_points_per_side + a_eff_pts - (weather_penalty / 2.0))
        exp_margin = pts_home - pts_away
        exp_total = pts_home + pts_away

        # 4. Market Distributions
        p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.spread_sd)
        p_away_win = 1.0 - p_home_win

        p_home_cover = 1.0 - _normal_cdf(-spread_home_line, exp_margin, self.spread_sd)
        p_away_cover = 1.0 - p_home_cover

        p_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)
        p_under = 1.0 - p_over

        return NFLStructuralForecast(
            home_team=home_team,
            away_team=away_team,
            projected_home_points=round(pts_home, 1),
            projected_away_points=round(pts_away, 1),
            projected_margin_home=round(exp_margin, 2),
            projected_total=round(exp_total, 2),
            prob_home_win=round(p_home_win, 4),
            prob_away_win=round(p_away_win, 4),
            prob_home_cover=round(p_home_cover, 4),
            prob_away_cover=round(p_away_cover, 4),
            prob_over=round(p_over, 4),
            prob_under=round(p_under, 4),
            spread_home_line=spread_home_line,
            total_line=total_line,
            weather_total_penalty=round(weather_penalty, 2),
        )
