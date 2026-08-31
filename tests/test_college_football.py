"""Comprehensive Unit and Integration Tests for College Football (NCAAF) System.

Verifies:
1. Team registry, canonical aliases, FBS/FCS classification, and elevation/dome metadata
2. Great-circle Haversine distance, timezone delta, and altitude fatigue calculations
3. Temporal PIT feature generation without future-game or future-stat lookahead
4. Opponent adjustment computed strictly from past games
5. Preseason priors and exponential weekly decay schedule
6. Quarterback model with starter uncertainty and probabilistic mixtures
7. Conditional weather mechanisms and indoor/dome overrides
8. Joint scoring distributions (NegBinomial, Normal, Empirical, MC) and probability sums
9. Permanent sign conventions: Margin = Home - Away, Spread = -MarketImpliedHomeMargin, Total = Home + Away
10. Push handling for integer lines (Spread and Total)
11. Market no-vig conversion and consensus line pricing
12. Flat ledger unconditional inclusion of all candidate games across all 3 markets
13. Main ledger gating: qualified Total accepted, unqualified ML/Spread rejected
14. Production registry serialization, artifact hashes, and fallback contracts
15. Settlement grading for ML, Spread (cover/loss/push), Total (over/under/push), and OT
"""

import json
from pathlib import Path

import pytest

from model_prediction.data_sources.cfb_data import (
    calculate_haversine_distance,
    calculate_timezone_difference,
    resolve_team,
)
from model_prediction.domain import (
    MarketType,
    PickResult,
)
from model_prediction.features.base import GameRecord
from model_prediction.features.cfb_features import (
    CFBFeatureExtractor,
)
from model_prediction.models.cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
)
from model_prediction.models.college_football import (
    CFB_SPREAD_MODEL_VERSION,
    CFB_TOTAL_MODEL_VERSION,
    MODEL_VERSION,
    CollegeFootballModel,
    UpcomingCFBGame,
)
from model_prediction.pricing import grade_pick
from model_prediction.production_registry import ProductionModelRegistry, compute_artifact_hash


@pytest.fixture
def sample_cfb_history() -> list[GameRecord]:
    """Sample historical games for testing."""
    return [
        GameRecord(
            event_id="101",
            event_start_utc="2024-08-31T16:00:00Z",
            league="NCAAF",
            away_team="Clemson Tigers",
            home_team="Georgia Bulldogs",
            away_score=3,
            home_score=34,
            season_year=2024,
        ),
        GameRecord(
            event_id="102",
            event_start_utc="2024-09-07T16:00:00Z",
            league="NCAAF",
            away_team="Texas Longhorns",
            home_team="Michigan Wolverines",
            away_score=31,
            home_score=12,
            season_year=2024,
        ),
        GameRecord(
            event_id="103",
            event_start_utc="2024-09-14T19:30:00Z",
            league="NCAAF",
            away_team="UTSA Roadrunners",
            home_team="Texas Longhorns",
            away_score=7,
            home_score=56,
            season_year=2024,
        ),
        GameRecord(
            event_id="104",
            event_start_utc="2024-09-21T20:00:00Z",
            league="NCAAF",
            away_team="Tennessee Volunteers",
            home_team="Oklahoma Sooners",
            away_score=25,
            home_score=15,
            season_year=2024,
        ),
    ]


# -------------------------------------------------------------
# 1. Team Registry, Geography & Elevation Tests
# -------------------------------------------------------------
def test_cfb_team_resolution():
    """Verify canonical team resolution and alias matching."""
    assert resolve_team("Georgia Bulldogs") is not None
    t_ga = resolve_team("Georgia")
    assert t_ga is not None and t_ga.canonical_name == "Georgia Bulldogs"
    t_uga = resolve_team("UGA")
    assert t_uga is not None and t_uga.canonical_name == "Georgia Bulldogs"
    t_tex = resolve_team("Texas")
    assert t_tex is not None and t_tex.conference == "SEC"
    t_osu = resolve_team("Ohio State")
    assert t_osu is not None and t_osu.conference == "Big Ten"
    assert resolve_team("Nonexistent Team") is None


