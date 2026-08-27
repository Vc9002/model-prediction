from pathlib import Path

from model_prediction.calibration import FixedPlattCalibrator, IdentityCalibrator, calibration_metrics


def test_identity_calibrator_is_versioned() -> None:
    calibrator = IdentityCalibrator("model-v1")
    assert calibrator.transform(0.63) == 0.63
    assert calibrator.metadata.calibration_method == "identity"
    assert len(calibrator.metadata.artifact_hash) == 64


def test_metrics_refuse_small_sample_and_report_full_contract() -> None:
    assert calibration_metrics([0.5] * 5, [0, 1, 0, 1, 1])["status"] == "insufficient_sample"
    probabilities = [0.1 + 0.8 * (index / 39) for index in range(40)]
    outcomes = [int(probability >= 0.5) for probability in probabilities]
    metrics = calibration_metrics(probabilities, outcomes)
    assert metrics["status"] == "ok"
    assert {
        "brier_score",
        "log_loss",
        "calibration_intercept",
        "calibration_slope",
        "expected_calibration_error",
        "reliability_buckets",
    } <= metrics.keys()


def test_versioned_mlb_platt_calibrator_is_hash_verified_and_shrinks_overconfidence() -> None:
    path = Path(__file__).parents[1] / "config/models/archive/mlb-v0.2-platt-2026-07-07-to-10-v1.json"
    calibrator = FixedPlattCalibrator(path)
    assert calibrator.metadata.sample_size == 115
    assert calibrator.metadata.base_model_version == "mlb-analyst-poisson-trend-v0.2"
    assert calibrator.transform(0.70) < 0.70


def test_beta_calibrator_pulls_overconfidence_down():
    from model_prediction.calibration import BetaCalibrator, IdentityCalibrator

    # A model that says 0.9 but wins 60% of the time: beta calibration
    # must bend 0.9 toward the observed rate.
    probs = [0.9] * 60 + [0.1] * 40
    outcomes = [1] * 36 + [0] * 24 + [1] * 4 + [0] * 36  # 0.6 rate at 0.9, 0.1 rate at 0.1
    cal = BetaCalibrator.fit(probs, outcomes, base_model_version="t", minimum_sample=50)
    assert not isinstance(cal, IdentityCalibrator)
    assert cal.transform(0.9) < 0.9
    assert cal.transform(0.1) > 0.1
    # Monotone in probability.
    assert cal.transform(0.5) < cal.transform(0.8)


def test_beta_calibrator_identity_fallback_below_minimum_sample():
    from model_prediction.calibration import BetaCalibrator, IdentityCalibrator

    cal = BetaCalibrator.fit([0.7, 0.6], [1, 0], base_model_version="t")
    assert isinstance(cal, IdentityCalibrator)
