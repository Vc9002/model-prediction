"""Tests for the air-density weather-deviation shadow feature."""

from __future__ import annotations

import json

from model_prediction.features.air_density_weather import (
    air_density_distance_factor,
    park_monthly_temperature_norms,
)


def _write_snapshot_file(tmp_path, venue: str, month: int, temp: float, condition: str = "Clear") -> str:
    path = tmp_path / "game_snapshots.jsonl"
    rows = [
        {
            "game_start_utc": f"2026-0{month}-15T18:05:00Z",
            "venue_name": venue,
            "weather": {"temperature_f": temp, "condition": condition},
        }
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def test_park_monthly_temperature_norms_average(tmp_path):
    path = _write_snapshot_file(tmp_path, "Coors Field", 7, 80.0)
    norms = park_monthly_temperature_norms(path)
    assert norms[("Coors Field", 7)] == 80.0


def test_hotter_than_norm_raises_factor(tmp_path):
    # Norm 80F in July; game at 91F -> lighter air -> factor > 1.
    path = _write_snapshot_file(tmp_path, "Coors Field", 7, 80.0)
    result = air_density_distance_factor(
        "Coors Field", "2026-07-20T18:05:00Z", 91.0, "Clear", snapshot_path=path
    )
    assert result["status"] == "available"
    assert result["factor"] > 1.0


def test_colder_than_norm_lowers_factor(tmp_path):
    path = _write_snapshot_file(tmp_path, "Fenway Park", 4, 55.0)
    result = air_density_distance_factor(
        "Fenway Park", "2026-04-20T18:05:00Z", 44.0, "Clear", snapshot_path=path
    )
    assert result["status"] == "available"
    assert result["factor"] < 1.0


def test_at_norm_is_exactly_neutral(tmp_path):
    path = _write_snapshot_file(tmp_path, "Wrigley Field", 6, 70.0)
    result = air_density_distance_factor(
        "Wrigley Field", "2026-06-20T18:05:00Z", 70.0, "Clear", snapshot_path=path
    )
    assert result["status"] == "available"
    assert result["factor"] == 1.0


def test_indoor_is_neutral(tmp_path):
    path = _write_snapshot_file(tmp_path, "Tropicana Field", 6, 72.0, condition="Dome")
    result = air_density_distance_factor(
        "Tropicana Field", "2026-06-20T18:05:00Z", 72.0, "Dome", snapshot_path=path
    )
    assert result == {"factor": 1.0, "status": "indoor_climate_controlled"}


def test_roof_closed_is_neutral(tmp_path):
    path = _write_snapshot_file(tmp_path, "Chase Field", 6, 72.0, condition="Roof Closed")
    result = air_density_distance_factor(
        "Chase Field", "2026-06-20T18:05:00Z", 90.0, "Roof Closed", snapshot_path=path
    )
    assert result == {"factor": 1.0, "status": "indoor_climate_controlled"}


def test_missing_temperature_is_neutral(tmp_path):
    path = _write_snapshot_file(tmp_path, "Petco Park", 6, 70.0)
    result = air_density_distance_factor(
        "Petco Park", "2026-06-20T18:05:00Z", None, "Clear", snapshot_path=path
    )
    assert result == {"factor": 1.0, "status": "unavailable_from_source"}


def test_no_norm_for_park_month_is_neutral(tmp_path):
    path = _write_snapshot_file(tmp_path, "Petco Park", 6, 70.0)
    result = air_density_distance_factor(
        "Petco Park", "2026-09-20T18:05:00Z", 75.0, "Clear", snapshot_path=path
    )
    assert result == {"factor": 1.0, "status": "no_park_month_norm"}
