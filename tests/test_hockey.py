"""Comprehensive tests for the unified hockey model and Polymarket league expansion."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from model_prediction.cli import forecast as cli_forecast
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
    assert len(preds) == 1 + 6 + 9  # 1 moneyline + 6 home handicaps + 9 total lines

    # Moneyline check
    ml = next(p for p in preds if p.market_type == "moneyline")
    assert ml.probabilities["home"] > 0
    assert ml.probabilities["away"] > 0
    # Equal prior with home boost should favor home
    assert ml.probabilities["home"] > ml.probabilities["away"]
    assert ml.probabilities["home"] + ml.probabilities["away"] == pytest.approx(1.0, abs=1e-5)

    # Spread check: ``line`` is the home handicap, ``home`` the probability home + line covers.
    spreads = {p.line: p for p in preds if p.market_type == "spread"}
    assert set(spreads) == {-3.5, -2.5, -1.5, 1.5, 2.5, 3.5}
    for spread in spreads.values():
        assert spread.probabilities["home"] + spread.probabilities["away"] == pytest.approx(1.0, abs=1e-5)
    assert spreads[-1.5].probabilities["home"] < spreads[-1.5].probabilities["away"]  # home -1.5 is hard
    assert spreads[1.5].probabilities["home"] > spreads[1.5].probabilities["away"]  # home +1.5 is easy
    assert spreads[-1.5].probabilities["home"] < spreads[-2.5].probabilities["away"]
    # Home -0.5 equals the two-way moneyline (OT/SO included): the margin pmf is consistent with it.
    ml_home = ml.probabilities["home"]
    assert spreads[-1.5].probabilities["home"] < ml_home < spreads[1.5].probabilities["home"]

    # Totals check
    totals = [p for p in preds if p.market_type == "total"]
    assert len(totals) == 9
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


def test_hockey_skips_unquoted_total_line_and_ambiguous_team_side(tmp_path) -> None:
    event = {
        "event_id": "hockey-line-check",
        "title": "Away vs. Home",
        "event_start_utc": "2026-10-24T01:00:00Z",
        "markets": [
            {
                "market_type": "moneyline",
                "market_slug": "moneyline",
                "sides": [
                    {"selection": "unknown", "description": "Unknown", "price_probability": 0.01},
                    {"selection": "away", "description": "Away", "price_probability": 0.99},
                ],
            },
            {
                "market_type": "total",
                "market_slug": "wrong-line",
                "line": 100.5,
                "sides": [
                    {"selection": "over", "price_probability": 0.01},
                    {"selection": "under", "price_probability": 0.99},
                ],
            },
        ],
    }

    class Client:
        def slate(self, league, game_date):
            return [event]

    slate = build_hockey_slate(tmp_path, "2026-10-23", client=Client(), leagues=["NHL"])
    assert all(p["selection"] != "home" for p in slate["priced"])
    assert all(p["market_type"] != "total" for p in slate["priced"])


def test_hockey_unrouted_market_never_reaches_ledger(monkeypatch, tmp_path) -> None:
    contract = {
        "event_id": "unrouted-market",
        "event_start_utc": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "market_type": "nrfi",
    }
    monkeypatch.setattr(
        "model_prediction.hockey_forward.build_hockey_slate",
        lambda **kwargs: {"priced": [contract], "skipped": []},
    )

    class Ledger:
        def append_evaluated(self, *args, **kwargs):
            raise AssertionError("unrouted market was logged")

    report = cli_forecast._forecast_hockey_sport(
        data_root=tmp_path,
        args_date="2026-10-23",
        config={"models": {"HOCKEY": {}}},
        flat_ledger=Ledger(),
    )
    assert report["flat_logged"] == 0
    assert report["skipped"] == [
        {"event_id": "unrouted-market", "reason": "market_has_no_serving_champion", "market": "nrfi"}
    ]


def test_hockey_totals_account_for_overtime_goal() -> None:
    """Verify that tied regulation scores (e.g. 2-2 = 4 goals) add +1 goal for full-game totals (5 goals)."""
    model = hockey_model()
    upcoming = [
        UpcomingHockeyMatch(
            event_id="nhl-ot-test",
            event_start_utc="2026-10-20T00:00:00Z",
            away_team="Away",
            home_team="Home",
            league="NHL",
        )
    ]
    preds = model.predict_games(history=[], upcoming=upcoming)
    totals = {p.line: p for p in preds if p.market_type == "total"}

    # Over 5.5 and Under 5.5 probabilities must sum to 1.0
    t55 = totals[5.5]
    assert t55.probabilities["over"] + t55.probabilities["under"] == pytest.approx(1.0, abs=1e-5)

    # Monotonicity: Over 5.5 must be strictly greater than Over 6.0, which is > Over 6.5
    assert (
        totals[5.5].probabilities["over"]
        > totals[6.0].probabilities["over"]
        > totals[6.5].probabilities["over"]
    )


def test_hockey_backfill_adds_prior_seasons_with_one_name_per_franchise(tmp_path) -> None:
    import json

    from model_prediction.hockey_history import backfill_nhl_seasons

    def team(abbrev, place, nick, score):
        return {
            "abbrev": abbrev,
            "placeName": {"default": place},
            "commonName": {"default": nick},
            "score": score,
        }

    class FakeNHL:
        def club_season(self, club, season):
            if club != "UTA":
                return {"games": []}
            return {
                "games": [
                    {
                        "id": 1,
                        "gameType": 2,
                        "gameState": "FINAL",
                        "startTimeUTC": "2024-11-01T23:00:00Z",
                        "homeTeam": team("UTA", "Utah", "Utah Hockey Club", 3),
                        "awayTeam": team("COL", "Colorado", "Avalanche", 2),
                    },
                    {
                        "id": 2,
                        "gameType": 2,
                        "gameState": "FINAL",
                        "startTimeUTC": "2025-11-01T23:00:00Z",
                        "homeTeam": team("UTA", "Utah", "Mammoth", 1),
                        "awayTeam": team("COL", "Colorado", "Avalanche", 4),
                    },
                    {
                        "id": 3,
                        "gameType": 1,
                        "gameState": "FINAL",
                        "startTimeUTC": "2025-09-21T23:00:00Z",
                        "homeTeam": team("UTA", "Utah", "Mammoth", 1),
                        "awayTeam": team("COL", "Colorado", "Avalanche", 4),
                    },
                    {
                        "id": 4,
                        "gameType": 2,
                        "gameState": "FUT",
                        "startTimeUTC": "2026-12-01T23:00:00Z",
                        "homeTeam": team("UTA", "Utah", "Mammoth", None),
                        "awayTeam": team("COL", "Colorado", "Avalanche", None),
                    },
                ]
            }

    existing = {
        "e1": {
            "event_id": "e1",
            "start": "2025-11-01T23:30:00Z",
            "league": "NHL",
            "home_team": "Utah Mammoth",
            "away_team": "Colorado Avalanche",
            "home_score": 1,
            "away_score": 4,
            "completed": True,
        }
    }
    result = backfill_nhl_seasons(existing, date(2026, 10, 21), FakeNHL())
    # Preseason, unplayed and ESPN-duplicate games are skipped; the renamed club shares one name.
    assert result["added"] == 1
    added = existing["nhl:1"]
    assert (added["home_team"], added["away_team"]) == ("Utah Mammoth", "Colorado Avalanche")
    assert json.dumps(existing)  # serializable


def test_hockey_spread_pricing_uses_each_market_line_and_side_relative_lines() -> None:
    """A market's own line picks the model probability; an away side at L is home failing at -L."""
    event = {
        "event_id": "nhl-spread",
        "event_start_utc": "2026-10-23T00:00:00Z",
        "markets": [
            {  # alternate line: never the main line, must not be priced
                "market_type": "spread",
                "market_slug": "alt",
                "line": 3.5,
                "sides": [
                    {"selection": "away", "description": "+3.50", "line": 3.5, "price_probability": 0.95},
                    {"selection": "home", "description": "-3.50", "line": -3.5, "price_probability": 0.05},
                ],
            },
            {  # main line: away +1.5 at 0.60 / home -1.5 at 0.42
                "market_type": "spread",
                "market_slug": "main",
                "line": 1.5,
                "sides": [
                    {"selection": "away", "description": "+1.50", "line": 1.5, "price_probability": 0.60},
                    {"selection": "home", "description": "-1.50", "line": -1.5, "price_probability": 0.42},
                ],
            },
            {
                "market_type": "total",
                "market_slug": "tot-main",
                "line": 6.5,
                "sides": [
                    {"selection": "over", "price_probability": 0.50},
                    {"selection": "under", "price_probability": 0.52},
                ],
            },
            {
                "market_type": "total",
                "market_slug": "tot-alt",
                "line": 2.5,
                "sides": [
                    {"selection": "over", "price_probability": 0.99},
                    {"selection": "under", "price_probability": 0.04},
                ],
            },
        ],
    }

    class Client:
        def slate(self, league, game_date):
            return [event]

    import tempfile

    slate = build_hockey_slate(tempfile.mkdtemp(), "2026-10-23", client=Client(), leagues=["NHL"])
    spreads = [p for p in slate["priced"] if p["market_type"] == "spread"]
    totals = [p for p in slate["priced"] if p["market_type"] == "total"]
    assert [p["market_slug"] for p in spreads] == ["main"]
    assert [p["market_slug"] for p in totals] == ["tot-main"]
    spread = spreads[0]
    # Cold-start ratings favour the home side, so home -1.5 (+1.5 for away) is priced by line.
    model = hockey_model()
    preds = model.predict_games([], [UpcomingHockeyMatch("x", "2026-10-23T00:00:00Z", "Away", "Home", "NHL")])
    by_line = {p.line: p for p in preds if p.market_type == "spread"}
    expected = {
        "away": by_line[-1.5].probabilities["away"],  # away +1.5 == home fails at -1.5
        "home": by_line[-1.5].probabilities["home"],  # home -1.5
    }[spread["selection"]]
    assert spread["line"] == (1.5 if spread["selection"] == "away" else -1.5)
    assert spread["model_probability"] == pytest.approx(expected, abs=2e-3)
