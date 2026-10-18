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

import numpy as np

NFL_V5_MODEL_VERSION = "nfl-structural-v5"
NFL_LEAGUE_AVG_TOTAL = 44.5
NFL_LEAGUE_AVG_EPA_PLAY = 0.04
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

NFL_KEY_TOTALS: dict[int, float] = {
    51: 0.040,
    44: 0.038,
    41: 0.035,
    40: 0.035,
    43: 0.035,
    37: 0.034,
    47: 0.033,
    33: 0.028,
    48: 0.027,
}


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


def _tilt_distribution(
    raw_probs: dict[int, float], target_mean: float, max_iter: int = 10, tol: float = 1e-5
) -> dict[int, float]:
    """Exponentially tilt discrete distribution p'[x] = p[x] * exp(theta * (x - target_mean)) / Z
    so that E_{p'}[X] matches target_mean exactly, eliminating discrete key-number mean shrinkage
    while preserving exact key-number mass spikes.
    """
    total = sum(raw_probs.values())
    if total <= 0:
        return {round(target_mean): 1.0}
    p = {k: v / total for k, v in raw_probs.items()}
    items = sorted(p.items())
    xs = np.array([k for k, _ in items], dtype=float)
    probs = np.array([v for _, v in items], dtype=float)

    current_mean = float(np.sum(xs * probs))
    if abs(current_mean - target_mean) < tol:
        return dict(zip([int(x) for x in xs], probs.tolist()))

    theta = 0.0
    for _ in range(max_iter):
        shifted_x = xs - target_mean
        weights = probs * np.exp(np.clip(theta * shifted_x, -50.0, 50.0))
        z = np.sum(weights)
        if z <= 0 or not np.isfinite(z):
            break
        p_tilt = weights / z
        m = float(np.sum(shifted_x * p_tilt))  # We want m == 0 (i.e. mean == target_mean)
        if abs(m) < tol:
            return dict(zip([int(x) for x in xs], p_tilt.tolist()))
        var = float(np.sum((shifted_x - m) ** 2 * p_tilt))
        if var < 1e-9:
            break
        step = m / var
        step = max(-1.0, min(1.0, step))
        theta -= step
        if abs(step) < 1e-7:
            break

    shifted_x = xs - target_mean
    weights = probs * np.exp(np.clip(theta * shifted_x, -50.0, 50.0))
    z = np.sum(weights)
    if z > 0 and np.isfinite(z):
        p_tilt = weights / z
        return dict(zip([int(x) for x in xs], p_tilt.tolist()))
    return p


def discrete_spread_distribution(exp_margin: float, sd: float = NFL_DEFAULT_SPREAD_SD) -> dict[int, float]:
    """Compute discrete margin mass distribution over integer margins [-55, 55] with mean conservation."""
    raw_p: dict[int, float] = {}
    for m in range(-55, 56):
        if m == 0:
            raw_p[m] = 0.003
            continue
        p_norm = _normal_cdf(m + 0.5, exp_margin, sd) - _normal_cdf(m - 0.5, exp_margin, sd)
        abs_m = abs(m)
        if abs_m in NFL_KEY_MARGINS:
            emp = NFL_KEY_MARGINS[abs_m]
            norm_zero = _normal_cdf(abs_m + 0.5, 0.0, sd) - _normal_cdf(abs_m - 0.5, 0.0, sd)
            boost = emp / max(0.01, norm_zero)
            p = 0.65 * p_norm + 0.35 * (p_norm * boost)
        else:
            p = p_norm * 0.90
        raw_p[m] = max(0.0, p)
    return _tilt_distribution(raw_p, exp_margin)


def discrete_spread_prob(
    spread_home_line: float,
    exp_margin: float,
    sd: float = NFL_DEFAULT_SPREAD_SD,
) -> tuple[float, float, float]:
    """Calculate (p_home_cover, p_away_cover, p_push) under discrete NFL key margins."""
    cutoff = -spread_home_line
    probs = discrete_spread_distribution(exp_margin, sd)
    p_home_cover = sum(p for m, p in probs.items() if m > cutoff)
    p_away_cover = sum(p for m, p in probs.items() if m < cutoff)
    p_push = sum(p for m, p in probs.items() if m == cutoff)
    return p_home_cover, p_away_cover, p_push


def discrete_total_distribution(exp_total: float, sd: float = NFL_DEFAULT_TOTAL_SD) -> dict[int, float]:
    """Compute discrete total mass distribution over integer totals [10, 90] with mean conservation."""
    raw_p: dict[int, float] = {}
    for t in range(10, 91):
        p_norm = _normal_cdf(t + 0.5, exp_total, sd) - _normal_cdf(t - 0.5, exp_total, sd)
        if t in NFL_KEY_TOTALS:
            emp = NFL_KEY_TOTALS[t]
            norm_zero = _normal_cdf(t + 0.5, NFL_LEAGUE_AVG_TOTAL, sd) - _normal_cdf(
                t - 0.5, NFL_LEAGUE_AVG_TOTAL, sd
            )
            boost = emp / max(0.01, norm_zero)
            p = 0.70 * p_norm + 0.30 * (p_norm * boost)
        else:
            p = p_norm * 0.94
        raw_p[t] = max(0.0, p)
    return _tilt_distribution(raw_p, exp_total)


def discrete_total_prob(
    total_line: float,
    exp_total: float,
    sd: float = NFL_DEFAULT_TOTAL_SD,
) -> tuple[float, float, float]:
    """Calculate (p_over, p_under, p_push) under discrete NFL key totals."""
    probs = discrete_total_distribution(exp_total, sd)
    p_over = sum(p for t, p in probs.items() if t > total_line)
    p_under = sum(p for t, p in probs.items() if t < total_line)
    p_push = sum(p for t, p in probs.items() if t == total_line)
    return p_over, p_under, p_push


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
    short_week_penalty: float = 0.0
    prob_push_spread: float = 0.0
    prob_push_total: float = 0.0


