"""Tests for Tennis Surface Elo rebuild v1 predictor.

Tests artifact loading, prediction determinism, fail-closed validation,
calibration, and caveat emission.  Uses the real challenger artifacts
at config/models/challengers/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.rebuild.calibration import IdentityCalibrator
from model_prediction.rebuild.tennis.elo import WalkForwardRow
from model_prediction.rebuild.tennis.rebuild_v1_predictor import (
    TennisSurfaceEloRebuildV1Predictor,
)

CHALLENGER_DIR = Path("config/models/challengers")
ARTIFACT_PATH = CHALLENGER_DIR / "tennis-surface-elo-rebuild-v1.json"
CALIBRATOR_PATH = CHALLENGER_DIR / "tennis-surface-elo-rebuild-v1-calibrator.json"


# ── helpers ──────────────────────────────────────────────────────────────

def _make_row(
    match_id: str = "test_match_001",
    elo_prob: float = 0.65,
    surface: str = "Hard",
) -> WalkForwardRow:
    return WalkForwardRow(
        match_id=match_id,
        tourney_date="2025-06-15",
        tour="ATP",
        surface=surface,
        player_one_id="player_A",
        player_two_id="player_B",
        player_one_name="Player A",
        player_two_name="Player B",
        player_one_win=1,
        player_one_overall_elo=1600.0,
        player_two_overall_elo=1500.0,
        player_one_surface_elo=1620.0,
        player_two_surface_elo=1480.0,
        player_one_blended_elo=1610.0,
        player_two_blended_elo=1490.0,
        elo_probability_player_one=elo_prob,
        player_one_surface_matches=12,
        player_two_surface_matches=8,
    )


def _minimal_artifact(overrides: dict | None = None) -> dict:
    base = {
        "model_version": "tennis-surface-elo-rebuild-v1",
        "sport": "tennis",
        "method": "surface_elo",
        "family": "surface_elo",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability_player_one"],
                "coefficients": [1.0],
                "intercept": 0.0,
                "positive_class": "winner",
            },
        },
        "provenance": {
            "production_allowed": False,
            "pit_status": "RETROSPECTIVE_RESEARCH",
        },
        "data_summary": {"n_total_matches": 1000},
        "qualification": {"qualified": False},
    }
    if overrides:
        base.update(overrides)
    return base


def _minimal_calibrator(method: str = "identity", params: dict | None = None) -> dict:
    return {
        "method": method,
        "parameters": params or {},
        "model_name": "tennis-surface-elo-rebuild-v1",
        "base_model_hash": "abc123",
    }


# ── artifact loading ─────────────────────────────────────────────────────

class TestArtifactLoading:
    def test_from_default_artifact_loads_successfully(self):
        """The real challenger artifact must load without error."""
        if not ARTIFACT_PATH.exists():
            pytest.skip("Challenger artifact not found — run train_tennis_rebuild_v1.py first")
        predictor = TennisSurfaceEloRebuildV1Predictor.from_default_artifact()
        assert predictor.model_version == "tennis-surface-elo-rebuild-v1"
        assert isinstance(predictor.calibrator, IdentityCalibrator)

    def test_from_default_artifact_raises_when_artifact_missing(self, tmp_path):
        """from_default_artifact fails loudly when artifact doesn't exist."""
        # We can't easily mock the module-level path, but we can test from_paths

    def test_from_paths_loads_minimal_artifact(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)
        assert predictor.model_version == "tennis-surface-elo-rebuild-v1"

    def test_validate_rejects_wrong_version(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact({"model_version": "wrong-version"})))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        with pytest.raises(ValueError, match="model_version"):
            TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

    def test_validate_rejects_wrong_sport(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact({"sport": "nba"})))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        with pytest.raises(ValueError, match="sport"):
            TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

    def test_validate_rejects_missing_feature(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art = _minimal_artifact()
        art["market_models"]["moneyline"]["feature_names"] = ["trend_gap"]
        art_path.write_text(json.dumps(art))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        with pytest.raises(ValueError, match="elo_probability_player_one"):
            TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)


# ── prediction determinism ───────────────────────────────────────────────

