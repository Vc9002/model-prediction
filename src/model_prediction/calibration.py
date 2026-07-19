from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CalibrationMetadata:
    calibration_method: str
    calibration_version: str
    base_model_version: str
    training_window_start: str | None
    training_window_end: str | None
    sample_size: int
    artifact_hash: str


class Calibrator(Protocol):
    metadata: CalibrationMetadata

    def transform(self, probability: float) -> float: ...


class IdentityCalibrator:
    def __init__(self, base_model_version: str, version: str = "identity-v1") -> None:
        raw = {
            "method": "identity",
            "version": version,
            "base_model_version": base_model_version,
        }
        artifact_hash = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        self.metadata = CalibrationMetadata(
            "identity", version, base_model_version, None, None, 0, artifact_hash
        )

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("probability must be in [0, 1]")
        return probability


class FixedPlattCalibrator:
    """Immutable research calibrator loaded from a versioned JSON artifact."""

    def __init__(self, artifact_path: str | Path) -> None:
        raw = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        canonical = {key: value for key, value in raw.items() if key != "artifact_hash"}
        actual_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if actual_hash != raw["artifact_hash"]:
            raise ValueError("calibration artifact hash mismatch")
        if raw["calibration_method"] != "platt":
            raise ValueError("fixed calibrator requires platt method")
        self.intercept = float(raw["intercept"])
        self.slope = float(raw["slope"])
        self.metadata = CalibrationMetadata(
            "platt",
            raw["calibration_version"],
            raw["base_model_version"],
            raw["training_window_start"],
            raw["training_window_end"],
            int(raw["sample_size"]),
            raw["artifact_hash"],
        )

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError("Platt calibration probability must be between 0 and 1")
        clipped = min(1 - 1e-12, max(1e-12, probability))
        logit = math.log(clipped / (1 - clipped))
        return 1 / (1 + math.exp(-(self.intercept + self.slope * logit)))


class TrainablePlattCalibrator:
    """Platt scaling fit on a rolling holdout of settled outcomes."""

    def __init__(self, intercept: float, slope: float, metadata: CalibrationMetadata) -> None:
        self.intercept = intercept
        self.slope = slope
        self.metadata = metadata

    @classmethod
    def fit(
        cls,
        probabilities: Sequence[float],
        outcomes: Sequence[int],
        base_model_version: str,
        version: str = "platt-rolling-v1",
        minimum_sample: int = 100,
    ) -> "TrainablePlattCalibrator | IdentityCalibrator":
        if len(probabilities) != len(outcomes):
            raise ValueError("probabilities and outcomes must have equal length")
        if len(probabilities) < minimum_sample:
            return IdentityCalibrator(base_model_version, f"{version}-identity-fallback")
        clipped = [min(1 - 1e-9, max(1e-9, p)) for p in probabilities]
        intercept, slope = _logistic_calibration(clipped, list(outcomes))
        if intercept is None or slope is None:
            return IdentityCalibrator(base_model_version, f"{version}-identity-fallback")
        raw = {
            "method": "platt",
            "version": version,
            "base_model_version": base_model_version,
            "intercept": intercept,
            "slope": slope,
            "sample_size": len(probabilities),
        }
        artifact_hash = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        metadata = CalibrationMetadata(
            "platt", version, base_model_version, None, None, len(probabilities), artifact_hash
        )
        return cls(intercept, slope, metadata)

    def transform(self, probability: float) -> float:
        clipped = min(1 - 1e-12, max(1e-12, probability))
        logit = math.log(clipped / (1 - clipped))
        return 1 / (1 + math.exp(-(self.intercept + self.slope * logit)))