class NFLStructuralV5Engine:
    """Unified NFL score simulation and market translation engine."""

    def __init__(
        self,
        spread_sd: float = NFL_DEFAULT_SPREAD_SD,
        total_sd: float = NFL_DEFAULT_TOTAL_SD,
        short_week_drag: float = 1.0,
    ) -> None:
        self.spread_sd = spread_sd
        self.total_sd = total_sd
        self.short_week_drag = short_week_drag

    def forecast_game(
        self,
        home_team: str,
        away_team: str,
        home_off_epa: float = 0.04,
        home_def_epa: float = 0.04,
        away_off_epa: float = 0.04,
        away_def_epa: float = 0.04,
        home_qb_tier_adj: float = 0.0,
        away_qb_tier_adj: float = 0.0,
        wind_mph: float = 5.0,
        temperature_f: float = 65.0,
        is_dome: bool = False,
        home_rest_days: int = 7,
        away_rest_days: int = 7,
        spread_home_line: float = -3.5,
        total_line: float = 44.5,
        is_neutral_site: bool = False,
    ) -> NFLStructuralForecast:
        base_points_per_side = NFL_LEAGUE_AVG_TOTAL / 2.0  # 22.25 pts

        # 1. EPA / Efficiency Points Projection (~ 32.0 pts per 1.0 EPA/play delta across 65 plays)
        # Note: def_epa is opponent EPA allowed (higher = more porous defense).
        h_eff_epa = (home_off_epa - NFL_LEAGUE_AVG_EPA_PLAY) + (away_def_epa - NFL_LEAGUE_AVG_EPA_PLAY)
        a_eff_epa = (away_off_epa - NFL_LEAGUE_AVG_EPA_PLAY) + (home_def_epa - NFL_LEAGUE_AVG_EPA_PLAY)
        h_eff_pts = h_eff_epa * 32.0 + home_qb_tier_adj
        a_eff_pts = a_eff_epa * 32.0 + away_qb_tier_adj

        # 2. Situational Rest Disparity & Short-Week Factor (TNF drag calibrated to empirical ~1.0 pt)
        h_rest_adj = (home_rest_days - away_rest_days) * 0.175
        a_rest_adj = (away_rest_days - home_rest_days) * 0.175
        short_week_drag = 0.0
        if home_rest_days <= 5 and away_rest_days <= 5:
            short_week_drag = (
                self.short_week_drag
            )  # Full short-week turnaround (e.g. Thursday Night Football)
        elif home_rest_days <= 5 or away_rest_days <= 5:
            short_week_drag = self.short_week_drag / 2.0  # Asymmetric short-week turnaround

        # 3. Weather Penalties
        weather_penalty = 0.0
        if not is_dome:
            if wind_mph > 20.0:
                weather_penalty += 4.5
            elif wind_mph > 14.0:
                weather_penalty += 2.0
            if temperature_f < 25.0:
                weather_penalty += 1.5

        hfa = 0.0 if is_neutral_site else NFL_HFA_POINTS
        pts_home = max(
            6.0,
            base_points_per_side
            + hfa
            + h_eff_pts
            + h_rest_adj
            - (weather_penalty / 2.0)
            - (short_week_drag / 2.0),
        )
        pts_away = max(
            6.0,
            base_points_per_side + a_eff_pts + a_rest_adj - (weather_penalty / 2.0) - (short_week_drag / 2.0),
        )
        exp_margin = round(pts_home - pts_away, 2)
        exp_total = round(pts_home + pts_away, 2)

        # 4. Market Distributions with Discrete Key Margin & Total Modeling
        p_home_win = 1.0 - _normal_cdf(0.0, exp_margin, self.spread_sd)
        p_away_win = 1.0 - p_home_win

        p_home_cover, p_away_cover, p_push_spread = discrete_spread_prob(
            spread_home_line=spread_home_line,
            exp_margin=exp_margin,
            sd=self.spread_sd,
        )
        tot_spread = p_home_cover + p_away_cover
        if tot_spread > 0:
            p_home_cover_norm = p_home_cover / tot_spread
            p_away_cover_norm = p_away_cover / tot_spread
        else:
            p_home_cover_norm = p_home_cover
            p_away_cover_norm = p_away_cover

        p_over, p_under, p_push_total = discrete_total_prob(
            total_line=total_line,
            exp_total=exp_total,
            sd=self.total_sd,
        )
        tot_total = p_over + p_under
        if tot_total > 0:
            p_over_norm = p_over / tot_total
            p_under_norm = p_under / tot_total
        else:
            p_over_norm = p_over
            p_under_norm = p_under

        return NFLStructuralForecast(
            home_team=home_team,
            away_team=away_team,
            projected_home_points=round(pts_home, 1),
            projected_away_points=round(pts_away, 1),
            projected_margin_home=round(exp_margin, 2),
            projected_total=round(exp_total, 2),
            prob_home_win=round(p_home_win, 6),
            prob_away_win=round(p_away_win, 6),
            prob_home_cover=round(p_home_cover_norm, 6),
            prob_away_cover=round(p_away_cover_norm, 6),
            prob_over=round(p_over_norm, 6),
            prob_under=round(p_under_norm, 6),
            spread_home_line=spread_home_line,
            total_line=total_line,
            weather_total_penalty=round(weather_penalty, 2),
            short_week_penalty=round(short_week_drag, 2),
            prob_push_spread=round(p_push_spread, 6),
            prob_push_total=round(p_push_total, 6),
        )
