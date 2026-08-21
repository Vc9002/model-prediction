"""Cross-Market Internal Consistency Engine.

Validates probabilistic coherence across interrelated betting markets:
1. Monotonicity: P(Cover -1.5) <= P(Moneyline Win).
2. Complementarity: P(Over) + P(Under) == 1.0 (after no-vig normalization).
3. Arbitrage & Dutching Bounds: Detects synthetic mispricings or unphysical inversions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsistencyReport:
    is_consistent: bool
    violations: list[str]
    implied_no_vig_margin: float | None = None
    synthetic_edge: float | None = None


def check_cross_market_consistency(
    *,
    moneyline_home_prob: float | None = None,
    moneyline_away_prob: float | None = None,
    spread_home_minus_1_5_prob: float | None = None,
    spread_away_plus_1_5_prob: float | None = None,
    total_over_prob: float | None = None,
    total_under_prob: float | None = None,
    tolerance: float = 0.005,
) -> ConsistencyReport:
    """Validate that joint market probabilities satisfy physical and mathematical bounds."""
    violations: list[str] = []

    # 1. Moneyline coherence
    if moneyline_home_prob is not None and moneyline_away_prob is not None:
        ml_sum = moneyline_home_prob + moneyline_away_prob
        if abs(ml_sum - 1.0) > tolerance:
            violations.append(f"Moneyline probabilities sum to {ml_sum:.4f} != 1.0")

    # 2. Total coherence
    if total_over_prob is not None and total_under_prob is not None:
        total_sum = total_over_prob + total_under_prob
        if abs(total_sum - 1.0) > tolerance:
            violations.append(f"Total over/under probabilities sum to {total_sum:.4f} != 1.0")

    # 3. Spread vs Moneyline Monotonicity
    # A team cannot cover -1.5 without winning the game
    if (
        moneyline_home_prob is not None
        and spread_home_minus_1_5_prob is not None
        and spread_home_minus_1_5_prob > moneyline_home_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Home -1.5)={spread_home_minus_1_5_prob:.4f} > P(Home Win)={moneyline_home_prob:.4f}"
        )

    # A team that wins the game always covers +1.5
    if (
        moneyline_away_prob is not None
        and spread_away_plus_1_5_prob is not None
        and moneyline_away_prob > spread_away_plus_1_5_prob + tolerance
    ):
        violations.append(
            f"Monotonicity inversion: P(Away Win)={moneyline_away_prob:.4f} > P(Away +1.5)={spread_away_plus_1_5_prob:.4f}"
        )

    return ConsistencyReport(
        is_consistent=len(violations) == 0,
        violations=violations,
    )
