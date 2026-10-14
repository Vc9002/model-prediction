"""Esports series derivative pricing engine.

Provides closed-form Best-of-3 (Bo3) map spread (-1.5 / +1.5) and
map totals (Over/Under 2.5) distributions derived from series win probability.
"""

from __future__ import annotations

from dataclasses import dataclass


def infer_map_win_probability(p_match: float) -> float:
    """Invert Bo3 series formula P_match = p^2 * (3 - 2p) to find single-map win probability p."""
    p_clamped = max(0.001, min(0.999, float(p_match)))
    # Numerical bisection since P_match(p) is strictly monotonic on [0, 1]
    low, high = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (low + high)
        val = (mid**2) * (3.0 - 2.0 * mid)
        if val < p_clamped:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True)
class EsportsBo3Derivatives:
    map_win_p1: float
    map_win_p2: float
    match_win_p1: float
    match_win_p2: float
    spread_p1_cover_neg_1pt5: float  # P(2-0 win for P1) = p^2
    spread_p2_cover_pos_1pt5: float  # P(P2 wins >= 1 map) = 1 - p^2
    spread_p2_cover_neg_1pt5: float  # P(0-2 win for P2) = (1-p)^2
    spread_p1_cover_pos_1pt5: float  # P(P1 wins >= 1 map) = 1 - (1-p)^2
    total_maps_over_2pt5: float  # P(2-1 or 1-2) = 2p(1-p)
    total_maps_under_2pt5: float  # P(2-0 or 0-2) = p^2 + (1-p)^2
    expected_total_maps: float  # 2 + 2p(1-p)


def price_esports_bo3_derivatives(p_match: float) -> EsportsBo3Derivatives:
    """Calculate exact derivative probabilities for a Bo3 match."""
    p = infer_map_win_probability(p_match)
    q = 1.0 - p

    p_2_0 = p**2
    p_0_2 = q**2
    p_3_maps = 2.0 * p * q
    p_2_maps = p_2_0 + p_0_2

    return EsportsBo3Derivatives(
        map_win_p1=round(p, 6),
        map_win_p2=round(q, 6),
        match_win_p1=round(p_match, 6),
        match_win_p2=round(1.0 - p_match, 6),
        spread_p1_cover_neg_1pt5=round(p_2_0, 6),
        spread_p2_cover_pos_1pt5=round(1.0 - p_2_0, 6),
        spread_p2_cover_neg_1pt5=round(p_0_2, 6),
        spread_p1_cover_pos_1pt5=round(1.0 - p_0_2, 6),
        total_maps_over_2pt5=round(p_3_maps, 6),
        total_maps_under_2pt5=round(p_2_maps, 6),
        expected_total_maps=round(2.0 + p_3_maps, 3),
    )
