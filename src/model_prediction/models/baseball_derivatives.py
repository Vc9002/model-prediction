"""Runline spread and total runs pricing for Asian Baseball (KBO & NPB)."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


@dataclass(frozen=True)
class BaseballDerivativePricing:
    home_win_decisive: float
    away_win_decisive: float
    tie_prob: float
    runline_home_minus_1pt5: float
    runline_away_plus_1pt5: float
    runline_away_minus_1pt5: float
    runline_home_plus_1pt5: float
    expected_total_runs: float
    totals_prob: dict[float, dict[str, float]]


def price_international_baseball_derivatives(
    p_home_decisive: float,
    tie_prob: float,
    league: str = "kbo",
    lines: tuple[float, ...] = (7.5, 8.5, 9.5, 10.5),
    expected_total: float | None = None,
) -> BaseballDerivativePricing:
    """Calculate runline spread and total runs distributions."""
    is_kbo = str(league).lower() == "kbo"
    baseline_total = float(expected_total) if expected_total is not None else (9.8 if is_kbo else 7.2)
    sigma_total = 3.8 if is_kbo else 3.2
    sigma_margin = 3.4

    p_clamped = max(0.01, min(0.99, p_home_decisive))
    y = 2.0 * p_clamped - 1.0
    a = 0.147
    sgn = 1.0 if y >= 0 else -1.0
    term1 = 2.0 / (math.pi * a) + math.log(max(1e-12, 1.0 - y * y)) / 2.0
    term2 = math.log(max(1e-12, 1.0 - y * y)) / a
    inv_erf = sgn * math.sqrt(math.sqrt(max(0.0, term1**2 - term2)) - term1)
    mu_margin = sigma_margin * math.sqrt(2.0) * inv_erf

    p_home_cover_neg_1pt5 = 1.0 - _normal_cdf(1.5, mu_margin, sigma_margin)
    p_away_cover_pos_1pt5 = 1.0 - p_home_cover_neg_1pt5

    p_away_cover_neg_1pt5 = _normal_cdf(-1.5, mu_margin, sigma_margin)
    p_home_cover_pos_1pt5 = 1.0 - p_away_cover_neg_1pt5

    totals_prob = {}
    for line in lines:
        p_over = 1.0 - _normal_cdf(line, baseline_total, sigma_total)
        totals_prob[line] = {
            "over": round(p_over, 4),
            "under": round(1.0 - p_over, 4),
        }

    return BaseballDerivativePricing(
        home_win_decisive=round(p_home_decisive, 4),
        away_win_decisive=round(1.0 - p_home_decisive, 4),
        tie_prob=round(tie_prob, 4),
        runline_home_minus_1pt5=round(p_home_cover_neg_1pt5, 4),
        runline_away_plus_1pt5=round(p_away_cover_pos_1pt5, 4),
        runline_away_minus_1pt5=round(p_away_cover_neg_1pt5, 4),
        runline_home_plus_1pt5=round(p_home_cover_pos_1pt5, 4),
        expected_total_runs=baseline_total,
        totals_prob=totals_prob,
    )
