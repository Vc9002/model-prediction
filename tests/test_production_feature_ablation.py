import json
from pathlib import Path

import pytest

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


def test_current_cs2_artifact_fails_closed_on_source_data_drift(monkeypatch, tmp_path: Path) -> None:
    """The current CS2 artifact fails closed when its source data drifts.

    K split (2026-08-15): the live matches.jsonl tracks the ROLLING
    artifact under the runtime root — the frozen config/models copy is
    the promoted snapshot and intentionally lags live data, so the
    previous "frozen artifact in sync with live source" assertion is no
    longer the invariant. The fail-closed drift guard itself still must
    raise, not degrade silently.
    """
    config = load_config()
    artifact_path = Path(config["models"]["CS2"]["production_artifact"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    # Integrity of the shipped artifact itself.
    assert artifact["artifact_hash"] == artifact_hash(artifact)

    # Fail-closed on drift: replay the real artifact in a tmp tree whose
    # matches.jsonl differs from the pinned hash -> must raise.
    (tmp_path / "config" / "models").mkdir(parents=True)
    (tmp_path / "config" / "models" / "cs2-tiered-elo-v6.json").write_text(
        artifact_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "data" / "esports" / "cs2").mkdir(parents=True)
    (tmp_path / "data" / "esports" / "cs2" / "matches.jsonl").write_text(
        "drifted source data\n", encoding="utf-8"
    )
    test_config = {
        "models": {
            "CS2": {
                **config["models"]["CS2"],
                "production_artifact": "config/models/cs2-tiered-elo-v6.json",
            }
        }
    }
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="cs2 match data drifted from production artifact"):
        _esports_model(test_config, "cs2")


def test_esports_artifact_resolution_prefers_rolling_when_present(monkeypatch, tmp_path: Path) -> None:
    """K split: the ablation resolves the rolling runtime artifact when
    one exists and falls back to the configured frozen copy otherwise."""
    from model_prediction.production_feature_ablation import _resolve_esports_artifact
    from model_prediction.runtime_paths import rolling_models_root

    configured = tmp_path / "config" / "models" / "cs2-tiered-elo-v6.json"
    configured.parent.mkdir(parents=True)
    configured.write_text("{}", encoding="utf-8")

    # No rolling copy -> configured frozen artifact.
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    assert _resolve_esports_artifact(configured) == configured

    # Rolling copy exists -> it wins.
    rolling = rolling_models_root()
    rolling.mkdir(parents=True, exist_ok=True)
    rolling_copy = rolling / configured.name
    rolling_copy.write_text("{}", encoding="utf-8")
    assert _resolve_esports_artifact(configured) == rolling_copy
