"""Unit tests for NFL Quarterback and Offensive Line Feature Engine."""

from model_prediction.features.nfl_qb_oline import (
    NFLOffensiveLineProfile,
    evaluate_qb_profile,
    extract_nfl_matchup_features,
)


def test_evaluate_qb_profile_elite():
    # Elite starter: +0.22 EPA, +4.5 CPOE
    qb = evaluate_qb_profile("Patrick Mahomes", "KC", 0.22, 4.5, 0.12, 0.018, 500, is_starter=True)
    assert qb.is_starter is True
    assert qb.spread_value_pts >= 5.0
    assert qb.epa_per_dropback > 0.10


def test_evaluate_qb_profile_backup():
    qb = evaluate_qb_profile("Backup QB", "NYJ", 0.0, 0.0, 0.25, 0.045, 0, is_starter=False)
    assert qb.is_starter is False
    assert qb.spread_value_pts == 0.0
    assert qb.epa_per_dropback < 0.0


def test_extract_nfl_matchup_features():
    home_qb = evaluate_qb_profile("Josh Allen", "BUF", 0.20, 3.8, 0.14, 0.022, 500, is_starter=True)
    away_qb = evaluate_qb_profile("Backup QB", "MIA", -0.10, -4.0, 0.26, 0.045, 100, is_starter=False)

    home_oline = NFLOffensiveLineProfile(
        "BUF", 75.0, 70.0, missing_starters=0, adjusted_sack_rate=0.045, oline_penalty_pts=0.0
    )
    away_oline = NFLOffensiveLineProfile(
        "MIA", 50.0, 48.0, missing_starters=2, adjusted_sack_rate=0.095, oline_penalty_pts=-1.0
    )

    feats = extract_nfl_matchup_features(home_qb, away_qb, home_oline, away_oline)
    assert feats.qb_value_gap > 4.0
    assert feats.oline_protection_gap > 0.0
    assert feats.projected_spread_margin > 6.0  # Large favorite margin
    assert feats.projected_total_points > 40.0
