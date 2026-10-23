"""Serving-time calibration: artifact selection, the trust guard, and the auto-buyer."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from model_prediction.calibration_pipeline import (
    MIN_SERVING_SAMPLE,
    calibrate_for_serving,
    run_full_calibration_pipeline,
)
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio.auto_executor import AutoExecutionConfig, AutoPolymarketBuyer

PLATT_HALF_SLOPE = {"intercept": 0.0, "slope": 0.5}  # pulls 0.54 down to ~0.52


def _write_artifact(root: Path, model: str = "m-v1", market: str = "moneyline", **overrides) -> None:
    payload = {
        "sport": "tennis",
        "market": market,
        "model_version": model,
        "champion_method": "platt",
        "parameters": PLATT_HALF_SLOPE,
        "sample_size": 150,
        "baseline_brier": 0.2400,
        "calibrated_oof_brier": 0.2350,
        "calibrated_at_utc": "2026-10-21T00:00:00Z",
    } | overrides
    directory = root / "calibration" / "models"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{model}.{market}_calibrator.json").write_text(json.dumps(payload), encoding="utf-8")


def test_calibrate_for_serving_applies_a_trustworthy_calibrator(tmp_path):
    _write_artifact(tmp_path)
    result = calibrate_for_serving("m-v1", "moneyline", 0.54, data_root=tmp_path)
    assert result.applied and result.reason == "applied" and result.method == "platt-recalibrated-v1"
    assert result.raw_probability == 0.54
    assert result.probability == pytest.approx(0.52, abs=0.002)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"sample_size": MIN_SERVING_SAMPLE - 1}, "sample_too_small"),
        ({"calibrated_oof_brier": 0.2395}, "no_oof_improvement"),  # worse-or-equal-ish: below the 0.001 bar
        ({"calibrated_oof_brier": 0.2600}, "no_oof_improvement"),  # champion worse than raw model
        ({"calibrated_oof_brier": None}, "no_oof_improvement"),
        ({"champion_method": "identity"}, "identity_champion"),
    ],
)
def test_calibrate_for_serving_leaves_untrusted_calibrators_unapplied(tmp_path, overrides, reason):
    _write_artifact(tmp_path, **overrides)
    result = calibrate_for_serving("m-v1", "moneyline", 0.54, data_root=tmp_path)
    assert not result.applied and result.reason == reason
    assert result.probability == 0.54 and result.method == "identity"


def test_calibrate_for_serving_rejects_an_artifact_whose_payload_disagrees_with_its_filename(tmp_path):
    _write_artifact(tmp_path)
    path = tmp_path / "calibration" / "models" / "m-v1.moneyline_calibrator.json"
    path.write_text(
        json.dumps({**json.loads(path.read_text(encoding="utf-8")), "market": "spread"}), encoding="utf-8"
    )
    result = calibrate_for_serving("m-v1", "moneyline", 0.54, data_root=tmp_path)
    assert not result.applied and result.reason == "artifact_mismatch"


def test_calibrate_for_serving_never_raises_on_missing_or_corrupt_artifacts(tmp_path):
    assert calibrate_for_serving("m-v1", "moneyline", 0.6, data_root=tmp_path).reason == "no_artifact"
    path = tmp_path / "calibration" / "models"
    path.mkdir(parents=True)
    (path / "m-v1.moneyline_calibrator.json").write_text("{not json", encoding="utf-8")
    assert calibrate_for_serving("m-v1", "moneyline", 0.6, data_root=tmp_path).reason == "unreadable_artifact"


def test_calibrate_for_serving_is_keyed_by_market(tmp_path):
    _write_artifact(tmp_path, market="spread")
    assert calibrate_for_serving("m-v1", "moneyline", 0.54, data_root=tmp_path).reason == "no_artifact"
    assert calibrate_for_serving("m-v1", "spread", 0.54, data_root=tmp_path).applied


def test_calibrate_for_serving_clamps_extreme_outputs(tmp_path):
    _write_artifact(tmp_path, parameters={"intercept": 0.0, "slope": 30.0})
    assert calibrate_for_serving("m-v1", "moneyline", 0.99, data_root=tmp_path).probability == 0.99
    assert calibrate_for_serving("m-v1", "moneyline", 0.01, data_root=tmp_path).probability == 0.01


def _overconfident_group(n: int, seed: int) -> tuple[list[float], list[int]]:
    import random

    rng = random.Random(seed)
    probabilities = [rng.uniform(0.3, 0.8) for _ in range(n)]
    # True win rate is only half as far from 0.5 as the model claims.
    outcomes = [1 if rng.random() < 0.5 + 0.5 * (p - 0.5) else 0 for p in probabilities]
    return probabilities, outcomes


def test_pipeline_writes_one_artifact_per_model_and_market_with_largest_sample(tmp_path, monkeypatch):
    groups = {
        ("tennis", "moneyline", "m-v1", "main"): _overconfident_group(240, 1),
        ("tennis", "moneyline", "m-v1", "gated"): _overconfident_group(40, 2),
        ("tennis", "total", "m-v1", "main"): _overconfident_group(60, 3),
    }
    monkeypatch.setattr(
        "model_prediction.calibration_pipeline.extract_settled_model_predictions", lambda *a, **k: groups
    )
    run_full_calibration_pipeline(
        data_root=tmp_path, output_dir=tmp_path / "out", min_sample=20, update_artifacts=True
    )
    models = tmp_path / "calibration" / "models"
    assert sorted(p.name for p in models.glob("*.json")) == [
        "m-v1.moneyline_calibrator.json",
        "m-v1.total_calibrator.json",
    ]
    moneyline = json.loads((models / "m-v1.moneyline_calibrator.json").read_text(encoding="utf-8"))
    assert moneyline["sample_size"] == 240 and moneyline["market"] == "moneyline"
    assert (
        json.loads((models / "m-v1.total_calibrator.json").read_text(encoding="utf-8"))["sample_size"] == 60
    )


def _buyer_decision(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_executor._today_auto_buyer_totals", lambda *_a, **_k: (0.0, 0.0)
    )
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(whitelisted_models=("m-v1",), min_edge=0.035, max_edge=0.20),
        live_quote_fn=lambda slug: {"ask": 0.50, "market_slug": slug, "side": "long"},
        data_root=tmp_path,
    )
    pick = {
        "pick_id": "p1",
        "model_id": "m-v1",
        "status": "open",
        "market_type": "moneyline",
        "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
        "model_probability": 0.54,
        "market_probability": 0.50,
    }
    return buyer.evaluate_and_execute([pick])


def test_buyer_decides_on_the_calibrated_probability_and_records_both(tmp_path, monkeypatch):
    # Raw edge 0.04 clears min_edge 0.035; the calibrated 0.52 (edge 0.02) does not.
    assert len(_buyer_decision(tmp_path, monkeypatch).dry_run_orders) == 1
    _write_artifact(tmp_path)
    res = _buyer_decision(tmp_path, monkeypatch)
    assert res.rejected_low_edge == 1 and not res.dry_run_orders


def test_buyer_order_carries_raw_and_calibrated_probability(tmp_path, monkeypatch):
    _write_artifact(tmp_path, parameters={"intercept": 0.0, "slope": 1.0}, calibrated_oof_brier=0.2300)
    res = _buyer_decision(tmp_path, monkeypatch)
    order = res.dry_run_orders[0]
    assert order["model_probability_raw"] == 0.54
    assert order["calibration_method"] == "platt-recalibrated-v1"
    assert order["model_probability"] == pytest.approx(0.54, abs=0.002)
