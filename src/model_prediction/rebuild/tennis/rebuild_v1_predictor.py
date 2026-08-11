"""Rebuild-native predictor for `tennis-surface-elo-rebuild-v1`.

Loads the frozen challenger artifact + calibrator from
`config/models/challengers/` and exposes a single `predict(row, **overrides)`
interface that takes a `WalkForwardRow` and returns a structured prediction
dict.  No refit — the artifact and calibrator are loaded once as immutable
JSON blobs and reused for every call.

This is raw Surface Elo (no LR on top): the model's probability is the
`elo_probability_player_one` field on the row itself, optionally calibrated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model_prediction.rebuild.calibration import Calibrator, load_calibrator
from model_prediction.rebuild.tennis.elo import WalkForwardRow

CHALLENGER_DIR = Path("config/models/challengers")
ARTIFACT_PATH = CHALLENGER_DIR / "tennis-surface-elo-rebuild-v1.json"
CALIBRATOR_PATH = CHALLENGER_DIR / "tennis-surface-elo-rebuild-v1-calibrator.json"


@dataclass
class TennisSurfaceEloRebuildV1Prediction:
    """Structured prediction from the rebuild-native Surface Elo model."""
    winner_prob: float
    loser_prob: float
    predicted_player_one_id: str
    predicted_player_one_name: str
    model_name: str
    method: str
    feature_names: list[str]
    coefficients: list[float]
    intercept: float
    calibrator_method: str
    calibration_applied: bool
    caveats: list[str] = field(default_factory=list)


class TennisSurfaceEloRebuildV1Predictor:
    """Load-once predictor for `tennis-surface-elo-rebuild-v1`.

    Usage::

        from model_prediction.rebuild.tennis.elo import WalkForwardRow
        from model_prediction.rebuild.tennis.rebuild_v1_predictor import (
            TennisSurfaceEloRebuildV1Predictor,
        )

        predictor = TennisSurfaceEloRebuildV1Predictor.from_default_artifact()
        row: WalkForwardRow = ...
        pred = predictor.predict(row)

    The predictor validates the artifact's identity at load time (model
    version, sport, feature names) and refuses to predict if the artifact
    doesn't match expectations — fail-closed.
    """

    def __init__(
        self,
        artifact: dict[str, Any],
        calibrator: Calibrator,
        *,
        artifact_path: Path | None = None,
        calibrator_path: Path | None = None,
    ) -> None:
        self._artifact = artifact
        self._calibrator = calibrator
        self._artifact_path = artifact_path
        self._calibrator_path = calibrator_path
        self._validate_artifact()

    # ── factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_default_artifact(cls) -> TennisSurfaceEloRebuildV1Predictor:
        """Load from the default challenger artifact paths.

        Reads ``config/models/challengers/tennis-surface-elo-rebuild-v1.json``
        and the paired calibrator artifact.
        """
        if not ARTIFACT_PATH.exists():
            raise FileNotFoundError(
                f"Artifact not found at {ARTIFACT_PATH.resolve()}. "
                "Run scripts/train_tennis_rebuild_v1.py first."
            )
        if not CALIBRATOR_PATH.exists():
            raise FileNotFoundError(
                f"Calibrator not found at {CALIBRATOR_PATH.resolve()}. "
                "Run scripts/train_tennis_rebuild_v1.py first."
            )
        artifact = json.loads(ARTIFACT_PATH.read_text())
        cal_raw = json.loads(CALIBRATOR_PATH.read_text())
        calibrator = load_calibrator(cal_raw["method"], cal_raw.get("parameters", {}))
        return cls(artifact, calibrator, artifact_path=ARTIFACT_PATH, calibrator_path=CALIBRATOR_PATH)

    @classmethod
    def from_paths(cls, artifact_path: Path, calibrator_path: Path) -> TennisSurfaceEloRebuildV1Predictor:
        """Load from explicit artifact paths (for testing)."""
        artifact = json.loads(artifact_path.read_text())
        cal_raw = json.loads(calibrator_path.read_text())
        calibrator = load_calibrator(cal_raw["method"], cal_raw.get("parameters", {}))
        return cls(artifact, calibrator, artifact_path=artifact_path, calibrator_path=calibrator_path)

    # ── validation ───────────────────────────────────────────────────────

    def _validate_artifact(self) -> None:
        """Fail-closed: refuse to operate on a misidentified artifact."""
        expected_version = "tennis-surface-elo-rebuild-v1"
        actual_version = self._artifact.get("model_version")
        if actual_version != expected_version:
            raise ValueError(
                f"Artifact model_version is {actual_version!r}, expected {expected_version!r}"
            )
        if self._artifact.get("sport") != "tennis":
            raise ValueError(
                f"Artifact sport is {self._artifact.get('sport')!r}, expected 'tennis'"
            )
        moneyline = self._artifact.get("market_models", {}).get("moneyline", {})
        feature_names = moneyline.get("feature_names", [])
        if "elo_probability_player_one" not in feature_names:
            raise ValueError(
                f"Artifact missing 'elo_probability_player_one' in moneyline feature_names: {feature_names}"
            )

    # ── predict ──────────────────────────────────────────────────────────

    def predict(
        self,
        row: WalkForwardRow,
        *,
        force_edge: str | None = None,
    ) -> TennisSurfaceEloRebuildV1Prediction:
        """Produce a structured prediction from a WalkForwardRow.

        Args:
            row: A populated ``WalkForwardRow`` (the Elo snapshot before the
                match outcome, with ``elo_probability_player_one`` already
                computed by the Surface Elo formula).
            force_edge: If ``"winner"`` or ``"loser"``, override the predicted
                winner to that side (for scenario analysis).  Default
                ``None`` uses the higher-probability side.

        Returns:
            ``TennisSurfaceEloRebuildV1Prediction`` with winner/loser probs
            and metadata.
        """
        raw_prob = float(row.elo_probability_player_one)
        cal_prob = self._calibrator.transform(raw_prob)

        winner_prob = cal_prob
        loser_prob = 1.0 - cal_prob

        # Determine predicted winner
        if force_edge == "winner":
            predicted_id = row.player_one_id
            predicted_name = row.player_one_name
        elif force_edge == "loser":
            predicted_id = row.player_two_id
            predicted_name = row.player_two_name
        elif winner_prob >= loser_prob:
            predicted_id = row.player_one_id
            predicted_name = row.player_one_name
        else:
            predicted_id = row.player_two_id
            predicted_name = row.player_two_name

        moneyline = self._artifact.get("market_models", {}).get("moneyline", {})

        caveats: list[str] = []
        if not self._artifact.get("provenance", {}).get("production_allowed", False):
            caveats.append("production_not_allowed: research only, per artifact provenance")
        if self._artifact.get("provenance", {}).get("pit_status") == "RETROSPECTIVE_RESEARCH":
            caveats.append("retrospective_research: capture-time-only provenance, not prospective PIT evidence")

        return TennisSurfaceEloRebuildV1Prediction(
            winner_prob=winner_prob,
            loser_prob=loser_prob,
            predicted_player_one_id=predicted_id,
            predicted_player_one_name=predicted_name,
            model_name=self._artifact.get("model_version", "unknown"),
            method=self._artifact.get("method", "unknown"),
            feature_names=list(moneyline.get("feature_names", [])),
            coefficients=list(moneyline.get("coefficients", [])),
            intercept=float(moneyline.get("intercept", 0.0)),
            calibrator_method=self._calibrator.method,
            calibration_applied=self._calibrator.method != "identity" and abs(cal_prob - raw_prob) > 1e-9,
            caveats=caveats,
        )

    # ── accessors ────────────────────────────────────────────────────────

    @property
    def artifact(self) -> dict[str, Any]:
        return dict(self._artifact)

    @property
    def calibrator(self) -> Calibrator:
        return self._calibrator

    @property
    def model_version(self) -> str:
        return str(self._artifact.get("model_version", "unknown"))

    @property
    def production_allowed(self) -> bool:
        return bool(self._artifact.get("provenance", {}).get("production_allowed", False))
