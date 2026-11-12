import pytest

from model_prediction.domain import ModelOrigin
from model_prediction.lifecycle import (
    can_create_qualified_call,
    evaluate_locked_holdout,
    model_enabled,
)


def test_model_enabled_flag_and_qualification() -> None:
    assert can_create_qualified_call(True, ModelOrigin.STATISTICAL_MODEL)
    assert not can_create_qualified_call(False, ModelOrigin.STATISTICAL_MODEL)
    assert model_enabled({"status": "shadow_qualified"}) and not model_enabled({"status": "retired"})
    assert model_enabled({"enabled": True, "status": "retired"})
    assert not can_create_qualified_call(True, ModelOrigin.ANALYST_ESTIMATE)


def test_qualification_base_gate_is_locked_holdout_accuracy() -> None:
    decision = evaluate_locked_holdout(
        calls=50,
        hits=33,
        total_predictions=200,
        locked_holdout=True,
        brier_score=0.31,
        calibration={"ece": 0.20},
        roi=-0.50,
        price_diagnostics={"bid_ask_spread": 0.25},
    )

    assert decision.qualified is True
    assert decision.hit_rate == 0.66
    assert decision.roi == -0.50
    assert decision.secondary_reporting_complete is True


@pytest.mark.parametrize(
    ("calls", "hits", "locked_holdout", "failure_fragment"),
    [
        (49, 40, True, "below required 50"),
        (50, 29, True, "below required 60.00%"),
        (50, 40, False, "not a locked holdout"),
    ],
)
def test_qualification_rejects_only_primary_gate_failures(
    calls: int, hits: int, locked_holdout: bool, failure_fragment: str
) -> None:
    decision = evaluate_locked_holdout(
        calls=calls,
        hits=hits,
        total_predictions=100,
        locked_holdout=locked_holdout,
    )
    assert decision.qualified is False
    assert any(failure_fragment in failure for failure in decision.failures)
