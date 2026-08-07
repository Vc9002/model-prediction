"""Tests for canonical identity (src/model_prediction/rebuild/identity.py).

Named in CLAUDE.md's own "Required Focused Tests" list
(tests/rebuild/test_identity.py) but never created until now --
IdentityRegistry had zero real callers anywhere in this codebase
(verified via grep), so none of its real behavior -- fuzzy matching,
fail-closed ambiguity, effective-dated cross-source mappings -- was ever
exercised outside its own file.
"""

from __future__ import annotations

from typing import ClassVar

from model_prediction.rebuild.identity import (
    CanonicalIdentity,
    IdentityRegistry,
    jaccard_similarity,
    normalize_name,
    resolve_espn_scoreboard_event_id,
    resolve_espn_scoreboard_team_ids,
    resolve_event_by_team_pair,
    resolve_or_register_event,
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


class TestResolveEspnScoreboardTeamIdsCrossSportCollision:
    """Real, serious bug found live (2026-08-07): ESPN's team `id` is only
    unique *within* one sport's own numbering -- WNBA team id "20" and MLB
    team id "20" are two completely unrelated real teams. Confirmed live:
    WNBA's "Atlanta Dream" (ESPN team id 20) was silently resolved to
    MLB's "Washington Nationals" (also ESPN team id 20) because both
    collectors passed the same bare source_id="espn_public" into
    resolve_or_register_team(), and entity_mappings' (source_id,
    source_entity_id) key has no sport dimension. Fixed by namespacing
    source_id with sport inside resolve_espn_scoreboard_team_ids() itself."""

    def test_same_espn_team_id_in_two_sports_resolves_to_two_different_teams(self, tmp_path):
        registry = _registry(tmp_path)
        mlb_home, mlb_away = resolve_espn_scoreboard_team_ids(
            registry, "mlb", "espn_public",
            {"id": "20", "displayName": "Washington Nationals"},
            {"id": "15", "displayName": "Atlanta Braves"},
            "2026-08-07T00:00:00+00:00",
        )
        wnba_home, wnba_away = resolve_espn_scoreboard_team_ids(
            registry, "wnba", "espn_public",
            {"id": "20", "displayName": "Atlanta Dream"},
            {"id": "15", "displayName": "Washington Mystics"},
            "2026-08-07T00:00:00+00:00",
        )
        # The real bug: before the fix, wnba_home (ESPN id "20") would
        # resolve to the already-registered MLB entity for ESPN id "20"
        # (Washington Nationals) instead of a genuinely new WNBA team.
        assert wnba_home != mlb_home
        assert wnba_away != mlb_away
        assert registry.resolve("espn_public:mlb", "20").canonical_name == "Washington Nationals"
        assert registry.resolve("espn_public:wnba", "20").canonical_name == "Atlanta Dream"
        # And the old, unnamespaced source_id is genuinely unused now --
        # nothing was ever registered directly under bare "espn_public".
        assert registry.resolve("espn_public", "20") is None


class TestResolveOrRegisterEvent:
    """The real entry point for canonical event identity -- mirrors
    resolve_or_register_team()'s register-or-reuse shape, keyed on
    (source_id, source_event_id) with no fuzzy name matching (two real
    games can share both team names and date -- a doubleheader -- so
    guessing from a name string would risk silently merging them)."""

    def test_first_observation_registers_a_new_canonical_identity(self, tmp_path):
        registry = _registry(tmp_path)
        identity = resolve_or_register_event(
            registry, sport="mlb", source_id="espn_public:mlb", source_event_id="401816384",
            canonical_name="Los Angeles Angels @ Baltimore Orioles",
            effective_from_utc="2026-07-20T22:35Z",
        )
        assert identity.entity_type == "event"
        assert identity.sport == "mlb"

    def test_repeated_observation_of_the_same_source_id_reuses_the_identity(self, tmp_path):
        registry = _registry(tmp_path)
        first = resolve_or_register_event(
            registry, sport="mlb", source_id="espn_public:mlb", source_event_id="401816384",
            canonical_name="Los Angeles Angels @ Baltimore Orioles",
            effective_from_utc="2026-07-20T22:35Z",
        )
        second = resolve_or_register_event(
            registry, sport="mlb", source_id="espn_public:mlb", source_event_id="401816384",
            canonical_name="Los Angeles Angels @ Baltimore Orioles",
            effective_from_utc="2026-07-20T22:35Z",
        )
        assert second.entity_id == first.entity_id

    def test_two_genuinely_different_events_get_different_identities(self, tmp_path):
        registry = _registry(tmp_path)
        first = resolve_or_register_event(
            registry, sport="mlb", source_id="espn_public:mlb", source_event_id="401816384",
            canonical_name="Los Angeles Angels @ Baltimore Orioles",
            effective_from_utc="2026-07-20T22:35Z",
        )
        second = resolve_or_register_event(
            registry, sport="mlb", source_id="espn_public:mlb", source_event_id="401816999",
            canonical_name="Los Angeles Angels @ Baltimore Orioles",
            effective_from_utc="2026-07-20T22:35Z",
        )
        assert first.entity_id != second.entity_id


class TestResolveEspnScoreboardEventId:
    """The real helper every ESPN-scoreboard-shaped collector calls. Named
    directly in CLAUDE.md's canonical-identity test requirements:
    doubleheaders and neutral-site games must resolve to correct,
    non-conflated identities."""

    _home: ClassVar = {"id": "1", "displayName": "Baltimore Orioles"}
    _away: ClassVar = {"id": "3", "displayName": "Los Angeles Angels"}

    def test_missing_espn_event_id_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        result = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T20:00:00+00:00",
        )
        assert result is None

    def test_doubleheader_two_real_games_same_teams_same_day_get_different_ids(self, tmp_path):
        # A real doubleheader: the same two teams play twice on the same
        # calendar date. ESPN assigns each game its own real, distinct
        # event id -- this must produce two distinct canonical events, not
        # one collapsed identity.
        registry = _registry(tmp_path)
        game_1 = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816384",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T18:05Z", "2026-07-20T17:00:00+00:00",
            venue_name="Oriole Park at Camden Yards",
        )
        game_2 = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816385",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T21:30:00+00:00",
            venue_name="Oriole Park at Camden Yards",
        )
        assert game_1 is not None
        assert game_2 is not None
        assert game_1 != game_2

    def test_neutral_site_game_resolves_and_records_the_neutral_venue(self, tmp_path):
        # A neutral-site game's venue belongs to neither team -- resolution
        # must not depend on venue matching a "home" team's usual park.
        registry = _registry(tmp_path)
        event_id = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401900001",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-06-13T17:05Z", "2026-06-13T16:00:00+00:00",
            venue_name="London Stadium",
        )
        assert event_id is not None
        resolved = registry.resolve("espn_public:mlb", "401900001")
        assert resolved is not None
        assert resolved.attributes["venue"] == "London Stadium"

    def test_rerunning_collection_reuses_the_same_canonical_event(self, tmp_path):
        registry = _registry(tmp_path)
        first = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816384",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T20:00:00+00:00",
        )
        second = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816384",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T21:00:00+00:00",
        )
        assert first == second

    def test_same_espn_event_id_in_two_sports_resolves_to_two_different_events(self, tmp_path):
        # Same defensive namespacing as resolve_espn_scoreboard_team_ids()'s
        # real cross-sport team-id collision fix -- applied here on
        # principle since ESPN's per-sport event-id numbering was never
        # verified collision-free either.
        registry = _registry(tmp_path)
        mlb_event = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "500",
            self._home, self._away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T20:00:00+00:00",
        )
        wnba_event = resolve_espn_scoreboard_event_id(
            registry, "wnba", "espn_public", "500",
            {"id": "20", "displayName": "Atlanta Dream"}, {"id": "15", "displayName": "Washington Mystics"},
            "wnba:team:home", "wnba:team:away",
            "2026-07-20T22:35Z", "2026-07-20T20:00:00+00:00",
        )
        assert mlb_event != wnba_event


