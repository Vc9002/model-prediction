from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.providers.base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
)
from model_prediction.rebuild.tennis.live import (
    LiveRejectReason,
    TennisLivePolicy,
    validate_live_events,
)
from model_prediction.rebuild.tennis.normalize import (
    TennisNormalizationContext,
    normalize_matches,
    normalize_players,
    normalize_rankings,
)
from model_prediction.rebuild.tennis.pit import eligible_prior_matches, ranking_as_of
from model_prediction.rebuild.tennis.policy import (
    HISTORICAL_SOURCE_POLICY,
    HistoricalSourcePolicy,
    TennisSourcePolicyError,
)
from model_prediction.rebuild.tennis.snapshot import (
    SnapshotFile,
    TennisSnapshotManifest,
    verify_local_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tennis"


def _frame(name: str) -> pl.DataFrame:
    return pl.DataFrame(json.loads((FIXTURES / name).read_text()))


def _metadata(observed: str = "2026-08-09T00:00:00+00:00") -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider="jeff_sackmann",
        sport="tennis",
        endpoint_family="approved_local_snapshot",
        requested_parameters={"tour": "ATP"},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=None,
        content_hash="a" * 64,
        schema_hash="b" * 64,
        source_version="1" * 40,
        source_grade=SourceGrade.B,
    )


def _manifest(**updates: object) -> TennisSnapshotManifest:
    values: dict[str, object] = {
        "provider": "jeff_sackmann",
        "tour": "ATP",
        "source_repository_url": "https://github.com/JeffSackmann/tennis_atp",
        "source_revision": "1" * 40,
        "retrieved_at_utc": "2026-08-09T00:00:00+00:00",
        "license_id": "CC-BY-NC-SA-4.0",
        "attribution": "Synthetic contract fixture; source shape attributed to Jeff Sackmann / Tennis Abstract",
        "availability_basis": "capture_time_only",
        "history_complete": False,
        "files": (SnapshotFile("synthetic.csv", "a" * 64, 1),),
    }
    values.update(updates)
    manifest = TennisSnapshotManifest(**values)  # type: ignore[arg-type]
    manifest.validate()
    return manifest


def _context() -> TennisNormalizationContext:
    return TennisNormalizationContext(_manifest(), _metadata())


def test_removed_primary_and_all_remote_mirrors_are_policy_blocked():
    result = HISTORICAL_SOURCE_POLICY.unavailable_result()
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "SOURCE_UNAVAILABLE" in (result.reason or "")
    for location in (
        "https://github.com/JeffSackmann/tennis_atp",
        "https://example.invalid/tennis-mirror",
    ):
        with pytest.raises(TennisSourcePolicyError, match="remote tennis sources"):
            HISTORICAL_SOURCE_POLICY.require_approved_local_root(location)


def test_manifest_rejects_a_mirror_and_every_unverified_history_claim():
    with pytest.raises(ValueError, match="mirror"):
        _manifest(source_repository_url="https://example.invalid/mirror")
    with pytest.raises(ValueError, match="capture_time_only"):
        _manifest(availability_basis="git_revision_upper_bound", history_complete=False)
    with pytest.raises(ValueError, match="history_complete"):
        _manifest(history_complete=True)


def test_disabled_policy_cannot_be_turned_on_by_a_manifest(tmp_path):
    with pytest.raises(TennisSourcePolicyError, match="disabled"):
        verify_local_snapshot(tmp_path, _manifest())


def test_explicitly_approved_local_snapshot_is_hash_verified_without_network(tmp_path):
    content = b"synthetic-only\n"
    (tmp_path / "synthetic.csv").write_bytes(content)
    manifest = _manifest(files=(SnapshotFile(
        "synthetic.csv", hashlib.sha256(content).hexdigest(), len(content),
    ),))
    approved = HistoricalSourcePolicy(
        provider="jeff_sackmann",
        enabled=True,
        network_download_allowed=False,
        approved_for_commercial_use=False,
        license_id="CC-BY-NC-SA-4.0",
        availability_basis="capture_time_only",
        former_primary_urls=HISTORICAL_SOURCE_POLICY.former_primary_urls,
        reason="unit-test-only local approval",
    )
    verify_local_snapshot(tmp_path.resolve(), manifest, policy=approved)
    (tmp_path / "synthetic.csv").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        verify_local_snapshot(tmp_path.resolve(), manifest, policy=approved)


def test_strict_normalizers_preserve_capture_time_and_date_granularity():
    players = normalize_players(_frame("synthetic_players.json"), _context())
    rankings = normalize_rankings(_frame("synthetic_rankings.json"), _context())
    matches = normalize_matches(_frame("synthetic_matches.json"), _context())

    assert players.height == 2
    assert set(players["identity_status"]) == {"UNRESOLVED"}
    assert set(rankings["temporal_granularity"]) == {"DATE_ONLY"}
    assert rankings["effective_at_utc"].null_count() == rankings.height
    assert set(matches["temporal_granularity"]) == {"TOURNAMENT_START_DATE_ONLY"}
    assert matches["actual_start_utc"].null_count() == matches.height
    assert not any(matches["historical_observation_verified"])
    assert set(matches["observed_at_utc"]) == {"2026-08-09T00:00:00+00:00"}


