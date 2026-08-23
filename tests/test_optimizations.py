"""Unit tests verifying quantitative, computational, and architectural optimizations."""

from __future__ import annotations

import math

import numpy as np

from model_prediction.cross_market_consistency import (
    calculate_dutching_arbitrage,
    check_cross_market_consistency,
)
from model_prediction.meta_calibrator import SharedMetaCalibrator
from model_prediction.total_score import (
    analytical_spread_probabilities,
    analytical_totals_probabilities,
)


def test_cross_market_consistency_spread_and_dutching() -> None:
    # 1. Monotonicity checks
    report = check_cross_market_consistency(
        moneyline_home_prob=0.60,
        moneyline_away_prob=0.40,
        spread_home_minus_1_5_prob=0.65,  # Inversion: cover -1.5 cannot exceed win prob
        spread_away_plus_1_5_prob=0.45,
    )
    assert not report.is_consistent
    assert any("Home -1.5" in v for v in report.violations)

    # 2. Dutching arbitrage detection
    # Example: Book A gives 2.10 on Home, Book B gives 2.10 on Away
    # Total implied = 1/2.10 + 1/2.10 = 0.476 + 0.476 = 0.952 < 1.0 (Arbitrage!)
    arb_result = calculate_dutching_arbitrage([2.10, 2.10])
    assert arb_result["is_arbitrage"] is True
    assert arb_result["arbitrage_roi_pct"] > 4.5
    assert len(arb_result["optimal_stakes"]) == 2
    assert math.isclose(sum(arb_result["optimal_stakes"]), 1.0, rel_tol=1e-3)

    # No arbitrage example: Book A gives 1.90, Book B gives 1.90 (Implied > 1.0)
    no_arb = calculate_dutching_arbitrage([1.90, 1.90])
    assert no_arb["is_arbitrage"] is False
    assert no_arb["arbitrage_roi_pct"] == 0.0


def test_meta_calibrator_sample_weights_and_batch_calibration() -> None:
    calibrator = SharedMetaCalibrator(method="platt")

    # Generate synthetic uncalibrated probabilities and outcomes
    np.random.seed(42)
    raw_p = np.random.uniform(0.2, 0.8, size=50).tolist()
    outcomes = (np.random.uniform(0, 1, size=50) < np.array(raw_p)).astype(int).tolist()
    # Exponential weights favoring recent observations
    weights = np.exp(np.linspace(-1.0, 0.0, 50)).tolist()

    res = calibrator.fit(raw_p, outcomes, sample_weights=weights)
    assert res.sample_size == 50
    assert calibrator.is_fitted

    # Single calibration
    p_single = calibrator.calibrate(0.55)
    assert 0.0 < p_single < 1.0

    # Vectorized batch calibration
    batch_input = [0.30, 0.50, 0.70]
    batch_calibrated = calibrator.calibrate_batch(batch_input)
    assert isinstance(batch_calibrated, np.ndarray)
    assert len(batch_calibrated) == 3
    for p in batch_calibrated:
        assert 0.0 < p < 1.0


def test_analytical_totals_and_spread_probabilities() -> None:
    # Home projected runs = 4.5, Away projected runs = 3.5 (Total = 8.0)
    # Testing total line = 8.0
    totals = analytical_totals_probabilities(4.5, 3.5, total_line=8.0)
    assert "prob_over" in totals
    assert "prob_under" in totals
    assert "prob_push" in totals
    total_sum = totals["prob_over"] + totals["prob_under"] + totals["prob_push"]
    assert math.isclose(total_sum, 1.0, rel_tol=1e-3)
    assert totals["prob_over"] > 0.35
    assert totals["prob_under"] > 0.35
    assert totals["prob_push"] > 0.05

    # Spread line = -1.5 for home team (Expected margin = +1.0)
    spread = analytical_spread_probabilities(4.5, 3.5, spread_line=-1.5)
    assert "prob_cover_home" in spread
    assert "prob_cover_away" in spread
    assert "prob_push" in spread
    spread_sum = spread["prob_cover_home"] + spread["prob_cover_away"] + spread["prob_push"]
    assert math.isclose(spread_sum, 1.0, rel_tol=1e-3)
    assert spread["prob_cover_home"] > 0.30


