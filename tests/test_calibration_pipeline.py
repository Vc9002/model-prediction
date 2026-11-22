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


def _ncaab_row(
    model: str, engine: str | None, *, raw: float, served: float, result: str, market: str = "spread"
):
    features = {"history_games": 20} if engine is None else {"margin_engine": engine}
    return {
        "sport": "ncaab",
        "market_type": market,
        "model_version": model,
        "status": "settled",
        "result": result,
        "model_probability": served,
        "model_probability_raw": raw,
        "model_input_snapshot_json": json.dumps({"features": features}),
    }


class _FakeLedgers:
    def __init__(self, rows):
        self._ledgers = {"ncaab": type("L", (), {"rows": lambda _self: list(rows)})()}


def _patch_ledgers(monkeypatch, rows, flat_rows=()):
    monkeypatch.setattr(
        "model_prediction.calibration_pipeline.MultiSportPickLedger",
        lambda root, flat=False: _FakeLedgers(flat_rows if flat else rows),
    )


def test_ncaab_pools_are_engine_pure(monkeypatch, tmp_path: Path):
    from model_prediction.calibration_pipeline import extract_settled_model_predictions

    rows = [
        _ncaab_row("ncaab-spread-v6", "v6", raw=0.61, served=0.61, result="win"),
        _ncaab_row("ncaab-spread-v6", None, raw=0.62, served=0.62, result="loss"),  # pre-11-16: v6 engine
        _ncaab_row("ncaab-spread-v6", "v7", raw=0.63, served=0.63, result="win"),
        _ncaab_row("ncaab-spread-v6", "v7", raw=0.64, served=0.64, result="loss"),
        _ncaab_row("ncaab-spread-v7", "v7", raw=0.65, served=0.65, result="win"),
        {
            **_ncaab_row("ncaab-spread-v6", "v6", raw=0.66, served=0.66, result="win"),
            "model_input_snapshot_json": "{bad",
        },
    ]
    _patch_ledgers(monkeypatch, rows)
    grouped = extract_settled_model_predictions(data_root=tmp_path)
    assert grouped[("ncaab", "spread", "ncaab-spread-v6", "main")][0] == [0.61, 0.62, 0.66]
    assert grouped[("ncaab", "spread", "ncaab-spread-v7", "main")][0] == [0.63, 0.64, 0.65]


def test_ncaab_moneyline_v7m_pool_is_its_own_rows_only(monkeypatch, tmp_path: Path):
    """v7's moneyline rows are the Elo/margin blend; v7m's are margin-only. They must never pool."""
    from model_prediction.calibration_pipeline import (
        CALIBRATION_POOL_SOURCES,
        extract_settled_model_predictions,
        serving_guard_reason,
    )

    ml = {"market": "moneyline"}
    rows = [
        _ncaab_row("ncaab-spread-v7", "v7", raw=0.61, served=0.61, result="win", **ml),
        _ncaab_row("ncaab-spread-v6", "v7", raw=0.62, served=0.62, result="loss", **ml),
        _ncaab_row("ncaab-moneyline-v7m", "v7", raw=0.63, served=0.63, result="win", **ml),
    ]
    _patch_ledgers(monkeypatch, rows)
    grouped = extract_settled_model_predictions(data_root=tmp_path)
    assert grouped[("ncaab", "moneyline", "ncaab-moneyline-v7m", "main")][0] == [0.63]
    assert grouped[("ncaab", "moneyline", "ncaab-spread-v7", "main")][0] == [0.61, 0.62]
    assert "ncaab-moneyline-v7m" not in CALIBRATION_POOL_SOURCES

    base = {
        "market": "moneyline",
        "model_version": "ncaab-moneyline-v7m",
        "champion_method": "beta",
        "sample_size": 300,
        "baseline_brier": 0.25,
        "calibrated_oof_brier": 0.24,
        "fit_source": "settled_live_rows",
    }

    def reason(fit):
        return serving_guard_reason({**base, "fit_model_versions": fit}, "ncaab-moneyline-v7m", "moneyline")

    assert reason(["ncaab-moneyline-v7m"]) is None
    assert reason(["ncaab-moneyline-v7m", "ncaab-spread-v7"]) == "foreign_fit_provenance"
    assert reason(["ncaab-spread-v7"]) == "foreign_fit_provenance"


