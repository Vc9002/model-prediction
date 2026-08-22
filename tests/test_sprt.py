"""Unit tests for Sequential Probability Ratio Test (SPRT)."""

from __future__ import annotations

import pytest

from model_prediction.rebuild.sprt import BernoulliSPRT, GaussianSPRT


def test_bernoulli_sprt_accept_strong_edge():
    sprt = BernoulliSPRT(p0=0.50, p1=0.58, alpha=0.05, beta=0.10)

    # 40 wins in 50 trials (80% hit rate) -> should hit upper bound (ACCEPT_H1)
    outcomes = [1] * 40 + [0] * 10
    decision = sprt.evaluate(outcomes)

    assert decision.verdict == "ACCEPT_H1"
    assert decision.log_likelihood_ratio >= decision.upper_bound
    assert decision.n_samples == 50
    assert decision.details["win_rate"] == 0.80


def test_bernoulli_sprt_reject_negative_edge():
    sprt = BernoulliSPRT(p0=0.50, p1=0.55, alpha=0.05, beta=0.10)

    # 10 wins in 50 trials (20% hit rate) -> hits lower bound (REJECT_H1)
    outcomes = [1] * 10 + [0] * 40
    decision = sprt.evaluate(outcomes)

    assert decision.verdict == "REJECT_H1"
    assert decision.log_likelihood_ratio <= decision.lower_bound
    assert decision.details["win_rate"] == 0.20


def test_bernoulli_sprt_continue_testing():
    sprt = BernoulliSPRT(p0=0.50, p1=0.55, alpha=0.05, beta=0.10)

    # 5 wins in 10 trials (small sample, inconclusive)
    outcomes = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
    decision = sprt.evaluate(outcomes)

    assert decision.verdict == "CONTINUE_TESTING"
    assert decision.lower_bound < decision.log_likelihood_ratio < decision.upper_bound


def test_gaussian_sprt_accept_brier_improvement():
    sprt = GaussianSPRT(target_delta=0.010, estimated_sigma=0.05, alpha=0.05, beta=0.10)

    # 100 observations with strong positive improvement (+0.020 per pick)
    deltas = [0.020] * 100
    decision = sprt.evaluate(deltas)

    assert decision.verdict == "ACCEPT_H1"
    assert decision.log_likelihood_ratio >= decision.upper_bound


def test_gaussian_sprt_reject_degradation():
    sprt = GaussianSPRT(target_delta=0.010, estimated_sigma=0.05, alpha=0.05, beta=0.10)

    # 100 observations with degradation (-0.010 per pick)
    deltas = [-0.010] * 100
    decision = sprt.evaluate(deltas)

    assert decision.verdict == "REJECT_H1"
    assert decision.log_likelihood_ratio <= decision.lower_bound


def test_sprt_parameter_validation():
    with pytest.raises(ValueError, match="p1 must be greater than p0"):
        BernoulliSPRT(p0=0.55, p1=0.50)

    with pytest.raises(ValueError, match="target_delta must be positive"):
        GaussianSPRT(target_delta=-0.01)
