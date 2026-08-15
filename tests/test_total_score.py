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
