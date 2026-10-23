"""Esports series derivative pricing engine.

Provides closed-form Best-of-3 (Bo3) and Best-of-5 (Bo5) map spread (-1.5 / +1.5 / -2.5 / +2.5)
and map totals (Over/Under 2.5, 3.5, 4.5) distributions derived from series win probability.
"""

from __future__ import annotations

from dataclasses import dataclass


def infer_map_win_probability(p_match: float) -> float:
    """Invert Bo3 series formula P_match = p^2 * (3 - 2p) to find single-map win probability p."""
    p_clamped = max(0.001, min(0.999, float(p_match)))
    low, high = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (low + high)
        val = (mid**2) * (3.0 - 2.0 * mid)
        if val < p_clamped:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def infer_map_win_probability_bo5(p_match: float) -> float:
    """Invert Bo5 series formula P_match = p^3 * (10 - 15p + 6p^2) to find single-map win probability p."""
    p_clamped = max(0.001, min(0.999, float(p_match)))
    low, high = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (low + high)
        val = (mid**3) * (10.0 - 15.0 * mid + 6.0 * (mid**2))
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


@dataclass(frozen=True)
class EsportsBo5Derivatives:
    map_win_p1: float
    map_win_p2: float
    match_win_p1: float
    match_win_p2: float
    spread_p1_cover_neg_1pt5: float  # P(3-0 or 3-1 win for P1) = p^3 + 3p^3(1-p)
    spread_p2_cover_pos_1pt5: float  # P(P2 wins >= 2 maps) = 1 - spread_p1_cover_neg_1pt5
    spread_p2_cover_neg_1pt5: float  # P(0-3 or 1-3 win for P2) = q^3 + 3q^3(1-q)
    spread_p1_cover_pos_1pt5: float  # P(P1 wins >= 2 maps) = 1 - spread_p2_cover_neg_1pt5
    spread_p1_cover_neg_2pt5: float  # P(3-0 sweep for P1) = p^3
    spread_p2_cover_pos_2pt5: float  # P(P2 wins >= 1 map) = 1 - p^3
    spread_p2_cover_neg_2pt5: float  # P(0-3 sweep for P2) = q^3
    spread_p1_cover_pos_2pt5: float  # P(P1 wins >= 1 map) = 1 - q^3
    total_maps_over_3pt5: float  # P(4 or 5 maps) = 1 - (p^3 + q^3)
    total_maps_under_3pt5: float  # P(3 maps) = p^3 + q^3
    total_maps_over_4pt5: float  # P(5 maps) = 6 p^2 q^2
    total_maps_under_4pt5: float  # P(3 or 4 maps) = 1 - 6 p^2 q^2
    expected_total_maps: float


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


def price_esports_bo5_derivatives(p_match: float) -> EsportsBo5Derivatives:
    """Calculate exact derivative probabilities for a Bo5 match."""
    p = infer_map_win_probability_bo5(p_match)
    q = 1.0 - p

    p_3_0 = p**3
    p_3_1 = 3.0 * (p**3) * q
    p_3_2 = 6.0 * (p**3) * (q**2)

    p_0_3 = q**3
    p_1_3 = 3.0 * (q**3) * p
    p_2_3 = 6.0 * (q**3) * (p**2)

    p1_cov_neg_1pt5 = p_3_0 + p_3_1
    p2_cov_neg_1pt5 = p_0_3 + p_1_3

    p_3_maps = p_3_0 + p_0_3
    p_4_maps = p_3_1 + p_1_3
    p_5_maps = p_3_2 + p_2_3

    exp_total = 3.0 * p_3_maps + 4.0 * p_4_maps + 5.0 * p_5_maps

    return EsportsBo5Derivatives(
        map_win_p1=round(p, 6),
        map_win_p2=round(q, 6),
        match_win_p1=round(p_match, 6),
        match_win_p2=round(1.0 - p_match, 6),
        spread_p1_cover_neg_1pt5=round(p1_cov_neg_1pt5, 6),
        spread_p2_cover_pos_1pt5=round(1.0 - p1_cov_neg_1pt5, 6),
        spread_p2_cover_neg_1pt5=round(p2_cov_neg_1pt5, 6),
        spread_p1_cover_pos_1pt5=round(1.0 - p2_cov_neg_1pt5, 6),
        spread_p1_cover_neg_2pt5=round(p_3_0, 6),
        spread_p2_cover_pos_2pt5=round(1.0 - p_3_0, 6),
        spread_p2_cover_neg_2pt5=round(p_0_3, 6),
        spread_p1_cover_pos_2pt5=round(1.0 - p_0_3, 6),
        total_maps_over_3pt5=round(p_4_maps + p_5_maps, 6),
        total_maps_under_3pt5=round(p_3_maps, 6),
        total_maps_over_4pt5=round(p_5_maps, 6),
        total_maps_under_4pt5=round(p_3_maps + p_4_maps, 6),
        expected_total_maps=round(exp_total, 3),
    )


def price_esports_derivatives(
    p_match: float,
    best_of: int = 3,
) -> EsportsBo3Derivatives | EsportsBo5Derivatives:
    """Unified entrypoint for pricing esports match derivatives across formats."""
    if best_of == 5:
        return price_esports_bo5_derivatives(p_match)
    return price_esports_bo3_derivatives(p_match)


class EsportsDerivativeModel:
    """Serving identity of the esports map/game handicap and total lanes.

    The prices come from ``price_esports_bo3_derivatives`` / ``price_esports_bo5_derivatives``
    applied to the title's tiered-Elo series probability (see ``esports.forecast_esports_slate``).
    """

    sport = "esports"
    version = "esports-derivatives-v6"


def esports_derivative_model() -> EsportsDerivativeModel:
    return EsportsDerivativeModel()
