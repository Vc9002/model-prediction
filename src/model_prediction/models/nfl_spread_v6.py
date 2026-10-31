"""Dedicated NFL Point Spread and Coherent Moneyline Model v6.

Completely independent from the totals model. Focuses strictly on scoring margin,
modern NFL home field advantage (+1.40 pts), EPA/play differentials, QB tier adjustments,
situational rest advantage, regularized key margin mixture, and calibrated logistic moneyline conversion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

NFL_SPREAD_V6_VERSION = "nfl-spread-structural-v6"
NFL_EMPIRICAL_MARGIN_SD = 13.98
NFL_MODERN_HFA_POINTS = 1.40
NFL_LOGISTIC_SCALE = 13.5

# Top discrete key margins in modern NFL
NFL_KEY_MARGIN_WEIGHTS: dict[int, float] = {
    3: 0.151,
    7: 0.077,
    6: 0.070,
    10: 0.050,
    4: 0.051,
    14: 0.046,
    1: 0.048,
    2: 0.049,
}


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if math.isnan(x) or math.isnan(mean) or math.isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))
    return max(0.0001, min(0.9999, raw))


@dataclass(frozen=True)
class NFLSpreadForecast:
    home_team: str
    away_team: str
    projected_home_points: float
    projected_away_points: float
    projected_margin_home: float
    prob_home_win: float
    prob_away_win: float
    prob_home_cover: float
    prob_away_cover: float
    spread_home_line: float
    effective_sd: float


class NFLSpreadModelV6:
    """Independent NFL Point Spread and Coherent Moneyline prediction engine."""

    def __init__(
        self,
        margin_sd: float = NFL_EMPIRICAL_MARGIN_SD,
        home_field_advantage: float = NFL_MODERN_HFA_POINTS,
        key_number_weight: float = 0.15,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = NFL_LOGISTIC_SCALE,
        max_cover_prob: float = 0.65,
        min_cover_prob: float = 0.35,
        version: str = NFL_SPREAD_V6_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.home_field_advantage = home_field_advantage
        self.key_number_weight = key_number_weight
        self.use_logistic_moneyline = use_logistic_moneyline
        self.logistic_scale = logistic_scale
        self.max_cover_prob = max_cover_prob
        self.min_cover_prob = min_cover_prob
        self.version = version
        self.sport = "nfl"
        self.league = "NFL"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NFLSpreadModelV6:
        return cls(
            margin_sd=float(artifact.get("margin_sd", NFL_EMPIRICAL_MARGIN_SD)),
            home_field_advantage=float(artifact.get("home_field_advantage_points", NFL_MODERN_HFA_POINTS)),
            key_number_weight=float(artifact.get("key_number_weight", 0.15)),
            use_logistic_moneyline=bool(artifact.get("use_logistic_moneyline", True)),
            logistic_scale=float(artifact.get("logistic_scale", NFL_LOGISTIC_SCALE)),
            max_cover_prob=float(artifact.get("max_cover_prob", 0.65)),
            min_cover_prob=float(artifact.get("min_cover_prob", 0.35)),
            version=str(artifact.get("model_version", NFL_SPREAD_V6_VERSION)),
        )

    def forecast_spread(
        self,
        home_team: str,
        away_team: str,
        home_off_epa: float = 0.00,
        home_def_epa: float = 0.00,
        away_off_epa: float = 0.00,
        away_def_epa: float = 0.00,
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        spread_home_line: float = -3.5,
        is_neutral_site: bool = False,
        league_avg_epa_play: float = 0.00,
        base_points_per_side: float = 23.0,
    ) -> NFLSpreadForecast:
        # 1. EPA / Efficiency Points Projection (~32.0 pts per 1.0 EPA/play delta across 65 plays)
        h_eff_epa = (home_off_epa - league_avg_epa_play) + (away_def_epa - league_avg_epa_play)
        a_eff_epa = (away_off_epa - league_avg_epa_play) + (home_def_epa - league_avg_epa_play)
        h_eff_pts = h_eff_epa * 32.0 + home_qb_tier_adj
        a_eff_pts = a_eff_epa * 32.0 + away_qb_tier_adj

        # 2. Rest Advantage
        h_rest_adj = (home_rest_days - away_rest_days) * 0.175
        a_rest_adj = (away_rest_days - home_rest_days) * 0.175

        hfa = 0.0 if is_neutral_site else self.home_field_advantage
        pts_home = max(6.0, base_points_per_side + hfa + h_eff_pts + h_rest_adj)
        pts_away = max(6.0, base_points_per_side + a_eff_pts + a_rest_adj)
        exp_margin = round(pts_home - pts_away, 2)

        # 3. Regularized Key-Margin CDF:
        # Cutoff: home covers if actual_margin > -spread_home_line
        # (e.g. spread_home_line = -3.5 -> home covers if actual_margin >= 4)
        cutoff = -spread_home_line
        p_raw_home_cover = 1.0 - _normal_cdf(cutoff, exp_margin, self.margin_sd)

        # Smooth discrete key number mixture: add discrete key weight without tail distortion
        key_bump = 0.0
        for km, kw in NFL_KEY_MARGIN_WEIGHTS.items():
            if cutoff < km <= cutoff + 1.0:
                # Line sits right below key number (e.g. -2.5 below 3) -> boosts home cover
                key_bump += kw * 0.30
            elif cutoff - 1.0 < km <= cutoff:
                # Line sits right above key number (e.g. -3.5 above 3) -> reduces home cover
                key_bump -= kw * 0.30

        p_home_cover = (1.0 - self.key_number_weight) * p_raw_home_cover + self.key_number_weight * (
            p_raw_home_cover + key_bump
        )
        # Bounded probability: prevent extreme overconfidence on alternate spreads
        p_home_cover = max(self.min_cover_prob, min(self.max_cover_prob, p_home_cover))
        p_away_cover = 1.0 - p_home_cover

        # 4. Coherent Logistic Moneyline Conversion:
        if self.use_logistic_moneyline:
            p_home_win = 1.0 / (1.0 + 10.0 ** (-exp_margin / self.logistic_scale))
        else:
            p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.margin_sd)
        p_home_win = max(0.05, min(0.95, p_home_win))
        p_away_win = 1.0 - p_home_win

        return NFLSpreadForecast(
            home_team=home_team,
            away_team=away_team,
            projected_home_points=round(pts_home, 1),
            projected_away_points=round(pts_away, 1),
            projected_margin_home=exp_margin,
            prob_home_win=round(p_home_win, 6),
            prob_away_win=round(p_away_win, 6),
            prob_home_cover=round(p_home_cover, 6),
            prob_away_cover=round(p_away_cover, 6),
            spread_home_line=spread_home_line,
            effective_sd=self.margin_sd,
        )