def test_soccer_bivariate_derivative_markets() -> None:
    from model_prediction.models.soccer_dixon_coles import build_score_grid

    grid = build_score_grid(lambda_h=1.65, lambda_a=1.15, rho=-0.05)

    # 1. Draw No Bet (DNB)
    dnb = grid.prob_draw_no_bet()
    assert math.isclose(dnb["home"] + dnb["away"], 1.0, rel_tol=1e-3)
    assert dnb["home"] > dnb["away"]

    # 2. Clean Sheet
    cs = grid.prob_clean_sheet()
    assert 0.0 < cs["home"] < 1.0
    assert 0.0 < cs["away"] < 1.0

    # 3. Win To Nil
    wtn = grid.prob_win_to_nil()
    assert 0.0 < wtn["home"] < 1.0
    assert 0.0 < wtn["away"] < 1.0
    assert wtn["home"] <= grid.prob_home_win()
    assert wtn["away"] <= grid.prob_away_win()

    # 4. Exact goals table
    egt = grid.prob_exact_goals_table(max_counted_goals=6)
    assert "0" in egt
    assert "6+" in egt
    total_prob = sum(egt.values())
    assert math.isclose(total_prob, 1.0, rel_tol=1e-3)


def test_execution_ticket_helpers(tmp_path, monkeypatch) -> None:
    from model_prediction.execution_ticket import create_ticket, extract_order, is_ticket_valid

    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))

    sample_order = {"market_id": "m123", "side": "yes", "amount_usd": 15.0}
    ticket = create_ticket(sample_order, ttl_seconds=60)

    assert is_ticket_valid(ticket) is True
    extracted = extract_order(ticket)
    assert extracted == sample_order

    # Invalid / corrupted ticket
    corrupted = ticket[:-4] + "ffff"
    assert is_ticket_valid(corrupted) is False
    assert extract_order(corrupted) is None

    # Garbage ticket string
    assert is_ticket_valid("not_a_real_ticket") is False
    assert extract_order("not_a_real_ticket") is None


def test_wnba_derivative_probabilities() -> None:
    from model_prediction.features.wnba_pace_four_factors import (
        TeamFourFactors,
        project_wnba_derivative_probabilities,
        project_wnba_game_total,
    )

    home_ff = TeamFourFactors(
        team="Home Team",
        games_played=15,
        pace_40m=81.0,
        efg_pct=0.52,
        tov_pct=0.14,
        oreb_pct=0.28,
        ft_rate=0.24,
        off_rating=104.0,
        def_rating=98.0,
        projected_points_per_game=84.2,
    )
    away_ff = TeamFourFactors(
        team="Away Team",
        games_played=15,
        pace_40m=78.0,
        efg_pct=0.48,
        tov_pct=0.18,
        oreb_pct=0.24,
        ft_rate=0.20,
        off_rating=96.0,
        def_rating=102.0,
        projected_points_per_game=74.9,
    )

    game_proj = project_wnba_game_total(home_ff, away_ff)
    assert game_proj["projected_total"] > 150.0
    assert game_proj["projected_margin"] > 0.0  # Home favored

    derivs = project_wnba_derivative_probabilities(
        projected_total=game_proj["projected_total"],
        projected_margin=game_proj["projected_margin"],
        total_line=160.5,
        spread_line=-4.5,
    )
    assert "prob_over" in derivs
    assert "prob_under" in derivs
    assert math.isclose(derivs["prob_over"] + derivs["prob_under"], 1.0, rel_tol=1e-3)
    assert "prob_home_cover" in derivs
    assert "prob_away_cover" in derivs
    assert math.isclose(derivs["prob_home_cover"] + derivs["prob_away_cover"], 1.0, rel_tol=1e-3)
