"""WNBA Unified Structural Game Engine (wnba-spread-structural-v3 & wnba-total-possession-v3).

Unified possession-efficiency scoring model:
1. Possessions: pace_home + pace_away - league_pace + situational adjustments (B2B, rest).
2. Points Per Possession (PPP): team offensive rating vs opponent defensive rating + player minutes impact.
3. Expected Points:
   - Pts_H = Possessions * PPP_H
   - Pts_A = Possessions * PPP_A
4. Derives consistent Moneyline, Spread, and Total markets from the bivariate score distribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

WNBA_SPREAD_V3_MODEL_VERSION = "wnba-spread-structural-v3"
WNBA_TOTAL_V3_MODEL_VERSION = "wnba-total-possession-v3"
WNBA_LEAGUE_AVG_PACE = 80.5
WNBA_LEAGUE_AVG_PPP = 1.025
WNBA_HFA_PPP_BOOST = 0.035  # ~ 2.8 points HFA per game
WNBA_DEFAULT_SPREAD_SD = 11.8
WNBA_DEFAULT_TOTAL_SD = 12.5


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


@dataclass(frozen=True)
class WNBAStructuralForecast:
    """Standard forecast output from the unified WNBA structural engine."""

    home_team: str
    away_team: str
    projected_possessions: float
    projected_home_ppp: float
    projected_away_ppp: float
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


class WNBAStructuralV3Engine:
    """Unified WNBA game simulation and market translation engine."""

    def __init__(
        self,
        spread_sd: float = WNBA_DEFAULT_SPREAD_SD,
        total_sd: float = WNBA_DEFAULT_TOTAL_SD,
    ) -> None:
        self.spread_sd = spread_sd
        self.total_sd = total_sd

    def forecast_game(
        self,
        home_team: str,
        away_team: str,
        home_pace: float = WNBA_LEAGUE_AVG_PACE,
        away_pace: float = WNBA_LEAGUE_AVG_PACE,
        home_ortg: float = WNBA_LEAGUE_AVG_PPP,
        home_drtg: float = WNBA_LEAGUE_AVG_PPP,
        away_ortg: float = WNBA_LEAGUE_AVG_PPP,
        away_drtg: float = WNBA_LEAGUE_AVG_PPP,
        home_rest_days: int = 2,
        away_rest_days: int = 2,
        spread_home_line: float = -4.5,
        total_line: float = 164.5,
    ) -> WNBAStructuralForecast:
        """Simulate expected game outcome and derive coherent market distributions."""
        # 1. Expected Possessions
        pace_adj = 0.0
        if home_rest_days == 0:
            pace_adj -= 1.2
        if away_rest_days == 0:
            pace_adj -= 1.2
        possessions = max(65.0, (home_pace + away_pace) - WNBA_LEAGUE_AVG_PACE + pace_adj)

        # 2. Points Per Possession (PPP)
        ppp_home = home_ortg + (WNBA_LEAGUE_AVG_PPP - away_drtg) + WNBA_HFA_PPP_BOOST
        ppp_away = away_ortg + (WNBA_LEAGUE_AVG_PPP - home_drtg)

        # 3. Expected Points
        pts_home = possessions * ppp_home
        pts_away = possessions * ppp_away
        exp_margin = pts_home - pts_away
        exp_total = pts_home + pts_away

        # 4. Market Distributions
        # Moneyline: P(Margin > 0)
        p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.spread_sd)
        p_away_win = 1.0 - p_home_win

        # Spread: Home covers if (Margin - MarketHomeImpliedMargin) > 0 where MarketHomeImplied = -spread_home_line
        # i.e. Margin > -spread_home_line
        p_home_cover = 1.0 - _normal_cdf(-spread_home_line, exp_margin, self.spread_sd)
        p_away_cover = 1.0 - p_home_cover

        # Total: Over if ActualTotal > total_line
        p_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)
        p_under = 1.0 - p_over

        return WNBAStructuralForecast(
            home_team=home_team,
            away_team=away_team,
            projected_possessions=round(possessions, 1),
            projected_home_ppp=round(ppp_home, 3),
            projected_away_ppp=round(ppp_away, 3),
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
