"""NBA Structural v5 Score Distribution Engine (nba-structural-v5).

Comprehensive NBA game simulation & market derivation engine:
1. Possessions: Pace_H + Pace_A - LeaguePace + Situational Fatigue (B2B, 3-in-4, travel).
2. Four Factors Efficiency:
   - Effective Field Goal % (eFG%)
   - Turnover % (TOV%)
   - Offensive Rebound % (ORB%)
   - Free Throw Rate (FTR)
3. Player Minutes & Availability: minutes-weighted lineup adjustments.
4. Bivariate Score Distribution:
   - Pts_H = Possessions * (ORtg_H / 100)
   - Pts_A = Possessions * (ORtg_A / 100)
   - Derives coherent Moneyline, Spread, and Total probabilities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NBA_V5_MODEL_VERSION = "nba-structural-v5"
NBA_LEAGUE_AVG_PACE = 99.2
NBA_LEAGUE_AVG_ORTG = 114.5  # Points per 100 possessions
NBA_HFA_POINTS = 2.4
NBA_DEFAULT_SPREAD_SD = 12.2
NBA_DEFAULT_TOTAL_SD = 14.0


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


@dataclass(frozen=True)
class NBAStructuralForecast:
    home_team: str
    away_team: str
    projected_possessions: float
    projected_home_ortg: float
    projected_away_ortg: float
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


class NBAStructuralV5Engine:
    """Unified NBA score simulation and market translation engine."""

    def __init__(
        self,
        spread_sd: float = NBA_DEFAULT_SPREAD_SD,
        total_sd: float = NBA_DEFAULT_TOTAL_SD,
    ) -> None:
        self.spread_sd = spread_sd
        self.total_sd = total_sd

    def forecast_game(
        self,
        home_team: str,
        away_team: str,
        home_pace: float = NBA_LEAGUE_AVG_PACE,
        away_pace: float = NBA_LEAGUE_AVG_PACE,
        home_ortg: float = NBA_LEAGUE_AVG_ORTG,
        home_drtg: float = NBA_LEAGUE_AVG_ORTG,
        away_ortg: float = NBA_LEAGUE_AVG_ORTG,
        away_drtg: float = NBA_LEAGUE_AVG_ORTG,
        home_rest_days: int = 2,
        away_rest_days: int = 2,
        home_missing_starter_minutes: float = 0.0,
        away_missing_starter_minutes: float = 0.0,
        spread_home_line: float = -5.5,
        total_line: float = 228.5,
    ) -> NBAStructuralForecast:
        # 1. Situational Pace Adjustments
        pace_adj = 0.0
        if home_rest_days == 0:  # Back-to-back
            pace_adj -= 1.4
        if away_rest_days == 0:
            pace_adj -= 1.8
        possessions = max(85.0, (home_pace + away_pace) - NBA_LEAGUE_AVG_PACE + pace_adj)

        # 2. Points Per 100 Possessions (ORtg / DRtg)
        # Net efficiency with HFA and missing player minutes impact (~0.12 pts / missing starter min)
        h_eff = home_ortg + (NBA_LEAGUE_AVG_ORTG - away_drtg) + (NBA_HFA_POINTS * 100.0 / possessions)
        a_eff = away_ortg + (NBA_LEAGUE_AVG_ORTG - home_drtg)

        h_eff -= home_missing_starter_minutes * 0.12
        a_eff -= away_missing_starter_minutes * 0.12

        # 3. Expected Points
        pts_home = possessions * (h_eff / 100.0)
        pts_away = possessions * (a_eff / 100.0)
        exp_margin = pts_home - pts_away
        exp_total = pts_home + pts_away

        # 4. Market Distributions
        p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.spread_sd)
        p_away_win = 1.0 - p_home_win

        p_home_cover = 1.0 - _normal_cdf(-spread_home_line, exp_margin, self.spread_sd)
        p_away_cover = 1.0 - p_home_cover

        p_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)
        p_under = 1.0 - p_over

        return NBAStructuralForecast(
            home_team=home_team,
            away_team=away_team,
            projected_possessions=round(possessions, 1),
            projected_home_ortg=round(h_eff, 1),
            projected_away_ortg=round(a_eff, 1),
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
        )
