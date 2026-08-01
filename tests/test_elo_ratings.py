"""Tests for the shared Elo logistic formula (expected_win_probability),
consolidated 2026-07-31 from four independently-written copies across
features/elo_ratings.py (team sports), esports.py (NeutralElo),
international_baseball.py (HomeElo), and models/tennis.py -- so a future
sign/formula correction only needs to happen once."""

from __future__ import annotations

import pytest

from model_prediction.features.elo_ratings import expected_win_probability


def test_equal_ratings_is_a_coinflip() -> None:
    assert expected_win_probability(1500.0, 1500.0) == pytest.approx(0.5)


def test_higher_rating_is_favored() -> None:
    assert expected_win_probability(1600.0, 1500.0) > 0.5
    assert expected_win_probability(1500.0, 1600.0) < 0.5


def test_symmetric_across_sides() -> None:
    p = expected_win_probability(1650.0, 1450.0)
    q = expected_win_probability(1450.0, 1650.0)
    assert p == pytest.approx(1 - q)


def test_home_advantage_shifts_probability_toward_the_advantaged_side() -> None:
    neutral = expected_win_probability(1500.0, 1500.0)
    with_advantage = expected_win_probability(1500.0, 1500.0, advantage=50.0)
    assert with_advantage > neutral


def test_standard_400_point_gap_is_about_ten_to_one() -> None:
    # A textbook Elo property: a 400-point gap implies ~10:1 odds (~0.909).
    assert expected_win_probability(1900.0, 1500.0) == pytest.approx(10 / 11, abs=1e-3)


def test_matches_the_pre_consolidation_formula_exactly() -> None:
    # Regression check: the four call sites this replaced all reduce to
    # 1 / (1 + 10 ** (-(rating_a + advantage - rating_b) / 400)).
    rating_a, rating_b, advantage = 1587.3, 1442.1, 24.0
    expected = 1.0 / (1.0 + 10.0 ** (-(rating_a + advantage - rating_b) / 400.0))
    assert expected_win_probability(rating_a, rating_b, advantage) == expected
