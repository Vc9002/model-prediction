"""Elasticities (added 2026-07-30) replace estimate_runs()'s previous implicit
assumption that every run-scaling factor (offense, opposing starter weakness,
opposing bullpen weakness, park, weather) moves the estimate exactly
proportionally (exponent 1.0). These tests verify the exponentiation itself,
independent of what real values were fit -- that lives in the production
yaml's comments and DEBUG.md, not here.
"""

from __future__ import annotations

import textwrap

import pytest

from model_prediction.models.mlb import MLBGameFeatures as _Features
from model_prediction.models.mlb import (
    PitcherForm,
    TeamForm,
    estimate_runs,
    load_formula_spec,
)

_BASE_YAML = """
away_field_run_factor: 1.0
factor_bounds:
  bullpen_weakness: [0.5, 2.0]
  park: [0.8, 1.3]
  weather: [0.85, 1.2]
  offense: [0.7, 1.4]
  starter_weakness: [0.5, 2.0]
feature_schema_version: v3
formula_version: mlb-analyst-poisson-trend-v0.2
home_field_run_factor: 1.0
league_runs_per_team_game: 4.5
league_starter_era: 4.2
league_strikeout_rate: 0.22
league_walk_rate: 0.08
recent_half_life_games: 10.0
recent_prior_strength_games: 12.0
starter_recent_prior_innings: 20.0
starter_season_prior_innings: 40.0
starter_rate_prior_batters_faced: 60.0
offense_elasticity: {offense_elasticity}
starter_weakness_elasticity: {starter_weakness_elasticity}
bullpen_elasticity: {bullpen_elasticity}
park_elasticity: {park_elasticity}
weather_elasticity: {weather_elasticity}
seed_method: stable_hash
simulation:
  simulations: 100
  seed: 42
  shared_environment_variance: 0.06
  team_specific_variance: 0.12
  extra_inning_home_probability_bounds: [0.50, 0.55]
starter_recent_weight: 0.7
starter_season_weight: 0.3
strikeout_weight: 0.15
uncertainty:
  base: 0.02
  model_form: 0.01
  pitcher: 0.015
  rookie_starter: 0.03
  bullpen_unavailable: 0.02
  weather_unavailable: 0.01
  lineup_projected: 0.015
  lineup_unavailable: 0.025
  missing_xfip: 0.01
  missing_wrc_plus: 0.01
  minimum: 0.02
  maximum: 0.15
walk_weight: 0.1
"""


def _spec(tmp_path, **elasticities):
    values = {
        "offense_elasticity": 1.0,
        "starter_weakness_elasticity": 1.0,
        "bullpen_elasticity": 1.0,
        "park_elasticity": 1.0,
        "weather_elasticity": 1.0,
        **elasticities,
    }
    path = tmp_path / "spec.yaml"
    path.write_text(textwrap.dedent(_BASE_YAML).format(**values), encoding="utf-8")
    return load_formula_spec(path)


def _pitcher() -> PitcherForm:
    return PitcherForm(
        player_id="p", name="p", throwing_hand="R", starts_before_game=20,
        season_innings=100.0, season_earned_runs=40, season_strikeouts=90,
        season_walks=30, season_batters_faced=420,
        last_five_innings=28.0, last_five_earned_runs=12,
        last_five_strikeouts=25, last_five_walks=8, last_five_batters_faced=120,
    )


def _features(*, park_factor: float, bullpen_weakness: float) -> _Features:
    form = TeamForm(runs_scored=(4, 5, 3, 6, 4), runs_allowed=(3, 4, 5, 3, 4), wins=3, losses=2)
    return _Features(
        event_id="e1", event_start_utc="2026-07-01T20:00:00Z",
        decision_timestamp_utc="2026-07-01T18:00:00Z",
        away_team="Away", home_team="Home",
        away_form=form, home_form=form,
        away_starter=_pitcher(), home_starter=_pitcher(),
        away_bullpen_weakness=bullpen_weakness, home_bullpen_weakness=bullpen_weakness,
        park_factor=park_factor, weather_factor=1.0,
    )