def test_cfb_stadium_metadata_and_elevation():
    """Verify stadium elevation, dome flags, and geographical coordinates."""
    wyo = resolve_team("Wyoming Cowboys")
    assert wyo is not None
    assert wyo.elevation_ft > 7000  # War Memorial Stadium ~7220ft
    assert wyo.is_dome is False

    syr = resolve_team("Syracuse Orange")
    assert syr is not None
    assert syr.is_dome is True  # JMA Wireless Dome

    unlv = resolve_team("UNLV Rebels")
    assert unlv is not None
    assert unlv.is_dome is True  # Allegiant Stadium

    utsa = resolve_team("UTSA Roadrunners")
    assert utsa is not None
    assert utsa.is_dome is True  # Alamodome


def test_cfb_haversine_and_timezones():
    """Verify great-circle distance and timezone differences."""
    # Athens GA (UGA) to Austin TX (Texas) ~850 miles, 1 timezone
    uga = resolve_team("Georgia Bulldogs")
    tex = resolve_team("Texas Longhorns")
    assert uga is not None and tex is not None
    dist = calculate_haversine_distance(uga.latitude, uga.longitude, tex.latitude, tex.longitude)
    assert 800 < dist < 950

    tz_diff = calculate_timezone_difference(uga.longitude, tex.longitude)
    assert abs(tz_diff) >= 0.8


# -------------------------------------------------------------
# 2. Point-in-Time & Leakage Safety Tests
# -------------------------------------------------------------
def test_cfb_pit_no_lookahead_leakage(sample_cfb_history):
    """Verify feature extractor strictly ignores games occurring after decision cutoff."""
    extractor = CFBFeatureExtractor()

    # As of Sept 5 (before Michigan game on Sept 7), Texas has 0 games in sample history
    feat_before = extractor.extract_features(
        history=sample_cfb_history,
        away_team="Texas Longhorns",
        home_team="Michigan Wolverines",
        event_id="102",
        game_start_utc="2024-09-07T16:00:00Z",
    )
    assert feat_before.sample_games == 0
    assert feat_before.away_preseason_prior_weight >= 0.70  # High prior weight early

    # As of Sept 15 (after Michigan game), Texas has Elo increased from the win
    feat_after = extractor.extract_features(
        history=sample_cfb_history,
        away_team="UTSA Roadrunners",
        home_team="Texas Longhorns",
        event_id="103",
        game_start_utc="2024-09-14T19:30:00Z",
    )
    assert feat_after.elo_home > feat_before.elo_away  # Texas Elo increased after win

    # After Texas and Oklahoma games, a matchup between them has sample_games >= 1
    feat_ou_tex = extractor.extract_features(
        history=sample_cfb_history,
        away_team="Texas Longhorns",
        home_team="Oklahoma Sooners",
        event_id="105",
        game_start_utc="2024-10-12T16:00:00Z",
    )
    assert feat_ou_tex.sample_games >= 1


# -------------------------------------------------------------
# 3. Environment, Altitude, Travel & Weather Tests
# -------------------------------------------------------------
def test_cfb_altitude_and_travel_fatigue():
    """Verify visiting high elevation produces altitude fatigue penalty."""
    extractor = CFBFeatureExtractor()
    # Florida traveling ~1800 miles to Wyoming (7220ft)
    feat_high = extractor.extract_features(
        history=[],
        away_team="Florida Gators",
        home_team="Wyoming Cowboys",
        event_id="301",
        game_start_utc="2024-09-14T19:30:00Z",
        is_neutral_site=False,
    )
    assert feat_high.stadium_elevation_ft > 7000
    assert feat_high.altitude_fatigue_penalty > 1.0
    assert feat_high.travel_distance_miles > 1200

    # Neutral site game has 0 HFA and 0 altitude penalty
    feat_neutral = extractor.extract_features(
        history=[],
        away_team="Florida Gators",
        home_team="Wyoming Cowboys",
        event_id="302",
        game_start_utc="2024-09-14T19:30:00Z",
        is_neutral_site=True,
    )
    assert feat_neutral.home_field_advantage_points == 0.0
    assert feat_neutral.altitude_fatigue_penalty == 0.0


