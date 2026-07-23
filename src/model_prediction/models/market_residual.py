"""Learned market-residual layer.

Logistic regression on [logit(model_p), logit(market_p)] trained on a rolling
90-day window of settled picks, producing a calibrated probability. This is
the ONLY place market prices may combine with model output — the independent
game models never see a market price.

Falls back to identity when the training sample is too small (< 100 settled
binary outcomes), reporting that status explicitly. Artifacts persist as JSON
with a SHA-256 hash so a decision row can pin the exact residual version.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from ..domain import parse_utc, utc_now


RESIDUAL_VERSION = "market-residual-logistic-v1"
MINIMUM_SAMPLE = 100
ROLLING_WINDOW_DAYS = 90


def _logit(probability: float) -> float:
    clipped = min(1 - 1e-9, max(1e-9, probability))
    return math.log(clipped / (1 - clipped))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass(frozen=True)
class ResidualTrainingRow:
    settled_at_utc: str
    model_probability: float
    market_probability: float
    outcome: int  # 1 = selection won, 0 = lost


class MarketResidualModel:
    """coefficients: [intercept, w_model_logit, w_market_logit]."""

    def __init__(self, coefficients: tuple[float, float, float] | None, sample_size: int) -> None:
        self.coefficients = coefficients
        self.sample_size = sample_size

    @property
    def is_identity(self) -> bool:
        return self.coefficients is None

    @property
    def version(self) -> str:
        return RESIDUAL_VERSION if not self.is_identity else f"{RESIDUAL_VERSION}-identity-fallback"

    def calibrated_probability(self, model_probability: float, market_probability: float) -> float:
        if self.coefficients is None:
            return model_probability
        intercept, w_model, w_market = self.coefficients
        score = intercept + w_model * _logit(model_probability) + w_market * _logit(market_probability)
        return _sigmoid(score)

    # ------------------------------------------------------------- training

    @classmethod
    def train(cls, rows: Sequence[ResidualTrainingRow], now=None) -> "MarketResidualModel":
        current = now or utc_now()
        cutoff = current - timedelta(days=ROLLING_WINDOW_DAYS)
        usable = [
            row
            for row in rows
            if row.outcome in (0, 1) and parse_utc(row.settled_at_utc) >= cutoff
        ]
        if len(usable) < MINIMUM_SAMPLE:
            return cls(None, len(usable))
        xs = [(1.0, _logit(row.model_probability), _logit(row.market_probability)) for row in usable]
        ys = [row.outcome for row in usable]
        weights = [0.0, 1.0, 0.0]
        # Newton-Raphson IRLS, 3 parameters.
        for _ in range(50):
            gradient = [0.0, 0.0, 0.0]
            hessian = [[0.0] * 3 for _ in range(3)]
            for x, y in zip(xs, ys, strict=True):
                p = _sigmoid(sum(w * v for w, v in zip(weights, x, strict=True)))
                error = y - p
                w_p = p * (1 - p)
                for i in range(3):
                    gradient[i] += error * x[i]
                    for j in range(3):
                        hessian[i][j] += w_p * x[i] * x[j]
            # Ridge for stability.
            for i in range(3):
                hessian[i][i] += 1e-4
            step = _solve_3x3(hessian, gradient)
            if step is None:
                return cls(None, len(usable))
            weights = [w + s for w, s in zip(weights, step, strict=True)]
            if sum(abs(s) for s in step) < 1e-9:
                break
        return cls((weights[0], weights[1], weights[2]), len(usable))

    # -------------------------------------------------------------- storage

    def to_artifact(self) -> dict[str, object]:
        artifact: dict[str, object] = {
            "model_version": self.version,
            "coefficients": list(self.coefficients) if self.coefficients else None,
            "sample_size": self.sample_size,
            "rolling_window_days": ROLLING_WINDOW_DAYS,
            "minimum_sample": MINIMUM_SAMPLE,
        }
        artifact["artifact_hash"] = hashlib.sha256(
            json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return artifact

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_artifact(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "MarketResidualModel":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        canonical = {key: value for key, value in raw.items() if key != "artifact_hash"}
        expected = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if expected != raw.get("artifact_hash"):
            raise ValueError("market residual artifact hash mismatch")
        coefficients = raw.get("coefficients")
        return cls(tuple(coefficients) if coefficients else None, int(raw.get("sample_size", 0)))


def _solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting."""
    a = [[*row[:], v] for row, v in zip(matrix, vector, strict=True)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col] / a[col][col]
            for k in range(col, 4):
                a[row][k] -= factor * a[col][k]
    return [a[i][3] / a[i][i] for i in range(3)]