def test_zero_elasticity_makes_a_factor_irrelevant(tmp_path):
    """With park_elasticity=0.0, changing the real park factor must not move
    the run estimate at all -- the whole point of a real (near-)zero fitted
    elasticity is that the feature isn't trusted to matter."""
    spec = _spec(tmp_path, park_elasticity=0.0)
    neutral = estimate_runs(_features(park_factor=1.0, bullpen_weakness=1.0), spec)
    hitters_park = estimate_runs(_features(park_factor=1.25, bullpen_weakness=1.0), spec)
    assert neutral.away_expected_runs == pytest.approx(hitters_park.away_expected_runs)
    assert neutral.home_expected_runs == pytest.approx(hitters_park.home_expected_runs)


def test_full_elasticity_matches_direct_multiplication(tmp_path):
    """elasticity=1.0 must reproduce the pre-rebuild behavior exactly:
    factor**1.0 == factor, so this is a strict generalization, not a
    behavior change, for any spec that keeps every elasticity at 1.0."""
    spec = _spec(tmp_path)  # all elasticities default to 1.0
    baseline = estimate_runs(_features(park_factor=1.0, bullpen_weakness=1.0), spec)
    boosted_park = estimate_runs(_features(park_factor=1.2, bullpen_weakness=1.0), spec)
    # Both away and home expected runs scale by exactly the park ratio when
    # elasticity is 1.0 (park affects both sides identically).
    ratio = boosted_park.away_expected_runs / baseline.away_expected_runs
    assert ratio == pytest.approx(1.2, rel=1e-6)


def test_fractional_elasticity_dampens_the_factors_swing(tmp_path):
    """A real elasticity between 0 and 1 (like the fitted 0.211 for opposing
    starter weakness) must move the estimate less than the raw factor swing
    -- less than a full 1.0 multiplicative pass-through."""
    spec_full = _spec(tmp_path, starter_weakness_elasticity=1.0)
    spec_half = _spec(tmp_path, starter_weakness_elasticity=0.2)

    # away_expected depends on home_starter_weakness; vary the home starter's
    # season ERA to move that index away from neutral.
    weak_pitcher = PitcherForm(
        player_id="p", name="p", throwing_hand="R", starts_before_game=20,
        season_innings=100.0, season_earned_runs=90, season_strikeouts=60,
        season_walks=50, season_batters_faced=420,
        last_five_innings=28.0, last_five_earned_runs=25,
        last_five_strikeouts=15, last_five_walks=15, last_five_batters_faced=120,
    )
    form = TeamForm(runs_scored=(4, 5, 3, 6, 4), runs_allowed=(3, 4, 5, 3, 4), wins=3, losses=2)
    features = _Features(
        event_id="e1", event_start_utc="2026-07-01T20:00:00Z",
        decision_timestamp_utc="2026-07-01T18:00:00Z",
        away_team="Away", home_team="Home",
        away_form=form, home_form=form,
        away_starter=_pitcher(), home_starter=weak_pitcher,
        away_bullpen_weakness=1.0, home_bullpen_weakness=1.0,
        park_factor=1.0, weather_factor=1.0,
    )
    neutral_features = _features(park_factor=1.0, bullpen_weakness=1.0)

    full_swing = (
        estimate_runs(features, spec_full).away_expected_runs
        - estimate_runs(neutral_features, spec_full).away_expected_runs
    )
    dampened_swing = (
        estimate_runs(features, spec_half).away_expected_runs
        - estimate_runs(neutral_features, spec_half).away_expected_runs
    )
    assert abs(dampened_swing) < abs(full_swing)


def test_engine_version_rejects_the_un_promoted_v03_elasticity_refit() -> None:
    """Real safety net verified by review 2026-08-02: ENGINE_VERSION is the
    ONLY thing preventing config/models/mlb-analyst-poisson-trend-v0.3.yaml
    (a real fitted-elasticity refit, materially different from what's
    promoted -- e.g. bullpen_elasticity=0.069 vs. the promoted 0.0) from
    loading live. This locks that rejection in so a future edit to
    load_formula_spec's version check can't silently stop enforcing it.
    """
    from pathlib import Path

    v03_path = Path("config/models/mlb-analyst-poisson-trend-v0.3.yaml")
    if not v03_path.exists():
        pytest.skip("mlb-analyst-poisson-trend-v0.3.yaml not present in this checkout")
    with pytest.raises(ValueError, match="does not match the Trend Engine code"):
        load_formula_spec(v03_path)
