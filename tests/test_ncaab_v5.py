"""Unit tests and mathematical invariants for NCAAB v5 (dedicated spread and total models)."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v5 import NCAABSpreadModelV5
from model_prediction.models.ncaab_total_v5 import NCAABTotalModelV5
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


def _game(event_id, start, away, home, away_score, home_score):
    return GameRecord(
        event_id=event_id,
        event_start_utc=start,
        league="NCAAB",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
    )


def test_ncaab_spread_v5_neutral_site_zeroes_hca():
    """Neutral site games must not give arbitrary home-court advantage to designated home teams."""
    model = NCAABSpreadModelV5()

    game_home = UpcomingGame(
        event_id="test_home",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Duke",
        home_team="Kansas",
        spread_away_line=0.0,
        neutral_site=False,
    )
    game_neutral = UpcomingGame(
        event_id="test_neutral",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Duke",
        home_team="Kansas",
        spread_away_line=0.0,
        neutral_site=True,
    )

    preds_home = model.predict_spreads([], [game_home])
    preds_neutral = model.predict_spreads([], [game_neutral])

    feat_home = next(p for p in preds_home if p.market_type == "spread").feature_basis
    feat_neutral = next(p for p in preds_neutral if p.market_type == "spread").feature_basis

    assert feat_home["effective_hca"] == 7.2
    assert feat_neutral["effective_hca"] == 0.0
    assert feat_home["expected_margin"] > feat_neutral["expected_margin"]


def test_ncaab_spread_v5_current_season_sample_gating():
    """Teams with many prior-season games but 1 current-season game get thin-sample uncertainty."""
    model = NCAABSpreadModelV5()

    history = []
    for i in range(30):
        history.append(
            _game(
                f"last_season_{i}",
                f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "Team A",
                "Team B",
                65 + i % 5,
                70 + i % 5,
            )
        )
    history.append(_game("this_season_1", "2026-11-03T00:00:00Z", "Team A", "Team B", 68, 72))

    upcoming = UpcomingGame(
        event_id="test_game",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team A",
        home_team="Team B",
        spread_away_line=5.5,
    )
    preds = model.predict_spreads(history, [upcoming])
    spread_pred = next(p for p in preds if p.market_type == "spread")

    assert spread_pred.feature_basis["current_season_sample"] == 1
    assert spread_pred.uncertainty >= 0.15
    assert spread_pred.feature_basis["effective_margin_sd"] > model.margin_sd


def test_ncaab_spread_v5_logistic_moneyline_limits_underdogs():
    """Logistic mapping prevents the catastrophic normal-tail overconfidence on heavy underdogs."""
    model = NCAABSpreadModelV5()

    # Evenly rated teams on neutral site
    game = UpcomingGame(
        event_id="test_ml",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team A",
        home_team="Team B",
        neutral_site=True,
    )
    preds = model.predict_spreads([], [game])
    ml_pred = next(p for p in preds if p.market_type == "moneyline")
    assert abs(ml_pred.probabilities["home"] - 0.50) < 0.01

    # Team B is vastly superior in trend history (+18 margin)
    history = []
    for i in range(15):
        history.append(
            _game(f"g_b_{i}", f"2026-11-{(i % 10) + 1:02d}T00:00:00Z", f"Opp_{i}", "Team B", 50, 85)
        )
        history.append(
            _game(f"g_a_{i}", f"2026-11-{(i % 10) + 1:02d}T00:00:00Z", "Team A", f"Opp_{i}", 50, 75)
        )
    game_blowout = UpcomingGame(
        event_id="test_ml_blowout",
        event_start_utc="2026-11-20T00:00:00Z",
        away_team="Team A",
        home_team="Team B",
        neutral_site=True,
    )
    preds_blowout = model.predict_spreads(history, [game_blowout])
    ml_blowout = next(p for p in preds_blowout if p.market_type == "moneyline")
    # Dog probability should be tightly bounded (< 5%) on +18 margin
    assert ml_blowout.probabilities["away"] < 0.05


def test_ncaab_total_v5_pace_and_volatility():
    """Totals model scales expected total and volatility with matchup tempo."""
    model = NCAABTotalModelV5()

    game = UpcomingGame(
        event_id="test_total",
        event_start_utc="2026-11-05T00:00:00Z",
        away_team="Team Fast",
        home_team="Team Slow",
        total_line=142.5,
    )

    preds = model.predict_totals([], [game])
    assert len(preds) == 1
    total_pred = preds[0]
    assert total_pred.market_type == "total"
    assert 0.0 < total_pred.probabilities["over"] < 1.0
    assert 0.0 < total_pred.probabilities["under"] < 1.0
    assert abs(total_pred.probabilities["over"] + total_pred.probabilities["under"] - 1.0) < 1e-4


def test_ncaab_v5_from_artifact():
    """Both models instantiate correctly from dictionary artifacts."""
    spread_art = {
        "model_version": "ncaab-spread-v5-custom",
        "margin_sd": 11.2,
        "home_court_points": 3.2,
        "logistic_scale": 9.2,
    }
    spread_model = NCAABSpreadModelV5.from_artifact(spread_art)
    assert spread_model.version == "ncaab-spread-v5-custom"
    assert spread_model.margin_sd == 11.2
    assert spread_model.home_court_points == 3.2
    assert spread_model.logistic_scale == 9.2

    total_art = {
        "model_version": "ncaab-total-v5-custom",
        "total_sd": 14.2,
        "league_pace": 69.5,
    }
    total_model = NCAABTotalModelV5.from_artifact(total_art)
    assert total_model.version == "ncaab-total-v5-custom"
    assert total_model.total_sd == 14.2
    assert total_model.league_pace == 69.5


def test_ncaab_v5_production_registry_and_champions():
    """Production registry correctly wires NCAAB v5 models as champions."""
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    ml_champ = registry.champion("NCAAB", "moneyline")
    spread_champ = registry.champion("NCAAB", "spread")
    total_champ = registry.champion("NCAAB", "total")

    assert "ncaab-spread-v5" in registry.entries
    assert "ncaab-total-v5" in registry.entries
    assert ml_champ is not None and ml_champ.available
    assert ml_champ.model_id in ("ncaab-spread-v5", "ncaab-spread-v6")
    assert spread_champ is not None and spread_champ.available
    assert spread_champ.model_id in ("ncaab-spread-v5", "ncaab-spread-v6")
    assert total_champ is not None and total_champ.available
    assert total_champ.model_id in ("ncaab-total-v5", "ncaab-total-v6")


def test_ncaab_v5_artifact_hashes():
    """Model artifacts match their embedded SHA-256 canonical hash."""
    for filename in ("ncaab-spread-v5.json", "ncaab-total-v5.json"):
        path = Path(PROJECT_ROOT) / "config" / "models" / filename
        assert path.is_file(), f"Missing artifact: {path}"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        expected_hash = compute_artifact_hash(data)
        assert data.get("artifact_hash") == expected_hash
