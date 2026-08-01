from model_prediction.units import Exposure, UnitPolicy, edge_scaled_units, recommend_units


def test_current_unit_is_seven_fifty_dollars() -> None:
    assert UnitPolicy().unit_value_usd == 7.5


def test_unvalidated_model_is_capped_at_research_minimum() -> None:
    result = recommend_units(0.70, 0.01, -110, Exposure(), validated_model=False)
    assert result.is_call
    assert result.units == 1.0


def test_low_adjusted_edge_is_no_call() -> None:
    result = recommend_units(0.54, 0.02, -110, Exposure())
    assert not result.is_call
    assert result.units == 0


def test_exposure_cap_blocks_call() -> None:
    policy = UnitPolicy()
    result = recommend_units(0.7, 0, -110, Exposure(event_units=policy.max_event_units), policy, True)
    assert not result.is_call


def test_legacy_force_flag_cannot_create_units_when_edge_is_weak() -> None:
    result = recommend_units(0.52, 0.03, -110, Exposure(), force_shadow_call=True)
    assert not result.is_call
    assert result.units == 0


def test_one_point_seven_five_units_is_reachable() -> None:
    # Edge-scaled: 0.87 prob has 0.37 edge → caps at 2.0U max
    result = recommend_units(0.87, 0.0, -110, Exposure(), validated_model=True)
    assert result.is_call
    assert result.units == 2.0


def test_sub_minimum_kelly_stake_is_now_edge_scaled() -> None:
    # Edge-scaled sizing overrides Kelly cutoff. 0.60 prob has 0.10 edge →
    # 0.5 + 0.10 * (2.0 - 0.5) / 0.15 = 1.5U. This is now a call.
    result = recommend_units(0.60, 0.01, -110, Exposure(), validated_model=True)
    assert result.is_call
    assert result.units == 1.5


def test_kelly_stake_at_or_above_minimum_is_a_call() -> None:
    # Edge-scaled: 0.6235 raw edge 0.1235, minus 0.004 uncertainty = 0.1195
    # adjusted edge -> 1.0 + 0.1195 * (2.0-1.0)/0.15 = 1.7967 -> 1.75U
    result = recommend_units(0.6235, 0.004, -110, Exposure(), validated_model=True)
    assert result.is_call
    assert result.units == 1.75


def test_edge_scaled_units_shrinks_as_uncertainty_rises_for_the_same_probability() -> None:
    """Operator directive, 2026-07-31: edge_scaled_units took model_uncertainty
    as a parameter but never read it -- two picks with identical
    model_probability got identically-sized stakes regardless of how
    confident the model actually was. Fixed by haircutting the raw distance
    from 50/50 by the model's own uncertainty before scaling."""
    policy = UnitPolicy()
    low_uncertainty = edge_scaled_units(0.65, 0.01, -110, policy)
    mid_uncertainty = edge_scaled_units(0.65, 0.10, -110, policy)
    high_uncertainty = edge_scaled_units(0.65, 0.20, -110, policy)
    assert low_uncertainty > mid_uncertainty > high_uncertainty


def test_edge_scaled_units_floors_at_min_pick_units_never_goes_negative() -> None:
    # Uncertainty larger than the raw edge must floor at min_pick_units
    # (never zero, never negative) -- edge_scaled_units only decides SIZE,
    # not whether this is a real call at all.
    policy = UnitPolicy()
    assert edge_scaled_units(0.55, 0.40, -110, policy) == policy.min_pick_units
    assert edge_scaled_units(0.55, 5.0, -110, policy) == policy.min_pick_units


def test_edge_scaled_units_ignores_uncertainty_sign() -> None:
    # A negative model_uncertainty (shouldn't happen, but must not silently
    # *increase* sizing if it ever does) is treated as zero, not subtracted
    # as a negative (which would inflate the effective edge instead of
    # shrinking it).
    policy = UnitPolicy()
    assert edge_scaled_units(0.65, -0.10, -110, policy) == edge_scaled_units(0.65, 0.0, -110, policy)


def test_zero_edge_still_sizes_at_the_floor_regardless_of_uncertainty() -> None:
    policy = UnitPolicy()
    assert edge_scaled_units(0.5, 0.0, -110, policy) == policy.min_pick_units
    assert edge_scaled_units(0.5, 0.2, -110, policy) == policy.min_pick_units


def test_unit_range_is_one_to_two() -> None:
    # Widened 2026-07-31 (operator directive) from 0.5-2.0 to 1.0-2.0: 1U is
    # the floor for the least confident logged pick, 2U the ceiling for the
    # most confident.
    policy = UnitPolicy()
    assert policy.min_pick_units == 1.0
    assert policy.max_pick_units == 2.0
