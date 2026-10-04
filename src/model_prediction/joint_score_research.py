"""Fitted NCAAF joint-score research model with empirical residual uncertainty.

One discrete score distribution supplies all three markets. This is a new
research artifact, not an implementation of the production incumbent.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from .research_generation import digest

SCHEMA = "ncaaf-joint-score-research-v1"


def fit(
    x_train: np.ndarray,
    scores_train: np.ndarray,
    x_select: np.ndarray,
    scores_select: np.ndarray,
    x_calibrate: np.ndarray,
    scores_calibrate: np.ndarray,
    feature_names: list[str],
) -> dict[str, Any]:
    """Fit means on train, choose penalty on select, retain calibration errors.

    No test array is accepted. Home and away residuals stay paired to preserve
    their observed covariance rather than impose arbitrary dispersion.
    """
    for x, y in ((x_train, scores_train), (x_select, scores_select), (x_calibrate, scores_calibrate)):
        if x.ndim != 2 or x.shape[1] != len(feature_names) or y.shape != (len(x), 2) or len(x) < 20:
            raise ValueError("invalid_joint_training_shape")
        if (
            not np.isfinite(x).all()
            or not np.isfinite(y).all()
            or (y < 0).any()
            or not np.equal(y, np.floor(y)).all()
        ):
            raise ValueError("invalid_joint_training_values")
    scaler = StandardScaler().fit(x_train)
    variants = []
    for alpha in (1.0, 10.0, 100.0):
        model = Ridge(alpha=alpha).fit(scaler.transform(x_train), scores_train)
        loss = float(np.abs(model.predict(scaler.transform(x_select)) - scores_select).mean())
        variants.append((loss, alpha, model))
    _, alpha, model = min(variants, key=lambda item: (item[0], item[1]))
    residuals = scores_calibrate - model.predict(scaler.transform(x_calibrate))
    artifact = {
        "schema": SCHEMA,
        "model_ids": {
            market: f"ncaaf-{market}-joint-research-20260911-v2"
            for market in ("moneyline", "spread", "total")
        },
        "feature_names": feature_names,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficients": model.coef_.tolist(),
        "intercepts": model.intercept_.tolist(),
        "residual_pairs": residuals.tolist(),
        "residual_covariance": np.cov(residuals, rowvar=False).tolist(),
        "selected_alpha": alpha,
        "selection": [{"alpha": a, "mean_team_score_mae": loss} for loss, a, _ in variants],
        "discrete_score_policy": "round_to_nearest_integer_then_clip_at_zero",
        "moneyline_policy": "condition_on_unequal_final_scores_no_overtime_score_fabrication",
        "historical_source_observation_times_verified": False,
        "promote": False,
    }
    artifact["artifact_hash"] = digest(artifact)
    return artifact


def validate(artifact: dict[str, Any]) -> None:
    if artifact.get("schema") != SCHEMA or artifact.get("artifact_hash") != digest(
        {k: v for k, v in artifact.items() if k != "artifact_hash"}
    ):
        raise ValueError("joint_artifact_hash_or_schema_mismatch")
    n = len(artifact["feature_names"])
    arrays = {
        name: np.asarray(artifact[name], dtype=float)
        for name in ("scaler_mean", "scaler_scale", "coefficients", "intercepts", "residual_pairs")
    }
    if (
        arrays["scaler_mean"].shape != (n,)
        or arrays["scaler_scale"].shape != (n,)
        or arrays["coefficients"].shape != (2, n)
        or arrays["intercepts"].shape != (2,)
        or arrays["residual_pairs"].ndim != 2
        or arrays["residual_pairs"].shape[1] != 2
        or len(arrays["residual_pairs"]) < 20
    ):
        raise ValueError("invalid_joint_artifact_shape")
    if any(not np.isfinite(a).all() for a in arrays.values()) or (arrays["scaler_scale"] <= 0).any():
        raise ValueError("invalid_joint_artifact_values")


def score_samples(artifact: dict[str, Any], features: np.ndarray) -> np.ndarray:
    validate(artifact)
    x = np.asarray(features, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(artifact["feature_names"]) or not np.isfinite(x).all():
        raise ValueError("invalid_joint_features")
    standardized = (x - np.asarray(artifact["scaler_mean"])) / np.asarray(artifact["scaler_scale"])
    means = standardized @ np.asarray(artifact["coefficients"]).T + np.asarray(artifact["intercepts"])
    return np.maximum(0, np.rint(means[:, None, :] + np.asarray(artifact["residual_pairs"])[None, :, :]))


def probabilities(
    artifact: dict[str, Any], features: np.ndarray, market: str, *, line: float | None = None
) -> np.ndarray:
    samples = score_samples(artifact, features)
    margin = samples[:, :, 0] - samples[:, :, 1]
    valid = margin != 0  # Final college-football games have an eventual winner.
    denominator = valid.sum(1)
    if (denominator == 0).any():
        raise ValueError("no_unequal_final_score_support")
    if market == "moneyline":
        if line is not None:
            raise ValueError("unexpected_moneyline_line")
        home = ((margin > 0) & valid).sum(1) / denominator
        away = ((margin < 0) & valid).sum(1) / denominator
        return np.stack((away, home), axis=1)
    if (
        market not in {"spread", "total"}
        or isinstance(line, bool)
        or line is None
        or not np.isfinite(line)
        or not float(line * 2).is_integer()
    ):
        raise ValueError("exact_half_or_integer_contract_line_required")
    value = margin if market == "spread" else samples.sum(2)
    threshold = -line if market == "spread" else line
    return np.stack(
        [
            ((value < threshold) & valid).sum(1) / denominator,
            ((value == threshold) & valid).sum(1) / denominator,
            ((value > threshold) & valid).sum(1) / denominator,
        ],
        axis=1,
    )
