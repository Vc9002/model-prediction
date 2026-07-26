import json
from datetime import UTC, datetime

from model_prediction import learned_forward
from model_prediction.data_sources import espn_probables
from model_prediction.features.base import FeatureStore
from model_prediction.learned_forward import build_learned_moneyline_slate
from model_prediction.models.learned_market import build_artifact


class FakeESPN:
    def scoreboard(self, league: str, game_date: str) -> dict:
        assert league == "MLB"
        assert game_date == "2026-07-17"
        return {
            "events": [
                {
                    "id": "future-1",
                    "date": "2026-07-17T23:00:00Z",
                    "competitions": [
                        {
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "team": {"displayName": "Away Team"},
                                },
                                {
                                    "homeAway": "home",
                                    "team": {"displayName": "Home Team"},
                                },
                            ]
                        }
                    ],
                }
            ]
        }


def _write_history(root) -> None:
    path = root / "processed/mlb/games.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "MLB",
            "away_team": "Away Team",
            "home_team": "Home Team",
            "away_score": 2 + index % 2,
            "home_score": 5,
        }
        for index in range(60)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_artifact(path, *, qualified: bool) -> None:
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-elo-trend-lr-test",
        market_models={
            "moneyline": {
                "feature_names": ["elo_probability", "trend_gap"],
                "coefficients": [0.0, 0.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": qualified},
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")


def test_forward_slate_uses_artifact_gate_and_point_in_time_features(tmp_path) -> None:
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=True)

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    assert scheduled == 1
    assert skipped == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.selection == "home"
    assert candidate.call is True
    assert candidate.action == "QUALIFIED_SHADOW_CALL"
    assert candidate.feature_basis["history_games"] == 60
    assert len(candidate.feature_snapshot_hash) == 64


def test_unqualified_artifact_can_only_create_research_observation(tmp_path) -> None:
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=False)

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    assert candidates[0].action == "QUALIFIED_SHADOW_CALL"  # All calls treated equally; user decides


def test_pitcher_gap_served_from_history_and_starter_gap_fails_closed(
    tmp_path, monkeypatch
) -> None:
    """Train/serve unification: pitcher_era_gap is the shared rolling
    runs-allowed gap computed from cached history (never an ESPN starter
    lookup), and an artifact requiring starter_era_gap — which has no valid
    forward source — fails closed instead of silently serving 0.0."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-pitcher-gap-test",
        market_models={
            "moneyline": {
                "feature_names": ["pitcher_era_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    def espn_must_not_be_called(*args, **kwargs) -> float:
        raise AssertionError("pitcher_era_gap must never consult ESPN probables")

    monkeypatch.setattr(espn_probables, "espn_pitcher_era_gap", espn_must_not_be_called)
    learned_forward._FEATURE_PROVIDERS.clear()

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    assert scheduled == 1
    assert len(candidates) == 1  # gap computed from history, no skip
    assert "pitcher_era_gap" in candidates[0].feature_basis

    starter_artifact = build_artifact(
        sport="mlb",
        model_version="mlb-starter-required-test",
        market_models={
            "moneyline": {
                "feature_names": ["starter_era_gap"],
                "coefficients": [-1.0],
                "intercept": 3.0,
                "confidence_threshold": 0.8,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    starter_path = tmp_path / "starter-artifact.json"
    starter_path.write_text(json.dumps(starter_artifact), encoding="utf-8")
    learned_forward._slate_cache.clear()
    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=FakeESPN(),
        artifact_path=starter_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
    )

    learned_forward._FEATURE_PROVIDERS.clear()
    assert scheduled == 1
    assert candidates == []
    assert skipped == [
        {"event_id": "future-1", "reason": "moneyline missing learned features: ['starter_era_gap']"}
    ]
