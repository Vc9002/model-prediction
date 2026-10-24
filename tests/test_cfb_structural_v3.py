"""Tests for College Football Structural v3 Model (cfb-structural-v3)."""

from __future__ import annotations

import json

import yaml

from model_prediction.config import PROJECT_ROOT
from model_prediction.models.cfb_structural_v3 import (
    CFB_BLOWOUT_SPREAD_THRESHOLD,
    CFB_V3_MODEL_VERSION,
    CFBStructuralV3Model,
    cfb_structural_v3_model,
)
from model_prediction.models.college_football import UpcomingCFBGame
from model_prediction.production_registry import ProductionModelRegistry


def test_cfb_structural_v3_model_instantiation():
    model = CFBStructuralV3Model()
    assert model.version == CFB_V3_MODEL_VERSION
    assert model.home_advantage_points == 2.5
    assert model.margin_sd == 14.8
    assert model.total_sd == 15.5
    assert cfb_structural_v3_model.version == CFB_V3_MODEL_VERSION


def test_cfb_structural_v3_blowout_margin_expansion():
    model = CFBStructuralV3Model()
    normal_sd = model.compute_effective_margin_sd(spread_home_line=-7.5, uncertainty=0.04)
    blowout_sd = model.compute_effective_margin_sd(spread_home_line=-31.5, uncertainty=0.04)
    assert normal_sd == 14.8
    assert blowout_sd > normal_sd
    # Excess spread is 31.5 - 21.5 = 10.0 pts -> 1.0 + 0.025 * 10 = 1.25x
    assert round(blowout_sd, 1) == round(14.8 * (1.0 + 0.025 * (31.5 - CFB_BLOWOUT_SPREAD_THRESHOLD)), 1)


def test_cfb_structural_v3_forecast_coherence():
    model = CFBStructuralV3Model()
    game = UpcomingCFBGame(
        event_id="cfb-test-1",
        event_start_utc="2026-10-24T19:00:00Z",
        away_team="Pittsburgh Panthers",
        home_team="Miami Hurricanes",
        spread_home_line=-19.5,
        total_line=55.5,
    )
    preds = model.predict_matchup(history=[], game=game)

    assert len(preds) == 3
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    sp_pred = next(p for p in preds if p.market_type == "spread")
    tot_pred = next(p for p in preds if p.market_type == "total")

    assert ml_pred.model_version == CFB_V3_MODEL_VERSION
    assert sp_pred.model_version == CFB_V3_MODEL_VERSION
    assert tot_pred.model_version == CFB_V3_MODEL_VERSION

    assert abs(sum(ml_pred.probabilities.values()) - 1.0) < 0.01
    assert abs(sum(sp_pred.probabilities.values()) - 1.0) < 0.01
    assert abs(sum(tot_pred.probabilities.values()) - 1.0) < 0.01


def test_cfb_structural_v3_artifact_and_registry_wiring():
    artifact_path = PROJECT_ROOT / "config/models/cfb-structural-v3.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["model_version"] == CFB_V3_MODEL_VERSION
    assert artifact["margin_sd"] == 14.8
    assert artifact["total_sd"] == 15.5

    prod_cfg = yaml.safe_load((PROJECT_ROOT / "config/production.yaml").read_text(encoding="utf-8"))
    reg = ProductionModelRegistry.from_config(prod_cfg, repo_root=PROJECT_ROOT)
    for market in ["moneyline", "spread", "total"]:
        champ = reg.champion("NCAAF", market)
        assert champ is not None
        assert champ.model_id == CFB_V3_MODEL_VERSION