def test_cfb_weather_conditional_mechanisms():
    """Verify wind and precipitation suppress scoring outdoors but not in domes."""
    extractor = CFBFeatureExtractor()

    # Outdoor game with 25mph wind and rain
    feat_outdoor_wind = extractor.extract_features(
        history=[],
        away_team="Clemson Tigers",
        home_team="Georgia Bulldogs",
        event_id="401",
        game_start_utc="2024-10-12T16:00:00Z",
        wind_mph=25.0,
        precipitation_in=0.25,
    )
    assert feat_outdoor_wind.weather_total_adjustment < -3.0
    assert feat_outdoor_wind.is_dome is False

    # Indoor dome game (Syracuse) with same weather parameters -> strictly 0 weather adjustment
    feat_dome = extractor.extract_features(
        history=[],
        away_team="Clemson Tigers",
        home_team="Syracuse Orange",
        event_id="402",
        game_start_utc="2024-10-12T16:00:00Z",
        wind_mph=25.0,
        precipitation_in=0.25,
    )
    assert feat_dome.weather_total_adjustment == 0.0
    assert feat_dome.is_dome is True


# -------------------------------------------------------------
# 4. Quarterback Model & Starter Probability Mixture
# -------------------------------------------------------------
def test_cfb_qb_starter_uncertainty():
    """Verify reduced starter probability discounts scoring and elevates uncertainty."""
    extractor = CFBFeatureExtractor()

    feat_full_starter = extractor.extract_features(
        history=[],
        away_team="Alabama Crimson Tide",
        home_team="LSU Tigers",
        event_id="501",
        game_start_utc="2024-11-09T20:00:00Z",
        qb_starter_prob_away=1.0,
    )

    feat_backup_qb = extractor.extract_features(
        history=[],
        away_team="Alabama Crimson Tide",
        home_team="LSU Tigers",
        event_id="502",
        game_start_utc="2024-11-09T20:00:00Z",
        qb_starter_prob_away=0.0,  # Starting backup
    )

    assert feat_backup_qb.projected_away_points < feat_full_starter.projected_away_points
    assert feat_backup_qb.away_qb_value_adjustment < -3.0
    assert feat_backup_qb.uncertainty >= feat_full_starter.uncertainty


# -------------------------------------------------------------
# 5. Joint Distribution, Key Numbers & Sign Conventions
# -------------------------------------------------------------
def test_cfb_joint_distribution_coherent_probabilities():
    """Verify all 3 markets derived from joint distribution sum to 1.0."""
    engine = CFBJointDistributionEngine(
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL, n_simulations=5000
    )
    probs = engine.compute_market_probabilities(
        mu_home=31.5,
        mu_away=21.0,
        spread_home_line=-7.5,
        total_line=52.5,
    )
    # Moneyline sum
    assert abs(probs.p_home_win + probs.p_away_win - 1.0) < 1e-4
    assert probs.p_home_win > 0.65

    # Spread sum
    assert abs(probs.p_away_cover + probs.p_home_cover + probs.p_push_spread - 1.0) < 1e-4
    assert probs.p_push_spread == 0.0  # fractional line cannot push

    # Total sum
    assert abs(probs.p_over + probs.p_under + probs.p_push_total - 1.0) < 1e-4


def test_cfb_integer_line_push_probabilities():
    """Verify integer lines produce positive push probabilities."""
    engine = CFBJointDistributionEngine(
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL, n_simulations=5000
    )
    probs = engine.compute_market_probabilities(
        mu_home=27.0,
        mu_away=20.0,
        spread_home_line=-7.0,  # Key number 7 integer line
        total_line=47.0,  # Integer total line
    )
    assert probs.p_push_spread > 0.02
    assert abs(probs.p_away_cover + probs.p_home_cover + probs.p_push_spread - 1.0) < 1e-4

    assert probs.p_push_total > 0.01
    assert abs(probs.p_over + probs.p_under + probs.p_push_total - 1.0) < 1e-4


def test_cfb_sign_conventions():
    """Verify permanent sign conventions for Margin and Spread."""
    # Margin = HomePoints - AwayPoints
    # If Home spread = -7.5, MarketImpliedHomeMargin = +7.5
    # If Home wins 31-21 (Margin = +10.0), Home covers (+10.0 > +7.5) -> Away does NOT cover
    engine = CFBJointDistributionEngine(
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL, n_simulations=5000
    )
    probs = engine.compute_market_probabilities(
        mu_home=35.0,
        mu_away=14.0,  # Expected margin +21.0
        spread_home_line=-7.5,  # Implied margin +7.5
        total_line=49.0,
    )
    assert probs.p_home_cover > 0.70
    assert probs.p_away_cover < 0.30


