"""Research-only regularized logit corrections around an exact incumbent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from .models.learned_market import LearnedMarketArtifact, artifact_hash


def checked_inputs(base: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    base, x = np.asarray(base, dtype=float), np.asarray(x, dtype=float)
    if (
        base.ndim != 1
        or x.ndim != 2
        or len(base) != len(x)
        or not np.isfinite(x).all()
        or not np.isfinite(base).all()
        or not ((base > 0) & (base < 1)).all()
    ):
        raise ValueError("invalid_residual_inputs")
    return base, x


def fit_offset(base: np.ndarray, x: np.ndarray, y: np.ndarray, penalty: float) -> dict[str, Any]:
    base, x = checked_inputs(base, x)
    y = np.asarray(y, dtype=float)
    if y.shape != base.shape or not np.isin(y, [0, 1]).all() or not np.isfinite(penalty) or penalty <= 0:
        raise ValueError("invalid_residual_labels_or_penalty")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.0)
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    offset = logit(base)

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = offset + design @ theta
        loss = float(np.mean(np.logaddexp(0, z) - y * z) + penalty * np.dot(theta, theta) / 2)
        gradient = design.T @ (expit(z) - y) / len(y) + penalty * theta
        return loss, gradient

    result = minimize(objective, np.zeros(design.shape[1]), jac=True, method="L-BFGS-B")
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("residual_optimization_failed")
    return {
        "kind": "offset_logit",
        "penalty": penalty,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "coefficients": result.x.tolist(),
    }


def predict_offset(parameters: dict[str, Any], base: np.ndarray, x: np.ndarray) -> np.ndarray:
    base, x = checked_inputs(base, x)
    if parameters["kind"] == "identity":
        return base.copy()
    if parameters["kind"] != "offset_logit":
        raise ValueError("unknown_residual_kind")
    mean, scale, coefficients = (
        np.asarray(parameters[key], dtype=float) for key in ("mean", "scale", "coefficients")
    )
    if (
        mean.shape != (x.shape[1],)
        or scale.shape != mean.shape
        or coefficients.shape != (x.shape[1] + 1,)
        or not np.isfinite(np.r_[mean, scale, coefficients]).all()
        or not (scale > 0).all()
    ):
        raise ValueError("invalid_residual_parameters")
    z = logit(base) + coefficients[0] + ((x - mean) / scale) @ coefficients[1:]
    return np.clip(expit(z), 1e-6, 1 - 1e-6)


def date_split(dates: list[str]) -> dict[str, np.ndarray]:
    days = sorted(set(dates))
    if len(days) < 15:
        raise ValueError("insufficient_residual_dates")
    first, second = int(len(days) * 0.6), int(len(days) * 0.8)
    groups = {"train": set(days[:first]), "select": set(days[first:second]), "test": set(days[second:])}
    return {
        name: np.array([i for i, day in enumerate(dates) if day in values], dtype=int)
        for name, values in groups.items()
    }


def predict_artifact(path: Path, features: np.ndarray) -> np.ndarray:
    payload = json.loads(path.read_text())
    if (
        payload.get("artifact_hash") != artifact_hash(payload)
        or payload.get("method") != "incumbent_offset_logit_v1"
    ):
        raise ValueError("invalid_residual_artifact")
    incumbent = LearnedMarketArtifact(payload["incumbent_artifact"])
    names = incumbent.raw["market_models"]["moneyline"]["feature_names"]
    if payload["feature_names"][: len(names)] != [f"incumbent:{n}" for n in names]:
        raise ValueError("incumbent_feature_order_mismatch")
    features = np.asarray(features, dtype=float)
    if (
        features.ndim != 2
        or features.shape[1] != len(payload["feature_names"])
        or not np.isfinite(features).all()
    ):
        raise ValueError("invalid_residual_features")
    base = np.array(
        [
            incumbent.probability("moneyline", dict(zip(names, row[: len(names)], strict=True)))
            for row in features
        ]
    )
    return predict_offset(payload["parameters"], base, features)
