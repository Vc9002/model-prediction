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
    path = Path(__file__).parents[1] / "config/models/mlb-v0.2-platt-2026-07-07-to-10-v1.json"
    calibrator = FixedPlattCalibrator(path)
    assert calibrator.metadata.sample_size == 115
    assert calibrator.metadata.base_model_version == "mlb-analyst-poisson-trend-v0.2"
    assert calibrator.transform(0.70) < 0.70
