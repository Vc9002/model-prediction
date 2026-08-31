"""Tests for College Football Structural v2 Model (cfb-structural-v2)."""

from model_prediction.models.cfb_structural_v2 import (
    CFB_V2_MODEL_VERSION,
    CFBStructuralV2Model,
)
from model_prediction.models.college_football import UpcomingCFBGame


def test_cfb_structural_v2_model_instantiation():
    model = CFBStructuralV2Model()
    assert model.version == CFB_V2_MODEL_VERSION
    assert model.home_advantage_points > 0


def test_cfb_structural_v2_forecast_coherence():
    model = CFBStructuralV2Model()
    game = UpcomingCFBGame(
        event_id="game-1",
        event_start_utc="2026-09-01T19:00:00Z",
        away_team="Alabama Crimson Tide",
        home_team="Georgia Bulldogs",
        spread_home_line=-6.5,
        total_line=55.5,
    )
    preds = model.predict_matchup(history=[], game=game)

    assert len(preds) == 3
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    sp_pred = next(p for p in preds if p.market_type == "spread")
    tot_pred = next(p for p in preds if p.market_type == "total")

    assert max(ml_pred.probabilities.values()) >= 0.50
    assert max(sp_pred.probabilities.values()) >= 0.50
    assert max(tot_pred.probabilities.values()) >= 0.50
