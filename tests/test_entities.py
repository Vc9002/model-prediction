import pytest

from model_prediction.domain import League
from model_prediction.entities import CanonicalTeam, EntityRegistry, EntityResolutionError, SourceTeamAlias


def test_official_abbreviation_case_punctuation_and_source_alias_resolve(registry) -> None:
    expected = "mlb-nyy"
    assert registry.resolve(League.MLB, "New York Yankees").canonical_team_id == expected
    assert registry.resolve(League.MLB, "NYY").canonical_team_id == expected
    assert registry.resolve(League.MLB, "nyy").canonical_team_id == expected
    assert registry.resolve(League.MLB, "N.Y.Y.").canonical_team_id == expected
    assert registry.resolve(League.MLB, "NY Yankees").canonical_team_id == expected


def test_unknown_and_cross_league_alias_fail(registry) -> None:
    with pytest.raises(EntityResolutionError, match="unknown"):
        registry.resolve(League.MLB, "NYY typo")
    with pytest.raises(EntityResolutionError, match="unknown"):
        registry.resolve(League.NBA, "NYY")


def test_ambiguity_is_rejected() -> None:
    alias = SourceTeamAlias("test", None, "Same", "same")
    teams = [
        CanonicalTeam("mlb-a", League.MLB, "Alpha", "AAA", True, None, None, (alias,)),
        CanonicalTeam("mlb-b", League.MLB, "Beta", "BBB", True, None, None, (alias,)),
    ]
    registry = EntityRegistry(teams, "test")
    with pytest.raises(EntityResolutionError, match="ambiguous"):
        registry.resolve(League.MLB, "Same")


def test_canonical_ids_are_stable(registry) -> None:
    assert registry.resolve(League.MLB, "Oakland A's").canonical_team_id == "mlb-ath"
    assert registry.resolve(League.MLB, "Athletics").canonical_team_id == "mlb-ath"


def test_alias_validity_window_is_respected() -> None:
    old = SourceTeamAlias("test", None, "Old Name", "oldname", valid_to="2020-12-31")
    team = CanonicalTeam("mlb-stable", League.MLB, "Current Name", "CUR", True, None, None, (old,))
    registry = EntityRegistry([team], "test")
    assert registry.resolve(League.MLB, "Old Name", "2020-06-01T00:00:00Z") == team
    with pytest.raises(EntityResolutionError, match="unknown"):
        registry.resolve(League.MLB, "Old Name", "2021-06-01T00:00:00Z")
