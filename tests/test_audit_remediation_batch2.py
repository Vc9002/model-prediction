"""Unit tests for Batch 2 Audit Remediations across model-prediction.

Verifies:
1. Soccer league model home advantage no-squaring fix.
2. NBA market models canonical v2 artifact harmonization & NBA_CONFIG alignment.
3. International baseball (KBO / NPB) MarketScopedModel adapter compliance.
4. Calibration pipeline expanding-window time-series split (zero future lookahead).
5. Esports Elo match format weighting (Bo1 vs Bo3 vs Bo5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from model_prediction.calibration import run_calibration_tournament
from model_prediction.esports import NeutralElo
from model_prediction.models.basketball_leagues import NBA_CONFIG
from model_prediction.models.market_models import (
    KBOMoneylineModel,
    KBOSpreadModel,
    NPBTotalModel,
    get_market_model,
)
from model_prediction.soccer.league_model import LeagueSoccerConfig, LeagueSoccerModel


def test_soccer_league_model_home_advantage_no_squaring():
    """Verify that LeagueSoccerModel does NOT square home advantage (HA ratio == cfg.home_advantage)."""
    cfg_standard = LeagueSoccerConfig(
        league_code="EPL",
        model_version="epl-v1",
        baseline=1.40,
        home_advantage=1.25,
        dc_rho=-0.05,
        divide_away=False,
    )
    model_standard = LeagueSoccerModel(cfg_standard)
    strengths = {
        "Arsenal": {"attack": 1.0, "defense": 1.0, "games": 10.0},
        "Chelsea": {"attack": 1.0, "defense": 1.0, "games": 10.0},
    }
    pred_standard = model_standard.predict_one(strengths, "Arsenal", "Chelsea")
    assert pytest.approx(pred_standard["home_rate"]) == 1.40 * 1.25
    assert pytest.approx(pred_standard["away_rate"]) == 1.40
    # Ratio must be exactly 1.25, NOT 1.25^2 = 1.5625
    assert pytest.approx(pred_standard["home_rate"] / pred_standard["away_rate"]) == 1.25

    # Test symmetric divide_away mode
    cfg_sym = LeagueSoccerConfig(
        league_code="EPL",
        model_version="epl-v1",
        baseline=1.40,
        home_advantage=1.25,
        dc_rho=-0.05,
        divide_away=True,
    )
    model_sym = LeagueSoccerModel(cfg_sym)
    pred_sym = model_sym.predict_one(strengths, "Arsenal", "Chelsea")
    assert pytest.approx(pred_sym["home_rate"] / pred_sym["away_rate"]) == 1.25


def test_nba_market_models_v2_canonical_resolution():
    """Verify NBA spread and total market models route to production v2 champion artifacts."""
    spread_mod = get_market_model("NBA", "spread")
    assert spread_mod.model_version == "nba-spread-margin-v2"

    total_mod = get_market_model("NBA", "total")
    assert total_mod.model_version == "nba-total-margin-v2"

    ml_mod = get_market_model("NBA", "moneyline")
    assert ml_mod.model_version == "nba-moneyline-elo-trend-lr-v4"

    # Verify NBA_CONFIG parameters match nba-spread-margin-v2 artifact
    assert NBA_CONFIG.model_version == "nba-spread-margin-v2"
    assert NBA_CONFIG.margin_sd == 14.6
    assert NBA_CONFIG.home_court_points == 1.8
    assert NBA_CONFIG.elo_weight == 0.50
    assert NBA_CONFIG.trend_weight == 0.50
    assert NBA_CONFIG.rest_weight == 0.30


def test_international_baseball_market_models_adapter():
    """Verify KBO/NPB MarketScopedModels can call predict_games without AttributeError."""

    @dataclass
    class UpcomingMatch:
        event_id: str
        event_start_utc: str
        home_team: str
        away_team: str

    matches = [
        UpcomingMatch(
            event_id="kbo_test_1",
            event_start_utc="2026-05-10T09:00:00Z",
            home_team="Doosan Bears",
            away_team="LG Twins",
        )
    ]

    # KBO Moneyline
    kbo_ml = KBOMoneylineModel()
    ml_preds = kbo_ml.predict_games([], matches)
    assert len(ml_preds) == 1
    assert ml_preds[0].market_type == "moneyline"
    assert "home" in ml_preds[0].probabilities
    assert "away" in ml_preds[0].probabilities
    assert "draw" in ml_preds[0].probabilities  # First-class 12-inning tie rule

    # KBO Spread
    kbo_sp = KBOSpreadModel()
    sp_preds = kbo_sp.predict_games([], matches)
    assert len(sp_preds) == 1
    assert sp_preds[0].market_type == "spread"
    assert sp_preds[0].line == -1.5

    # NPB Total
    npb_tot = NPBTotalModel()
    tot_preds = npb_tot.predict_games([], matches)
    assert len(tot_preds) == 1
    assert tot_preds[0].market_type == "total"
    assert "over" in tot_preds[0].probabilities
    assert "under" in tot_preds[0].probabilities


def test_calibration_tournament_temporal_split():
    """Verify run_calibration_tournament runs with temporal expanding-window split."""
    np.random.seed(42)
    probs = np.random.uniform(0.1, 0.9, 120).tolist()
    outcomes = [1 if np.random.rand() < p else 0 for p in probs]

    res = run_calibration_tournament(
        probabilities=probs,
        outcomes=outcomes,
        base_model_version="test-model",
        n_splits=3,
        minimum_sample=40,
        temporal=True,
    )

    assert res["status"] == "ok"
    assert res["champion_method"] in {"identity", "temperature", "platt", "beta", "isotonic"}
    assert res["champion_calibrator"] is not None
    assert len(res["scorecard"]) == 5


def test_esports_best_of_format_k_scaling():
    """Verify that EsportsEloModel scales update magnitude by best-of match format."""
    # Base NeutralElo book
    book_bo1 = NeutralElo(k=20.0, ratings={"teamA": 1500.0, "teamB": 1500.0})
    book_bo3 = NeutralElo(k=20.0, ratings={"teamA": 1500.0, "teamB": 1500.0})
    book_bo5 = NeutralElo(k=20.0, ratings={"teamA": 1500.0, "teamB": 1500.0})

    row_bo1 = {"team1_id": "teamA", "team2_id": "teamB", "winner_id": "teamA", "best_of": 1}
    row_bo3 = {"team1_id": "teamA", "team2_id": "teamB", "winner_id": "teamA", "best_of": 3}
    row_bo5 = {"team1_id": "teamA", "team2_id": "teamB", "winner_id": "teamA", "best_of": 5}

    book_bo1.update(row_bo1)
    book_bo3.update(row_bo3)
    book_bo5.update(row_bo5)

    delta_bo1 = book_bo1.ratings["teamA"] - 1500.0
    delta_bo3 = book_bo3.ratings["teamA"] - 1500.0
    delta_bo5 = book_bo5.ratings["teamA"] - 1500.0

    # Delta for Bo1 should be 0.70x Bo3, and Bo5 should be 1.25x Bo3
    assert pytest.approx(delta_bo1 / delta_bo3) == 0.70
    assert pytest.approx(delta_bo5 / delta_bo3) == 1.25
    assert delta_bo1 < delta_bo3 < delta_bo5


def test_daily_pipeline_process_lock(tmp_path):
    """Verify that _with_process_lock and run_daily enforce singleton process execution."""
    from model_prediction.cli.daily import _with_process_lock, run_daily
    from model_prediction.daily_lock import acquire_lock, release_lock

    call_count = 0

    @_with_process_lock
    def dummy_pipeline(args, config, registry, bans, ledger, audit, data_root):
        nonlocal call_count
        call_count += 1
        return {"status": "ok", "count": call_count}

    # 1. First run acquires lock and executes
    res1 = dummy_pipeline(None, None, None, None, None, None, str(tmp_path))
    assert res1["status"] == "ok"
    assert call_count == 1

    # 2. Acquire lock externally to simulate another running daily process
    external_lock = acquire_lock(tmp_path / "daily_pipeline.lock")
    assert external_lock is not None

    try:
        # 3. Concurrent call should cleanly abort without executing the function
        res2 = dummy_pipeline(None, None, None, None, None, None, str(tmp_path))
        assert res2["status"] == "aborted"
        assert res2["reason"] == "concurrent_daily_process_locked"
        assert call_count == 1  # Not executed!

        # 4. run_daily itself should also abort cleanly under the held lock
        import argparse

        dummy_args = argparse.Namespace(date="2026-05-10")
        res_daily = run_daily(dummy_args, {}, None, None, None, None, str(tmp_path))
        assert res_daily["status"] == "aborted"
        assert res_daily["reason"] == "concurrent_daily_process_locked"
    finally:
        release_lock(external_lock)

    # 5. After lock release, execution succeeds again
    res3 = dummy_pipeline(None, None, None, None, None, None, str(tmp_path))
    assert res3["status"] == "ok"
    assert call_count == 2
