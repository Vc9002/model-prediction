"""Tests for canonical identity (src/model_prediction/rebuild/identity.py).

Named in CLAUDE.md's own "Required Focused Tests" list
(tests/rebuild/test_identity.py) but never created until now --
IdentityRegistry had zero real callers anywhere in this codebase
(verified via grep), so none of its real behavior -- fuzzy matching,
fail-closed ambiguity, effective-dated cross-source mappings -- was ever
exercised outside its own file.
"""

from __future__ import annotations

from model_prediction.rebuild.identity import (
    CanonicalIdentity,
    IdentityRegistry,
    jaccard_similarity,
    normalize_name,
    resolve_or_register_team,
)
from model_prediction.rebuild.metadata import MetadataDB


def _registry(tmp_path) -> IdentityRegistry:
    meta = MetadataDB(str(tmp_path / "test.db"))
    return IdentityRegistry(meta)


class TestNormalizeName:
    def test_case_and_punctuation_insensitive(self):
        assert normalize_name("St. Louis Cardinals") == normalize_name("st louis cardinals")

    def test_accented_characters_preserved_not_crashed(self):
        # normalize_name doesn't strip accents today -- documenting real
        # current behavior rather than asserting an unimplemented feature.
        assert normalize_name("Club América") == "club américa"


class TestJaccardSimilarity:
    def test_identical_names_score_one(self):
        assert jaccard_similarity("Seattle Mariners", "Seattle Mariners") == 1.0

    def test_disjoint_names_score_zero(self):
        assert jaccard_similarity("Seattle Mariners", "Detroit Tigers") == 0.0

    def test_suffix_variant_scores_high_but_not_perfect(self):
        score = jaccard_similarity("Seattle Mariners", "Seattle Mariners Baseball Club")
        assert 0.4 < score < 1.0


class TestIdentityRegistryRegisterAndResolve:
    def test_register_then_resolve_round_trips(self, tmp_path):
        registry = _registry(tmp_path)

        identity = registry.register(
            entity_type="team", canonical_name="Seattle Mariners", sport="mlb",
            effective_from_utc="2026-01-01",
            source_id="espn_public", source_entity_id="12",
        )

        resolved = registry.resolve("espn_public", "12")
        assert resolved is not None
        assert resolved.entity_id == identity.entity_id

    def test_unmapped_source_id_resolves_to_none(self, tmp_path):
        registry = _registry(tmp_path)
        assert registry.resolve("espn_public", "does-not-exist") is None

    def test_bad_entity_type_fails_closed(self, tmp_path):
        try:
            CanonicalIdentity(
                entity_id="x", entity_type="not_a_real_type", canonical_name="X",
                sport="mlb", effective_from_utc="2026-01-01",
            )
            raised = False
        except ValueError:
            raised = True
        assert raised


class TestProposeMatchFailsClosed:
    def test_confident_match_is_returned(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team", canonical_name="Seattle Mariners", sport="mlb",
            effective_from_utc="2026-01-01",
        )

        proposed, confidence = registry.propose_match(
            entity_type="team", sport="mlb", name="Seattle Mariners",
        )
        assert proposed is not None
        assert confidence == 1.0

    def test_low_confidence_match_fails_closed_to_none(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team", canonical_name="Seattle Mariners", sport="mlb",
            effective_from_utc="2026-01-01",
        )

        # "Seattle" alone shares only 1 of 2 tokens with "Seattle Mariners" --
        # real ambiguity (could be Seattle Kraken, Seattle Sounders in a
        # differently-scoped registry), must not silently auto-match.
        proposed, confidence = registry.propose_match(
            entity_type="team", sport="mlb", name="Seattle", min_confidence=0.90,
        )
        assert proposed is None
        assert confidence < 0.90

    def test_wrong_sport_is_never_matched(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team", canonical_name="Seattle Mariners", sport="mlb",
            effective_from_utc="2026-01-01",
        )

        proposed, _ = registry.propose_match(
            entity_type="team", sport="nba", name="Seattle Mariners",
        )
        assert proposed is None


class TestResolveOrRegisterTeam:
    """The real function real collectors should call -- resolves a source's
    team ID to a canonical identity, registering or reusing as needed."""

    def test_first_observation_registers_a_new_canonical_identity(self, tmp_path):
        registry = _registry(tmp_path)

        identity = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="12",
            team_name="Seattle Mariners", effective_from_utc="2026-01-01",
        )

        assert identity.canonical_name == "Seattle Mariners"
        assert identity.sport == "mlb"

    def test_repeated_observation_of_the_same_source_id_reuses_the_identity(self, tmp_path):
        registry = _registry(tmp_path)
        first = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="12",
            team_name="Seattle Mariners", effective_from_utc="2026-01-01",
        )

        second = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="12",
            team_name="Seattle Mariners", effective_from_utc="2026-06-01",
        )

        assert second.entity_id == first.entity_id

    def test_a_second_source_observing_the_same_real_team_maps_to_the_same_identity(self, tmp_path):
        # Real cross-source identity resolution: two different sources
        # (ESPN and, say, a future Statcast team-ID source) both refer to
        # the real Seattle Mariners -- they must resolve to one canonical
        # identity, not two duplicate ones, once the name is a confident
        # match.
        registry = _registry(tmp_path)
        espn_identity = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="12",
            team_name="Seattle Mariners", effective_from_utc="2026-01-01",
        )

        other_source_identity = resolve_or_register_team(
            registry, sport="mlb", source_id="some_other_source", source_team_id="SEA-99",
            team_name="Seattle Mariners", effective_from_utc="2026-01-01",
        )

        assert other_source_identity.entity_id == espn_identity.entity_id
        # And the new source mapping is now real and independently resolvable.
        assert registry.resolve("some_other_source", "SEA-99").entity_id == espn_identity.entity_id

    def test_two_genuinely_different_teams_get_different_identities(self, tmp_path):
        registry = _registry(tmp_path)
        mariners = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="12",
            team_name="Seattle Mariners", effective_from_utc="2026-01-01",
        )
        tigers = resolve_or_register_team(
            registry, sport="mlb", source_id="espn_public", source_team_id="6",
            team_name="Detroit Tigers", effective_from_utc="2026-01-01",
        )
        assert mariners.entity_id != tigers.entity_id