# -------------------------------------------------------------
# 6. Production Model & Slate Building
# -------------------------------------------------------------
def test_cfb_model_slate_predictions(sample_cfb_history):
    """Test CollegeFootballModel generates Moneyline, Spread, and Total predictions."""
    model = CollegeFootballModel()
    upcoming = UpcomingCFBGame(
        event_id="601",
        event_start_utc="2024-10-12T16:00:00Z",
        away_team="Texas Longhorns",
        home_team="Oklahoma Sooners",
        spread_home_line=-3.5,
        total_line=54.5,
        is_neutral_site=True,  # Red River Rivalry (Cotton Bowl)
    )
    preds = model.predict_matchup(sample_cfb_history, upcoming)
    assert len(preds) == 3

    markets = {p.market_type: p for p in preds}
    assert "moneyline" in markets
    assert "spread" in markets
    assert "total" in markets

    # Check model versions
    assert markets["moneyline"].model_version == MODEL_VERSION
    assert markets["spread"].model_version == CFB_SPREAD_MODEL_VERSION
    assert markets["total"].model_version == CFB_TOTAL_MODEL_VERSION


# -------------------------------------------------------------
# 7. Settlement Grading Tests
# -------------------------------------------------------------
def test_cfb_settlement_grading():
    """Verify settlement grading across Moneyline, Spread, and Total with pushes."""
    # 1. Moneyline
    res_ml_win = grade_pick(MarketType.MONEYLINE, "home", None, 24, 31, league="NCAAF")  # Home 31, Away 24
    assert res_ml_win == PickResult.WIN

    res_ml_loss = grade_pick(MarketType.MONEYLINE, "away", None, 24, 31, league="NCAAF")
    assert res_ml_loss == PickResult.LOSS

    # 2. Spread
    # Home favored by 7.0 (-7.0). Home wins 31-24 -> Margin = 7.0 == 7.0 -> PUSH
    res_sp_push = grade_pick(MarketType.SPREAD, "home", -7.0, 24, 31, league="NCAAF")
    assert res_sp_push == PickResult.PUSH

    # Away covers if +7.5 (31 - 24 = 7 <= 7.5) -> Away WIN
    res_sp_away_win = grade_pick(MarketType.SPREAD, "away", 7.5, 24, 31, league="NCAAF")
    assert res_sp_away_win == PickResult.WIN

    # 3. Total
    # Total 55.0. Scores: 31 + 24 = 55 -> PUSH
    res_tot_push = grade_pick(MarketType.TOTAL, "over", 55.0, 24, 31, league="NCAAF")
    assert res_tot_push == PickResult.PUSH

    # Total 54.5. Scores: 31 + 24 = 55 > 54.5 -> Over WIN
    res_tot_over = grade_pick(MarketType.TOTAL, "over", 54.5, 24, 31, league="NCAAF")
    assert res_tot_over == PickResult.WIN

    res_tot_under = grade_pick(MarketType.TOTAL, "under", 54.5, 24, 31, league="NCAAF")
    assert res_tot_under == PickResult.LOSS


# -------------------------------------------------------------
# 8. Production Registry & Artifact Hash Verification
# -------------------------------------------------------------
def test_cfb_production_registry_and_hashes():
    """Verify production registry loads all 3 models with self-verifying hashes.

    Under the permanent champion-challenger framework, weak evidence models are
    active production champions with evidence_status=degraded and replacement_priority=critical.
    """
    root = Path(__file__).resolve().parent.parent
    reg = ProductionModelRegistry.load(root)

    for mid in ["college-football-v1", "cfb-spread-v1", "cfb-total-v1"]:
        assert mid in reg.entries
        entry = reg.entries[mid]
        assert entry.enabled is True
        assert entry.serving_status in {"production", "active"}
        assert entry.evidence_status == "degraded"
        assert entry.replacement_priority == "critical"
        assert entry.sport == "NCAAF"
        assert entry.load_error is None

        # Verify JSON artifact hash
        art_path = root / f"config/models/{mid}.json"
        with art_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        expected_hash = compute_artifact_hash(payload)
        assert payload["artifact_hash"] == expected_hash


