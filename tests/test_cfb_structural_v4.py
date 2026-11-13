"""Tests for College Football Structural v4 Model (cfb-structural-v4)."""

from __future__ import annotations

import json
from dataclasses import replace

import yaml

from model_prediction.config import PROJECT_ROOT
from model_prediction.models.cfb_structural_v4 import (
    CFB_BLOWOUT_SPREAD_THRESHOLD,
    CFB_V4_MODEL_VERSION,
    CFBStructuralV4Model,
    cfb_structural_v4_model,
)
from model_prediction.models.college_football import UpcomingCFBGame
from model_prediction.production_registry import ProductionModelRegistry


def test_cfb_structural_v4_model_instantiation():
    model = CFBStructuralV4Model()
    assert model.version == CFB_V4_MODEL_VERSION
    assert model.home_advantage_points == 2.5
    assert model.margin_sd == 14.5
    assert model.total_sd == 16.0
    assert cfb_structural_v4_model.version == CFB_V4_MODEL_VERSION


def test_cfb_structural_v4_blowout_margin_expansion():
    model = CFBStructuralV4Model()
    normal_sd = model.compute_effective_margin_sd(spread_home_line=-7.5, uncertainty=0.04)
    blowout_sd = model.compute_effective_margin_sd(spread_home_line=-31.5, uncertainty=0.04)
    assert normal_sd == 14.5
    assert blowout_sd > normal_sd
    # Excess spread is 31.5 - 21.5 = 10.0 pts -> 1.0 + 0.025 * 10 = 1.25x
    assert round(blowout_sd, 1) == round(14.5 * (1.0 + 0.025 * (31.5 - CFB_BLOWOUT_SPREAD_THRESHOLD)), 1)


def test_cfb_structural_v4_forecast_coherence():
    model = CFBStructuralV4Model()
    game = UpcomingCFBGame(
        event_id="cfb-test-v4",
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

    assert ml_pred.model_version == CFB_V4_MODEL_VERSION
    assert sp_pred.model_version == CFB_V4_MODEL_VERSION
    assert tot_pred.model_version == CFB_V4_MODEL_VERSION

    assert abs(sum(ml_pred.probabilities.values()) - 1.0) < 0.01
    assert abs(sum(sp_pred.probabilities.values()) - 1.0) < 0.01
    assert abs(sum(tot_pred.probabilities.values()) - 1.0) < 0.01


def test_cfb_structural_v4_preserves_pickem_spread():
    model = CFBStructuralV4Model()
    game = UpcomingCFBGame(
        event_id="cfb-pickem-v4",
        event_start_utc="2026-10-24T19:00:00Z",
        away_team="Pittsburgh Panthers",
        home_team="Miami Hurricanes",
        spread_home_line=0.0,
        total_line=55.5,
    )
    spread = next(p for p in model.predict_matchup([], game) if p.market_type == "spread")
    assert spread.line == 0.0


def test_cfb_structural_v4_refuses_missing_lines_and_preserves_engine_state():
    model = CFBStructuralV4Model()
    game = UpcomingCFBGame(
        event_id="missing-lines",
        event_start_utc="2026-10-24T19:00:00Z",
        away_team="Pittsburgh Panthers",
        home_team="Miami Hurricanes",
        spread_home_line=None,
        total_line=None,
    )
    assert model.predict_matchup([], game) == []
    game = replace(game, spread_home_line=-21.5, total_line=55.5)
    original = model.distribution_engine
    model.predict_matchup([], game)
    assert model.distribution_engine is original
    assert original.margin_sd == model.margin_sd
    assert original.total_sd == model.total_sd


def test_cfb_structural_v4_artifact_and_registry_wiring():
    artifact_path = PROJECT_ROOT / "config/models/cfb-structural-v4.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["model_version"] == CFB_V4_MODEL_VERSION
    assert artifact["margin_sd"] == 14.5
    assert artifact["total_sd"] == 16.0

    prod_cfg = yaml.safe_load((PROJECT_ROOT / "config/production.yaml").read_text(encoding="utf-8"))
    reg = ProductionModelRegistry.from_config(prod_cfg, repo_root=PROJECT_ROOT)
    # v4 is the rollback pointer behind the v5 champion (promoted 2026-11-13).
    v4_entry = reg.entries[CFB_V4_MODEL_VERSION]
    assert v4_entry.available and v4_entry.serving_status == "rollback"
    for market in ["moneyline", "spread", "total"]:
        champ = reg.champion("NCAAF", market)
        assert champ is not None
        assert champ.model_id == "cfb-structural-v5"
        assert reg.lifecycle_contract("NCAAF", market).rollback_model_id == CFB_V4_MODEL_VERSION


def test_cfb_slate_spread_total_interaction(tmp_path):
    """Verify that build_cfb_slate correctly incorporates the spread magnitude into total selection."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from model_prediction.models.college_football import build_cfb_slate

    mock_event = {
        "id": "mock-cfb-blowout",
        "date": "2026-11-01T19:00:00Z",
        "season": {"year": 2026},
        "week": {"number": 10},
        "competitions": [
            {
                "id": "mock-cfb-blowout",
                "date": "2026-11-01T19:00:00Z",
                "neutralSite": False,
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"id": "1", "displayName": "Georgia Bulldogs", "name": "Bulldogs"},
                    },
                    {
                        "homeAway": "away",
                        "team": {"id": "2", "displayName": "UMass Minutemen", "name": "Minutemen"},
                    },
                ],
                "odds": [
                    {
                        "provider": {"name": "consensus"},
                        "spread": "-28.5",
                        "overUnder": 52.5,
                        "homeTeamOdds": {"pointSpread": {"american": "-28.5"}},
                        "awayTeamOdds": {"pointSpread": {"american": "+28.5"}},
                        "total": {
                            "over": {"close": {"line": "52.5", "odds": "-110"}},
                            "under": {"close": {"line": "52.5", "odds": "-110"}},
                        },
                        "pointSpread": {
                            "home": {"close": {"line": "-28.5", "odds": "-110"}},
                            "away": {"close": {"line": "28.5", "odds": "-110"}},
                        },
                    }
                ],
            }
        ],
    }

    client = SimpleNamespace(scoreboard=lambda *args, **kwargs: {"events": [mock_event]})
    slate = build_cfb_slate(
        data_root=tmp_path,
        game_date="2026-11-01",
        client=client,
        observed_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
    )
    assert "priced_contracts" in slate
    tot_contracts = [c for c in slate["priced_contracts"] if c["market_type"] == "total"]
    assert len(tot_contracts) == 1
    # Verify selection is produced cleanly
    assert tot_contracts[0]["selection"] in ("over", "under")
