"""Unit tests for Soccer Joint Bivariate Dixon-Coles Grid model and parameter optimization engine.

Tests:
1. Low-score correlation tau function behavior and properties.
2. Score grid probability normalization to 1.0.
3. Consistency between 1X2, Over/Under, and BTTS derived from the same joint grid.
4. Asian Handicap matrix and line calculations (full, half, quarter lines).
5. Exponential time decay weighting w_k = exp(-xi * (t_now - t_k)).
6. DixonColesEngine MLE fitting and sum(alpha) = 1.0 constraint enforcement.
7. Temporal cross-validation helper for optimal xi search.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from model_prediction.models.soccer_dixon_coles import (
    BivariateScoreGrid,
    DixonColesEngine,
    build_score_grid,
    compute_match_weights,
    dixon_coles_tau,
    optimize_decay_xi,
    ranked_probability_score,
    tau,
    temporal_cross_validation,
    time_decay_weight,
)


def test_low_score_correlation_tau_function() -> None:
    """Test Dixon-Coles tau adjustment for low scores (0,0), (1,0), (0,1), (1,1) and higher scores."""
    lh = 1.6
    la = 1.2
    rho = -0.10

    # Specific formula verification
    assert dixon_coles_tau(0, 0, lh, la, rho) == pytest.approx(1.0 - lh * la * rho, abs=1e-12)
    assert dixon_coles_tau(1, 0, lh, la, rho) == pytest.approx(1.0 + la * rho, abs=1e-12)
    assert dixon_coles_tau(0, 1, lh, la, rho) == pytest.approx(1.0 + lh * rho, abs=1e-12)
    assert dixon_coles_tau(1, 1, lh, la, rho) == pytest.approx(1.0 - rho, abs=1e-12)

    # Concrete values check
    # tau(0,0) = 1 - 1.6 * 1.2 * (-0.1) = 1 + 0.192 = 1.192
    assert dixon_coles_tau(0, 0, lh, la, rho) == pytest.approx(1.192, abs=1e-6)
    # tau(1,0) = 1 + 1.2 * (-0.1) = 0.88
    assert dixon_coles_tau(1, 0, lh, la, rho) == pytest.approx(0.88, abs=1e-6)
    # tau(0,1) = 1 + 1.6 * (-0.1) = 0.84
    assert dixon_coles_tau(0, 1, lh, la, rho) == pytest.approx(0.84, abs=1e-6)
    # tau(1,1) = 1 - (-0.1) = 1.10
    assert dixon_coles_tau(1, 1, lh, la, rho) == pytest.approx(1.10, abs=1e-6)

    # Higher score cells (x >= 2 or y >= 2) must all return exactly 1.0
    high_score_cells = [(2, 0), (0, 2), (2, 1), (1, 2), (2, 2), (3, 0), (0, 3), (3, 2), (5, 4)]
    for x, y in high_score_cells:
        assert dixon_coles_tau(x, y, lh, la, rho) == 1.0
        assert tau(x, y, lh, la, rho) == 1.0

    # When rho == 0, tau is 1.0 for ALL cells
    for x in range(5):
        for y in range(5):
            assert dixon_coles_tau(x, y, lh, la, 0.0) == 1.0


def test_score_grid_probability_sums_to_one() -> None:
    """Test that the bivariate score grid normalizes and sums to 1.0 across diverse parameter settings."""
    test_cases = [
        {"lh": 1.5, "la": 1.1, "rho": -0.12},
        {"lh": 3.2, "la": 2.7, "rho": -0.05},
        {"lh": 0.7, "la": 0.5, "rho": -0.15},
        {"lh": 1.4, "la": 1.4, "rho": 0.0},
        {"lh": 2.1, "la": 0.9, "rho": 0.06},
    ]

    for tc in test_cases:
        grid_obj = build_score_grid(
            lambda_h=tc["lh"],
            lambda_a=tc["la"],
            rho=tc["rho"],
            max_goals=10,
        )

        assert isinstance(grid_obj, BivariateScoreGrid)
        assert grid_obj.grid.shape == (11, 11)

        # Probabilities must be non-negative
        assert np.all(grid_obj.grid >= 0.0)

        # Probabilities must sum exactly to 1.0
        grid_sum = float(np.sum(grid_obj.grid))
        assert grid_sum == pytest.approx(1.0, abs=1e-12)

        # Exact score accessor
        assert grid_obj.exact_score(0, 0) == grid_obj.grid[0, 0]
        assert grid_obj.exact_score(2, 1) == grid_obj.grid[2, 1]
        assert grid_obj.exact_score(15, 0) == 0.0  # Out of range


def test_grid_derived_market_consistency() -> None:
    """Test consistency across 1X2, BTTS, and Over/Under derived from the same joint score grid."""
    lh = 1.65
    la = 1.15
    rho = -0.08
    grid = build_score_grid(lambda_h=lh, lambda_a=la, rho=rho, max_goals=10)

    # 1. Test 1X2 market consistency
    p_1x2 = grid.prob_1x2()
    p_home = p_1x2["home"]
    p_draw = p_1x2["draw"]
    p_away = p_1x2["away"]

    assert p_home > 0.0
    assert p_draw > 0.0
    assert p_away > 0.0
    assert p_home + p_draw + p_away == pytest.approx(1.0, abs=1e-12)

    # Verify 1X2 matches explicit lower/diag/upper triangle sums
    expected_home = sum(grid.grid[h, a] for h in range(11) for a in range(h))
    expected_draw = sum(grid.grid[h, h] for h in range(11))
    expected_away = sum(grid.grid[h, a] for a in range(11) for h in range(a))
    assert p_home == pytest.approx(expected_home, abs=1e-12)
    assert p_draw == pytest.approx(expected_draw, abs=1e-12)
    assert p_away == pytest.approx(expected_away, abs=1e-12)

    # 2. Test BTTS consistency
    p_btts = grid.prob_btts()
    btts_yes = p_btts["yes"]
    btts_no = p_btts["no"]

    assert btts_yes > 0.0
    assert btts_no > 0.0
    assert btts_yes + btts_no == pytest.approx(1.0, abs=1e-12)

    expected_btts_yes = sum(grid.grid[h, a] for h in range(1, 11) for a in range(1, 11))
    expected_btts_no = sum(grid.grid[0, a] for a in range(11)) + sum(grid.grid[h, 0] for h in range(1, 11))
    assert btts_yes == pytest.approx(expected_btts_yes, abs=1e-12)
    assert btts_no == pytest.approx(expected_btts_no, abs=1e-12)

    # 3. Test Over / Under totals consistency
    totals_lines = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
    ou_table = grid.prob_over_under_table(totals_lines)

    for line in totals_lines:
        res = ou_table[line]
        assert res["push"] == 0.0  # Half-goal lines have 0 push
        assert res["over"] + res["under"] == pytest.approx(1.0, abs=1e-12)

    # Monotonicity checks: P(Over L) decreases as L increases
    overs = [ou_table[line]["over"] for line in totals_lines]
    unders = [ou_table[line]["under"] for line in totals_lines]
    assert all(overs[i] > overs[i + 1] for i in range(len(overs) - 1))
    assert all(unders[i] < unders[i + 1] for i in range(len(unders) - 1))

    # 4. Cross-market joint relationships
    # P(Under 0.5) == P(0-0)
    p_00 = grid.exact_score(0, 0)
    assert ou_table[0.5]["under"] == pytest.approx(p_00, abs=1e-12)

    # P(Under 1.5) == P(0-0) + P(1-0) + P(0-1)
    p_under_15_manual = p_00 + grid.exact_score(1, 0) + grid.exact_score(0, 1)
    assert ou_table[1.5]["under"] == pytest.approx(p_under_15_manual, abs=1e-12)

    # Both Teams To Score requires at least 2 goals (1-1, 2-1, etc.) -> BTTS Yes <= Over 1.5
    assert btts_yes <= ou_table[1.5]["over"] + 1e-12


def test_asian_handicap_matrix_calculation() -> None:
    """Test Asian Handicap calculations for Level (0.0), Half (-0.5, +0.5, -1.5), and Quarter lines."""
    lh = 1.80
    la = 0.90
    rho = -0.05
    grid = build_score_grid(lambda_h=lh, lambda_a=la, rho=rho, max_goals=10)

    p_1x2 = grid.prob_1x2()

    # 1. Level Line (0.0 / Draw No Bet)
    ah_0 = grid.asian_handicap(0.0)
    assert ah_0.home_win == pytest.approx(p_1x2["home"], abs=1e-12)
    assert ah_0.draw == pytest.approx(p_1x2["draw"], abs=1e-12)
    assert ah_0.away_win == pytest.approx(p_1x2["away"], abs=1e-12)
    assert ah_0.home_win + ah_0.draw + ah_0.away_win == pytest.approx(1.0, abs=1e-12)
    assert ah_0.home_cover == pytest.approx(p_1x2["home"] + 0.5 * p_1x2["draw"], abs=1e-12)

    # 2. Half Lines (-0.5 and +0.5)
    ah_minus_05 = grid.asian_handicap(-0.5)
    assert ah_minus_05.draw == 0.0
    assert ah_minus_05.home_win == pytest.approx(p_1x2["home"], abs=1e-12)
    assert ah_minus_05.away_win == pytest.approx(p_1x2["draw"] + p_1x2["away"], abs=1e-12)
    assert ah_minus_05.home_cover == pytest.approx(ah_minus_05.home_win, abs=1e-12)

    ah_plus_05 = grid.asian_handicap(0.5)
    assert ah_plus_05.draw == 0.0
    assert ah_plus_05.home_win == pytest.approx(p_1x2["home"] + p_1x2["draw"], abs=1e-12)
    assert ah_plus_05.away_win == pytest.approx(p_1x2["away"], abs=1e-12)

    # 3. Line -1.5 (Home must win by 2+ goals)
    ah_minus_15 = grid.asian_handicap(-1.5)
    expected_win_by_2_plus = sum(grid.grid[h, a] for h in range(11) for a in range(11) if h - a >= 2)
    assert ah_minus_15.home_win == pytest.approx(expected_win_by_2_plus, abs=1e-12)
    assert ah_minus_15.away_win == pytest.approx(1.0 - expected_win_by_2_plus, abs=1e-12)

    # 4. Quarter Lines (-0.25 and +0.25)
    ah_minus_025 = grid.asian_handicap(-0.25)
    expected_cover_minus_025 = 0.5 * (ah_0.home_cover + ah_minus_05.home_cover)
    assert ah_minus_025.home_cover == pytest.approx(expected_cover_minus_025, abs=1e-12)

    ah_plus_025 = grid.asian_handicap(0.25)
    expected_cover_plus_025 = 0.5 * (ah_0.home_cover + ah_plus_05.home_cover)
    assert ah_plus_025.home_cover == pytest.approx(expected_cover_plus_025, abs=1e-12)

    # 5. Full Matrix across multiple lines
    lines = (-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5)
    matrix = grid.asian_handicap_matrix(lines)

    assert set(matrix.keys()) == set(lines)
    covers = [matrix[line]["home_cover"] for line in lines]
    # Monotonicity: Home cover probability must be non-decreasing as handicap increases
    for i in range(len(covers) - 1):
        assert covers[i] <= covers[i + 1] + 1e-12


def test_decay_parameter_weighting() -> None:
    """Test exponential time-decay weighting w_k = exp(-xi * (t_now - t_k))."""
    t_now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    t_today = t_now
    t_100d_ago = t_now - timedelta(days=100)
    t_365d_ago = t_now - timedelta(days=365)

    # 1. If xi == 0, all weights must be exactly 1.0
    assert time_decay_weight(t_today, t_now, xi=0.0) == 1.0
    assert time_decay_weight(t_100d_ago, t_now, xi=0.0) == 1.0
    assert time_decay_weight(t_365d_ago, t_now, xi=0.0) == 1.0

    # 2. Today's match with xi > 0 has weight 1.0
    xi = 0.002
    assert time_decay_weight(t_today, t_now, xi=xi) == pytest.approx(1.0, abs=1e-12)

    # 3. Matches in past have exact exponential decay exp(-xi * delta_days)
    w_100 = time_decay_weight(t_100d_ago, t_now, xi=xi)
    w_365 = time_decay_weight(t_365d_ago, t_now, xi=xi)

    assert w_100 == pytest.approx(math.exp(-0.002 * 100), abs=1e-6)
    assert w_365 == pytest.approx(math.exp(-0.002 * 365), abs=1e-6)
    assert 0.0 < w_365 < w_100 < 1.0

    # 4. Supports string and date types
    d_str = "2026-05-12T12:00:00Z"
    d_obj = date(2026, 5, 12)
    w_from_str = time_decay_weight(d_str, t_now, xi=xi)
    w_from_date = time_decay_weight(d_obj, t_now, xi=xi)
    assert w_from_str == pytest.approx(w_from_date, abs=1e-2)

    # 5. Vectorized compute_match_weights
    dates = [t_today, t_100d_ago, t_365d_ago]
    weights = compute_match_weights(dates, t_now=t_now, xi=xi)
    assert len(weights) == 3
    assert weights[0] == pytest.approx(1.0, abs=1e-12)
    assert weights[1] == pytest.approx(w_100, abs=1e-12)
    assert weights[2] == pytest.approx(w_365, abs=1e-12)


def _generate_synthetic_matches(n_rounds: int = 15) -> list[dict[str, object]]:
    """Helper to generate a clean synthetic round-robin match dataset."""
    teams = ["Arsenal", "Chelsea", "Liverpool", "ManCity"]
    base_attacks = {"Arsenal": 1.5, "Chelsea": 1.2, "Liverpool": 1.6, "ManCity": 2.0}
    base_defenses = {"Arsenal": 0.9, "Chelsea": 1.1, "Liverpool": 0.8, "ManCity": 0.7}
    home_boost = 1.20

    matches = []
    base_date = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    idx = 0

    for r in range(n_rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                match_dt = base_date + timedelta(days=idx * 2)
                lh = base_attacks[h] * base_defenses[a] * home_boost * 0.5
                la = base_attacks[a] * base_defenses[h] * 0.5
                # Deterministic pseudo-Poisson scores for reproducibility
                h_goals = round(lh + 0.3 * math.sin(idx))
                a_goals = round(la + 0.3 * math.cos(idx))
                matches.append(
                    {
                        "event_id": f"syn-{idx}",
                        "home_team": h,
                        "away_team": a,
                        "home_score": max(0, h_goals),
                        "away_score": max(0, a_goals),
                        "event_start_utc": match_dt.isoformat(),
                    }
                )
                idx += 1
    return matches


def test_dixon_coles_engine_mle_fit_and_sum_alpha_constraint() -> None:
    """Test DixonColesEngine MLE estimation, sum(alpha)=1 constraint, and prediction API."""
    matches = _generate_synthetic_matches(n_rounds=12)
    engine = DixonColesEngine(xi=0.001)

    # Fit engine
    engine.fit(matches, sum_alpha_constraint=1.0)

    assert engine.is_fitted is True
    assert set(engine.teams) == {"Arsenal", "Chelsea", "Liverpool", "ManCity"}

    # 1. Enforce sum(alpha) == 1.0 constraint
    alpha_sum = sum(engine.attack_params.values())
    assert alpha_sum == pytest.approx(1.0, abs=1e-6)

    # 2. Parameters must be in sensible domains
    for team in engine.teams:
        assert engine.attack_params[team] > 0.0
        assert engine.defense_params[team] > 0.0
    assert 0.5 <= engine.home_advantage <= 2.5
    assert -0.5 <= engine.rho <= 0.5

    # 3. Expected goals calculation
    lh, la = engine.predict_expected_goals("ManCity", "Chelsea")
    assert lh > 0.0
    assert la > 0.0
    # ManCity is stronger in attack and defense, playing at home
    assert lh > la

    # 4. Predict match consolidated result
    pred = engine.predict_match("ManCity", "Chelsea")
    assert pred.prob_home > pred.prob_away
    assert pred.prob_home + pred.prob_draw + pred.prob_away == pytest.approx(1.0, abs=1e-6)
    assert pred.btts_yes + pred.btts_no == pytest.approx(1.0, abs=1e-6)
    assert 2.5 in pred.over_under
    assert 0.0 in pred.asian_handicap
    assert pred.score_grid.grid.shape == (11, 11)

    # 5. Serialization dictionary
    d = pred.as_dict()
    assert d["home_team"] == "ManCity"
    assert "prob_home" in d
    assert "btts_yes" in d


def test_ranked_probability_score_calculation() -> None:
    """Test RPS calculation for soccer 3-way probabilities."""
    # Perfect home win prediction
    rps_perfect = ranked_probability_score(1.0, 0.0, 0.0, actual_home_goals=2, actual_away_goals=0)
    assert rps_perfect == pytest.approx(0.0, abs=1e-12)

    # Completely wrong prediction
    rps_wrong = ranked_probability_score(0.0, 0.0, 1.0, actual_home_goals=2, actual_away_goals=0)
    # p1=0, o1=1 -> (0-1)^2 = 1; p2=0, o2=1 -> (0-1)^2 = 1; 0.5 * (1 + 1) = 1.0
    assert rps_wrong == pytest.approx(1.0, abs=1e-12)

    # Draw actual
    rps_draw = ranked_probability_score(0.2, 0.6, 0.2, actual_home_goals=1, actual_away_goals=1)
    # p1=0.2, o1=0 -> (0.2)^2 = 0.04; p2=0.8, o2=1 -> (-0.2)^2 = 0.04; 0.5 * 0.08 = 0.04
    assert rps_draw == pytest.approx(0.04, abs=1e-12)


def test_temporal_cross_validation_xi_search() -> None:
    """Test temporal walk-forward cross validation and optimal xi grid search."""
    matches = _generate_synthetic_matches(n_rounds=10)
    assert len(matches) == 120

    # 1. Single xi temporal CV evaluation
    cv_score_0 = temporal_cross_validation(
        matches=matches,
        xi=0.0,
        n_splits=3,
        min_train_matches=30,
        metric="rps",
    )
    assert math.isfinite(cv_score_0)
    assert cv_score_0 > 0.0

    cv_score_logloss = temporal_cross_validation(
        matches=matches,
        xi=0.001,
        n_splits=3,
        min_train_matches=30,
        metric="log_loss",
    )
    assert math.isfinite(cv_score_logloss)
    assert cv_score_logloss > 0.0

    # 2. Grid search for optimal xi
    candidates = (0.0, 0.001, 0.003)
    best_xi, results = optimize_decay_xi(
        matches=matches,
        xi_candidates=candidates,
        n_splits=3,
        min_train_matches=30,
        metric="rps",
    )

    assert best_xi in candidates
    assert len(results) == len(candidates)
    for cand in candidates:
        assert cand in results
        assert math.isfinite(results[cand])
    assert best_xi == min(results.keys(), key=lambda k: results[k])


def test_dixon_coles_engine_empty_or_small_data_guards() -> None:
    """Test defensive error handling for edge cases."""
    engine = DixonColesEngine()

    with pytest.raises(ValueError, match="empty match history"):
        engine.fit([])

    with pytest.raises(ValueError, match="Need at least 2 distinct teams"):
        engine.fit(
            [
                {"home_team": "TeamA", "away_team": "TeamA", "home_score": 1, "away_score": 0},
            ]
        )

    with pytest.raises(RuntimeError, match="must be fitted"):
        engine.predict_expected_goals("TeamA", "TeamB")
