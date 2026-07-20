"""End-to-end MLB moneyline forward pipeline tests.

Exercises build_learned_moneyline_slate for MLB with the production
feature set: elo_probability, trend_gap, park_factor, starter_era_gap.
Also covers edge cases: insufficient history, already-started events,
below-threshold no-calls, multi-game slates, and feature basis integrity.
"""

import json
from datetime import datetime, timezone

import pytest

from model_prediction.features.base import FeatureStore
from model_prediction.learned_forward import build_learned_moneyline_slate
from model_prediction.models.learned_market import build_artifact


# ── Helpers ────────────────────────────────────────────────────────────────

def _write_history(root, *, home_team="Home Team", away_team="Away Team",
                   game_count=60, start_month=5, extra_rows=None) -> None:
    path = root / "processed/mlb/games.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(game_count):
        rows.append({
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-{start_month:02d}-{index % 28 + 1:02d}T12:00:00Z",
            "league": "MLB",
            "away_team": away_team,
            "home_team": home_team,
            "away_score": 2 + index % 2,
            "home_score": 5,
        })
    if extra_rows:
        rows.extend(extra_rows)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_artifact(path, *, qualified=True, feature_names=None,
                    coefficients=None, intercept=3.0, threshold=0.55):
    if feature_names is None:
        feature_names = ["elo_probability", "trend_gap", "park_factor", "starter_era_gap"]
    if coefficients is None:
        coefficients = [2.88, -0.024, -0.99, -0.018]
    # Match coefficient count to feature count
    if len(coefficients) != len(feature_names):
        coefficients = [0.0] * len(feature_names)
    artifact = build_artifact(
        sport="mlb",
        model_version="mlb-test-v1",
        market_models={
            "moneyline": {
                "feature_names": feature_names,
                "coefficients": coefficients,
                "intercept": intercept,
                "confidence_threshold": threshold,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": qualified},
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")


class _FakeESPN:
    """Returns one MLB game for 2026-07-17."""
    def __init__(self, events=None, home="Home Team", away="Away Team",
                 event_id="future-1", game_date="2026-07-17",
                 event_time="2026-07-17T23:00:00Z"):
        self.events = events or [{
            "id": event_id,
            "date": event_time,
            "competitions": [{
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": away}},
                    {"homeAway": "home", "team": {"displayName": home}},
                ]
            }],
        }]
        self._game_date = game_date

    def scoreboard(self, league: str, game_date: str) -> dict:
        assert league == "MLB"
        assert game_date == self._game_date
        return {"events": self.events}


# ── Core pipeline tests ────────────────────────────────────────────────────

def test_full_mlb_forward_slate_produces_home_call(tmp_path):
    """Build a full MLB slate with production-like features and verify call."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=True)

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert scheduled == 1
    assert skipped == []
    assert len(candidates) == 1
    c = candidates[0]
    assert c.selection == "home"
    assert c.call is True
    assert c.action == "QUALIFIED_SHADOW_CALL"
    assert c.reason == "CALL_LEARNED_CONFIDENCE"
    assert c.model_version == "mlb-test-v1"
    assert c.model_qualified is True
    assert len(c.model_artifact_hash) == 64
    assert len(c.feature_snapshot_hash) == 64


def test_probability_is_between_zero_and_one(tmp_path):
    """Model probability must be strictly inside (0, 1)."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    c = candidates[0]
    assert 0 < c.home_probability < 1
    assert 0 < c.model_probability < 1


def test_feature_basis_includes_all_required_features(tmp_path):
    """Every feature in the artifact's feature_names must be in the basis."""
    features = ["elo_probability", "trend_gap", "park_factor"]
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, feature_names=features)

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    basis = candidates[0].feature_basis
    for name in features:
        assert name in basis, f"Feature '{name}' missing from basis"
    assert basis["history_games"] == 60
    assert basis["home_history_games"] > 0
    assert basis["away_history_games"] > 0


# ── Edge case tests ────────────────────────────────────────────────────────

def test_already_started_event_is_skipped(tmp_path):
    """Events where start <= observed_at must be skipped."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(event_time="2026-07-17T11:00:00Z"),  # before observed
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert scheduled == 1
    assert candidates == []
    assert len(skipped) == 1
    assert "event_started" in skipped[0]["reason"]


def test_insufficient_history_raises(tmp_path):
    """Fewer than 50 historical games must raise ValueError."""
    _write_history(tmp_path, game_count=30)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)

    with pytest.raises(ValueError, match="requires 50"):
        build_learned_moneyline_slate(
            sport="mlb",
            game_date="2026-07-17",
            store=FeatureStore(tmp_path),
            client=_FakeESPN(),
            artifact_path=artifact_path,
            observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
        )


def test_insufficient_team_history_skips_event(tmp_path):
    """Teams with fewer than 10 games each are skipped."""
    # Build history where Home Team has 60 games but Rare Team has only 5.
    # Home Team appears in every game; Rare Team only in the first 5.
    path = tmp_path / "processed/mlb/games.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(60):
        away = "Rare Team" if i < 5 else "Other Team"
        rows.append({
            "event_id": f"history-{i}",
            "event_start_utc": f"2026-05-{i % 28 + 1:02d}T12:00:00Z",
            "league": "MLB",
            "away_team": away,
            "home_team": "Home Team",
            "away_score": 2,
            "home_score": 5,
        })
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(away="Rare Team"),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert scheduled == 1
    assert candidates == []
    assert len(skipped) == 1
    assert "insufficient_team_history" in skipped[0]["reason"]


def test_below_threshold_produces_no_call(tmp_path):
    """A low-confidence prediction should still produce a candidate with call=False."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    # Intercept=0 + all-zero coefs → 50/50 probability. Threshold 0.95 ensures
    # neither side clears, producing a no-call.
    _write_artifact(artifact_path, threshold=0.95, intercept=0.0,
                    coefficients=[0.0], feature_names=["elo_probability"])

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    c = candidates[0]
    assert c.call is False
    assert c.action == "NO_CALL_BELOW_LEARNED_CONFIDENCE"
    assert c.reason == "NO_CALL_BELOW_LEARNED_CONFIDENCE"


def test_unqualified_artifact_still_produces_candidates(tmp_path):
    """Unqualified models still produce shadow calls for research."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path, qualified=False)

    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert candidates[0].model_qualified is False
    assert candidates[0].call is True  # Still calls — research decision


# ── Multi-game slate ───────────────────────────────────────────────────────

def test_multi_game_slate_returns_all_candidates(tmp_path):
    """A slate with 3 games (same teams, different IDs) returns 3 candidates."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)

    events = [
        {"id": f"game-{i}", "date": f"2026-07-17T{20+i:02d}:00:00Z",
         "competitions": [{"competitors": [
             {"homeAway": "away", "team": {"displayName": "Away Team"}},
             {"homeAway": "home", "team": {"displayName": "Home Team"}},
         ]}]}
        for i in range(3)
    ]

    candidates, skipped, scheduled = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(events=events),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert scheduled == 3
    assert len(candidates) == 3
    assert skipped == []


# ── WNBA gate does NOT affect MLB ──────────────────────────────────────────

def test_wnba_availability_gate_does_not_activate_for_mlb(tmp_path):
    """The 5pp availability gate is WNBA-only; MLB must pass through unchanged."""
    _write_history(tmp_path)
    artifact_path = tmp_path / "artifact.json"
    _write_artifact(artifact_path)
    # No availability data exists — if the gate fired for MLB it would crash
    candidates, _, _ = build_learned_moneyline_slate(
        sport="mlb",
        game_date="2026-07-17",
        store=FeatureStore(tmp_path),
        client=_FakeESPN(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
    )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.selection == "home"  # Normal MLB call, no availability interference
    assert 0 < c.home_probability < 1
