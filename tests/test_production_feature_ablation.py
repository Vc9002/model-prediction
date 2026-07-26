import json

from model_prediction.config import load_config
from model_prediction.models.learned_market import LearnedMarketArtifact, artifact_hash, build_artifact
from model_prediction.production_feature_ablation import (
    _decision,
    _esports_model,
    _frozen_score_split,
    _holm,
    _probability_metrics,
    _reproduction_gate,
)
from model_prediction.validation import ValidationRow


def test_frozen_split_ignores_new_rows_after_declared_holdout() -> None:
    rows = [
        ValidationRow(day, day, 1, 0.5, 0, 1, 1, False, False)
        for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")
    ]
    training = {
        "coefficient_fit": {"start": "2026-01-01", "end": "2026-01-01", "observations": 1},
        "threshold_selection": {"start": "2026-01-02", "end": "2026-01-02", "observations": 1},
        "locked_holdout": {"start": "2026-01-03", "end": "2026-01-03", "observations": 1},
    }
    train, validation, holdout, _ = _frozen_score_split(rows, training)
    assert [len(train), len(validation), len(holdout)] == [1, 1, 1]


def test_probability_metrics_reports_requested_scores() -> None:
    result = _probability_metrics([0.8, 0.2], [1, 0])
    assert result["observations"] == 2
    assert result["accuracy"] == 1.0
    assert result["brier_score"] == 0.04


def test_holm_adjustment_is_monotone() -> None:
    assert _holm({"a": 0.01, "b": 0.02, "c": 0.5}) == {
        "a": 0.03,
        "b": 0.04,
        "c": 0.5,
    }


def test_provenance_blocker_forces_remove_candidate() -> None:
    status, _ = _decision(
        {
            "provenance": {"status": "blocked", "reason": "not point in time"},
            "validation_brier_delta": 0,
            "paired_uncertainty": {
                "candidate_minus_baseline": {
                    "brier_score": 0,
                    "brier_ci_95": [-1, 1],
                    "log_loss": 0,
                }
            },
        },
        1.0,
    )
    assert status == "REMOVE CANDIDATE"


def test_generic_artifact_hash_excludes_hash_field() -> None:
    payload = {"schema_version": "test"}
    payload["artifact_hash"] = artifact_hash(payload)
    assert payload["artifact_hash"] == artifact_hash(payload)


def test_reproduction_gate_requires_exact_calls_and_tight_coefficients() -> None:
    artifact = LearnedMarketArtifact(
        build_artifact(
            sport="test",
            model_version="test",
            market_models={
                "moneyline": {
                    "feature_names": ["elo_probability"],
                    "coefficients": [2.0],
                    "intercept": -1.0,
                    "confidence_threshold": 0.6,
                }
            },
            training={},
            qualification={
                "calls": 10,
                "hits": 7,
                "brier_score": 0.2,
                "calibration": {"log_loss": 0.6},
            },
        )
    )
    result = _reproduction_gate(
        artifact,
        {"coefficients": {"elo_probability": 2.0}, "intercept": -1.0},
        {"calls": 10, "hits": 7, "brier_score": 0.2, "log_loss": 0.6},
    )
    assert result["passed"] is True


def test_current_cs2_artifact_hash_passes_integrity_check() -> None:
    config = load_config()
    artifact_path = config["models"]["CS2"]["production_artifact"]
    artifact = json.loads(open(artifact_path, encoding="utf-8").read())

    result = _esports_model(config, "cs2")

    # Hash is valid — artifact integrity passes, status is not UNTESTABLE_ARTIFACT_INTEGRITY
    assert result["status"] != "UNTESTABLE_ARTIFACT_INTEGRITY"
    assert result["artifact_hash"] == artifact["artifact_hash"]
    assert result["artifact_hash"] == artifact_hash(artifact)
    assert "source_evidence" in result
