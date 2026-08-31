import numpy as np

from model_prediction.phase_f_prospective import evaluate_prospective_battery


def test_prospective_battery_empty():
    res = evaluate_prospective_battery([], [], [])
    assert res.sample_size == 0
    assert res.passed_all_gates is False


def test_prospective_battery_synthetic_qualified():
    np.random.seed(42)
    n = 1000
    actuals = np.random.normal(9.0, 3.0, n).tolist()
    preds = [a + np.random.normal(0, 0.5) for a in actuals]
    market = [a + np.random.normal(0, 1.5) for a in actuals]

    # Generate truly well-calibrated probabilities
    binary_probs = np.random.uniform(0.2, 0.8, n).tolist()
    binary_outcomes = [1 if np.random.rand() < p else 0 for p in binary_probs]
    prices = [0.50] * n

    res = evaluate_prospective_battery(
        predictions=preds,
        actuals=actuals,
        market_lines=market,
        binary_outcomes=binary_outcomes,
        binary_probs=binary_probs,
        prices=prices,
    )
    assert res.sample_size == n
    assert res.residual_mae < 1.0
    assert abs(res.unconditional_bias) < 0.25
    assert res.brier_score < 0.25
    assert res.expected_calibration_error < 0.05
    assert 0.80 <= res.calibration_slope <= 1.20
    assert res.passed_all_gates is True
