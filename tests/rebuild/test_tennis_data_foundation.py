from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.providers.base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
)
from model_prediction.rebuild.tennis.audit import audit_tennis_data
from model_prediction.rebuild.tennis.foundation import TennisFoundation
from model_prediction.rebuild.tennis.normalize import (
    normalize_espn_scoreboard,
    normalize_tennismylife_matches,
)
from model_prediction.rebuild.tennis.pit import eligible_matches_as_of, eligible_prior_matches_for_player
from model_prediction.rebuild.tennis.store import TennisNormalizedStore

FIXTURES = Path(__file__).parent / "fixtures/tennis"


def _metadata(
    provider: str, observed: str = "2026-08-09T00:00:00+00:00", content: str = "a"
) -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider=provider,
        sport="tennis",
        endpoint_family="fixture",
        requested_parameters={},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=200,
        content_hash=content * 64,
        schema_hash="b" * 64,
        source_version=f"{provider}:fixture",
        source_grade=SourceGrade.B,
        license_id="test-license",
        license_url="https://example.invalid/license",
        attribution_required=True,
        attribution_text="Test attribution",
        upstream_rights_status="unresolved",
        commercial_use_status="unresolved",
        production_allowed=False,
    )


def _mylife_fixture() -> pl.DataFrame:
    return pl.DataFrame(json.loads((FIXTURES / "mylife_atp_2024.json").read_text()))


def _espn_fixture() -> pl.DataFrame:
    return pl.DataFrame(json.loads((FIXTURES / "espn_wta_scoreboard.json").read_text()))


def test_tennismylife_normalization_encodes_retirement_and_walkover_explicitly():
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    assert matches.height == 3
    by_num = {row["match_num"]: row for row in matches.iter_rows(named=True)}
    assert by_num[1]["result_type"] == "completed"
    assert by_num[1]["completed"] is True
    assert by_num[2]["result_type"] == "retirement"
    assert by_num[2]["completed"] is False
    assert by_num[3]["result_type"] == "walkover"
    assert by_num[3]["completed"] is False
    assert matches["availability_basis"][0] == "capture_time_only"
    assert matches["upstream_rights_status"][0] == "unresolved"
    assert matches["production_allowed"][0] is False


def test_tennismylife_identity_is_provider_scoped_not_name_derived():
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    row = matches.filter(pl.col("match_num") == 1).row(0, named=True)
    assert row["winner_tennis_player_id"] == "tennis_mylife:104925"
    assert row["winner_provider_player_id"] == "104925"
    assert row["winner_player_name"] == "Novak Djokovic"


def test_tennismylife_canonical_match_id_is_tourney_scoped():
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    ids = matches["canonical_match_id"].to_list()
    assert ids == [
        "tennis_mylife:2024-580:1",
        "tennis_mylife:2024-580:2",
        "tennis_mylife:2024-580:3",
    ]


def test_espn_normalization_filters_doubles_and_encodes_retirement():
    events = normalize_espn_scoreboard(_espn_fixture(), _metadata("tennis_espn"))
    # 3 fixture rows, 1 is doubles (missing per-player names) -- must be filtered, not crash.
    assert events.height == 2
    by_competition = {row["canonical_match_id"]: row for row in events.iter_rows(named=True)}
    final = by_competition["espn_tennis:421-2026:183001"]
    assert final["result_type"] == "completed"
    assert final["competitor_1_player_name"] == "Iga Swiatek"
    retired = by_competition["espn_tennis:421-2026:183002"]
    assert retired["result_type"] == "retirement"
    assert retired["completed"] is False


def test_pit_eligibility_hides_matches_observed_after_decision():
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    before = eligible_matches_as_of(matches, datetime(2026, 8, 8, tzinfo=UTC))
    after = eligible_matches_as_of(matches, datetime(2026, 8, 10, tzinfo=UTC))
    assert before.is_empty()
    assert after.height == 3


