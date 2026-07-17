from model_prediction.units import Exposure, UnitPolicy, recommend_units


def test_unvalidated_model_is_capped_at_research_minimum() -> None:
    result = recommend_units(0.70, 0.01, -110, Exposure(), validated_model=False)
    assert result.is_call
    assert result.units == 0.5


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
    # Edge-scaled: 0.6235 has 0.1235 edge → 0.5 + 0.1235 * 10 = 1.735 → 1.75U
    result = recommend_units(0.6235, 0.004, -110, Exposure(), validated_model=True)
    assert result.is_call
    assert result.units == 1.75
