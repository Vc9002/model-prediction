import json

import pytest

from model_prediction.models.learned_market import (
    LearnedMarketArtifact,
    artifact_hash,
    build_artifact,
    learn_confidence_threshold,
)


def _artifact() -> dict:
    return build_artifact(
        sport="mlb",
        model_version="mlb-learned-market-v1",
        market_models={
            "moneyline": {
                "feature_names": ["raw_probability", "trend_gap"],
                "coefficients": [5.0, 0.0],
                "intercept": -2.5,
                "confidence_threshold": 0.65,
            }
        },
        training={"timestamp_valid_prices": False},
        qualification={"qualified": True, "calls": 50, "hit_rate": 0.66},
    )


def test_artifact_hash_and_probability() -> None:
    model = LearnedMarketArtifact(_artifact())
    assert model.probability("moneyline", {"raw_probability": 0.7, "trend_gap": 1.0}) > 0.7
    assert model.qualified is True


def test_artifact_rejects_tampering(tmp_path) -> None:
    raw = _artifact()
    raw["market_models"]["moneyline"]["intercept"] = 99
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="hash mismatch"):
        LearnedMarketArtifact.load(path)


def test_gate_uses_confidence_even_when_price_ev_is_negative() -> None:
    model = LearnedMarketArtifact(_artifact())
    called = model.decide(
        "moneyline",
        {
            "home": {
                "features": {"raw_probability": 0.8, "trend_gap": 0.0},
                "market_probability": 0.95,
                "decimal_odds": 1.05,
            },
            "away": {
                "features": {"raw_probability": 0.2, "trend_gap": 0.0},
                "market_probability": 0.40,
                "decimal_odds": 2.5,
            },
        },
        minimum_edge=0.02,
    )
    assert called.call is True
    assert called.selection == "home"
    assert called.edge < 0
    assert called.expected_value < 0
    assert called.reason == "CALL_LEARNED_CONFIDENCE"


def test_gate_does_not_require_market_prices() -> None:
    model = LearnedMarketArtifact(_artifact())
    called = model.decide(
        "moneyline",
        {
            "home": {"features": {"raw_probability": 0.8, "trend_gap": 0.0}},
            "away": {"features": {"raw_probability": 0.2, "trend_gap": 0.0}},
        },
    )
    assert called.call is True
    assert called.market_probability is None
    assert called.expected_value is None


def test_binary_gate_uses_complement_for_negative_class() -> None:
    model = LearnedMarketArtifact(_artifact())
    called = model.decide_binary(
        "moneyline",
        {"raw_probability": 0.1, "trend_gap": 0.0},
    )
    assert called.call is True
    assert called.selection == "away"
    assert called.probability > 0.8


def test_threshold_is_learned_from_observed_confidences() -> None:
    probabilities = [0.9] * 40 + [0.8] * 20 + [0.55] * 40
    outcomes = [1] * 40 + [1] * 10 + [0] * 10 + [0] * 40
    threshold, stats = learn_confidence_threshold(
        probabilities,
        outcomes,
        target_hit_rate=0.65,
        minimum_calls=50,
    )
    assert threshold == 0.8
    assert stats["validation_calls"] == 60
    assert stats["validation_hit_rate"] > 0.65


def test_artifact_hash_ignores_only_hash_field() -> None:
    raw = _artifact()
    assert artifact_hash(raw) == raw["artifact_hash"]


def _temperature_artifact() -> dict:
    raw = _artifact()
    raw["market_models"]["moneyline"]["calibration"] = {
        "method": "temperature",
        "temperature": 0.8,
    }
    raw["artifact_hash"] = artifact_hash(raw)
    return raw


def test_temperature_calibration_sharpens_served_probability() -> None:
    model = LearnedMarketArtifact(_temperature_artifact())
    raw_p = LearnedMarketArtifact(_artifact()).probability(
        "moneyline", {"raw_probability": 0.7, "trend_gap": 1.0}
    )
    calibrated = model.probability("moneyline", {"raw_probability": 0.7, "trend_gap": 1.0})
    if raw_p > 0.5:
        assert calibrated > raw_p  # T<1 sharpens
    else:
        assert calibrated < raw_p
    assert 0.0 < calibrated < 1.0


def test_unknown_calibration_method_fails_closed() -> None:
    raw = _artifact()
    raw["market_models"]["moneyline"]["calibration"] = {
        "method": "nonexistent",
        "temperature": 0.8,
    }
    raw["artifact_hash"] = artifact_hash(raw)
    model = LearnedMarketArtifact(raw)
    try:
        model.probability("moneyline", {"raw_probability": 0.7, "trend_gap": 1.0})
    except ValueError as error:
        assert "nonexistent" in str(error)
    else:
        raise AssertionError("unknown calibration method must fail closed")


def test_invalid_temperature_fails_closed() -> None:
    raw = _artifact()
    raw["market_models"]["moneyline"]["calibration"] = {
        "method": "temperature",
        "temperature": 0.0,
    }
    raw["artifact_hash"] = artifact_hash(raw)
    model = LearnedMarketArtifact(raw)
    try:
        model.probability("moneyline", {"raw_probability": 0.7, "trend_gap": 1.0})
    except ValueError as error:
        assert "temperature" in str(error)
    else:
        raise AssertionError("non-positive temperature must fail closed")
