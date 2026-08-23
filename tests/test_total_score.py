from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.total_score import TotalScoreArtifact, build_total_score_rows


def _games(count: int, final_total_delta: int = 0) -> list[GameRecord]:
    teams = ("A", "B", "C", "D")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    games = []
    for index in range(count):
        away = teams[index % 4]
        home = teams[(index + 1 + index // 4) % 4]
        if home == away:
            home = teams[(teams.index(home) + 1) % 4]
        away_score = 80 + teams.index(away) * 5 + index % 7
        home_score = 82 + teams.index(home) * 4 + (index * 3) % 9
        if index == count - 1:
            home_score += final_total_delta
        games.append(
            GameRecord(
                event_id=str(index),
                event_start_utc=(start + timedelta(days=index)).isoformat(),
                league="TEST",
                away_team=away,
                home_team=home,
                away_score=away_score,
                home_score=home_score,
            )
        )
    return games


def test_target_game_score_cannot_leak_into_its_features() -> None:
    ordinary = build_total_score_rows(_games(100))
    extreme_final = build_total_score_rows(_games(100, final_total_delta=100))

    assert ordinary[-1].event_id == extreme_final[-1].event_id
    assert ordinary[-1].features == extreme_final[-1].features
    assert ordinary[-1].actual_total + 100 == extreme_final[-1].actual_total


def test_total_score_artifact_hash_and_prediction() -> None:
    payload = {
        "artifact_hash": "",
        "feature_names": ["x"],
        "coefficients": [2.0],
        "intercept": 1.0,
    }
    from model_prediction.total_score import _artifact_hash

    payload["artifact_hash"] = _artifact_hash(payload)
    artifact = TotalScoreArtifact(payload)

    assert artifact.predict({"x": 3.0}) == 7.0
    with pytest.raises(ValueError, match="missing total-score features"):
        artifact.predict({})
    payload["coefficients"] = [3.0]
    with pytest.raises(ValueError, match="hash mismatch"):
        TotalScoreArtifact(payload)


def test_mlb_pitching_runs_allowed():
    from model_prediction.total_score import mlb_pitching_runs_allowed

    # Normal starter on full rest (5.5 IP, 3.60 ERA) + Bullpen (3.5 IP, 4.50 ERA)
    # Expected: (3.60 / 9 * 5.5) + (4.50 / 9 * 3.5) = 2.20 + 1.75 = 3.95
    res = mlb_pitching_runs_allowed(
        starter_era=3.60, starter_expected_ip=5.5, bullpen_era=4.50, rest_days=5.0
    )
    assert res["rest_penalty_applied"] is False
    assert pytest.approx(res["expected_runs_allowed"], abs=0.01) == 3.95
    assert pytest.approx(res["starter_runs"], abs=0.01) == 2.20
    assert pytest.approx(res["bullpen_runs"], abs=0.01) == 1.75

    # Short rest (< 4 days) adds +0.50 ERA penalty
    res_short = mlb_pitching_runs_allowed(
        starter_era=3.60, starter_expected_ip=5.5, bullpen_era=4.50, rest_days=3.0
    )
    assert res_short["rest_penalty_applied"] is True
    assert res_short["effective_starter_era"] == 4.10
    assert res_short["expected_runs_allowed"] > res["expected_runs_allowed"]


def test_stadium_wind_orientation_multiplier():
    from model_prediction.total_score import stadium_wind_orientation_multiplier

    # Dome is completely neutral (1.0)
    assert stadium_wind_orientation_multiplier(wind_speed_mph=20, wind_direction_deg=0, is_dome=True) == 1.0

    # Wind blowing directly out to center (park=0 deg, wind=0 deg -> cos(0)=1)
    mult_out = stadium_wind_orientation_multiplier(
        wind_speed_mph=15.0, wind_direction_deg=0.0, park_orientation_deg=0.0, temp_f=72.0
    )
    assert mult_out > 1.05  # positive boost

    # Wind blowing directly in from center (park=0 deg, wind=180 deg -> cos(pi)=-1)
    mult_in = stadium_wind_orientation_multiplier(
        wind_speed_mph=15.0, wind_direction_deg=180.0, park_orientation_deg=0.0, temp_f=72.0
    )
    assert mult_in < 0.95  # negative suppression

    # High temperature boosts run expectancy
    mult_hot = stadium_wind_orientation_multiplier(wind_speed_mph=0.0, wind_direction_deg=0.0, temp_f=95.0)
    assert mult_hot > 1.0


def test_mlb_totals_v2_projected_runs():
    from model_prediction.total_score import mlb_pitching_runs_allowed, mlb_totals_v2_projected_runs

    home_pit = mlb_pitching_runs_allowed(starter_era=3.50, starter_expected_ip=6.0, bullpen_era=4.00)
    away_pit = mlb_pitching_runs_allowed(starter_era=4.50, starter_expected_ip=5.0, bullpen_era=4.50)

    proj = mlb_totals_v2_projected_runs(
        home_pitching=home_pit,
        away_pitching=away_pit,
        home_lineup_ops_ratio=1.05,
        away_lineup_ops_ratio=0.95,
        park_factor=1.04,
        wind_weather_multiplier=1.02,
    )
    assert proj["total_projected_runs"] > 6.0
    assert proj["home_projected_runs"] > proj["away_projected_runs"]
    assert pytest.approx(proj["total_projected_runs"], abs=0.01) == (
        proj["home_projected_runs"] + proj["away_projected_runs"]
    )
