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

import pytest

from model_prediction.rebuild.identity import (
    CanonicalIdentity,
    IdentityRegistry,
    jaccard_similarity,
    normalize_name,
    resolve_espn_roster_player_id,
    resolve_espn_scoreboard_event_id,
    resolve_espn_scoreboard_team_ids,
    resolve_espn_scoreboard_venue_id,
    resolve_event_by_team_pair,
    resolve_or_link_polymarket_event_id,
    resolve_or_link_statcast_game_pk,
    resolve_or_register_event,
    resolve_or_register_player,
    resolve_or_register_team,
    resolve_or_register_venue,
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
            entity_type="team",
            canonical_name="Seattle Mariners",
            sport="mlb",
            effective_from_utc="2026-01-01",
            source_id="espn_public",
            source_entity_id="12",
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
                entity_id="x",
                entity_type="not_a_real_type",
                canonical_name="X",
                sport="mlb",
                effective_from_utc="2026-01-01",
            )
            raised = False
        except ValueError:
            raised = True
        assert raised


class TestProposeMatchFailsClosed:
    def test_confident_match_is_returned(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team",
            canonical_name="Seattle Mariners",
            sport="mlb",
            effective_from_utc="2026-01-01",
        )

        proposed, confidence = registry.propose_match(
            entity_type="team",
            sport="mlb",
            name="Seattle Mariners",
        )
        assert proposed is not None
        assert confidence == 1.0

    def test_low_confidence_match_fails_closed_to_none(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team",
            canonical_name="Seattle Mariners",
            sport="mlb",
            effective_from_utc="2026-01-01",
        )

        # "Seattle" alone shares only 1 of 2 tokens with "Seattle Mariners" --
        # real ambiguity (could be Seattle Kraken, Seattle Sounders in a
        # differently-scoped registry), must not silently auto-match.
        proposed, confidence = registry.propose_match(
            entity_type="team",
            sport="mlb",
            name="Seattle",
            min_confidence=0.90,
        )
        assert proposed is None
        assert confidence < 0.90

    def test_wrong_sport_is_never_matched(self, tmp_path):
        registry = _registry(tmp_path)
        registry.register(
            entity_type="team",
            canonical_name="Seattle Mariners",
            sport="mlb",
            effective_from_utc="2026-01-01",
        )

        proposed, _ = registry.propose_match(
            entity_type="team",
            sport="nba",
            name="Seattle Mariners",
        )
        assert proposed is None


def _register_team(registry, source_id, source_entity_id, name, effective_from_utc, sport="mlb"):
    return resolve_or_register_team(
        registry,
        sport=sport,
        source_id=source_id,
        source_team_id=source_entity_id,
        team_name=name,
        effective_from_utc=effective_from_utc,
    )


def _register_venue(registry, source_id, source_entity_id, name, effective_from_utc, sport="mlb"):
    return resolve_or_register_venue(
        registry,
        sport=sport,
        source_id=source_id,
        source_venue_id=source_entity_id,
        venue_name=name,
        effective_from_utc=effective_from_utc,
    )


def _register_event(registry, source_id, source_entity_id, name, effective_from_utc, sport="mlb"):
    return resolve_or_register_event(
        registry,
        sport=sport,
        source_id=source_id,
        source_event_id=source_entity_id,
        canonical_name=name,
        effective_from_utc=effective_from_utc,
    )


def _register_player(registry, source_id, source_entity_id, name, effective_from_utc, sport="nba"):
    return resolve_or_register_player(
        registry,
        sport=sport,
        source_id=source_id,
        source_player_id=source_entity_id,
        player_name=name,
        effective_from_utc=effective_from_utc,
    )