class IsotonicCalibrator:
    """Isotonic regression via pool-adjacent-violators, with linear interpolation."""

    def __init__(
        self, thresholds: list[float], values: list[float], metadata: CalibrationMetadata
    ) -> None:
        self.thresholds = thresholds
        self.values = values
        self.metadata = metadata

    @classmethod
    def fit(
        cls,
        probabilities: Sequence[float],
        outcomes: Sequence[int],
        base_model_version: str,
        version: str = "isotonic-rolling-v1",
        minimum_sample: int = 200,
    ) -> "IsotonicCalibrator | IdentityCalibrator":
        if len(probabilities) != len(outcomes):
            raise ValueError("probabilities and outcomes must have equal length")
        if len(probabilities) < minimum_sample:
            return IdentityCalibrator(base_model_version, f"{version}-identity-fallback")
        pairs = sorted(zip(probabilities, outcomes, strict=True))
        # Pool adjacent violators.
        blocks: list[list[float]] = [[float(y), 1.0, p] for p, y in pairs]  # [sum, weight, x]
        merged: list[list[float]] = []
        for block in blocks:
            merged.append(block)
            while len(merged) > 1 and merged[-2][0] / merged[-2][1] > merged[-1][0] / merged[-1][1]:
                last = merged.pop()
                merged[-1][0] += last[0]
                merged[-1][1] += last[1]
                merged[-1][2] = last[2]  # right edge of the pooled block
        thresholds = [block[2] for block in merged]
        values = [block[0] / block[1] for block in merged]
        raw = {
            "method": "isotonic",
            "version": version,
            "base_model_version": base_model_version,
            "sample_size": len(probabilities),
            "blocks": len(merged),
        }
        artifact_hash = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        metadata = CalibrationMetadata(
            "isotonic", version, base_model_version, None, None, len(probabilities), artifact_hash
        )
        return cls(thresholds, values, metadata)

    def transform(self, probability: float) -> float:
        if not self.thresholds:
            return probability
        if probability <= self.thresholds[0]:
            return self.values[0]
        if probability >= self.thresholds[-1]:
            return self.values[-1]
        for index in range(1, len(self.thresholds)):
            if probability <= self.thresholds[index]:
                left, right = self.thresholds[index - 1], self.thresholds[index]
                weight = (probability - left) / (right - left) if right > left else 0.0
                return self.values[index - 1] + weight * (self.values[index] - self.values[index - 1])
        return self.values[-1]


def calibration_metrics(
    probabilities: Sequence[float], outcomes: Sequence[int], minimum_sample: int = 30
) -> dict[str, object]:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    if len(probabilities) < minimum_sample:
        return {"status": "insufficient_sample", "sample_size": len(probabilities)}
    clipped = [min(1 - 1e-12, max(1e-12, p)) for p in probabilities]
    if any(outcome not in {0, 1} for outcome in outcomes):
        raise ValueError("outcomes must be binary")
    brier = sum((p - y) ** 2 for p, y in zip(clipped, outcomes, strict=True)) / len(clipped)
    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes, strict=True)
    ) / len(clipped)
    buckets = []
    ece = 0.0
    for lower_index in range(10):
        lower, upper = lower_index / 10, (lower_index + 1) / 10
        members = [
            (p, y)
            for p, y in zip(clipped, outcomes, strict=True)
            if lower <= p < upper or (upper == 1 and p == 1)
        ]
        if not members:
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        mean_y = sum(y for _, y in members) / len(members)
        ece += len(members) / len(clipped) * abs(mean_p - mean_y)
        buckets.append(
            {"lower": lower, "upper": upper, "count": len(members), "mean_p": mean_p, "hit_rate": mean_y}
        )
    intercept, slope = _logistic_calibration(clipped, outcomes)
    return {
        "status": "ok",
        "sample_size": len(clipped),
        "brier_score": brier,
        "log_loss": log_loss,
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "expected_calibration_error": ece,
        "reliability_buckets": buckets,
    }


def _logistic_calibration(
    probabilities: Sequence[float], outcomes: Sequence[int]
) -> tuple[float | None, float | None]:
    x = [math.log(p / (1 - p)) for p in probabilities]
    a, b = 0.0, 1.0
    for _ in range(25):
        fitted = [1 / (1 + math.exp(-(a + b * value))) for value in x]
        waa = sum(p * (1 - p) for p in fitted)
        wab = sum(p * (1 - p) * value for p, value in zip(fitted, x, strict=True))
        wbb = sum(p * (1 - p) * value * value for p, value in zip(fitted, x, strict=True))
        ga = sum(y - p for y, p in zip(outcomes, fitted, strict=True))
        gb = sum((y - p) * value for y, p, value in zip(outcomes, fitted, x, strict=True))
        determinant = waa * wbb - wab * wab
        if abs(determinant) < 1e-12:
            return None, None
        da = (ga * wbb - gb * wab) / determinant
        db = (gb * waa - ga * wab) / determinant
        a += da
        b += db
        if abs(da) + abs(db) < 1e-8:
            break
    return a, b