def test_prior_matches_for_player_only_within_same_provider_id_space():
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    decision = datetime(2026, 8, 10, tzinfo=UTC)
    djokovic_matches = eligible_prior_matches_for_player(
        matches, tennis_player_id="tennis_mylife:104925", decision_time_utc=decision,
    )
    assert djokovic_matches.height == 1
    assert djokovic_matches["winner_player_name"][0] == "Novak Djokovic"
    # Retirement/walkover matches are excluded by completed_only in eligible_matches_as_of.
    zverev_matches = eligible_prior_matches_for_player(
        matches, tennis_player_id="tennis_mylife:126094", decision_time_utc=decision,
    )
    assert zverev_matches.is_empty()


def test_store_is_content_idempotent_and_partitions_by_tour_and_year(tmp_path):
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    store = TennisNormalizedStore(tmp_path)
    paths = store.write_matches(matches)
    assert store.write_matches(matches) == paths
    stored = store.read_matches(tour="atp", year=2024)
    assert stored.height == 3
    assert (tmp_path / "tennis" / "matches" / "tour=atp" / "year=2024").exists()


def test_current_events_partition_by_capture_date(tmp_path):
    events = normalize_espn_scoreboard(_espn_fixture(), _metadata("tennis_espn", observed="2026-08-09T12:00:00+00:00"))
    store = TennisNormalizedStore(tmp_path)
    store.write_current_events(events)
    assert (tmp_path / "tennis" / "current_events" / "tour=wta" / "date=2026-08-09").exists()
    assert store.read_current_events(tour="wta").height == 2


def test_audit_reports_honest_result_type_breakdown(tmp_path):
    matches = normalize_tennismylife_matches(_mylife_fixture(), _metadata("tennis_mylife"), tour="atp")
    events = normalize_espn_scoreboard(_espn_fixture(), _metadata("tennis_espn"))
    store = TennisNormalizedStore(tmp_path)
    store.write_matches(matches)
    store.write_current_events(events)
    report = audit_tennis_data(store)
    assert report["status"] == "HEALTHY"
    assert report["match_result_types"] == {"completed": 1, "retirement": 1, "walkover": 1}
    assert report["current_event_result_types"] == {"completed": 1, "retirement": 1}
    assert report["production_allowed"] is False


def test_audit_against_empty_store_is_honest_unavailable(tmp_path):
    store = TennisNormalizedStore(tmp_path)
    report = audit_tennis_data(store)
    assert report["status"] == "UNAVAILABLE"


def test_foundation_backfill_writes_manifest_and_is_idempotent(tmp_path):
    class FixtureTennisMyLifeProvider:
        def year_matches(self, tour: str, year: int, *, kind: str = "main", force: bool = False) -> ProviderResult:
            assert tour == "atp"
            assert year == 2024
            return ProviderResult(ProviderStatus.AVAILABLE, _metadata("tennis_mylife"), _mylife_fixture())

    foundation = TennisFoundation(tmp_path, tennis_mylife=FixtureTennisMyLifeProvider())  # type: ignore[arg-type]
    first = foundation.backfill_matches("atp", [2024])
    second = foundation.backfill_matches("atp", [2024])
    assert first["seasons"][0]["dataset_hash"] == second["seasons"][0]["dataset_hash"]
    assert first["seasons"][0]["rows"] == 3


def test_foundation_collect_current_writes_normalized_events(tmp_path):
    class FixtureESPNTennisProvider:
        def scoreboard(self, tour: str, *, force: bool = False) -> ProviderResult:
            assert tour == "wta"
            return ProviderResult(ProviderStatus.AVAILABLE, _metadata("tennis_espn"), _espn_fixture())

    foundation = TennisFoundation(tmp_path, espn=FixtureESPNTennisProvider())  # type: ignore[arg-type]
    report = foundation.collect_current("wta")
    assert report["status"] == "AVAILABLE"
    assert report["rows"] == 2


def test_foundation_backfill_without_configured_provider_fails_closed(tmp_path):
    foundation = TennisFoundation(tmp_path)
    with pytest.raises(RuntimeError, match="TennisMyLife provider is not configured"):
        foundation.backfill_matches("atp", [2024])
    with pytest.raises(RuntimeError, match="ESPN tennis provider is not configured"):
        foundation.collect_current("wta")
