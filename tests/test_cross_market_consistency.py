"""Unit tests for Cross-Market Internal Consistency Engine."""

from __future__ import annotations

from model_prediction.cross_market_consistency import (
    check_cross_market_consistency,
    validate_cross_market_pick_coherence,
)


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


def test_validate_cross_market_pick_coherence() -> None:
    # 1. Negative spread on Home contradicts existing Away Moneyline
    existing = [{"market_type": "moneyline", "selection": "away", "line": None, "decision": "CALL"}]
    ok, err = validate_cross_market_pick_coherence("spread", "home", -2.5, existing)
    assert not ok
    assert "CROSS_MARKET_CONTRADICTION" in err

    # 2. Candidate Away Moneyline contradicts existing Home -1.5 spread
    existing_spread = [{"market_type": "spread", "selection": "home", "line": -1.5, "decision": "CALL"}]
    ok, err = validate_cross_market_pick_coherence("moneyline", "away", None, existing_spread)
    assert not ok
    assert "CROSS_MARKET_CONTRADICTION" in err

    # 3. Small spread conflict on line <= 3.0
    existing_ml = [{"market_type": "moneyline", "selection": "home", "line": None, "decision": "CALL"}]
    ok, err = validate_cross_market_pick_coherence("spread", "away", 2.5, existing_ml)
    assert not ok
    assert "CROSS_MARKET_CONFLICT" in err

    # 4. Big spread (+10.5) underdog cover alongside favorite Moneyline is valid!
    ok, err = validate_cross_market_pick_coherence("spread", "away", 10.5, existing_ml)
    assert ok
    assert err is None

    # 5. Agreeing side (Home ML and Home -2.5) is valid
    ok, err = validate_cross_market_pick_coherence(
        "spread",
        "home",
        -2.5,
        [{"market_type": "moneyline", "selection": "home", "line": None, "decision": "CALL"}],
    )
    assert ok
    assert err is None


def test_live_lowercase_open_pick_blocks_opposing_favorite() -> None:
    existing = [
        {"market_type": "moneyline", "selection": "home", "line": None, "decision": "CALL", "status": "open"}
    ]
    ok, err = validate_cross_market_pick_coherence("spread", "away", -4.5, existing)
    assert not ok
    assert err and "CROSS_MARKET_CONTRADICTION" in err