class TestResolveOrRegisterCommonShape:
    """The four ``resolve_or_register_*`` entry points (team/event/venue/
    player) all implement the same register-or-reuse contract: first
    observation registers a new canonical identity, a repeated observation
    of the same (source_id, source_entity_id) reuses it, and two genuinely
    different real-world entities never collide. Parametrized across all
    four rather than hand-duplicated per entity type -- team/event/venue/
    player each still get their own dedicated class below for the behavior
    that genuinely differs between them (fuzzy cross-source matching for
    team/venue only; event's no-fuzzy-matching doubleheader safety; player's
    duplicate-name handling)."""

    _REGISTER: ClassVar = {
        "team": (_register_team, "Seattle Mariners", "Detroit Tigers"),
        "event": (_register_event, "Angels @ Orioles (game 1)", "Angels @ Orioles (game 2)"),
        "venue": (_register_venue, "Citizens Bank Park", "Oriole Park at Camden Yards"),
        "player": (_register_player, "Jayson Tatum", "LeBron James"),
    }

    @pytest.mark.parametrize("entity_type", sorted(_REGISTER))
    def test_first_observation_registers_a_new_canonical_identity(self, tmp_path, entity_type):
        register, name, _ = self._REGISTER[entity_type]
        registry = _registry(tmp_path)

        identity = register(registry, "espn_public", "12", name, "2026-01-01")

        assert identity.canonical_name == name
        assert identity.entity_type == entity_type

    @pytest.mark.parametrize("entity_type", sorted(_REGISTER))
    def test_repeated_observation_of_the_same_source_id_reuses_the_identity(self, tmp_path, entity_type):
        register, name, _ = self._REGISTER[entity_type]
        registry = _registry(tmp_path)

        first = register(registry, "espn_public", "12", name, "2026-01-01")
        second = register(registry, "espn_public", "12", name, "2026-06-01")

        assert second.entity_id == first.entity_id

    @pytest.mark.parametrize("entity_type", sorted(_REGISTER))
    def test_two_genuinely_different_entities_get_different_identities(self, tmp_path, entity_type):
        register, name_a, name_b = self._REGISTER[entity_type]
        registry = _registry(tmp_path)

        first = register(registry, "espn_public", "12", name_a, "2026-01-01")
        second = register(registry, "espn_public", "6", name_b, "2026-01-01")

        assert first.entity_id != second.entity_id


class TestResolveOrRegisterFuzzyCrossSourceMatch:
    """Team and venue (but NOT event or player -- see their own classes'
    docstrings for why) support fuzzy cross-source name matching: two
    different sources observing the same real-world team/venue under
    slightly different name strings must resolve to one canonical
    identity, not two duplicates."""

    _REGISTER: ClassVar = {"team": _register_team, "venue": _register_venue}
    _NAME: ClassVar = {"team": "Seattle Mariners", "venue": "Citizens Bank Park"}

    @pytest.mark.parametrize("entity_type", sorted(_REGISTER))
    def test_a_second_source_observing_the_same_real_entity_maps_to_the_same_identity(
        self, tmp_path, entity_type
    ):
        register, name = self._REGISTER[entity_type], self._NAME[entity_type]
        registry = _registry(tmp_path)

        primary = register(registry, "espn_public", "12", name, "2026-01-01")
        other_source = register(registry, "some_other_source", "ALT-99", name, "2026-01-01")

        assert other_source.entity_id == primary.entity_id
        # And the new source mapping is now real and independently resolvable.
        assert registry.resolve("some_other_source", "ALT-99").entity_id == primary.entity_id


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
            registry,
            "mlb",
            "espn_public",
            {"id": "20", "displayName": "Washington Nationals"},
            {"id": "15", "displayName": "Atlanta Braves"},
            "2026-08-07T00:00:00+00:00",
        )
        wnba_home, wnba_away = resolve_espn_scoreboard_team_ids(
            registry,
            "wnba",
            "espn_public",
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
            registry,
            "mlb",
            "espn_public",
            "",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        assert result is None

    def test_doubleheader_two_real_games_same_teams_same_day_get_different_ids(self, tmp_path):
        # A real doubleheader: the same two teams play twice on the same
        # calendar date. ESPN assigns each game its own real, distinct
        # event id -- this must produce two distinct canonical events, not
        # one collapsed identity.
        registry = _registry(tmp_path)
        game_1 = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T18:05Z",
            "2026-07-20T17:00:00+00:00",
            venue_name="Oriole Park at Camden Yards",
        )
        game_2 = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816385",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T21:30:00+00:00",
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
            registry,
            "mlb",
            "espn_public",
            "401900001",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-06-13T17:05Z",
            "2026-06-13T16:00:00+00:00",
            venue_name="London Stadium",
        )
        assert event_id is not None
        resolved = registry.resolve("espn_public:mlb", "401900001")
        assert resolved is not None
        assert resolved.attributes["venue"] == "London Stadium"

    def test_rerunning_collection_reuses_the_same_canonical_event(self, tmp_path):
        registry = _registry(tmp_path)
        first = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        second = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T21:00:00+00:00",
        )
        assert first == second

    def test_same_espn_event_id_in_two_sports_resolves_to_two_different_events(self, tmp_path):
        # Same defensive namespacing as resolve_espn_scoreboard_team_ids()'s
        # real cross-sport team-id collision fix -- applied here on
        # principle since ESPN's per-sport event-id numbering was never
        # verified collision-free either.
        registry = _registry(tmp_path)
        mlb_event = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "500",
            self._home,
            self._away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        wnba_event = resolve_espn_scoreboard_event_id(
            registry,
            "wnba",
            "espn_public",
            "500",
            {"id": "20", "displayName": "Atlanta Dream"},
            {"id": "15", "displayName": "Washington Mystics"},
            "wnba:team:home",
            "wnba:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
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
            registry,
            "mlb",
            "espn_public",
            "401816384",
            {"id": "1", "displayName": "Baltimore Orioles"},
            {"id": "3", "displayName": "Los Angeles Angels"},
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        found = resolve_event_by_team_pair(
            registry,
            "mlb",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
        )
        assert found == event_id

    def test_doubleheader_ambiguity_fails_closed(self, tmp_path):
        registry = _registry(tmp_path)
        home, away = (
            {"id": "1", "displayName": "Baltimore Orioles"},
            {"id": "3", "displayName": "Los Angeles Angels"},
        )
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            home,
            away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T18:05Z",
            "2026-07-20T17:00:00+00:00",
        )
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816385",
            home,
            away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T21:30:00+00:00",
        )
        assert (
            resolve_event_by_team_pair(
                registry,
                "mlb",
                "mlb:team:home",
                "mlb:team:away",
                "2026-07-20",
            )
            is None
        )

    def test_no_match_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        assert (
            resolve_event_by_team_pair(
                registry,
                "mlb",
                "mlb:team:home",
                "mlb:team:away",
                "2026-07-20",
            )
            is None
        )

    def test_missing_canonical_team_id_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        assert resolve_event_by_team_pair(registry, "mlb", None, "mlb:team:away", "2026-07-20") is None


