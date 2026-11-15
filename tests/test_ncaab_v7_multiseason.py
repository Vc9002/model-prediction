"""Pure-function tests for the NCAAB v7 multi-season shadow components (quick 261115-m5r)."""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.models.ncaab_v7_multiseason import (
    CONFERENCE_ID_NAMES,
    LEGACY_PAC12_TIER,
    accumulate_normal_equations,
    build_asof_conferences,
    geometric_season_weights,
    ridge_by_accumulation,
    tier_for,
    variant_grid,
    variant_key,
)
from model_prediction.models.ncaab_v7_shadow import ridge_team_strengths


def test_accumulated_ridge_equals_dense_ridge() -> None:
    rng = np.random.default_rng(5)
    n = 9
    home = rng.integers(0, n, 400)
    away = (home + rng.integers(1, n, 400)) % n
    margin = rng.normal(3, 11, 400)
    weights = rng.uniform(0.2, 1.0, 400)
    venue = (rng.uniform(size=400) > 0.25).astype(float)
    prior = rng.normal(0, 4, n)
    dense = ridge_team_strengths(home, away, margin, weights, venue == 0.0, n, prior, 5.0)
    acc = ridge_by_accumulation(home, away, margin, weights, venue, n, prior, 5.0)
    assert np.allclose(dense[0], acc[0]) and dense[1] == pytest.approx(acc[1])
    gram = accumulate_normal_equations(home, away, margin, weights, venue, n)
    reuse = ridge_by_accumulation(home, away, margin, weights, venue, n, prior, 5.0, gram=gram)
    assert np.allclose(acc[0], reuse[0])


def test_geometric_weights_decay_and_cap() -> None:
    seasons = np.array([2027, 2026, 2025, 2024, 2023, 2028])
    w = geometric_season_weights(seasons, 2027, 0.5, max_age=3)
    assert w.tolist() == [1.0, 0.5, 0.25, 0.125, 0.0, 0.0]  # age 4 dropped, future season dropped


def test_asof_map_gives_realigned_team_its_own_season_tier() -> None:
    events = [
        {
            "season_year": 2024,
            "home_team": "Texas",
            "away_team": "X",
            "home_conference_id": "8",
            "away_conference_id": None,
        },
        {
            "season_year": 2025,
            "home_team": "Texas",
            "away_team": "X",
            "home_conference_id": "23",
            "away_conference_id": None,
        },
    ]
    m = build_asof_conferences(events)
    assert m[2024] == {"Texas": "8"} and m[2025] == {"Texas": "23"}  # non-D1 (None id) never enters the map
    assert tier_for("8", 2024) == tier_for("23", 2025) == 1680.0


def test_unknown_conference_id_fails_closed_and_d1_independent_is_listed() -> None:
    with pytest.raises(ValueError, match="no name"):
        tier_for("999", 2025)
    assert tier_for("43", 2024) == 1180.0  # listed D1 independents: non-D1 prior, not an error


def test_legacy_pac12_era_override_only_through_2024() -> None:
    assert tier_for("21", 2024) == LEGACY_PAC12_TIER
    assert tier_for("21", 2027) == 1550.0  # the rebuilt 2026-27 Pac-12 uses the table value


def test_team_with_two_ids_in_one_season_raises() -> None:
    events = [
        {
            "season_year": 2025,
            "home_team": "T",
            "away_team": "Y",
            "home_conference_id": "8",
            "away_conference_id": None,
        },
        {
            "season_year": 2025,
            "home_team": "T",
            "away_team": "Y",
            "home_conference_id": "23",
            "away_conference_id": None,
        },
    ]
    with pytest.raises(ValueError, match="two conference ids"):
        build_asof_conferences(events)


def test_every_conference_id_resolves_to_a_tier_table_name() -> None:
    from model_prediction.models.ncaab_spread_v6 import DEFAULT_CONFERENCE_TIER_PRIORS

    missing = [n for n in CONFERENCE_ID_NAMES.values() if n not in DEFAULT_CONFERENCE_TIER_PRIORS]
    assert missing == []


def test_variant_grid_keys_are_unique() -> None:
    keys = [variant_key(*v) for v in variant_grid()]
    assert len(keys) == len(set(keys)) == 48
