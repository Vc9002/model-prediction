import hashlib
import json

import pytest

from model_prediction.config import PROJECT_ROOT
from model_prediction.models.mlb import (
    MeasuredEdgeMarginModel,
    MeasuredEdgeTotalsModel,
    load_formula_spec,
)


def _write_margin_artifact(path, scale: float, offset: float):
    artifact = {
        "model_version": "measured-edge-margin-v3",
        "base_score_model_version": "mlb-analyst-poisson-trend-v0.3",
        "calibration_method": "flat_probability_shrinkage_toward_half",
        "scale": scale,
        "offset": offset,
        "trained_on_games": 1,
        "trained_through_date": "2026-01-01",
        "model_name": "Measured Edge Margin",
        "calibration_version": "measured-edge-margin-v3",
    }
    canonical = {k: v for k, v in artifact.items() if k != "artifact_hash"}
    artifact["artifact_hash"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(artifact), encoding="utf-8")


@pytest.fixture
def spec():
    return load_formula_spec(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml")


def test_heavy_shrinkage_calibration_is_allowed_when_correctly_centered(tmp_path, spec):
    """A real backtest can legitimately require scale far from 1 -- what
    matters is that the resulting calibration stays centered near 0.5 for a
    50/50 raw input, not that offset alone stays within some fixed range."""
    path = tmp_path / "measured-edge-margin-v3.json"
    _write_margin_artifact(path, scale=0.1007, offset=0.4667)
    model = MeasuredEdgeMarginModel(path, spec)
    assert 0.45 < model.calibrate_selected_side(0.5) < 0.55


def test_calibration_with_large_bias_at_half_is_rejected(tmp_path, spec):
    """A genuinely biased calibration (would return ~0.9 for a coin-flip raw
    input) must still be rejected regardless of scale."""
    path = tmp_path / "measured-edge-margin-v3.json"
    _write_margin_artifact(path, scale=1.0, offset=0.4)
    with pytest.raises(ValueError, match="too much bias"):
        MeasuredEdgeMarginModel(path, spec)


def test_scale_outside_positive_range_is_rejected(tmp_path, spec):
    path = tmp_path / "measured-edge-margin-v3.json"
    _write_margin_artifact(path, scale=-0.1, offset=0.5)
    with pytest.raises(ValueError, match="scale outside governance bounds"):
        MeasuredEdgeMarginModel(path, spec)


def test_production_margin_and_totals_artifacts_load_cleanly(spec):
    margin = MeasuredEdgeMarginModel(
        PROJECT_ROOT / "config/models/measured-edge-margin-v3.json", spec
    )
    totals = MeasuredEdgeTotalsModel(
        PROJECT_ROOT / "config/models/measured-edge-totals-v3.json", spec
    )
    assert 0.45 < margin.calibrate_selected_side(0.5) < 0.55
    assert 0.45 < totals.calibrate_selected_side(0.5) < 0.55
