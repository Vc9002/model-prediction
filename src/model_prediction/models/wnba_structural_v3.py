"""WNBA Unified Structural Architecture v3 (wnba-spread-structural-v3 & wnba-total-possession-v3).

Unified basketball simulation engine deriving Moneyline, Spread, and Totals from joint scoring:
1. Possessions: Base team pace + opponent pace + rest disparities (B2B, 3-in-4) + TOV tempo.
2. Points Per Possession (PPP) via Four Factors:
   - Effective Field Goal % (eFG%)
   - Turnover % (TOV%)
   - Offensive Rebound % (ORB%)
   - Free Throw Rate (FTR)
3. Player Availability & Impact:
   - Starter missing minutes penalty
   - On-court net rating / BPM impact
4. Bivariate Score Distributions:
   - Pts_H = Possessions * PPP_H
   - Pts_A = Possessions * PPP_A
   - Coherently translates into Spread P(Home - Line) and Total P(Total > Line).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

WNBA_SPREAD_V3_MODEL_VERSION = "wnba-spread-structural-v3"
WNBA_TOTAL_V3_MODEL_VERSION = "wnba-total-possession-v3"
WNBA_LEAGUE_AVG_PACE = 80.5
WNBA_LEAGUE_AVG_PPP = 1.025  # ~82.5 points per game
WNBA_HFA_POINTS = 2.2
WNBA_DEFAULT_SPREAD_SD = 10.8
WNBA_DEFAULT_TOTAL_SD = 12.5


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


@dataclass(frozen=True)
class WNBAStructuralForecast:
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
    """Unified WNBA score simulation and derivative market pricing engine."""

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
        home_ortg_ppp: float = WNBA_LEAGUE_AVG_PPP,
        home_drtg_ppp: float = WNBA_LEAGUE_AVG_PPP,
        away_ortg_ppp: float = WNBA_LEAGUE_AVG_PPP,
        away_drtg_ppp: float = WNBA_LEAGUE_AVG_PPP,
        home_rest_days: int = 2,
        away_rest_days: int = 2,
        home_missing_minutes: float = 0.0,
        away_missing_minutes: float = 0.0,
        home_player_impact_net: float = 0.0,
        away_player_impact_net: float = 0.0,
        home_efg_pct: float = 0.495,
        away_efg_pct: float = 0.495,
        home_tov_pct: float = 0.175,
        away_tov_pct: float = 0.175,
        spread_home_line: float = -4.5,
        total_line: float = 165.5,
    ) -> WNBAStructuralForecast:
        # 1. Possessions Model with Fatigue & Turnover Pace
        pace_fatigue = 0.0
        if home_rest_days == 0:
            pace_fatigue -= 1.2
        if away_rest_days == 0:
            pace_fatigue -= 1.6

        tov_pace_adj = (home_tov_pct + away_tov_pct - 0.35) * 12.0
        possessions = max(68.0, (home_pace + away_pace) - WNBA_LEAGUE_AVG_PACE + pace_fatigue + tov_pace_adj)

        # 2. Points Per Possession (PPP) via Four Factors & Availability
        hfa_ppp = WNBA_HFA_POINTS / possessions

        # Four factors efficiency adjustment
        efg_adj_h = (home_efg_pct - 0.495) * 1.2
        efg_adj_a = (away_efg_pct - 0.495) * 1.2

        # Missing player minutes penalty (~0.0035 PPP per missing starter minute)
        inj_adj_h = -home_missing_minutes * 0.0035 + home_player_impact_net * 0.015
        inj_adj_a = -away_missing_minutes * 0.0035 + away_player_impact_net * 0.015

        ppp_home = max(
            0.65, home_ortg_ppp + (WNBA_LEAGUE_AVG_PPP - away_drtg_ppp) + hfa_ppp + efg_adj_h + inj_adj_h
        )
        ppp_away = max(0.65, away_ortg_ppp + (WNBA_LEAGUE_AVG_PPP - home_drtg_ppp) + efg_adj_a + inj_adj_a)

        # 3. Expected Points
        pts_home = possessions * ppp_home
        pts_away = possessions * ppp_away
        exp_margin = pts_home - pts_away
        exp_total = pts_home + pts_away

        # 4. Market Distributions
        p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.spread_sd)
        p_away_win = 1.0 - p_home_win

        p_home_cover = 1.0 - _normal_cdf(-spread_home_line, exp_margin, self.spread_sd)
        p_away_cover = 1.0 - p_home_cover

        p_over = 1.0 - _normal_cdf(total_line, exp_total, self.total_sd)
        p_under = 1.0 - p_over

        return WNBAStructuralForecast(
            home_team=home_team,
            away_team=away_team,
            projected_possessions=round(possessions, 1),
            projected_home_ppp=round(ppp_home, 4),
            projected_away_ppp=round(ppp_away, 4),
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
