from __future__ import annotations

import polars as pl
import pytest

from model_prediction.rebuild.wnba.horizon_builder import (
    _assert_research_source_provenance,
    _latest_targets,
)
from model_prediction.rebuild.wnba.time import sports_event_date


def _source_frame() -> pl.DataFrame:
    return pl.DataFrame([{
        "event_id": "late-et",
        "event_start_utc": "2026-05-02T02:00:00+00:00",
        "sports_event_date": "2026-05-01",
        "observed_at_utc": "2026-05-01T20:00:00+00:00",
        "raw_snapshot_hash": "raw-1",
        "availability_basis": "capture_time_only",
        "commercial_use_status": "unresolved",
        "production_allowed": False,
    }])


def test_late_eastern_game_stays_on_one_wnba_sports_date_across_utc_midnight():
    assert sports_event_date("2026-05-02T02:00:00+00:00") == "2026-05-01"
    targets = _latest_targets(_source_frame(), "2026-05-01")
    assert targets["event_id"].to_list() == ["late-et"]
    assert _latest_targets(_source_frame(), "2026-05-02").is_empty()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("availability_basis", None),
        ("availability_basis", "unknown"),
        ("commercial_use_status", None),
        ("commercial_use_status", "cleared"),
        ("production_allowed", None),
        ("production_allowed", True),
        ("raw_snapshot_hash", None),
        ("raw_snapshot_hash", ""),
    ],
)
def test_source_rights_and_provenance_fail_closed_before_feature_filtering(column, value):
    frame = _source_frame().with_columns(pl.lit(value).alias(column))
    with pytest.raises(ValueError):
        _assert_research_source_provenance(frame, "games")


def test_missing_source_provenance_column_fails_closed():
    with pytest.raises(ValueError, match="missing provenance"):
        _assert_research_source_provenance(
            _source_frame().drop("commercial_use_status"), "games",
        )