class TestResolveEventByTeamPair:
    """The fallback a source with no stable native event id of its own
    (Polymarket) uses to link into an already-registered canonical event.
    Must fail closed on doubleheaders -- team pair + date alone genuinely
    cannot disambiguate two real games between the same teams on the same
    day."""

    def test_finds_the_single_matching_event(self, tmp_path):
        registry = _registry(tmp_path)
        event_id = resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816384",
            {"id": "1", "displayName": "Baltimore Orioles"}, {"id": "3", "displayName": "Los Angeles Angels"},
            "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T20:00:00+00:00",
        )
        found = resolve_event_by_team_pair(
            registry, "mlb", "mlb:team:home", "mlb:team:away", "2026-07-20",
        )
        assert found == event_id

    def test_doubleheader_ambiguity_fails_closed(self, tmp_path):
        registry = _registry(tmp_path)
        home, away = {"id": "1", "displayName": "Baltimore Orioles"}, {"id": "3", "displayName": "Los Angeles Angels"}
        resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816384",
            home, away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T18:05Z", "2026-07-20T17:00:00+00:00",
        )
        resolve_espn_scoreboard_event_id(
            registry, "mlb", "espn_public", "401816385",
            home, away, "mlb:team:home", "mlb:team:away",
            "2026-07-20T22:35Z", "2026-07-20T21:30:00+00:00",
        )
        assert resolve_event_by_team_pair(
            registry, "mlb", "mlb:team:home", "mlb:team:away", "2026-07-20",
        ) is None

    def test_no_match_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        assert resolve_event_by_team_pair(
            registry, "mlb", "mlb:team:home", "mlb:team:away", "2026-07-20",
        ) is None

    def test_missing_canonical_team_id_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        assert resolve_event_by_team_pair(registry, "mlb", None, "mlb:team:away", "2026-07-20") is None