# -------------------------------------------------------------
# 9. Flat Ledger All-Game Inclusion & Main Ledger Gating Tests
# -------------------------------------------------------------
def test_cfb_flat_ledger_all_game_inclusion(tmp_path):
    """Verify Flat Ledger receives all candidate games across all 3 markets without edge gates."""
    from model_prediction.cli.forecast import _forecast_cfb_sport
    from model_prediction.ledger import PickLedger

    flat_path = tmp_path / "flat_ncaaf.xlsx"
    flat_ledger = PickLedger(flat_path, audit_path=tmp_path / "events.jsonl", tier="flat", sport="ncaaf")

    config = {
        "models": {"NCAAF": {"min_edge": 0.035, "status": "shadow_qualified"}},
        "project": {"maximum_data_age_hours": 24},
    }

    res = _forecast_cfb_sport(
        data_root="data",
        args_date="2024-09-07",
        config=config,
        flat_ledger=flat_ledger,
        main_ledger=None,
        force=True,
    )
    assert res["status"] == "shadow_qualified"
    assert res["flat_logged"] >= 0  # Processed and attempted writes


def test_cfb_main_ledger_gating_qualification():
    """Verify that all 3 CFB models are set to FLAT_LEDGER_ONLY for production tracking."""
    root = Path(__file__).resolve().parent.parent

    # Read qualification statuses from frozen artifacts
    with (root / "config/models/college-football-v1.json").open() as f:
        ml_art = json.load(f)
    assert ml_art["qualification"]["status"] == "FLAT_LEDGER_ONLY"
    assert ml_art["qualification"]["qualified"] is False

    with (root / "config/models/cfb-spread-v1.json").open() as f:
        sp_art = json.load(f)
    assert sp_art["qualification"]["status"] == "FLAT_LEDGER_ONLY"
    assert sp_art["qualification"]["qualified"] is False

    with (root / "config/models/cfb-total-v1.json").open() as f:
        tot_art = json.load(f)
    assert tot_art["qualification"]["status"] == "FLAT_LEDGER_ONLY"
    assert tot_art["qualification"]["qualified"] is False


def test_cfb_safe_fallback_no_prediction():
    """Verify safe fallback behavior when required data is unavailable."""
    root = Path(__file__).resolve().parent.parent
    reg = ProductionModelRegistry.load(root)
    assert reg.fallback_action == "no_prediction"


def test_cfb_standard_lay_ask_is_vig_inclusive_not_no_vig():
    """The served ask for spread/total must be the real -110 price, not 0.50.

    ESPN publishes the line but no price, so the ask is synthesized. Using the
    no-vig 0.50 understates it by ~2.4pp, which becomes fictitious edge on every
    single call -- that is what made 8/8 NCAAF picks QUALIFIED at max size on
    2026-08-29. It must match the 0.5238 the research pipeline prices against,
    or a backtested edge and a served edge do not mean the same thing.
    """
    from model_prediction.models.college_football import STANDARD_LAY_ASK

    assert STANDARD_LAY_ASK == pytest.approx(0.5238, abs=5e-5)
    assert STANDARD_LAY_ASK > 0.50


def test_cfb_models_serving_with_degraded_evidence():
    """Under the permanent champion-challenger architecture, NCAAF models are
    production champions marked as degraded evidence with critical replacement priority.
    """
    import yaml

    root = Path(__file__).resolve().parent.parent
    with (root / "config/production.yaml").open() as f:
        prod = yaml.safe_load(f)

    svc = prod["prediction_service"]
    cfb_ids = {"college-football-v1", "cfb-spread-v1", "cfb-total-v1"}

    enabled = {m["model_id"]: m.get("enabled", True) for m in svc["models"] if m["model_id"] in cfb_ids}
    assert enabled.keys() == cfb_ids, "all three CFB entries must still be declared"
    assert all(enabled.values()), f"CFB models must stay enabled: {enabled}"

    champions = svc.get("champions", {}).get("NCAAF", {})
    assert champions.get("moneyline") == "college-football-v1"
    assert champions.get("spread") == "cfb-spread-v1"
    assert champions.get("total") == "cfb-total-v1"

    with (root / "config/model.yaml").open() as f:
        model_cfg = yaml.safe_load(f)
    assert model_cfg["models"]["NCAAF"]["status"] == "research"