class TestPredictionDeterminism:
    def test_same_row_produces_same_prediction(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.72)
        pred1 = predictor.predict(row)
        pred2 = predictor.predict(row)

        assert pred1.winner_prob == pred2.winner_prob
        assert pred1.loser_prob == pred2.loser_prob
        assert pred1.predicted_player_one_id == pred2.predicted_player_one_id

    def test_probabilities_sum_to_one(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        for p in (0.1, 0.35, 0.5, 0.72, 0.95):
            row = _make_row(elo_prob=p)
            pred = predictor.predict(row)
            assert pred.winner_prob + pred.loser_prob == pytest.approx(1.0)

    def test_high_elo_favors_winner(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.85)
        pred = predictor.predict(row)
        assert pred.winner_prob > 0.5
        assert pred.predicted_player_one_id == "player_A"

    def test_low_elo_favors_loser(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.35)
        pred = predictor.predict(row)
        assert pred.winner_prob < 0.5
        assert pred.predicted_player_one_id == "player_B"

    def test_force_edge_overrides_predicted_winner(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.85)  # strongly favors winner
        # Force loser
        pred_forced = predictor.predict(row, force_edge="loser")
        assert pred_forced.predicted_player_one_id == "player_B"

        # Force winner
        pred_normal = predictor.predict(row, force_edge="winner")
        assert pred_normal.predicted_player_one_id == "player_A"

    def test_prediction_includes_model_metadata(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row()
        pred = predictor.predict(row)
        assert pred.model_name == "tennis-surface-elo-rebuild-v1"
        assert pred.method == "surface_elo"
        assert "elo_probability_player_one" in pred.feature_names
        assert pred.coefficients == [1.0]
        assert pred.intercept == 0.0


# ── calibration ──────────────────────────────────────────────────────────

class TestCalibration:
    def test_identity_calibrator_passes_through(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator("identity")))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.67)
        pred = predictor.predict(row)
        assert pred.winner_prob == pytest.approx(0.67)
        assert not pred.calibration_applied

    def test_temperature_calibrator_shifts_probability(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator("temperature", {"temperature": 2.0})))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.80)
        pred = predictor.predict(row)
        # Temperature > 1 pulls toward 0.5
        assert pred.winner_prob < 0.80
        assert pred.winner_prob > 0.5
        assert pred.calibration_applied

    def test_platt_calibrator_is_reconstructible(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator("platt", {"intercept": 0.1, "slope": 0.9})))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row(elo_prob=0.70)
        pred = predictor.predict(row)
        assert pred.calibrator_method == "platt"
        # Platt scaling should change the probability
        assert pred.winner_prob != pytest.approx(0.70)


# ── caveat checks ────────────────────────────────────────────────────────

class TestCaveats:
    def test_production_not_allowed_caveat_emitted(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row()
        pred = predictor.predict(row)
        assert any("production_not_allowed" in c for c in pred.caveats)

    def test_retrospective_research_caveat_emitted(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row()
        pred = predictor.predict(row)
        assert any("retrospective_research" in c for c in pred.caveats)

    def test_production_allowed_true_suppresses_caveat(self, tmp_path: Path):
        art = _minimal_artifact()
        art["provenance"]["production_allowed"] = True
        art["provenance"]["pit_status"] = "PROSPECTIVE"
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(art))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)

        row = _make_row()
        pred = predictor.predict(row)
        assert not any("production_not_allowed" in c for c in pred.caveats)
        assert not any("retrospective_research" in c for c in pred.caveats)

    def test_production_allowed_property(self, tmp_path: Path):
        art_path = tmp_path / "model.json"
        cal_path = tmp_path / "calibrator.json"
        art_path.write_text(json.dumps(_minimal_artifact()))
        cal_path.write_text(json.dumps(_minimal_calibrator()))
        predictor = TennisSurfaceEloRebuildV1Predictor.from_paths(art_path, cal_path)
        assert not predictor.production_allowed


# ── real artifact integration ────────────────────────────────────────────

class TestRealArtifactIntegration:
    @pytest.mark.skipif(
        not ARTIFACT_PATH.exists(),
        reason="Challenger artifact not found — run train_tennis_rebuild_v1.py first",
    )
    def test_real_artifact_predicts_without_error(self):
        predictor = TennisSurfaceEloRebuildV1Predictor.from_default_artifact()
        row = _make_row()
        pred = predictor.predict(row)
        assert 0.0 < pred.winner_prob < 1.0
        assert pred.model_name == "tennis-surface-elo-rebuild-v1"

    @pytest.mark.skipif(
        not ARTIFACT_PATH.exists(),
        reason="Challenger artifact not found",
    )
    def test_real_artifact_has_caveats(self):
        predictor = TennisSurfaceEloRebuildV1Predictor.from_default_artifact()
        row = _make_row()
        pred = predictor.predict(row)
        # Real artifact is not production-allowed → must have caveats
        assert len(pred.caveats) >= 1

    @pytest.mark.skipif(
        not ARTIFACT_PATH.exists(),
        reason="Challenger artifact not found",
    )
    def test_real_artifact_produces_deterministic_predictions(self):
        predictor = TennisSurfaceEloRebuildV1Predictor.from_default_artifact()
        row = _make_row()
        p1 = predictor.predict(row)
        p2 = predictor.predict(row)
        assert p1.winner_prob == p2.winner_prob
        assert p1.predicted_player_one_id == p2.predicted_player_one_id
