import pytest

from model_prediction.features.confidence_gate import evaluate, learn_threshold


def test_gate_has_no_unconfigured_sport_fallback() -> None:
    with pytest.raises(ValueError, match="no learned confidence threshold"):
        evaluate(0.99, "mlb")


def test_gate_uses_inclusive_production_boundary() -> None:
    decision = evaluate(0.65, "mlb", threshold=0.65)
    assert decision.call is True


def test_threshold_learning_targets_65_percent_and_50_calls() -> None:
    rows = (
        [{"confidence": 0.9, "probability": 0.9, "correct": True, "outcome": 1}] * 40
        + [{"confidence": 0.8, "probability": 0.8, "correct": True, "outcome": 1}] * 10
        + [{"confidence": 0.8, "probability": 0.8, "correct": False, "outcome": 0}] * 10
        + [{"confidence": 0.55, "probability": 0.55, "correct": False, "outcome": 0}] * 40
    )
    threshold, stats = learn_threshold(rows)
    assert threshold == 0.8
    assert stats["calls"] == 60
    assert stats["hit_rate"] > 0.65
