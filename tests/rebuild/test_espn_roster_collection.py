"""Real ESPN roster collection + canonical player identity resolution
(Task 3: player identity outside MLB, CLAUDE.md Part 1 sec 7/10).

MLB's player identity already comes from pybaseball's real player register
(resolve_mlbam_player_id(), verified live against 12 real starters). No
equivalent stable name->id crosswalk existed for NBA/WNBA/NFL/soccer until
this real ESPN-roster-endpoint wiring -- ESPNClient.roster() already existed
and had one real caller (wnba_availability_evaluation.py, an incumbent-system
consumer), but nothing in the rebuild pipeline resolved its athletes to a
canonical identity or wrote them through the medallion architecture.
"""

from __future__ import annotations

from unittest.mock import patch

from model_prediction.rebuild import MetadataDB
from model_prediction.rebuild.collectors import NBACollector, NFLCollector, SoccerCollector


class FakeESPNRosterClient:
    """Shape verified against a real cached ESPN roster payload
    (data/availability/wnba/espn_rosters/2026-8.json): athletes carry a
    real, stable numeric `id` alongside `displayName`, `position.abbreviation`,
    and `jersey`."""

    def roster(self, league, team_id, season):
        return {"team": {"id": team_id}, "athletes": [
            {"id": 4280850, "displayName": "Maya Caldwell", "position": {"abbreviation": "G"}, "jersey": "12"},
            {"id": 4433403, "displayName": "Other Player", "position": {"abbreviation": "F"}, "jersey": "34"},
        ]}


class TestNBACollectorRoster:
    def test_collect_espn_roster_writes_real_normalized_rows(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NBACollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            result = collector.collect_espn_roster("8", 2026, sport="wnba")

        assert result["status"] == "ok"
        assert result["players"] == 2

        df = collector.norm.read("wnba", "roster")
        assert df.height == 2
        names = set(df["player_name"].to_list())
        assert names == {"Maya Caldwell", "Other Player"}
        assert all(pid is not None for pid in df["player_canonical_id"].to_list())

    def test_two_players_get_two_distinct_canonical_identities(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NBACollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            collector.collect_espn_roster("8", 2026, sport="wnba")

        df = collector.norm.read("wnba", "roster")
        canonical_ids = df["player_canonical_id"].to_list()
        assert len(set(canonical_ids)) == 2

    def test_rerunning_collection_reuses_the_same_canonical_players(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NBACollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            collector.collect_espn_roster("8", 2026, sport="wnba")
            first = collector.identity.resolve("espn_public:wnba", "4280850").entity_id

            collector.collect_espn_roster("8", 2026, sport="wnba")
            second = collector.identity.resolve("espn_public:wnba", "4280850").entity_id

        assert first == second

    def test_roster_player_links_to_already_resolved_team_canonical_id(self, tmp_path):
        # Real cross-identity linking: if this team was already resolved
        # via scoreboard collection (same sport-namespaced source_id
        # resolve_espn_scoreboard_team_ids() uses), roster collection
        # should find and record that same canonical team id -- not
        # leave it null just because it arrived through a different
        # collection call.
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NBACollector(tmp_path / "data", meta)
        from model_prediction.rebuild.identity import resolve_or_register_team
        team = resolve_or_register_team(
            collector.identity, sport="wnba", source_id="espn_public:wnba", source_team_id="8",
            team_name="Atlanta Dream", effective_from_utc="2026-01-01",
        )

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            collector.collect_espn_roster("8", 2026, sport="wnba")

        df = collector.norm.read("wnba", "roster")
        assert set(df["team_canonical_id"].to_list()) == {team.entity_id}


class TestOtherSportsRosterWiring:
    """Confirms the shared _collect_espn_roster() helper is real and wired
    for NFL/soccer too, not just NBA/WNBA."""

    def test_nfl_collector_has_a_real_bound_roster_method(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = NFLCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            result = collector.collect_espn_roster("1", 2026)

        assert result["status"] == "ok"
        assert result["players"] == 2

    def test_soccer_collector_has_a_real_bound_roster_method(self, tmp_path):
        meta = MetadataDB(tmp_path / "metadata.db")
        collector = SoccerCollector(tmp_path / "data", meta)

        with patch(
            "model_prediction.data_sources.espn.ESPNClient",
            return_value=FakeESPNRosterClient(),
        ):
            result = collector.collect_espn_roster("1", 2026, league="EPL")

        assert result["status"] == "ok"
        assert result["players"] == 2
