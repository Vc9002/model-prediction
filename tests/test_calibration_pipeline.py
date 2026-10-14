"""Unit tests for the Automated Model Calibration Pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from model_prediction.calibration_pipeline import (
    evaluate_calibration_for_group,
    run_full_calibration_pipeline,
)


def test_evaluate_calibration_insufficient_sample():
    rec = evaluate_calibration_for_group(
        sport="mlb",
        market="moneyline",
        model_version="test-model-v1",
        tier="main",
        probabilities=[0.6, 0.4, 0.5],
        outcomes=[1, 0, 1],
        min_sample=10,
    )
    assert rec is None


def test_evaluate_calibration_healthy_model():
    np.random.seed(42)
    # Well-calibrated data with sufficient sample to test healthy calibration
    probs = np.random.uniform(0.1, 0.9, 500).tolist()
    outcomes = [1 if np.random.rand() < p else 0 for p in probs]

    rec = evaluate_calibration_for_group(
        sport="tennis",
        market="moneyline",
        model_version="tennis-elo-v1",
        tier="main",
        probabilities=probs,
        outcomes=outcomes,
        min_sample=50,
    )
    assert rec is not None
    assert rec.sample_size == 500
    assert 0.0 <= rec.brier_score <= 0.35
    assert rec.drift_status == "HEALTHY"
    assert rec.champion_calibrator_method in {"identity", "temperature", "platt", "beta", "isotonic"}
    assert isinstance(rec.reliability_buckets, list)
    assert len(rec.reliability_buckets) > 0


def test_evaluate_calibration_drift_detection():
    # Severely overconfident model: predicts 0.95 or 0.05, but reality is 50/50
    probs = [0.95] * 50 + [0.05] * 50
    outcomes = [1, 0] * 50

    rec = evaluate_calibration_for_group(
        sport="nfl",
        market="spread",
        model_version="overconfident-v1",
        tier="main",
        probabilities=probs,
        outcomes=outcomes,
        min_sample=20,
    )
    assert rec is not None
    # Slope will be near 0, far from 1.0 -> SEVERE_DRIFT
    assert rec.drift_status == "SEVERE_DRIFT"
    assert rec.drift_score > 0.60


def test_run_full_calibration_pipeline_end_to_end(tmp_path: Path):
    data_root = tmp_path / "data"
    output_dir = tmp_path / "outputs" / "calibration"
    data_root.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    # Run pipeline on empty/minimal data root
    report = run_full_calibration_pipeline(
        data_root=data_root,
        output_dir=output_dir,
        min_sample=10,
        update_artifacts=True,
    )

    assert "generated_at_utc" in report
    assert "system_status" in report
    assert (output_dir / "latest.json").exists()
    assert (output_dir / "calibration_audit.json").exists()
    assert (data_root / "calibration" / "calibration_health.json").exists()

    # Health file parseable
    health = json.loads((data_root / "calibration" / "calibration_health.json").read_text(encoding="utf-8"))
    assert "status" in health
    assert "last_checked_utc" in health
    assert "calibration_drift" in health


def test_cmd_calibrate_cli(capsys):
    from model_prediction.cli.main import main

    main(["calibrate", "--all", "--min-sample", "100"])
    captured = capsys.readouterr()
    assert "system_status" in captured.out
    assert "total_evaluated_models" in captured.out
