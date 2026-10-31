"""Dedicated NFL Point Spread and Coherent Moneyline Model v6.

Completely independent from the totals model. Focuses strictly on scoring margin:
1. Passing vs Rushing EPA Decomposition (passing efficiency explains ~72% of offensive variance).
2. Pass Protection vs Defensive Pass Rush Pressure Disparity (drive sustainability).
3. Quarterback Tier Adjustment (value over replacement starter).
4. Modern empirical Home Field Advantage (+1.35 pts; 0.0 on neutral sites).
5. Bayesian Market Anchoring (w_mkt = 0.80) to eliminate home bias and out-of-sample noise.
6. Regularized Key Margin Push Kernel (3: 15.1%, 7: 7.7%, 6: 7.0%, 4: 5.1%, 10: 5.0%).
7. Coherent Logistic Moneyline Mapping (s = 13.5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

NFL_SPREAD_V6_VERSION = "nfl-spread-structural-v6"
NFL_EMPIRICAL_MARGIN_SD = 13.80
NFL_MODERN_HFA_POINTS = 1.35
NFL_LOGISTIC_SCALE = 13.5
NFL_DEFAULT_MARKET_WEIGHT = 0.80

# Empirical modern NFL key margin frequencies
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
    """Overhauled NFL Point Spread and Coherent Moneyline prediction engine."""

    def __init__(
        self,
        margin_sd: float = NFL_EMPIRICAL_MARGIN_SD,
        home_field_advantage: float = NFL_MODERN_HFA_POINTS,
        key_number_weight: float = 0.18,
        market_weight: float = NFL_DEFAULT_MARKET_WEIGHT,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = NFL_LOGISTIC_SCALE,
        max_cover_prob: float = 0.65,
        min_cover_prob: float = 0.35,
        version: str = NFL_SPREAD_V6_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.home_field_advantage = home_field_advantage
        self.key_number_weight = key_number_weight
        self.market_weight = market_weight
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
            key_number_weight=float(artifact.get("key_number_weight", 0.18)),
            market_weight=float(artifact.get("market_weight", NFL_DEFAULT_MARKET_WEIGHT)),
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
        home_pass_epa: float | None = None,
        home_rush_epa: float | None = None,
        away_pass_epa: float | None = None,
        away_rush_epa: float | None = None,
        home_def_pass_epa: float | None = None,
        home_def_rush_epa: float | None = None,
        away_def_pass_epa: float | None = None,
        away_def_rush_epa: float | None = None,
        home_pressure_disparity: float = 0.0,
        away_pressure_disparity: float = 0.0,
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        spread_home_line: float = -3.5,
        is_neutral_site: bool = False,
        market_weight: float | None = None,
        base_points_per_side: float = 23.0,
    ) -> NFLSpreadForecast:
        w_mkt = self.market_weight if market_weight is None else market_weight

        # 1. EPA / Efficiency Points Projection:
        # If decomposed pass/rush metrics available, use them with modern empirical weights (0.72 / 0.28).
        # Otherwise, fall back gracefully to total EPA.
        if home_pass_epa is not None and away_pass_epa is not None:
            h_def_p = home_def_pass_epa if home_def_pass_epa is not None else 0.0
            h_def_r = home_def_rush_epa if home_def_rush_epa is not None else 0.0
            a_def_p = away_def_pass_epa if away_def_pass_epa is not None else 0.0
            a_def_r = away_def_rush_epa if away_def_rush_epa is not None else 0.0
            h_rush = home_rush_epa if home_rush_epa is not None else 0.0
            a_rush = away_rush_epa if away_rush_epa is not None else 0.0

            # Offense is weighted 0.65, Defense allowed weighted 0.35
            h_eff_pts = (
                (0.65 * home_pass_epa + 0.35 * a_def_p) * 23.0
                + (0.65 * h_rush + 0.35 * a_def_r) * 9.0
                + home_pressure_disparity * 3.5
                + home_qb_tier_adj
            )
            a_eff_pts = (
                (0.65 * away_pass_epa + 0.35 * h_def_p) * 23.0
                + (0.65 * a_rush + 0.35 * h_def_r) * 9.0
                + away_pressure_disparity * 3.5
                + away_qb_tier_adj
            )
        else:
            h_eff_epa = (home_off_epa) + (away_def_epa)
            a_eff_epa = (away_off_epa) + (home_def_epa)
            h_eff_pts = h_eff_epa * 28.0 + home_qb_tier_adj
            a_eff_pts = a_eff_epa * 28.0 + away_qb_tier_adj

        # 2. Situational Rest Disparity
        rest_diff = min(7, max(-7, home_rest_days - away_rest_days))
        h_rest_adj = rest_diff * 0.20
        a_rest_adj = -rest_diff * 0.20

        # 3. Modern Empirical HFA (+1.35 pts, 0.0 on neutral)
        hfa = 0.0 if is_neutral_site else self.home_field_advantage
        raw_home_pts = max(6.0, base_points_per_side + hfa + h_eff_pts + h_rest_adj)
        raw_away_pts = max(6.0, base_points_per_side + a_eff_pts + a_rest_adj)
        structural_margin = raw_home_pts - raw_away_pts

        # 4. Bayesian Market Anchoring (w_mkt = 0.80):
        # Market spread_home_line (e.g. -3.5) implies market expects home margin of +3.5.
        market_expected_margin = -spread_home_line
        exp_margin = round(w_mkt * market_expected_margin + (1.0 - w_mkt) * structural_margin, 2)

        pts_home = round(base_points_per_side + exp_margin / 2.0, 1)
        pts_away = round(base_points_per_side - exp_margin / 2.0, 1)

        # 5. Regularized Key-Margin Push Distribution:
        # Cutoff: home covers if actual_margin > -spread_home_line
        cutoff = -spread_home_line
        p_raw_home_cover = 1.0 - _normal_cdf(cutoff, exp_margin, self.margin_sd)

        # Smooth discrete key number mixture: add discrete key weight around key numbers
        key_bump = 0.0
        for km, kw in NFL_KEY_MARGIN_WEIGHTS.items():
            if cutoff < km <= cutoff + 1.0:
                # Line sits right below key number (e.g. -2.5 below 3) -> boosts home cover
                key_bump += kw * 0.35
            elif cutoff - 1.0 < km <= cutoff:
                # Line sits right above key number (e.g. -3.5 above 3) -> reduces home cover
                key_bump -= kw * 0.35

        p_home_cover = (1.0 - self.key_number_weight) * p_raw_home_cover + self.key_number_weight * (
            p_raw_home_cover + key_bump
        )
        # Bounded probability: prevent extreme overconfidence on alternate spreads
        p_home_cover = max(self.min_cover_prob, min(self.max_cover_prob, p_home_cover))
        p_away_cover = 1.0 - p_home_cover

        # 6. Coherent Logistic Moneyline Conversion:
        if self.use_logistic_moneyline:
            p_home_win = 1.0 / (1.0 + 10.0 ** (-exp_margin / self.logistic_scale))
        else:
            p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.margin_sd)
        p_home_win = max(0.05, min(0.95, p_home_win))
        p_away_win = 1.0 - p_home_win

        return NFLSpreadForecast(
            home_team=home_team,
            away_team=away_team,
            projected_home_points=pts_home,
            projected_away_points=pts_away,
            projected_margin_home=exp_margin,
            prob_home_win=round(p_home_win, 6),
            prob_away_win=round(p_away_win, 6),
            prob_home_cover=round(p_home_cover, 6),
            prob_away_cover=round(p_away_cover, 6),
            spread_home_line=spread_home_line,
            effective_sd=self.margin_sd,
        )
