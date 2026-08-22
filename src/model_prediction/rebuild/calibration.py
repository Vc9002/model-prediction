"""Probability calibration — Platt scaling, isotonic, temperature scaling.

All calibrators are fitted on data DISJOINT from base-model training.
Stored as separately hashed, mutually bound artifacts with base model hash.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .validation import brier_score, ece, log_loss


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
    parameters: ClassVar[dict[str, float]] = {}

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
        self,
        y_prob: Sequence[float],
        y_true: Sequence[int],
        base_model_hash: str = "",
    ) -> PlattCalibrator:
        """Fit Platt scaling on out-of-fold predictions (NOT training predictions)."""
        if len(y_prob) < 50:
            return PlattCalibrator(0.0, 1.0, "platt", base_model_hash)
        logits = np.array([[_logit(p)] for p in y_prob])
        lr = LogisticRegression(C=np.inf, solver="lbfgs")
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
        self,
        y_prob: Sequence[float],
        y_true: Sequence[int],
        base_model_hash: str = "",
        min_sample: int = 100,
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
        self,
        y_prob: Sequence[float],
        y_true: Sequence[int],
        base_model_hash: str = "",
        n_steps: int = 100,
    ) -> TemperatureScaling:
        if len(y_prob) < 50:
            return TemperatureScaling(1.0)
        temps = np.logspace(-1, 1, n_steps)
        best_t, best_loss = 1.0, float("inf")
        for t in temps:
            cal = np.array([_sigmoid(_logit(p) / t) for p in y_prob])
            loss = -np.mean(
                np.array(y_true) * np.log(np.clip(cal, 1e-12, 1))
                + (1 - np.array(y_true)) * np.log(np.clip(1 - cal, 1e-12, 1))
            )
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


def load_calibrator(method: str, parameters: dict[str, float]) -> Calibrator:
    """Reconstruct a fitted calibrator directly from a persisted artifact's
    `method`/`parameters` (e.g. `config/models/challengers/mlb-*-calibrator-v1.json`)
    -- no refit, no training data required. This is the real bridge live
    inference needs: a calibrator must be loaded once from its frozen
    artifact and reused for every prediction, never refit per request (that
    would silently make "the calibrator" a moving target across requests).

    isotonic is not supported here -- IsotonicCalibrator's real fitted
    curve is a full IsotonicRegression object, not a small JSON-friendly
    parameter set, so its persisted artifact (if one existed) could not be
    reconstructed this way; the frozen live combination uses temperature
    scaling, which this function does support."""
    if method == "platt":
        return PlattCalibrator(intercept=parameters["intercept"], slope=parameters["slope"])
    elif method == "temperature":
        return TemperatureScaling(temperature=parameters["temperature"])
    elif method == "identity":
        return IdentityCalibrator()
    raise ValueError(f"load_calibrator does not support method={method!r} (no reconstructible parameter set)")


# ── Chronological cross-fit calibration evaluation ───────────────────────────
#
# Real gap this closes (CLAUDE.md's next-phase Task 14): the four
# calibrators above were real and tested in isolation, but nothing ever
# evaluated them the way CLAUDE.md's own rule requires -- "fit calibrator
# on all OOF predictions, then report calibration performance on those
# same predictions" is calibration-set overfitting. These functions
# implement the real fix: chronological expanding-window cross-fitting,
# where a calibration method only ever sees labels strictly earlier than
# the block it's scored on.


def calibration_intercept_slope(y_prob: Sequence[float], y_true: Sequence[int]) -> tuple[float, float]:
    """Real calibration-intercept/slope diagnostic: fits
    logit(y) ~ intercept + slope * logit(p) via logistic regression and
    returns (intercept, slope) -- a perfectly calibrated set of
    predictions has intercept=0, slope=1. This is read-only diagnostics
    (mirrors PlattCalibrator.fit()'s own math), not a new calibrator to
    apply -- callers use it to describe how miscalibrated a block of
    (already-transformed-or-not) predictions is, not to transform them.
    Returns (0.0, 1.0) -- "looks perfectly calibrated, but this is a
    non-answer, not a real measurement" -- below 50 real observations,
    the same real sample-size floor PlattCalibrator.fit() itself uses."""
    if len(y_prob) < 50:
        return 0.0, 1.0
    logits = np.array([[_logit(p)] for p in y_prob])
    lr = LogisticRegression(C=np.inf, solver="lbfgs")
    lr.fit(logits, np.array(y_true))
    return float(lr.intercept_[0]), float(lr.coef_[0][0])


def chronological_calibration_blocks(n: int, n_blocks: int = 4) -> list[tuple[int, int]]:
    """Split n chronologically-ordered OOF rows into n_blocks contiguous
    index ranges (start, end) of roughly equal size (the last block
    absorbs any remainder). Real building block for expanding-window
    calibration cross-fitting -- block i is only ever used to calibrate
    predictions for a *later* block, never scored on itself."""
    if n_blocks < 2:
        raise ValueError("need at least 2 blocks (>=1 fit block, >=1 eval block)")
    block_size = n // n_blocks
    blocks: list[tuple[int, int]] = []
    start = 0
    for i in range(n_blocks):
        end = n if i == n_blocks - 1 else start + block_size
        blocks.append((start, end))
        start = end
    return blocks


@dataclass
class CrossFitCalibrationResult:
    """Real per-(model, calibration method) cross-fit result -- everything
    CLAUDE.md's Task 14 requires reported: n, log_loss, Brier, ECE,
    calibration intercept/slope, plus per-block detail so a real audit can
    verify no evaluation block's labels ever reached the calibrator fit on
    it."""

    method: str
    n_blocks: int
    per_block: list[dict[str, Any]] = field(default_factory=list)
    n_eval_total: int = 0
    log_loss: float | None = None
    brier: float | None = None
    ece: float | None = None
    calibration_intercept: float | None = None
    calibration_slope: float | None = None
    calibrated_probs: list[float] = field(default_factory=list)
    eval_labels: list[int] = field(default_factory=list)


def cross_fit_calibration_eval(
    probs: Sequence[float],
    labels: Sequence[int],
    method: str,
    n_blocks: int = 4,
) -> CrossFitCalibrationResult:
    """Real chronological expanding-window calibration cross-fit: for each
    evaluation block i (i=1..n_blocks-1), fits `method`'s calibrator on
    every row from block 0 through block i-1 only, then evaluates on block
    i. The calibration method never sees the labels it is scored on --
    structurally, not by convention (block i's labels are never passed to
    the fit call that produces the calibrator scored on block i).

    `probs`/`labels` must already be in real chronological order (the
    caller's own OOF construction order, matching every other real fold
    construction in this codebase)."""
    blocks = chronological_calibration_blocks(len(probs), n_blocks)
    all_calibrated: list[float] = []
    all_labels: list[int] = []
    per_block: list[dict[str, Any]] = []

    for i in range(1, len(blocks)):
        fit_start = blocks[0][0]
        fit_end = blocks[i - 1][1]
        eval_start, eval_end = blocks[i]
        fit_probs = list(probs[fit_start:fit_end])
        fit_labels = list(labels[fit_start:fit_end])
        eval_probs = list(probs[eval_start:eval_end])
        eval_labels = list(labels[eval_start:eval_end])
        if not eval_probs:
            continue

        cal = fit_calibrator(method, fit_probs, fit_labels)
        calibrated = [cal.transform(p) for p in eval_probs]

        all_calibrated.extend(calibrated)
        all_labels.extend(eval_labels)
        per_block.append(
            {
                "eval_block": i,
                "fit_n": len(fit_probs),
                "eval_n": len(eval_probs),
                "log_loss": log_loss(eval_labels, calibrated),
                "brier": brier_score(eval_labels, calibrated),
            }
        )

    if not all_labels:
        return CrossFitCalibrationResult(method=method, n_blocks=n_blocks, per_block=per_block)

    intercept, slope = calibration_intercept_slope(all_calibrated, all_labels)
    return CrossFitCalibrationResult(
        method=method,
        n_blocks=n_blocks,
        per_block=per_block,
        n_eval_total=len(all_labels),
        log_loss=log_loss(all_labels, all_calibrated),
        brier=brier_score(all_labels, all_calibrated),
        ece=ece(all_labels, all_calibrated),
        calibration_intercept=intercept,
        calibration_slope=slope,
        calibrated_probs=all_calibrated,
        eval_labels=all_labels,
    )
