"""Tests for the air-density shadow module (research worktree).

Anchors and properties come from the published sources documented in
the module docstring; the physics itself is standard (Bahill et al.
2009). No model is wired to this module, so these tests pin the
physics and the fail-closed contract only.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from model_prediction.features.air_density import (
    air_density,
    fly_ball_distance_factor,
    pressure_from_altitude_pa,
    run_environment_anchors,
)


def test_standard_conditions_give_unit_ratio():
    result = air_density(temp_c=15.0, pressure_pa=101325.0, relative_humidity=0.0)
    assert abs(result.density_ratio - 1.0) < 1e-6


def test_coors_style_altitude_anchor():
    """Denver-class elevation (~1,607 m) on a warm day -> ~20% density loss."""
    pressure = pressure_from_altitude_pa(1607.0)
    result = air_density(temp_c=25.0, pressure_pa=pressure, relative_humidity=40.0)
    # Published: Denver density is ~0.79-0.83 of sea level at summer temps.
    assert 0.77 < result.density_ratio < 0.86
    # ~20% density loss -> ~+9-10% fly-ball distance.
    assert 1.08 < fly_ball_distance_factor(result.density_ratio) < 1.11


def test_warm_air_is_less_dense_than_cold():
    cold = air_density(temp_c=0.0, pressure_pa=101325.0, relative_humidity=50.0)
    warm = air_density(temp_c=30.0, pressure_pa=101325.0, relative_humidity=50.0)
    assert warm.density_ratio < cold.density_ratio
    assert fly_ball_distance_factor(warm.density_ratio) > fly_ball_distance_factor(cold.density_ratio)


def test_humidity_lowers_density():
    dry = air_density(temp_c=25.0, pressure_pa=101325.0, relative_humidity=0.0)
    humid = air_density(temp_c=25.0, pressure_pa=101325.0, relative_humidity=90.0)
    assert humid.density_ratio < dry.density_ratio


def test_altitude_monotonic_in_pressure():
    assert pressure_from_altitude_pa(0.0) > pressure_from_altitude_pa(5000.0 * 0.3048)
    assert pressure_from_altitude_pa(1000.0) < pressure_from_altitude_pa(0.0)


def test_anchors_documented():
    anchors = run_environment_anchors()
    assert anchors["altitude_5000ft_runs_per_game_delta"] == 2.8
    assert anchors["warm_runs_per_game"] > anchors["cold_runs_per_game"]


@settings(max_examples=200)
@given(
    temp_c=st.floats(min_value=-20.0, max_value=45.0),
    pressure_pa=st.floats(min_value=60000.0, max_value=105000.0),
    rh=st.floats(min_value=0.0, max_value=100.0),
)
def test_density_ratio_bounds(temp_c, pressure_pa, rh):
    result = air_density(temp_c, pressure_pa, rh)
    # plausible game conditions: between ~0.5 (Coors, hot) and ~1.18
    # (cold, high pressure, dry) — physics bounds, not fit values
    assert 0.5 < result.density_ratio < 1.2


@settings(max_examples=200)
@given(ratio=st.floats(min_value=0.5, max_value=1.0))
def test_distance_factor_direction(ratio):
    factor = fly_ball_distance_factor(ratio)
    assert factor >= 1.0  # thinner air never shortens fly balls
    assert abs(factor - (1.0 / ratio) ** 0.4) < 1e-9


@settings(max_examples=200)
@given(
    temp_c=st.floats(min_value=-20.0, max_value=45.0),
    pressure_pa=st.floats(min_value=60000.0, max_value=105000.0),
    rh=st.floats(min_value=0.0, max_value=100.0),
    delta_t=st.floats(min_value=0.1, max_value=20.0),
)
def test_warmer_air_monotonic(temp_c, pressure_pa, rh, delta_t):
    """At fixed pressure/humidity, hotter air is monotonically less dense."""
    cool = air_density(temp_c, pressure_pa, rh)
    warm = air_density(temp_c + delta_t, pressure_pa, rh)
    assert warm.density_ratio < cool.density_ratio
