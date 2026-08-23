"""Unit tests for MLB v9 4-Gate Promotion Decision Engine (Roadmap Phase 23)."""

from scripts.mlb_v9_promotion_gate import evaluate_promotion_gates


def test_evaluate_promotion_gates_continue_shadow():
    shadow_metrics = {
        "settled_games": 45,
        "unique_dates": 3,
        "delta_log_loss": -0.0025,
        "p_log_loss_better": 0.88,
        "delta_brier": -0.0011,
        "p_brier_better": 0.89,
    }
    op_metrics = {"serving_coverage": 0.99, "latency_ms": 65.0}
    econ_metrics = {"rolling_clv_pp": 2.1, "executable_roi": 0.095}

    res = evaluate_promotion_gates(
        "mlb-v9-candidate-1", shadow_metrics, op_metrics, econ_metrics, min_prospective_games=200
    )
    assert res.predictive_gate.passed is True
    assert res.operational_gate.passed is True
    assert res.prospective_gate.passed is False  # Only 45/200 games
    assert res.economic_gate.passed is True
    assert res.overall_verdict == "CONTINUE_SHADOW"


def test_evaluate_promotion_gates_full_promotion_candidate():
    shadow_metrics = {
        "settled_games": 220,
        "unique_dates": 35,
        "delta_log_loss": -0.0022,
        "p_log_loss_better": 0.86,
        "delta_brier": -0.0010,
        "p_brier_better": 0.87,
    }
    op_metrics = {"serving_coverage": 0.99, "latency_ms": 65.0}
    econ_metrics = {"rolling_clv_pp": 2.1, "executable_roi": 0.095}

    res = evaluate_promotion_gates(
        "mlb-v9-candidate-1", shadow_metrics, op_metrics, econ_metrics, min_prospective_games=200
    )
    assert res.predictive_gate.passed is True
    assert res.operational_gate.passed is True
    assert res.prospective_gate.passed is True
    assert res.economic_gate.passed is True
    assert res.overall_verdict == "PROMOTION_CANDIDATE"
