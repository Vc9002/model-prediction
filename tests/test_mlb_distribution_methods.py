"""Joint score-distribution methods for the unified MLB score engine.

verify the three distribution methods (gamma_poisson, negative_binomial,
independent_poisson) all draw away/home runs from ONE coherent joint
distribution and derive moneyline/spread/total from that single draw — the
"one MLB score distribution for ML + spread + total" architecture from the
model roadmap. Default behavior (gamma_poisson) must stay bit-for-bit
deterministic across a method refactor, and each method must produce valid
probabilities with the correct push handling.
"""

from __future__ import annotations

import hashlib
import json
import textwrap

import numpy as np
import pytest

from model_prediction.domain import MarketType
from model_prediction.models.mlb import (
    DISTRIBUTION_METHODS,
    PitcherForm,
    TeamForm,
    _clip,
    compare_distribution_methods,
    derive_market_distribution,
    estimate_runs,
    load_formula_spec,
    simulate_game,
    stable_seed,
)
from model_prediction.models.mlb import (
    MLBGameFeatures as _Features,
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
formula_version: mlb-analyst-poisson-trend-v0.3
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
offense_elasticity: 1.0
starter_weakness_elasticity: 1.0
bullpen_elasticity: 1.0
park_elasticity: 1.0
weather_elasticity: 1.0
seed_method: stable_hash
simulation:
  simulations: 2000
  seed: 42
  shared_environment_variance: 0.06
  team_specific_variance: 0.12
  negative_binomial_phi: 1.2
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


def _spec(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text(textwrap.dedent(_BASE_YAML), encoding="utf-8")
    return load_formula_spec(path)


def _pitcher() -> PitcherForm:
    return PitcherForm(
        player_id="p",
        name="p",
        throwing_hand="R",
        starts_before_game=20,
        season_innings=100.0,
        season_earned_runs=40,
        season_strikeouts=90,
        season_walks=30,
        season_batters_faced=420,
        last_five_innings=28.0,
        last_five_earned_runs=12,
        last_five_strikeouts=25,
        last_five_walks=8,
        last_five_batters_faced=120,
    )


def _features() -> _Features:
    form = TeamForm(runs_scored=(4, 5, 3, 6, 4), runs_allowed=(3, 4, 5, 3, 4), wins=3, losses=2)
    return _Features(
        event_id="e1",
        event_start_utc="2026-07-01T20:00:00Z",
        decision_timestamp_utc="2026-07-01T18:00:00Z",
        away_team="Away",
        home_team="Home",
        away_form=form,
        home_form=form,
        away_starter=_pitcher(),
        home_starter=_pitcher(),
        away_bullpen_weakness=1.0,
        home_bullpen_weakness=1.0,
        park_factor=1.0,
        weather_factor=1.0,
    )


def _assert_valid(dist) -> None:
    total = dist.first_win_probability + dist.second_win_probability + dist.push_probability
    assert total == pytest.approx(1.0, abs=1e-6)
    for p in (dist.first_win_probability, dist.second_win_probability, dist.push_probability):
        assert 0.0 <= p <= 1.0


def test_distribution_methods_constant_is_the_public_catalog(tmp_path):
    assert set(DISTRIBUTION_METHODS) == {"gamma_poisson", "negative_binomial", "independent_poisson"}


def test_default_method_is_unchanged_and_deterministic(tmp_path):
    spec = _spec(tmp_path)
    features = _features()
    estimate = estimate_runs(features, spec)
    first = simulate_game(features, estimate, spec, simulations=2000)
    second = simulate_game(features, estimate, spec, simulations=2000)
    # Deterministic seed: same input -> identical draw.
    assert first.away_scores == second.away_scores
    assert first.home_scores == second.home_scores
    # Default method is gamma_poisson (the incumbent) — never silently changed.
    assert len(first.away_scores) == 2000

    # Bit-for-bit pin against the PRE-refactor stream. The method refactor
    # appended `method` to stable_seed's parts, silently changing every
    # incumbent gamma_poisson simulated price. The default path's seed
    # excludes method (restored 2026-08-13), so reproduce the old formula by
    # hand -- stable_seed(event_id, formula_version, decision_timestamp,
    # market_snapshot_hash, feature_snapshot_hash, seed_namespace="") -- and
    # require EXACT equality with what simulate_game emits.
    old_seed = stable_seed(
        features.event_id,
        spec.formula_version,
        features.decision_timestamp_utc,
        features.market_snapshot_hash,
        features.feature_snapshot_hash,
        "",
    )
    rng = np.random.default_rng(old_seed)
    shared_variance = float(spec.simulation["shared_environment_variance"])
    team_variance = float(spec.simulation["team_specific_variance"])
    shared = rng.gamma(1 / shared_variance, shared_variance, 2000)
    away_specific = rng.gamma(1 / team_variance, team_variance, 2000)
    home_specific = rng.gamma(1 / team_variance, team_variance, 2000)
    away = rng.poisson(estimate.away_expected_runs * shared * away_specific)
    home = rng.poisson(estimate.home_expected_runs * shared * home_specific)
    ties = away == home
    if ties.any():
        lower, upper = spec.simulation["extra_inning_home_probability_bounds"]
        home_probability = _clip(
            estimate.home_expected_runs / (estimate.home_expected_runs + estimate.away_expected_runs),
            (float(lower), float(upper)),
        )
        home_wins = rng.random(int(ties.sum())) < home_probability
        tie_indices = np.flatnonzero(ties)
        home[tie_indices[home_wins]] += 1
        away[tie_indices[~home_wins]] += 1
    assert first.away_scores == away.tolist()
    assert first.home_scores == home.tolist()

    # And pin the digest so any future drift in the default seed formula
    # fails loudly instead of silently shifting every price. Computed from
    # the stream above -- MUST NEVER CHANGE.
    digest = hashlib.sha256(
        json.dumps([first.away_scores, first.home_scores], separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == "17feb6c0ec0a030288e016466353ae8433ece0d8e66e692f50cf9e4d584c1310"


def test_unknown_method_rejected(tmp_path):
    spec = _spec(tmp_path)
    features = _features()
    estimate = estimate_runs(features, spec)
    with pytest.raises(ValueError, match="unknown distribution method"):
        simulate_game(features, estimate, spec, method="not_a_method")


def test_every_method_draws_valid_moneyline(tmp_path):
    spec = _spec(tmp_path)
    features = _features()
    estimate = estimate_runs(features, spec)
    for method in DISTRIBUTION_METHODS:
        sim = simulate_game(features, estimate, spec, simulations=2000, method=method)
        dist = derive_market_distribution(sim, MarketType.MONEYLINE)
        _assert_valid(dist)


def test_negative_binomial_overdisperses_relative_to_poisson(tmp_path):
    """NB with phi=1.2 must widen the total distribution vs independent
    Poisson — the concrete reason it is the first serious challenger."""
    spec = _spec(tmp_path)
    features = _features()
    estimate = estimate_runs(features, spec)
    poisson = simulate_game(features, estimate, spec, simulations=20000, method="independent_poisson")
    nb = simulate_game(features, estimate, spec, simulations=20000, method="negative_binomial")
    poisson_total = np.asarray(poisson.away_scores) + np.asarray(poisson.home_scores)
    nb_total = np.asarray(nb.away_scores) + np.asarray(nb.home_scores)
    assert nb_total.std() > poisson_total.std()


def test_compare_derives_all_three_markets_from_one_draw_per_method(tmp_path):
    spec = _spec(tmp_path)
    features = _features()
    estimate = estimate_runs(features, spec)
    result = compare_distribution_methods(
        features, estimate, spec, simulations=2000, spread_line=-1.5, total_line=8.5
    )
    assert set(result) == set(DISTRIBUTION_METHODS)
    for markets in result.values():
        assert set(markets) == {"moneyline", "spread", "total"}
        for dist in markets.values():
            _assert_valid(dist)
