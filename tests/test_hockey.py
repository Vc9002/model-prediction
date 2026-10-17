"""Comprehensive tests for the unified hockey model and Polymarket league expansion."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from model_prediction.data_sources.polymarket_us import (
    BBO_CAPTURE_SPORTS,
    LEAGUE_SLUGS,
    MARKET_TYPES,
    POLYMARKET_SPORT_LEAGUES,
    PolymarketUSClient,
)
from model_prediction.domain import League
from model_prediction.hockey_forward import ALL_HOCKEY_LEAGUES, build_hockey_slate
from model_prediction.models.hockey import (
    HockeyModel,
    UpcomingHockeyMatch,
    hockey_model,
)
from model_prediction.models.registry import get_model, model_spec
from model_prediction.portfolio.polymarket_scanner import _normalize_league
from model_prediction.sports import SUPPORTED_SPORTS, get_sport_module
from model_prediction.sports.naming import (
    canonical_model_id,
    display_model_name,
    get_model_descriptor,
    list_models_by_sport,
)


def test_hockey_leagues_taxonomy_and_polymarket_coverage() -> None:
    """Validate that all 8 Polymarket hockey leagues are properly mapped and captured."""
    assert "hockey" in POLYMARKET_SPORT_LEAGUES
    hockey_leagues = POLYMARKET_SPORT_LEAGUES["hockey"]
    expected_8_leagues = (
        "NHL",
        "AHL",
        "KHL",
        "DEL",
        "SHL",
        "SNHL",
        "CEHL",
        "LIIGA",
    )
    assert hockey_leagues == expected_8_leagues
    assert ALL_HOCKEY_LEAGUES == expected_8_leagues

    for lg in expected_8_leagues:
        assert lg in LEAGUE_SLUGS
        assert LEAGUE_SLUGS[lg].islower()

    assert "hockey" in BBO_CAPTURE_SPORTS

    # Market types mapping
    assert MARKET_TYPES.get("hockey_team_full_game_winner") == "moneyline"
    assert MARKET_TYPES.get("hockey_puck_line") == "spread"
    assert MARKET_TYPES.get("hockey_total_goals") == "total"


def test_domain_league_enum_and_sports_package() -> None:
    """Validate domain League enum and modular sports package inclusion."""
    assert League.HOCKEY == "HOCKEY"
    assert League.NHL == "NHL"
    assert League.AHL == "AHL"
    assert League.KHL == "KHL"
    assert League.DEL == "DEL"
    assert League.SHL == "SHL"
    assert League.SNHL == "SNHL"
    assert League.CEHL == "CEHL"
    assert League.LIIGA == "LIIGA"

    assert "hockey" in SUPPORTED_SPORTS
    mod = get_sport_module("hockey")
    assert hasattr(mod, "moneyline")
    assert hasattr(mod, "spread")
    assert hasattr(mod, "total")


def test_hockey_model_score_matrix_and_probabilities() -> None:
    """Verify bivariate Poisson score grid sums to 1.0 and satisfies mathematical constraints."""
    model = hockey_model()
    matrix = model.score_matrix(home_rate=3.2, away_rate=2.8, rho=-0.06)

    # Check non-negativity and sum to 1.0
    total_prob = sum(sum(row) for row in matrix)
    assert total_prob == pytest.approx(1.0, abs=1e-6)
    for row in matrix:
        for p in row:
            assert p >= 0.0

    # Ensure zero rates don't crash
    zero_matrix = model.score_matrix(home_rate=0.0, away_rate=0.0)
    assert sum(sum(r) for r in zero_matrix) == pytest.approx(1.0, abs=1e-6)


def test_hockey_model_predictions_markets() -> None:
    """Test point-in-time predictions across moneyline, spread, and totals."""
    model = hockey_model()
    upcoming = [
        UpcomingHockeyMatch(
            event_id="nhl-1",
            event_start_utc="2026-10-17T01:00:00Z",
            away_team="Pittsburgh Penguins",
            home_team="Colorado Avalanche",
            league="NHL",
        )
    ]

    preds = model.predict_games(history=[], upcoming=upcoming)
    assert len(preds) == 5  # 1 moneyline + 1 spread (1.5) + 3 totals (5.5, 6.0, 6.5)

    # Moneyline check
    ml = next(p for p in preds if p.market_type == "moneyline")
    assert ml.probabilities["home"] > 0
    assert ml.probabilities["away"] > 0
    # Equal prior with home boost should favor home
    assert ml.probabilities["home"] > ml.probabilities["away"]
    assert ml.probabilities["home"] + ml.probabilities["away"] == pytest.approx(1.0, abs=1e-5)

    # Spread check (puck line 1.5)
    spread = next(p for p in preds if p.market_type == "spread")
    assert spread.line == 1.5
    assert spread.probabilities["home"] + spread.probabilities["away"] == pytest.approx(1.0, abs=1e-5)
    assert spread.probabilities["home"] < spread.probabilities["away"]  # -1.5 is harder than +1.5

    # Totals check
    totals = [p for p in preds if p.market_type == "total"]
    assert len(totals) == 3
    t_55 = next(t for t in totals if t.line == 5.5)
    t_65 = next(t for t in totals if t.line == 6.5)
    assert t_55.probabilities["over"] + t_55.probabilities["under"] == pytest.approx(1.0, abs=1e-5)
    # P(total > 5.5) must be strictly greater than P(total > 6.5)
    assert t_55.probabilities["over"] > t_65.probabilities["over"]


def test_hockey_multi_league_calibration_constants() -> None:
    """Verify each league has custom baseline goal rates and home ice parameters."""
    model = hockey_model()
    nhl_consts = model.get_constants("NHL")
    khl_consts = model.get_constants("KHL")
    del_consts = model.get_constants("DEL")

    assert nhl_consts["baseline"] > khl_consts["baseline"]  # NHL higher scoring than KHL
    assert del_consts["home_boost"] >= 1.10
    assert model.get_constants("UNKNOWN")["baseline"] == 2.95


def test_sports_naming_and_catalog() -> None:
    """Verify model naming, descriptors, and alias resolution for hockey."""
    desc = get_model_descriptor("hockey-poisson-dc-v1")
    assert desc is not None
    assert desc.sport == "HOCKEY"
    assert desc.market == "moneyline"
    assert "Dixon-Coles" in desc.description

    # Aliases
    assert canonical_model_id("nhl-moneyline-v1") == "hockey-poisson-dc-v1"
    assert canonical_model_id("hockey-ml-v1") == "hockey-poisson-dc-v1"
    assert display_model_name("hockey-poisson-dc-v1") == "Hockey Moneyline (Poisson DC v1)"

    models = list_models_by_sport("HOCKEY")
    assert len(models) >= 3


def test_scanner_normalization_for_hockey() -> None:
    """Verify that polymarket scanner normalizes all hockey leagues and abbreviations."""
    for lg in ("NHL", "AHL", "KHL", "DEL", "SHL", "SNHL", "CEHL", "LIIGA", "ICE_HOCKEY", "HOCKEY"):
        assert _normalize_league(lg) == "HOCKEY"


def test_registry_integration() -> None:
    """Verify get_model('hockey') and model_spec(League.HOCKEY)."""
    m = get_model("hockey")
    assert isinstance(m, HockeyModel)
    assert m.version == "hockey-poisson-dc-v1"

    spec = model_spec(League.HOCKEY)
    assert spec.league == League.HOCKEY
    assert "Poisson" in spec.family


def test_build_hockey_slate_mocked(tmp_path) -> None:
    """Test build_hockey_slate with mock client discovering events across leagues."""
    mock_event = {
        "event_id": "test-nhl-1",
        "title": "PIT Penguins vs. COL Avalanche",
        "startTime": "2026-10-17T01:00:00Z",
        "markets": [
            {
                "market_id": "mkt-1",
                "market_slug": "aec-nhl-pit-col-2026-10-16",
                "market_type": "moneyline",
                "sides": [
                    {
                        "selection": "away",
                        "description": "PIT Penguins",
                        "price_probability": 0.40,
                    },
                    {
                        "selection": "home",
                        "description": "COL Avalanche",
                        "price_probability": 0.58,
                    },
                ],
            }
        ],
    }

    mock_client = MagicMock(spec=PolymarketUSClient)

    # Return mock_event for NHL, empty for others
    def mock_slate(league, dt, timezone=None):
        if league == "NHL":
            return [mock_event]
        return []

    mock_client.slate.side_effect = mock_slate

    slate = build_hockey_slate(
        data_root=tmp_path,
        game_date="2026-10-16",
        client=mock_client,
        leagues=["NHL", "AHL"],
    )

    assert slate["total_events_discovered"] == 1
    assert slate["priced_count"] == 1
    priced_item = slate["priced"][0]
    assert priced_item["event_id"] == "test-nhl-1"
    assert priced_item["league"] == "NHL"
    assert priced_item["market_type"] == "moneyline"
    assert priced_item["selection"] in ("home", "away")
    assert "edge_vs_executable_ask" in priced_item