def test_ncaab_calibrators_fit_on_raw_not_served_probability(monkeypatch, tmp_path: Path):
    from model_prediction.calibration_pipeline import extract_settled_model_predictions

    other = {**_ncaab_row("nba-x", None, raw=0.70, served=0.60, result="win"), "sport": "nba"}
    _patch_ledgers(
        monkeypatch, [_ncaab_row("ncaab-spread-v6", "v6", raw=0.70, served=0.60, result="win"), other]
    )
    grouped = extract_settled_model_predictions(data_root=tmp_path)
    assert grouped[("ncaab", "spread", "ncaab-spread-v6", "main")][0] == [0.70]
    # Other sports keep their existing field order (model_probability first).
    assert grouped[("nba", "spread", "nba-x", "main")][0] == [0.60]


def test_pooled_v7_artifact_declares_its_pool_and_passes_the_guard(monkeypatch, tmp_path: Path):
    from model_prediction.calibration_pipeline import serving_guard_reason

    rng = np.random.default_rng(7)
    rows = []
    for i in range(300):
        p = float(rng.uniform(0.1, 0.9))
        won = rng.uniform() < 0.5 + (p - 0.5) * 0.5  # overconfident by 2x: shrinking toward 50% wins
        model = "ncaab-spread-v7" if i % 3 == 0 else "ncaab-spread-v6"
        rows.append(_ncaab_row(model, "v7", raw=round(p, 4), served=0.5, result="win" if won else "loss"))
    _patch_ledgers(monkeypatch, rows)
    run_full_calibration_pipeline(
        data_root=tmp_path / "data",
        output_dir=tmp_path / "out",
        sport_filter="ncaab",
        update_artifacts=True,
    )
    path = tmp_path / "data" / "calibration" / "models" / "ncaab-spread-v7.spread_calibrator.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["fit_model_versions"] == ["ncaab-spread-v7", "ncaab-spread-v6"]
    assert "engine-pure" in payload["fit_pool"]
    assert payload["sample_size"] == 300
    assert payload["champion_method"] != "identity"
    assert serving_guard_reason(payload, "ncaab-spread-v7", "spread") is None
    # Only all v7-engine rows were given, so no v6 artifact is written.
    assert not (path.parent / "ncaab-spread-v6.spread_calibrator.json").exists()


def test_serving_guard_accepts_only_the_declared_pool():
    from model_prediction.calibration_pipeline import serving_guard_reason

    base = {
        "market": "spread",
        "champion_method": "beta",
        "sample_size": 300,
        "baseline_brier": 0.25,
        "calibrated_oof_brier": 0.24,
        "fit_source": "settled_live_rows",
    }

    def reason(model, fit):
        return serving_guard_reason(
            {**base, "model_version": model, "fit_model_versions": fit}, model, "spread"
        )

    assert reason("ncaab-spread-v7", ["ncaab-spread-v7", "ncaab-spread-v6"]) is None
    assert reason("ncaab-spread-v7", ["ncaab-spread-v7"]) is None
    assert reason("ncaab-spread-v7", ["ncaab-spread-v6"]) == "foreign_fit_provenance"
    assert reason("ncaab-spread-v7", ["ncaab-spread-v7", "ncaab-spread-v5"]) == "foreign_fit_provenance"
    assert reason("ncaab-spread-v6", ["ncaab-spread-v6", "ncaab-spread-v7"]) == "foreign_fit_provenance"
    assert (
        reason("nba-total-margin-v9", ["nba-total-margin-v9", "nba-total-margin-v7"])
        == "foreign_fit_provenance"
    )
