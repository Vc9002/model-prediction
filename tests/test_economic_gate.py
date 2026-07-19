import pytest

from model_prediction.economic_gate import (
    bootstrap_ci,
    economic_gate,
    max_drawdown,
    promotion_gate,
)


def test_max_drawdown_on_monotonic_gains_is_zero() -> None:
    result = max_drawdown([1.0, 1.0, 1.0])
    assert result.max_drawdown_units == 0.0
    assert result.peak_units == 3.0


def test_max_drawdown_finds_worst_peak_to_trough() -> None:
    # +5, +3 (peak=8), -10 (trough=-2, drawdown=10), +1
    result = max_drawdown([5.0, 3.0, -10.0, 1.0])
    assert result.max_drawdown_units == pytest.approx(10.0)
    assert result.peak_units == pytest.approx(8.0)
    assert result.trough_units == pytest.approx(-2.0)


def test_max_drawdown_empty_sequence() -> None:
    result = max_drawdown([])
    assert result.max_drawdown_units == 0.0
    assert result.peak_index == -1


def test_bootstrap_ci_is_deterministic_with_seed_and_brackets_the_mean() -> None:
    values = [1.0] * 50 + [-1.0] * 40  # mean slightly positive
    low, high = bootstrap_ci(values, seed=0)
    assert low <= sum(values) / len(values) <= high
    # Same seed reproduces the same interval.
    low2, high2 = bootstrap_ci(values, seed=0)
    assert (low, high) == (low2, high2)


def test_bootstrap_ci_rejects_empty_and_bad_confidence() -> None:
    with pytest.raises(ValueError):
        bootstrap_ci([])
    with pytest.raises(ValueError):
        bootstrap_ci([1.0], confidence=1.5)


def test_economic_gate_passes_with_healthy_metrics() -> None:
    result = economic_gate(
        calls=100,
        roi=0.05,
        mean_clv=0.01,
        pnl_sequence=[1.0, -0.5, 2.0] * 34,
        minimum_calls=50,
    )
    assert result.passed
    assert result.reasons == []


def test_economic_gate_fails_on_too_few_calls_and_negative_clv() -> None:
    result = economic_gate(calls=10, roi=0.05, mean_clv=-0.02, minimum_calls=50)
    assert not result.passed
    assert any("below the 50-call minimum" in reason for reason in result.reasons)
    assert any("not positive" in reason for reason in result.reasons)


def test_economic_gate_enforces_drawdown_limit() -> None:
    result = economic_gate(
        calls=60,
        roi=0.02,
        mean_clv=0.01,
        pnl_sequence=[5.0, -20.0],
        minimum_calls=10,
        maximum_drawdown_units=5.0,
    )
    assert not result.passed
    assert any("exceeds the 5.00U limit" in reason for reason in result.reasons)


def test_economic_gate_bootstrap_ci_excluding_loss_fails() -> None:
    # Consistently negative daily P&L with low variance -> the 95% CI upper
    # bound stays negative, so the gate should fail on this signal alone.
    losing_days = [-1.0, -0.9, -1.1, -1.0, -0.95, -1.05, -0.9, -1.1, -1.0, -0.95] * 3
    result = economic_gate(
        calls=60,
        roi=0.01,
        mean_clv=0.01,
        minimum_calls=10,
        daily_pnl_for_bootstrap=losing_days,
    )
    assert not result.passed
    assert any("does not exclude a loss" in reason for reason in result.reasons)


def test_promotion_gate_requires_both_gates() -> None:
    good_economic = economic_gate(calls=100, roi=0.05, mean_clv=0.01, minimum_calls=50)
    predictive_ok = {"status": "ok", "sample_size": 200, "brier_score": 0.2}
    result = promotion_gate(predictive_metrics=predictive_ok, economic=good_economic)
    assert result.passed

    predictive_insufficient = {"status": "insufficient_sample", "sample_size": 5}
    result2 = promotion_gate(predictive_metrics=predictive_insufficient, economic=good_economic)
    assert not result2.passed
    assert any("not evaluable" in reason for reason in result2.reasons)

    bad_economic = economic_gate(calls=1, roi=None, mean_clv=None, minimum_calls=50)
    result3 = promotion_gate(predictive_metrics=predictive_ok, economic=bad_economic)
    assert not result3.passed
