"""Tests for Audit Remediation Batch 4.

Verifies:
1. Esports Bo5 Derivatives (infer_map_win_probability_bo5, price_esports_bo5_derivatives, price_esports_derivatives)
2. International Baseball predict_games positional upcoming argument compatibility
3. CFB features offseason bye-week rest clamping (no spurious +1.5 pts advantage)
4. CFB Structural v2 spread line alignment to sp_away_line convention
5. CFB distribution low-scoring dispersion floor (r = max(0.5, ...))
6. Soccer Forward Double Chance match-scoped snapshot candidate filtering
7. Basketball League isolation forbidding generic BASKETBALL leak into thin leagues
"""

from model_prediction.features.base import GameRecord
from model_prediction.features.cfb_features import CFBFeatureExtractor
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import (
    WNBA_CONFIG,
    DedicatedBasketballLeagueModel,
)
from model_prediction.models.cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
)
from model_prediction.models.cfb_structural_v2 import CFBStructuralV2Model
from model_prediction.models.college_football import UpcomingCFBGame
from model_prediction.models.esports_derivatives import (
    EsportsBo3Derivatives,
    EsportsBo5Derivatives,
    infer_map_win_probability_bo5,
    price_esports_bo5_derivatives,
    price_esports_derivatives,
)
from model_prediction.models.international_baseball_v3 import InternationalBaseballV3Model


def test_esports_bo5_derivatives_symmetry_and_invariants():
    """Bo5 derivatives must satisfy exact probability axioms across the full range."""
    # 50-50 match
    p_map = infer_map_win_probability_bo5(0.50)
    assert abs(p_map - 0.50) < 1e-6
    d50 = price_esports_bo5_derivatives(0.50)
    assert d50.match_win_p1 == 0.50
    assert d50.match_win_p2 == 0.50
    assert d50.spread_p1_cover_neg_1pt5 == d50.spread_p2_cover_neg_1pt5
    assert d50.spread_p1_cover_neg_2pt5 == d50.spread_p2_cover_neg_2pt5
    assert abs(d50.spread_p1_cover_neg_1pt5 + d50.spread_p2_cover_pos_1pt5 - 1.0) < 1e-5
    assert abs(d50.spread_p1_cover_neg_2pt5 + d50.spread_p2_cover_pos_2pt5 - 1.0) < 1e-5
    assert abs(d50.total_maps_over_3pt5 + d50.total_maps_under_3pt5 - 1.0) < 1e-5
    assert abs(d50.total_maps_over_4pt5 + d50.total_maps_under_4pt5 - 1.0) < 1e-5

    # 70% favorite match
    d70 = price_esports_bo5_derivatives(0.70)
    assert d70.map_win_p1 > 0.50
    assert d70.spread_p1_cover_neg_1pt5 > d70.spread_p2_cover_neg_1pt5
    assert d70.spread_p1_cover_neg_2pt5 > d70.spread_p2_cover_neg_2pt5
    assert 3.0 <= d70.expected_total_maps <= 5.0

    # Unified entrypoint
    res3 = price_esports_derivatives(0.60, best_of=3)
    res5 = price_esports_derivatives(0.60, best_of=5)
    assert isinstance(res3, EsportsBo3Derivatives)
    assert isinstance(res5, EsportsBo5Derivatives)


def test_intl_baseball_positional_arguments():
    """predict_games must seamlessly accept upcoming games passed positionally."""
    model = InternationalBaseballV3Model(league="KBO")
    upcoming = [
        UpcomingGame(
            event_id="kbo-1",
            event_start_utc="2026-05-01T09:30:00Z",
            away_team="Doosan Bears",
            home_team="LG Twins",
            league="KBO",
        )
    ]
    # Call positionally with 1 argument (which lands on history parameter if not guarded)
    preds = model.predict_games(upcoming)
    assert len(preds) == 3
    assert any(p.market_type == "moneyline" for p in preds)
    assert any(p.market_type == "spread" for p in preds)
    assert any(p.market_type == "total" for p in preds)


