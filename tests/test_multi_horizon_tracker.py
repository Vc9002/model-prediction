"""Tests for Multi-Horizon Evidence Tracker."""

from pathlib import Path

from model_prediction.features.multi_horizon_tracker import (
    HorizonObservation,
    MultiHorizonTracker,
)


def test_infer_horizon_label():
    assert MultiHorizonTracker.infer_horizon_label(360) == "T-6h"
    assert MultiHorizonTracker.infer_horizon_label(180) == "T-3h"
    assert MultiHorizonTracker.infer_horizon_label(55) == "T-1h"
    assert MultiHorizonTracker.infer_horizon_label(28) == "T-30m"
    assert MultiHorizonTracker.infer_horizon_label(8) == "T-10m"


def test_multi_horizon_tracker_record_and_load(tmp_path: Path):
    tracker = MultiHorizonTracker(log_path=tmp_path / "horizons.jsonl")
    obs = HorizonObservation(
        event_id="mlb-lad-sf-2026-09-01",
        sport="MLB",
        market_slug="aec-mlb-lad-sf-2026-09-01",
        horizon_label="T-30m",
        observed_at_utc="2026-09-01T22:30:00Z",
        event_start_utc="2026-09-01T23:00:00Z",
        minutes_to_start=30.0,
        model_id="mlb-structural-v10-frozen",
        model_prob=0.585,
        market_fair_prob=0.540,
        market_bid=0.535,
        market_ask=0.545,
        starter_status="confirmed",
        lineup_status="confirmed",
        wind_mph=8.0,
        temperature_f=72.0,
        lineup_woba_delta=0.015,
        starter_csw_delta=0.020,
        market_move_from_open=0.010,
    )
    tracker.record_observation(obs)

    records = tracker.load_observations("mlb-lad-sf-2026-09-01")
    assert len(records) == 1
    assert records[0]["horizon_label"] == "T-30m"
    assert records[0]["lineup_woba_delta"] == 0.015