class TestResolveOrLinkPolymarketEventId:
    """Ties Polymarket's own event_id to the canonical event ESPN
    scoreboard collection already registered -- closing the real gap that
    the two id-spaces (ESPN's numeric event id, Polymarket's own event id)
    previously had nothing linking them."""

    home: ClassVar = {"id": "1", "displayName": "Baltimore Orioles"}
    away: ClassVar = {"id": "3", "displayName": "Los Angeles Angels"}

    def _seeded_registry(self, tmp_path):
        registry = _registry(tmp_path)
        event_id = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        return registry, event_id

    def test_known_canonical_event_id_links_directly(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        linked = resolve_or_link_polymarket_event_id(
            registry,
            "mlb",
            "70543",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )

        assert linked == espn_event_id
        assert registry.resolve("polymarket_us:mlb", "70543").entity_id == espn_event_id

    def test_rerun_is_idempotent(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        first = resolve_or_link_polymarket_event_id(
            registry,
            "mlb",
            "70543",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )
        second = resolve_or_link_polymarket_event_id(
            registry,
            "mlb",
            "70543",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )
        assert first == second == espn_event_id

    def test_falls_back_to_team_pair_lookup_when_canonical_id_not_supplied(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        linked = resolve_or_link_polymarket_event_id(
            registry,
            "mlb",
            "70543",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
        )
        assert linked == espn_event_id

    def test_doubleheader_without_known_id_fails_closed(self, tmp_path):
        # Real precision case this design exists for: with no
        # known_canonical_event_id, team-pair+date alone cannot
        # disambiguate a doubleheader -- must fail closed, not guess.
        registry = _registry(tmp_path)
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T18:05Z",
            "2026-07-20T17:00:00+00:00",
        )
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816385",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T21:30:00+00:00",
        )

        linked = resolve_or_link_polymarket_event_id(
            registry,
            "mlb",
            "70543",
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
        )
        assert linked is None

    def test_missing_polymarket_event_id_returns_none(self, tmp_path):
        registry, _ = self._seeded_registry(tmp_path)
        assert (
            resolve_or_link_polymarket_event_id(
                registry,
                "mlb",
                None,
                "mlb:team:home",
                "mlb:team:away",
                "2026-07-20",
            )
            is None
        )


class TestResolveOrLinkStatcastGamePk:
    """Ties Statcast/MLB-StatsAPI's own game_pk to the canonical event ESPN
    scoreboard collection already registered -- same shape as
    TestResolveOrLinkPolymarketEventId, since game_pk lives in the
    identical kind of separate id-space problem Polymarket's event_id
    does (Task 2 of the model-development phase)."""

    home: ClassVar = {"id": "1", "displayName": "Baltimore Orioles"}
    away: ClassVar = {"id": "3", "displayName": "Los Angeles Angels"}

    def _seeded_registry(self, tmp_path):
        registry = _registry(tmp_path)
        event_id = resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T20:00:00+00:00",
        )
        return registry, event_id

    def test_known_canonical_event_id_links_directly(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        linked = resolve_or_link_statcast_game_pk(
            registry,
            "mlb",
            824490,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )

        assert linked == espn_event_id
        assert registry.resolve("mlb_statsapi:mlb", "824490").entity_id == espn_event_id

    def test_rerun_is_idempotent(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        first = resolve_or_link_statcast_game_pk(
            registry,
            "mlb",
            824490,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )
        second = resolve_or_link_statcast_game_pk(
            registry,
            "mlb",
            824490,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
            known_canonical_event_id=espn_event_id,
        )
        assert first == second == espn_event_id

    def test_falls_back_to_team_pair_lookup_when_canonical_id_not_supplied(self, tmp_path):
        registry, espn_event_id = self._seeded_registry(tmp_path)

        linked = resolve_or_link_statcast_game_pk(
            registry,
            "mlb",
            824490,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
        )
        assert linked == espn_event_id

    def test_doubleheader_without_known_id_fails_closed(self, tmp_path):
        # The real precision case this design exists for: with no
        # known_canonical_event_id, team-pair+date alone cannot
        # disambiguate a doubleheader -- must fail closed, not guess.
        registry = _registry(tmp_path)
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816384",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T18:05Z",
            "2026-07-20T17:00:00+00:00",
        )
        resolve_espn_scoreboard_event_id(
            registry,
            "mlb",
            "espn_public",
            "401816385",
            self.home,
            self.away,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20T22:35Z",
            "2026-07-20T21:30:00+00:00",
        )

        linked = resolve_or_link_statcast_game_pk(
            registry,
            "mlb",
            824490,
            "mlb:team:home",
            "mlb:team:away",
            "2026-07-20",
        )
        assert linked is None

    def test_missing_game_pk_returns_none(self, tmp_path):
        registry, _ = self._seeded_registry(tmp_path)
        assert (
            resolve_or_link_statcast_game_pk(
                registry,
                "mlb",
                None,
                "mlb:team:home",
                "mlb:team:away",
                "2026-07-20",
            )
            is None
        )


class TestResolveEspnScoreboardVenueId:
    """The real helper every ESPN-scoreboard-shaped collector calls for
    canonical venue identity. ESPN's real venue object (verified against
    real collected raw snapshots under data/rebuild/raw/espn_public/) has
    id/fullName/address.city/address.state/indoor -- no lat/long/
    timezone/capacity/surface, so those are honestly left unset rather
    than fabricated."""

    def test_missing_id_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        result = resolve_espn_scoreboard_venue_id(
            registry,
            "mlb",
            "espn_public",
            {"fullName": "Citizens Bank Park"},
            "2026-07-20T20:00:00+00:00",
        )
        assert result is None

    def test_missing_name_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        result = resolve_espn_scoreboard_venue_id(
            registry,
            "mlb",
            "espn_public",
            {"id": "84"},
            "2026-07-20T20:00:00+00:00",
        )
        assert result is None

    def test_real_venue_shape_resolves_with_available_real_fields_only(self, tmp_path):
        registry = _registry(tmp_path)
        venue_obj = {
            "id": "84",
            "fullName": "Citizens Bank Park",
            "address": {"city": "Philadelphia", "state": "Pennsylvania"},
            "indoor": False,
        }
        venue_id = resolve_espn_scoreboard_venue_id(
            registry,
            "mlb",
            "espn_public",
            venue_obj,
            "2026-07-20T20:00:00+00:00",
        )
        assert venue_id is not None
        resolved = registry.resolve("espn_public:mlb", "84")
        assert resolved is not None
        assert resolved.canonical_name == "Citizens Bank Park"
        assert resolved.attributes["city"] == "Philadelphia"
        assert resolved.attributes["indoor"] is False
        # Honestly unset, not fabricated -- CLAUDE.md wants these but ESPN's
        # scoreboard venue object doesn't provide them.
        assert resolved.attributes["latitude"] is None
        assert resolved.attributes["capacity"] is None

    def test_rerunning_collection_reuses_the_same_canonical_venue(self, tmp_path):
        registry = _registry(tmp_path)
        venue_obj = {"id": "84", "fullName": "Citizens Bank Park"}
        first = resolve_espn_scoreboard_venue_id(
            registry,
            "mlb",
            "espn_public",
            venue_obj,
            "2026-07-20T20:00:00+00:00",
        )
        second = resolve_espn_scoreboard_venue_id(
            registry,
            "mlb",
            "espn_public",
            venue_obj,
            "2026-07-21T20:00:00+00:00",
        )
        assert first == second


class TestResolveOrRegisterPlayer:
    """Player-specific coverage beyond the common register/reuse/differ
    shape (see TestResolveOrRegisterCommonShape). Unlike team/venue, player
    deliberately does NOT do fuzzy name matching -- two different real
    players can share a full name, and guessing from a name string would
    silently merge them (see resolve_or_register_player()'s own docstring).
    This is the case that shape alone can't cover: same name, must still
    resolve to different identities."""

    def test_duplicate_player_names_get_different_identities(self, tmp_path):
        # CLAUDE.md names "duplicate player names" as a required test case:
        # two genuinely different real players who happen to share a full
        # name must resolve to two distinct canonical identities, not be
        # silently merged the way resolve_or_register_team()'s fuzzy name
        # matching would merge them (see resolve_or_register_player()'s
        # docstring for why fuzzy matching is deliberately not used here).
        registry = _registry(tmp_path)
        first = resolve_or_register_player(
            registry,
            sport="nba",
            source_id="espn_public:nba",
            source_player_id="111",
            player_name="Chris Johnson",
            effective_from_utc="2026-01-01",
        )
        second = resolve_or_register_player(
            registry,
            sport="nba",
            source_id="espn_public:nba",
            source_player_id="222",
            player_name="Chris Johnson",
            effective_from_utc="2026-01-01",
        )
        assert first.entity_id != second.entity_id
        assert registry.resolve("espn_public:nba", "111").entity_id == first.entity_id
        assert registry.resolve("espn_public:nba", "222").entity_id == second.entity_id


class TestResolveEspnRosterPlayerId:
    """The real helper an ESPN-roster-shaped collector calls for canonical
    player identity outside MLB. Real athlete shape verified against a
    real cached ESPN roster payload
    (data/availability/wnba/espn_rosters/2026-8.json): id, displayName,
    position.abbreviation, jersey."""

    def test_missing_id_returns_none(self, tmp_path):
        registry = _registry(tmp_path)
        result = resolve_espn_roster_player_id(
            registry,
            "wnba",
            "espn_public",
            {"displayName": "Maya Caldwell"},
            None,
            "2026-07-20T20:00:00+00:00",
        )
        assert result is None

    def test_real_athlete_shape_resolves_with_available_real_fields(self, tmp_path):
        registry = _registry(tmp_path)
        athlete = {
            "id": 4280850,
            "displayName": "Maya Caldwell",
            "position": {"abbreviation": "G"},
            "jersey": "12",
        }
        player_id = resolve_espn_roster_player_id(
            registry,
            "wnba",
            "espn_public",
            athlete,
            "wnba:team:atl",
            "2026-07-20T20:00:00+00:00",
        )
        assert player_id is not None
        resolved = registry.resolve("espn_public:wnba", "4280850")
        assert resolved is not None
        assert resolved.canonical_name == "Maya Caldwell"
        assert resolved.attributes["team_canonical_id"] == "wnba:team:atl"
        assert resolved.attributes["position"] == "G"

    def test_same_espn_athlete_id_in_two_sports_resolves_to_two_different_players(self, tmp_path):
        # Same defensive namespacing as every other resolve_espn_* helper
        # here.
        registry = _registry(tmp_path)
        nba_player = resolve_espn_roster_player_id(
            registry,
            "nba",
            "espn_public",
            {"id": 500, "displayName": "Player A"},
            None,
            "2026-07-20T20:00:00+00:00",
        )
        wnba_player = resolve_espn_roster_player_id(
            registry,
            "wnba",
            "espn_public",
            {"id": 500, "displayName": "Player B"},
            None,
            "2026-07-20T20:00:00+00:00",
        )
        assert nba_player != wnba_player
