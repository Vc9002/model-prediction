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


def test_beta_calibrator_identity_property():
    from model_prediction.calibration import BetaCalibrator, CalibrationMetadata

    meta = CalibrationMetadata("beta", "v1", "base_v1", None, None, 100, "hash123")
    cal = BetaCalibrator(a=1.0, b=1.0, c=0.0, metadata=meta)
    for p in [0.05, 0.2, 0.5, 0.75, 0.95]:
        assert abs(cal.transform(p) - p) < 1e-9


def test_beta_calibrator_pulls_overconfidence_down():
    from model_prediction.calibration import BetaCalibrator, IdentityCalibrator

    # A model that says 0.9 (wins 60%) and 0.1 (wins 25%): beta calibration
    # must bend both 0.9 and 0.1 toward their observed rates.
    probs = [0.9] * 60 + [0.1] * 40
    outcomes = [1] * 36 + [0] * 24 + [1] * 10 + [0] * 30  # 0.60 rate at 0.9, 0.25 rate at 0.1
    cal = BetaCalibrator.fit(probs, outcomes, base_model_version="t", minimum_sample=50)
    assert not isinstance(cal, IdentityCalibrator)
    assert hasattr(cal, "a") and hasattr(cal, "b") and hasattr(cal, "c")
    assert cal.transform(0.9) < 0.9
    assert cal.transform(0.1) > 0.1
    # Monotone in probability.
    assert cal.transform(0.5) < cal.transform(0.8)


def test_beta_calibrator_identity_fallback_below_minimum_sample():
    from model_prediction.calibration import BetaCalibrator, IdentityCalibrator

    cal = BetaCalibrator.fit([0.7, 0.6], [1, 0], base_model_version="t")
    assert isinstance(cal, IdentityCalibrator)


def test_calibration_tournament_runs_and_picks_champion():
    import numpy as np

    from model_prediction.calibration import run_calibration_tournament

    np.random.seed(42)
    probs = np.random.uniform(0.1, 0.9, 150).tolist()
    # Overconfident true probabilities
    outcomes = [1 if np.random.rand() < (0.5 + 0.4 * (p - 0.5)) else 0 for p in probs]

    res = run_calibration_tournament(probs, outcomes, base_model_version="test_m", n_splits=3)
    assert res["status"] == "ok"
    assert res["champion_method"] in {"identity", "temperature", "platt", "beta", "isotonic"}
    assert res["champion_calibrator"] is not None
    assert len(res["scorecard"]) == 5
    for method in ["identity", "temperature", "platt", "beta", "isotonic"]:
        assert method in res["scorecard"]
        assert "oof_log_loss" in res["scorecard"][method]
        assert "oof_brier_score" in res["scorecard"][method]
        assert "oof_ece" in res["scorecard"][method]
