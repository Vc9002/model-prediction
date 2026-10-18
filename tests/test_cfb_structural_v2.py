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


def test_cfb_structural_v2_calibrated_dispersion():
    model = CFBStructuralV2Model()
    assert model.total_sd == 17.5
    assert model.distribution_engine.score_correlation == 0.20


def test_cfb_total_empirical_gates():
    # Helper simulating the forecast.py total empirical gates
    def is_eligible_cfb_total(edge: float, prob: float, selection: str, line: float) -> bool:
        if edge > 0.10 or prob > 0.62:
            return False
        return not (selection.lower() == "under" and line >= 56.5)

    # 1. Edge > 0.10 rejected (overconfidence / consensus sharper)
    assert not is_eligible_cfb_total(edge=0.12, prob=0.60, selection="over", line=52.0)
    # 2. Probability > 0.62 rejected
    assert not is_eligible_cfb_total(edge=0.08, prob=0.65, selection="over", line=52.0)
    # 3. High Under (line >= 56.5) rejected (shootout garbage time risk)
    assert not is_eligible_cfb_total(edge=0.06, prob=0.58, selection="under", line=57.0)
    # 4. Valid Under clears
    assert is_eligible_cfb_total(edge=0.06, prob=0.58, selection="under", line=51.5)
    # 5. Valid Over clears
    assert is_eligible_cfb_total(edge=0.06, prob=0.58, selection="over", line=62.5)