def test_atp_and_wta_same_numeric_player_id_remain_separate_namespaces():
    atp = normalize_players(_frame("synthetic_players.json").head(1), _context())
    wta_context = TennisNormalizationContext(_manifest(tour="WTA"), _metadata())
    wta = normalize_players(_frame("synthetic_players.json").head(1), wta_context)
    assert atp["source_record_id"][0] == "ATP:900001"
    assert wta["source_record_id"][0] == "WTA:900001"
    assert atp["tour"][0] != wta["tour"][0]


def test_schema_drift_and_unstable_identity_fail_closed():
    drifted = _frame("synthetic_players.json").with_columns(pl.lit("surprise").alias("new_column"))
    with pytest.raises(ValueError, match="schema drift"):
        normalize_players(drifted, _context())

    unresolved = _frame("synthetic_players.json").with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("0")).otherwise(pl.col("player_id")).alias("player_id")
    )
    with pytest.raises(ValueError, match="player_id=0"):
        normalize_players(unresolved, _context())


def test_duplicate_ranking_key_fails_closed():
    rankings = _frame("synthetic_rankings.json")
    duplicate = pl.concat([rankings, rankings.head(1)])
    with pytest.raises(ValueError, match="duplicate primary key"):
        normalize_rankings(duplicate, _context())


def test_ranking_asof_excludes_same_day_and_capture_after_decision():
    rankings = normalize_rankings(_frame("synthetic_rankings.json"), _context())
    current = ranking_as_of(
        rankings,
        tour="ATP",
        player_source_id="900001",
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert current is not None
    assert current["ranking_date"] == "2026-08-03"

    retrospective = ranking_as_of(
        rankings,
        tour="ATP",
        player_source_id="900001",
        decision_time_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert retrospective is None


def test_match_pit_excludes_entire_same_tournament_date_bucket():
    matches = normalize_matches(_frame("synthetic_matches.json"), _context())
    prior = eligible_prior_matches(
        matches,
        target_tournament_start_date=date(2026, 8, 10),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert prior["source_match_id"].to_list() == ["ATP:SYN-2026-01:1"]

    retrospective = eligible_prior_matches(
        matches,
        target_tournament_start_date=date(2026, 8, 10),
        decision_time_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert retrospective.is_empty()


def _live_metadata(observed: str = "2026-08-10T11:58:00+00:00") -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider="synthetic_live_provider",
        sport="tennis",
        endpoint_family="events",
        requested_parameters={"date": "2026-08-10"},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=200,
        content_hash="c" * 64,
        schema_hash="d" * 64,
        source_grade=SourceGrade.C,
    )


def _live_result(frame: pl.DataFrame | None = None) -> ProviderResult:
    return ProviderResult(
        ProviderStatus.AVAILABLE,
        _live_metadata(),
        frame if frame is not None else _frame("synthetic_live_events.json"),
    )


def test_fresh_provider_neutral_live_event_passes():
    validated = validate_live_events(
        _live_result(),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert validated.available
    assert validated.frame is not None
    assert validated.frame.height == 1


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("discipline", "DOUBLES", LiveRejectReason.NOT_SINGLES),
        ("surface", "", LiveRejectReason.SURFACE_UNKNOWN),
        ("event_start_utc", "2026-08-10T11:59:00+00:00", LiveRejectReason.POST_START),
        ("player_a_canonical_id", "", LiveRejectReason.PLAYER_UNRESOLVED),
        ("player_b_canonical_id", "tennis:player:synthetic-a", LiveRejectReason.PLAYER_MAPPING_AMBIGUOUS),
    ],
)
def test_invalid_live_event_fails_closed(column: str, value: str, reason: LiveRejectReason):
    frame = _frame("synthetic_live_events.json").with_columns(pl.lit(value).alias(column))
    validated = validate_live_events(
        _live_result(frame),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert not validated.available
    assert validated.reason is reason


def test_live_source_stale_unavailable_empty_and_schema_drift_fail_closed():
    stale = ProviderResult(
        ProviderStatus.AVAILABLE,
        replace(_live_metadata(), observed_at_utc="2026-08-10T11:00:00+00:00"),
        _frame("synthetic_live_events.json"),
    )
    stale_result = validate_live_events(
        stale,
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
        policy=TennisLivePolicy(max_age_seconds=300),
    )
    assert stale_result.status is ProviderStatus.STALE
    assert stale_result.reason is LiveRejectReason.SOURCE_STALE

    unavailable = validate_live_events(
        ProviderResult.unavailable("provider 404"),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert unavailable.reason is LiveRejectReason.SOURCE_UNAVAILABLE

    empty = validate_live_events(
        _live_result(pl.DataFrame(schema={name: pl.String for name in sorted({
            "provider_event_id", "tour", "event_start_utc", "status", "discipline", "surface",
            "player_a_id", "player_b_id", "player_a_canonical_id", "player_b_canonical_id",
        })})),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert empty.reason is LiveRejectReason.NO_SCHEDULED_EVENTS

    drifted = validate_live_events(
        _live_result(_frame("synthetic_live_events.json").drop("surface")),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert drifted.status is ProviderStatus.DEGRADED
    assert drifted.reason is LiveRejectReason.SCHEMA_DRIFT


def test_duplicate_live_provider_event_id_is_ambiguous_not_first_row_wins():
    frame = _frame("synthetic_live_events.json")
    duplicate = pl.concat([frame, frame.with_columns(pl.lit("CLAY").alias("surface"))])
    validated = validate_live_events(
        _live_result(duplicate),
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert not validated.available
    assert validated.reason is LiveRejectReason.EVENT_AMBIGUOUS
    assert len(validated.rejected) == 2
