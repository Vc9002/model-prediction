"""Unit tests for MLB v9 Dual-Horizon Forecasting (Roadmap Phase 16)."""

from model_prediction.models.mlb_v9_horizons import (
    MLBHorizonForecast,
    compare_horizons,
)


def test_compare_horizons_negligible_shift():
    early = MLBHorizonForecast(
        horizon="early_projected",
        event_id="401816268",
        home_team="NYY",
        away_team="BOS",
        home_win_probability=0.550,
        projected_total_runs=8.5,
        lineup_source="projected_priors",
        feature_count=20,
        observed_at_utc="2026-08-23T14:00:00Z",
    )
    late = MLBHorizonForecast(
        horizon="late_confirmed",
        event_id="401816268",
        home_team="NYY",
        away_team="BOS",
        home_win_probability=0.558,
        projected_total_runs=8.6,
        lineup_source="confirmed_pregame",
        feature_count=22,
        observed_at_utc="2026-08-23T18:15:00Z",
    )

    comp = compare_horizons(early, late)
    assert comp.prob_shift_pp == 0.8
    assert comp.lineup_shift_significance == "negligible"


def test_compare_horizons_major_shift_on_star_scratch():
    early = MLBHorizonForecast(
        horizon="early_projected",
        event_id="401816269",
        home_team="LAD",
        away_team="SF",
        home_win_probability=0.640,
        projected_total_runs=9.0,
        lineup_source="projected_priors",
        feature_count=20,
        observed_at_utc="2026-08-23T14:00:00Z",
    )
    late = MLBHorizonForecast(
        horizon="late_confirmed",
        event_id="401816269",
        home_team="LAD",
        away_team="SF",
        home_win_probability=0.575,
        projected_total_runs=8.1,
        lineup_source="confirmed_pregame",
        feature_count=22,
        observed_at_utc="2026-08-23T18:15:00Z",
    )

    comp = compare_horizons(early, late)
    assert comp.prob_shift_pp == -6.5
    assert comp.lineup_shift_significance == "major"
