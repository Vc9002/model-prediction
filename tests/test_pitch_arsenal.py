"""Unit tests for MLB Pitch Arsenal and PitchArsenalTensor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from model_prediction.features.pitch_arsenal import (
    LEAGUE_PITCH_BENCHMARKS,
    PitchArsenalTensor,
    PitchArsenalTracker,
    PitchMetrics,
    PitchTrackingEvent,
    create_sample_pitch_arsenal,
    normalize_pitch_type,
)


def test_normalize_pitch_type():
    """Verify pitch code normalization to 8 canonical types."""
    assert normalize_pitch_type("FF") == "4-seam"
    assert normalize_pitch_type("four_seam") == "4-seam"
    assert normalize_pitch_type("SI") == "sinker"
    assert normalize_pitch_type("two-seam") == "sinker"
    assert normalize_pitch_type("FC") == "cutter"
    assert normalize_pitch_type("SL") == "slider"
    assert normalize_pitch_type("ST") == "sweeper"
    assert normalize_pitch_type("SV") == "sweeper"
    assert normalize_pitch_type("CH") == "changeup"
    assert normalize_pitch_type("CU") == "curveball"
    assert normalize_pitch_type("KC") == "curveball"
    assert normalize_pitch_type("FS") == "splitter"
    assert normalize_pitch_type("FO") == "splitter"
    assert normalize_pitch_type(None) is None
    assert normalize_pitch_type("unknown_pitch") is None


def test_pitch_metrics_shrinkage():
    """Verify Bayesian shrinkage toward league benchmarks for small samples."""
    # Empty pitch -> shrinks to benchmark
    pm_empty = PitchMetrics(pitch_type="4-seam", count=0, velocity=0.0)
    shrunk_empty = pm_empty.with_shrinkage(prior_pitches=50.0)
    assert shrunk_empty.velocity == LEAGUE_PITCH_BENCHMARKS["4-seam"]["velocity"]
    assert shrunk_empty.whiff_rate == LEAGUE_PITCH_BENCHMARKS["4-seam"]["whiff_rate"]

    # Small sample (10 pitches at 98 mph) -> pulled strongly toward league ~94.2
    pm_small = PitchMetrics(pitch_type="4-seam", count=10, velocity=98.0)
    shrunk_small = pm_small.with_shrinkage(prior_pitches=40.0)
    expected_v = (10 / 50) * 98.0 + (40 / 50) * LEAGUE_PITCH_BENCHMARKS["4-seam"]["velocity"]
    assert pytest.approx(shrunk_small.velocity, abs=0.05) == expected_v

    # Large sample (1000 pitches at 98 mph) -> minimally pulled
    pm_large = PitchMetrics(pitch_type="4-seam", count=1000, velocity=98.0)
    shrunk_large = pm_large.with_shrinkage(prior_pitches=40.0)
    assert pytest.approx(shrunk_large.velocity, abs=0.2) == 98.0


def test_pitch_arsenal_properties():
    """Verify PitchArsenal composite indicators and summary calculations."""
    arsenal = create_sample_pitch_arsenal(
        pitcher_id="p101",
        primary_type="4-seam",
        primary_velo=97.0,
        secondary_type="slider",
        secondary_velo=88.0,
        whiff_boost=0.08,
        total_pitches=800,
    )

    assert arsenal.pitcher_id == "p101"
    assert arsenal.total_pitches == 800
    assert arsenal.primary_pitch == "4-seam"
    assert arsenal.secondary_pitch == "slider"
    assert arsenal.repertoire_diversity > 0.8
    assert arsenal.max_velocity >= 97.0
    assert arsenal.weighted_velocity > 90.0
    assert arsenal.overall_whiff_rate > 0.25
    assert arsenal.overall_csw_rate > 0.28
    assert arsenal.fastball_usage > 0.40
    assert arsenal.breaking_ball_usage > 0.25
    assert arsenal.stuff_plus_proxy > 100.0

    mat = arsenal.get_metric_matrix()
    assert mat.shape == (8, 6)
    assert np.all(mat[:, 0] >= 0.0)  # Usage rates >= 0


def test_pitch_arsenal_tensor_roundtrips():
    """Verify PitchArsenalTensor serialization, vector conversions, and simulation modifiers."""
    arsenal = create_sample_pitch_arsenal(
        pitcher_id="p102",
        primary_type="4-seam",
        primary_velo=96.0,
        secondary_type="changeup",
        secondary_velo=86.5,
    )
    tensor = arsenal.to_tensor()
    assert isinstance(tensor, PitchArsenalTensor)
    assert tensor.pitch_matrix.shape == (8, 6)
    assert tensor.summary_vector.shape == (10,)
    assert tensor.vector.shape == (58,)
    assert len(tensor.feature_names) == 58

    # Numpy roundtrip
    arr = tensor.to_numpy()
    reconstructed = PitchArsenalTensor.from_numpy(arr)
    np.testing.assert_allclose(tensor.vector, reconstructed.vector, atol=1e-5)

    # Dict roundtrip
    d = tensor.to_dict()
    from_dict_tensor = PitchArsenalTensor.from_dict(d)
    np.testing.assert_allclose(tensor.vector, from_dict_tensor.vector, atol=1e-5)

    # Simulation modifiers
    mods = tensor.to_simulation_modifiers()
    assert "k_rate_mult" in mods
    assert "bb_rate_mult" in mods
    assert "hr_suppression" in mods
    assert "whiff_factor" in mods
    assert 0.65 <= mods["k_rate_mult"] <= 1.55
    assert 0.60 <= mods["bb_rate_mult"] <= 1.40
    assert 0.70 <= mods["hr_suppression"] <= 1.45


def test_strict_pit_sequential_tracker(tmp_path: Path):
    """Verify strict Point-in-Time tracking: no future pitches leak into past queries."""
    tracker = PitchArsenalTracker()
    pitcher_id = "ace_starter"

    # Game 1: 2024-04-01 (10 pitches, 4-seam at 95.0)
    for i in range(10):
        tracker.add_pitch_event(
            PitchTrackingEvent(
                pitcher_id=pitcher_id,
                timestamp_utc=datetime(2024, 4, 1, 19, i, tzinfo=UTC),
                pitch_type="4-seam",
                velocity=95.0,
                is_swing=True,
                is_whiff=(i % 2 == 0),
                is_called_strike=False,
            )
        )

    # Game 2: 2024-04-07 (20 pitches, 4-seam at 98.0, slider at 89.0)
    for i in range(10):
        tracker.add_pitch_event(
            PitchTrackingEvent(
                pitcher_id=pitcher_id,
                timestamp_utc=datetime(2024, 4, 7, 19, i, tzinfo=UTC),
                pitch_type="4-seam",
                velocity=98.0,
                is_swing=True,
                is_whiff=True,
                is_called_strike=False,
            )
        )
    for i in range(10):
        tracker.add_pitch_event(
            PitchTrackingEvent(
                pitcher_id=pitcher_id,
                timestamp_utc=datetime(2024, 4, 7, 19, 10 + i, tzinfo=UTC),
                pitch_type="slider",
                velocity=89.0,
                is_swing=True,
                is_whiff=True,
                is_called_strike=False,
            )
        )

    # Game 3: 2024-04-13 (Future game)
    for i in range(10):
        tracker.add_pitch_event(
            PitchTrackingEvent(
                pitcher_id=pitcher_id,
                timestamp_utc=datetime(2024, 4, 13, 19, i, tzinfo=UTC),
                pitch_type="cutter",
                velocity=92.0,
            )
        )

    # Query 1: Before Game 1 (2024-04-01 12:00 UTC) -> 0 pitches
    arsenal_q1 = tracker.get_arsenal(pitcher_id, as_of_utc="2024-04-01T12:00:00Z")
    assert arsenal_q1.total_pitches == 0

    # Query 2: Between Game 1 and Game 2 (2024-04-05 00:00 UTC) -> exactly 10 pitches
    arsenal_q2 = tracker.get_arsenal(pitcher_id, as_of_utc="2024-04-05T00:00:00Z")
    assert arsenal_q2.total_pitches == 10
    assert arsenal_q2.pitches["cutter"].count == 0  # Future cutter must not exist!
    assert arsenal_q2.pitches["slider"].count == 0  # Slider thrown on 04-07 must not exist!
    assert arsenal_q2.pitches["4-seam"].count == 10

    # Query 3: After Game 2, before Game 3 (2024-04-10 00:00 UTC) -> exactly 30 pitches
    arsenal_q3 = tracker.get_arsenal(pitcher_id, as_of_utc="2024-04-10T00:00:00Z")
    assert arsenal_q3.total_pitches == 30
    assert arsenal_q3.pitches["4-seam"].count == 20
    assert arsenal_q3.pitches["slider"].count == 10
    assert arsenal_q3.pitches["cutter"].count == 0  # Still no future cutter!

    # Query 4: After Game 3 (2024-04-15 00:00 UTC) -> exactly 40 pitches
    arsenal_q4 = tracker.get_arsenal(pitcher_id, as_of_utc="2024-04-15T00:00:00Z")
    assert arsenal_q4.total_pitches == 40
    assert arsenal_q4.pitches["cutter"].count == 10

    # Test persistence to JSONL
    dump_file = tmp_path / "pitches.jsonl"
    count = tracker.dump_jsonl(dump_file)
    assert count == 40
    loaded_tracker = PitchArsenalTracker.load_jsonl(dump_file)
    loaded_arsenal = loaded_tracker.get_arsenal(pitcher_id, as_of_utc="2024-04-10T00:00:00Z")
    assert loaded_arsenal.total_pitches == 30


def test_statcast_dict_ingest():
    """Verify ingestion of raw Statcast-like dictionary rows."""
    tracker = PitchArsenalTracker()
    raw_rows = [
        {
            "pitcher": 669203,
            "game_date": "2024-05-01",
            "timestamp_utc": "2024-05-01T20:00:00Z",
            "pitch_type": "FF",
            "release_speed": 97.4,
            "pfx_x": -4.2,
            "pfx_z": 16.8,
            "description": "swinging_strike",
        },
        {
            "pitcher": 669203,
            "game_date": "2024-05-01",
            "timestamp_utc": "2024-05-01T20:01:00Z",
            "pitch_type": "SL",
            "release_speed": 87.2,
            "pfx_x": 5.4,
            "pfx_z": 2.1,
            "description": "called_strike",
        },
    ]
    ingested = tracker.ingest_records(raw_rows)
    assert ingested == 2

    arsenal = tracker.get_arsenal(669203, as_of_utc="2024-05-02T00:00:00Z")
    assert arsenal.total_pitches == 2
    assert arsenal.pitches["4-seam"].count == 1
    assert arsenal.pitches["slider"].count == 1
