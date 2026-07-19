import pytest

from model_prediction.source_policy import (
    DEFAULT_SOURCES,
    EXCLUDED_SOURCES,
    SourceTier,
    assert_no_unapproved_paid_source,
    is_default_stack_compliant,
    sources_for_league,
)


def test_keyless_official_source_is_compliant() -> None:
    assert is_default_stack_compliant("espn")
    assert is_default_stack_compliant("polymarket_us")


def test_paid_source_is_not_default_stack_compliant() -> None:
    assert not is_default_stack_compliant("the_odds_api")
    assert not is_default_stack_compliant("sportsdataio")


def test_excluded_sources_are_never_compliant() -> None:
    assert not is_default_stack_compliant("liquipedia")
    assert not is_default_stack_compliant("riot_developer_api")
    assert "liquipedia" in EXCLUDED_SOURCES


def test_unknown_source_is_not_compliant() -> None:
    assert not is_default_stack_compliant("some_made_up_source")


def test_assert_no_unapproved_paid_source_passes_for_free_sources() -> None:
    assert_no_unapproved_paid_source(["espn", "polymarket_us", "open_meteo"])


def test_assert_no_unapproved_paid_source_allows_declared_optional_paid() -> None:
    # the_odds_api is registered AND marked PAID_OR_KEYED_OPTIONAL -- allowed
    # when explicitly requested (e.g. soccer's opt-in path), not silently.
    assert_no_unapproved_paid_source(["the_odds_api"])


def test_assert_no_unapproved_paid_source_raises_for_excluded() -> None:
    with pytest.raises(ValueError, match="excluded"):
        assert_no_unapproved_paid_source(["liquipedia"])


def test_assert_no_unapproved_paid_source_raises_for_unregistered() -> None:
    with pytest.raises(ValueError, match="not registered"):
        assert_no_unapproved_paid_source(["totally_unknown_source"])


def test_sources_for_league_orders_by_tier_preference() -> None:
    mlb_sources = sources_for_league("mlb")
    tiers = [spec.tier for spec in mlb_sources]
    assert tiers == sorted(tiers)
    assert all(SourceTier.CACHED_REPOSITORY_DATA <= t <= SourceTier.PAID_OR_KEYED_OPTIONAL for t in tiers)


def test_sources_for_league_is_case_insensitive() -> None:
    assert sources_for_league("MLB") == sources_for_league("mlb")


def test_default_sources_registry_has_no_key_required_flags_mismatched_with_tier() -> None:
    for key, spec in DEFAULT_SOURCES.items():
        if spec.requires_key:
            assert spec.tier is SourceTier.PAID_OR_KEYED_OPTIONAL, key
