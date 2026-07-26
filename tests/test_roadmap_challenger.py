from __future__ import annotations

from model_prediction.models.learned_market import LearnedMarketArtifact, build_artifact
from model_prediction.roadmap_challenger import (
    ADDITIONS,
    GATE_GRID,
    _all_combinations,
    _artifact_predict,
    _features_for,
    _gate_metrics,
    _holm_adjusted_p_values,
    _independent_effect_label,
    _variant_name,
)
from model_prediction.validation import ValidationRow


def _row(day: str, outcome: int) -> ValidationRow:
    return ValidationRow(day, day, outcome, 0.5, 0.0, 1.0, 1.0, False, False)


def test_factorial_contains_every_combination_and_incumbent() -> None:
    combinations = _all_combinations()
    assert len(combinations) == 2 ** len(ADDITIONS)
    assert combinations[0] == ()
    assert set(combinations[-1]) == set(ADDITIONS)


def test_feature_sets_extend_incumbent_without_duplicates() -> None:
    features = _features_for(("elo_probability", "trend_gap"), ("consistency", "hot_cold"))
    assert features == (
        "elo_probability",
        "trend_gap",
        "consistency_gap",
        "hot_cold_gap",
    )
    assert _variant_name(()) == "incumbent"


def test_gate_grid_and_metrics_are_deterministic() -> None:
    assert GATE_GRID[0] == 0.5
    assert GATE_GRID[-1] == 0.8
    metrics = _gate_metrics([0.8, 0.4], [_row("2026-01-01", 1), _row("2026-01-02", 0)], 0.6)
    assert metrics["calls"] == 2
    assert metrics["hit_rate"] == 1.0


def test_artifact_control_uses_pinned_coefficients() -> None:
    artifact = LearnedMarketArtifact(
        build_artifact(
            sport="nfl",
            model_version="test",
            market_models={
                "moneyline": {
                    "feature_names": ["elo_probability"],
                    "coefficients": [2.0],
                    "intercept": -1.0,
                    "confidence_threshold": 0.55,
                }
            },
            training={},
        )
    )
    probabilities = _artifact_predict(
        artifact, [_row("2026-01-01", 1)], ("elo_probability",)
    )
    assert probabilities == [0.5]


def test_holm_adjustment_is_monotone_in_rank_order() -> None:
    adjusted = _holm_adjusted_p_values({"a": 0.01, "b": 0.02, "c": 0.5})
    assert adjusted == {"a": 0.03, "b": 0.04, "c": 0.5}


def test_independent_effect_rejects_degenerate_and_zero_variance() -> None:
    common = {
        "validation_delta": -0.01,
        "holdout_delta": -0.01,
        "raw_p_value": 0.01,
        "adjusted_p_value": 0.02,
    }
    assert (
        _independent_effect_label(
            "schedule_missingness", unique_values=2, **common
        )
        == "REJECT_DEGENERATE"
    )
    assert (
        _independent_effect_label("back_to_back", unique_values=1, **common)
        == "NO_VARIANCE"
    )
