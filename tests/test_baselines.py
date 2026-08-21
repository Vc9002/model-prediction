"""Tests for quantitative baseline models."""

from __future__ import annotations

from model_prediction.features.baselines import (
    TeamRecordAccumulator,
    log5_matchup_probability,
    pythagorean_win_rate,
)


def test_pythagorean_equal_runs():
    assert pythagorean_win_rate(100, 100) == 0.50


def test_pythagorean_dominant_team():
    # 700 RS, 500 RA is ~0.64 win rate in MLB
    pw = pythagorean_win_rate(700, 500)
    assert 0.62 < pw < 0.68


def test_pythagorean_edge_cases():
    assert pythagorean_win_rate(0, 100) == 0.01
    assert pythagorean_win_rate(100, 0) == 0.99
    assert pythagorean_win_rate(0, 0) == 0.50


def test_log5_equal_teams():
    assert log5_matchup_probability(0.50, 0.50) == 0.50
    assert log5_matchup_probability(0.60, 0.60) == 0.50


def test_log5_favorite_vs_underdog():
    # 0.60 team vs 0.40 team -> ~0.692
    prob = log5_matchup_probability(0.60, 0.40)
    assert 0.68 < prob < 0.71


def test_team_record_accumulator():
    acc = TeamRecordAccumulator(wins=60, losses=40, runs_scored=500, runs_allowed=400)
    assert acc.total_games == 100
    smoothed_wr = acc.win_rate()
    assert 0.55 < smoothed_wr < 0.60
    pyth = acc.pythagorean_expectation()
    assert 0.57 < pyth < 0.63
