"""Unit tests for Cross-Market Internal Consistency Engine."""

from __future__ import annotations

from model_prediction.cross_market_consistency import check_cross_market_consistency


def test_consistent_markets_pass() -> None:
    report = check_cross_market_consistency(
        moneyline_home_prob=0.60,
        moneyline_away_prob=0.40,
        spread_home_minus_1_5_prob=0.45,
        spread_away_plus_1_5_prob=0.55,
        total_over_prob=0.52,
        total_under_prob=0.48,
    )
    assert report.is_consistent
    assert len(report.violations) == 0


def test_monotonicity_inversion_detected() -> None:
    # Violation: Home -1.5 probability higher than Home ML win probability
    report = check_cross_market_consistency(
        moneyline_home_prob=0.50,
        moneyline_away_prob=0.50,
        spread_home_minus_1_5_prob=0.58,  # Impossible inversion
        spread_away_plus_1_5_prob=0.42,
    )
    assert not report.is_consistent
    assert any("Monotonicity inversion" in v for v in report.violations)


def test_total_non_complementarity_detected() -> None:
    report = check_cross_market_consistency(
        total_over_prob=0.60,
        total_under_prob=0.60,  # Sum = 1.20 != 1.0
    )
    assert not report.is_consistent
    assert any("Total over/under" in v for v in report.violations)
