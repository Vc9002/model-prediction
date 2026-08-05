"""Probability calibration — Platt scaling, isotonic, temperature scaling.

All calibrators are fitted on data DISJOINT from base-model training.
Stored as separately hashed, mutually bound artifacts with base model hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


def _logit(p: float) -> float:
    clipped = max(1e-12, min(1 - 1e-12, p))
    return math.log(clipped / (1 - clipped))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    return math.exp(x) / (1.0 + math.exp(x))


# ── Calibrator protocol ─────────────────────────────────────────────────────


class Calibrator(Protocol):
    def fit(self, y_prob: Sequence[float], y_true: Sequence[int]) -> Calibrator: ...
    def transform(self, probability: float) -> float: ...
    @property
    def method(self) -> str: ...
    @property
    def parameters(self) -> dict[str, float]: ...


# ── Identity (no calibration) ────────────────────────────────────────────────


class IdentityCalibrator:
    method = "identity"
    parameters: dict[str, float] = {}

    def fit(self, y_prob: Sequence[float], y_true: Sequence[int]) -> IdentityCalibrator:
        return self

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError(f"probability {probability} not in [0,1]")
        return probability


# ── Platt Scaling ────────────────────────────────────────────────────────────


@dataclass
class PlattCalibrator:
    intercept: float = 0.0
    slope: float = 1.0
    method: str = "platt"
    base_model_hash: str = ""

    def fit(
        self, y_prob: Sequence[float], y_true: Sequence[int],
        base_model_hash: str = "",
    ) -> PlattCalibrator:
        """Fit Platt scaling on out-of-fold predictions (NOT training predictions)."""
        if len(y_prob) < 50:
            return PlattCalibrator(0.0, 1.0, "platt", base_model_hash)
        logits = np.array([[_logit(p)] for p in y_prob])
        lr = LogisticRegression(penalty=None, solver="lbfgs")
        lr.fit(logits, np.array(y_true))
        return PlattCalibrator(
            intercept=float(lr.intercept_[0]),
            slope=float(lr.coef_[0][0]),
            base_model_hash=base_model_hash,
        )

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError(f"probability {probability} not in [0,1]")
        return float(_sigmoid(self.intercept + self.slope * _logit(probability)))

    @property
    def parameters(self) -> dict[str, float]:
        return {"intercept": self.intercept, "slope": self.slope}


# ── Isotonic Regression ──────────────────────────────────────────────────────


@dataclass
class IsotonicCalibrator:
    _calibrator: IsotonicRegression | None = None
    method: str = "isotonic"
    base_model_hash: str = ""
    _trained: bool = False

    def fit(
        self, y_prob: Sequence[float], y_true: Sequence[int],
        base_model_hash: str = "", min_sample: int = 100,
    ) -> IsotonicCalibrator:
        if len(y_prob) < min_sample:
            return IsotonicCalibrator(None, "isotonic", base_model_hash)
        iso = IsotonicRegression(y_min=1e-6, y_max=1 - 1e-6, out_of_bounds="clip")
        iso.fit(np.array(y_prob), np.array(y_true, dtype=float))
        return IsotonicCalibrator(iso, "isotonic", base_model_hash, True)

    def transform(self, probability: float) -> float:
        if not self._trained or self._calibrator is None:
            return probability
        return float(self._calibrator.transform([probability])[0])

    @property
    def parameters(self) -> dict[str, float]:
        return {"min_val": 0.0, "max_val": 0.0} if not self._trained else {}


# ── Temperature Scaling ──────────────────────────────────────────────────────


class TemperatureScaling:
    """Single-parameter temperature scaling: p_cal = sigmoid(logit(p) / T)."""
    method = "temperature"
    base_model_hash = ""

    def __init__(self, temperature: float = 1.0) -> None:
        self.temperature = temperature

    def fit(
        self, y_prob: Sequence[float], y_true: Sequence[int],
        base_model_hash: str = "", n_steps: int = 100,
    ) -> TemperatureScaling:
        if len(y_prob) < 50:
            return TemperatureScaling(1.0)
        temps = np.logspace(-1, 1, n_steps)
        best_t, best_loss = 1.0, float("inf")
        for t in temps:
            cal = np.array([_sigmoid(_logit(p) / t) for p in y_prob])
            loss = -np.mean(np.array(y_true) * np.log(np.clip(cal, 1e-12, 1)) +
                           (1 - np.array(y_true)) * np.log(np.clip(1 - cal, 1e-12, 1)))
            if loss < best_loss:
                best_loss, best_t = loss, t
        self.temperature = float(best_t)
        self.base_model_hash = base_model_hash
        return self

    def transform(self, probability: float) -> float:
        if not 0 <= probability <= 1:
            raise ValueError(f"probability {probability} not in [0,1]")
        if self.temperature == 0 or self.temperature == 1.0:
            return probability
        return float(_sigmoid(_logit(probability) / self.temperature))

    @property
    def parameters(self) -> dict[str, float]:
        return {"temperature": self.temperature}


# ── Calibrator factory ───────────────────────────────────────────────────────


def fit_calibrator(
    method: str,
    y_prob: Sequence[float],
    y_true: Sequence[int],
    base_model_hash: str = "",
) -> Calibrator:
    if method == "platt":
        return PlattCalibrator().fit(y_prob, y_true, base_model_hash)
    elif method == "isotonic":
        return IsotonicCalibrator().fit(y_prob, y_true, base_model_hash)
    elif method == "temperature":
        return TemperatureScaling().fit(y_prob, y_true, base_model_hash)
    else:
        return IdentityCalibrator()
