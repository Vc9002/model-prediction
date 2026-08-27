"""Tests for the first-inning PA simulator.

Pins determinism (seeded), the fallback-to-priors behavior for missing
features, and the direction of the power modulation. The simulator is a
mechanism, not a tuned model — these are property tests, not accuracy
tests.
"""

from __future__ import annotations

from model_prediction.models.mlb_first_inning import FirstInningGameRow
from model_prediction.models.mlb_first_inning_sim import (
    first_inning_run_distribution,
    nrfi_probability,
    simulate_first_inning,
)


def _row(top3: float = 0.32, k_pct: float = 0.22, bb_pct: float = 0.08) -> FirstInningGameRow:
    return FirstInningGameRow(
        game_pk=1,
        game_start_utc="2026-06-01T23:05:00Z",
        home_team="H",
        away_team="A",
        venue_name="P",
        features={
            "home_starter_k_pct": k_pct,
            "home_starter_bb_pct": bb_pct,
            "away_starter_k_pct": k_pct,
            "away_starter_bb_pct": bb_pct,
            "home_top3_composite": top3,
            "away_top3_composite": top3,
        },
        nrfi=0,
        runs_1st_total=0.0,
    )


def test_deterministic_seeded_simulation():
    row = _row()
    a = simulate_first_inning(row, n_sims=500, seed=7)
    b = simulate_first_inning(row, n_sims=500, seed=7)
    assert a == b
    c = simulate_first_inning(row, n_sims=500, seed=8)
    assert a != c  # different seed, different draws (overwhelmingly likely)


def test_run_distribution_sums_to_one_and_nrfi_is_zero_mass():
    sims = simulate_first_inning(_row(), n_sims=2000)
    dist = first_inning_run_distribution(sims)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    # The zero-run mass matches the reported NRFI probability.
    assert dist.get(0, 0.0) == nrfi_probability(sims)


def test_power_modulation_reduces_zero_run_share():
    weak = nrfi_probability(simulate_first_inning(_row(top3=0.20), n_sims=4000, seed=1))
    strong = nrfi_probability(simulate_first_inning(_row(top3=0.44), n_sims=4000, seed=1))
    # Stronger top-of-order offense -> fewer scoreless innings.
    assert strong < weak


def test_missing_features_fall_back_to_league_rates():
    row = FirstInningGameRow(
        game_pk=1,
        game_start_utc="2026-06-01T23:05:00Z",
        home_team="H",
        away_team="A",
        venue_name="P",
        features={},
        nrfi=0,
        runs_1st_total=0.0,
    )
    sims = simulate_first_inning(row, n_sims=4000)
    p = nrfi_probability(sims)
    # League-mean inputs should land near the measured league NRFI rate
    # (0.5106; the base rates carry a one-time calibration to it).
    assert 0.46 < p < 0.57