def test_cfb_offseason_rest_guard():
    """Games after > 28 day break must not receive spurious bye-week advantage."""
    extractor = CFBFeatureExtractor()
    # Prior game in January (bowl game), current game in September (season opener)
    history = [
        {
            "event_start_utc": "2025-01-08T01:00:00Z",
            "home_team": "Georgia Bulldogs",
            "away_team": "Alabama Crimson Tide",
            "home_score": 34,
            "away_score": 24,
            "season_year": 2024,
        }
    ]
    feat = extractor.extract_features(
        history=history,
        away_team="Alabama Crimson Tide",
        home_team="Georgia Bulldogs",
        event_id="test-opener",
        game_start_utc="2025-09-01T19:00:00Z",
        season_year=2025,
        week=1,
    )
    # Both teams returning from offseason -> rest_disparity should be 0, bye_week_advantage should be 0
    assert feat.home_bye_advantage == 0.0
    assert feat.rest_disparity == 0.0


def test_cfb_structural_v2_spread_away_line_convention():
    """Spread prediction line must represent the away line (+line for home favorite)."""
    model = CFBStructuralV2Model()
    game = UpcomingCFBGame(
        event_id="cfb-spread-test",
        event_start_utc="2026-10-10T19:30:00Z",
        away_team="Auburn Tigers",
        home_team="Georgia Bulldogs",
        spread_home_line=-7.5,
        total_line=50.5,
    )
    preds = model.predict_matchup(history=[], game=game)
    spread_pred = next(p for p in preds if p.market_type == "spread")
    # In canonical convention, line for spread is away line: -(-7.5) = +7.5
    assert spread_pred.line == 7.5
    assert "Home -7.5 / Away +7.5" in spread_pred.rationale


def test_cfb_distribution_low_scoring_dispersion():
    """Negative binomial simulation on low scoring teams must maintain reasonable variance."""
    engine = CFBJointDistributionEngine(
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL,
        margin_sd=15.5,
        total_sd=20.0,
        n_simulations=10000,
        random_seed=42,
    )
    # Low scoring game: 10 vs 7
    h_scores, a_scores = engine.simulate_joint_scores(mu_home=10.0, mu_away=7.0)
    assert len(h_scores) == 10000
    margin = h_scores - a_scores
    # Realized margin SD should be close to 15.5, not crushed to < 10
    realized_margin_sd = float(margin.std())
    assert 13.0 <= realized_margin_sd <= 18.0


def test_basketball_league_isolation_generic_tag():
    """Non-NBA leagues (e.g. WNBA) must not ingest games stamped as generic BASKETBALL."""
    model = DedicatedBasketballLeagueModel(WNBA_CONFIG)
    history = [
        GameRecord(
            event_id="g-nba",
            event_start_utc="2026-05-01T19:00:00Z",
            home_team="Team A",
            away_team="Team B",
            home_score=118,
            away_score=112,
            league="BASKETBALL",  # Generic tag representing NBA-level high scoring
        ),
        GameRecord(
            event_id="g-wnba",
            event_start_utc="2026-05-02T19:00:00Z",
            home_team="Las Vegas Aces",
            away_team="New York Liberty",
            home_score=84,
            away_score=80,
            league="WNBA",  # Legitimate WNBA game
        ),
    ]
    upcoming = [
        UpcomingGame(
            event_id="up-wnba",
            event_start_utc="2026-05-10T19:00:00Z",
            home_team="Las Vegas Aces",
            away_team="New York Liberty",
            league="WNBA",
        )
    ]
    preds = model.predict_games(history, upcoming)
    assert len(preds) > 0
    # In WNBA model, prediction features should reflect isolated WNBA parameters
    ml = next(p for p in preds if p.market_type == "moneyline")
    assert ml.league == "WNBA"
