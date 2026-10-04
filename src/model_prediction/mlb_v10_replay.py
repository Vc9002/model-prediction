"""Full artifact and feature replay for the existing MLB structural v10 shadow path."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .domain import parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "mlb-v10-structural-replay-v1"
VERSION = "mlb-structural-v10-frozen"


def empirical_probabilities(
    market_line: float, delta: float, alpha: float, beta: float, errors: np.ndarray
) -> tuple[float, float, float]:
    """Compute pregame outcome probabilities from the frozen empirical OOF error distribution."""
    mu_star = alpha + (beta * delta)
    r_star = mu_star + errors

    is_integer = float(market_line).is_integer()
    if is_integer:
        p_push = float(np.mean((r_star >= -0.5) & (r_star < 0.5)))
        p_over = float(np.mean(r_star >= 0.5))
        p_under = float(np.mean(r_star < -0.5))
    else:
        p_push = 0.0
        p_over = float(np.mean(r_star > 0.0))
        p_under = float(np.mean(r_star < 0.0))

    # Normalize and clip
    total_p = p_over + p_under + p_push
    if total_p > 0:
        p_over = round(float(np.clip(p_over / total_p, 0.001, 0.999)), 4)
        p_under = round(float(np.clip(p_under / total_p, 0.001, 0.999)), 4)
        p_push = round(float(np.clip(p_push / total_p, 0.0, 0.999)), 4) if is_integer else 0.0
        s = p_over + p_under + p_push
        p_over = round(p_over / s, 4)
        p_under = round(p_under / s, 4)
        p_push = round(1.0 - p_over - p_under, 4) if is_integer else 0.0
    else:
        p_over, p_under, p_push = 0.50, 0.50, 0.0

    return p_over, p_under, p_push


def artifact_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def code_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in ("models/mlb_structural_v10.py", "features/mlb_v10_features.py", "mlb_v10_replay.py")
    }


def replay_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    from .features.mlb_v10_features import MLBv10FeatureVector
    from .models.mlb_structural_v10 import MLBStructuralV10Model

    if payload["schema"] != SCHEMA or payload["code_sha256"] != code_hashes():
        raise ValueError("mlb_v10_replay_code_mismatch")
    artifact = payload["artifact"]
    if artifact["model_version"] != VERSION or payload["artifact_hash"] != artifact_hash(artifact):
        raise ValueError("mlb_v10_replay_artifact_mismatch")
    features = MLBv10FeatureVector(**payload["features"])
    model = MLBStructuralV10Model()
    weights = artifact["model_weights"]
    for side in ("away", "home"):
        estimator = getattr(model, "model_" + side)
        estimator.intercept_ = weights[side + "_intercept"]
        estimator.coef_ = np.asarray(weights[side + "_coefficients"], dtype=float)
        if not math.isfinite(estimator.intercept_) or not np.isfinite(estimator.coef_).all():
            raise ValueError("mlb_v10_replay_invalid_weights")
    model.fitted = True
    pred = model.predict(features)
    line = float(payload["line"])
    if not math.isfinite(line) or line * 2 != round(line * 2):
        raise ValueError("mlb_v10_replay_invalid_line")
    delta = round(pred.projected_total_runs - line, 2)
    calibration = artifact["market_calibration"]
    alpha, beta = float(calibration["m4_1_alpha"]), float(calibration["m4_1_beta"])
    errors = np.asarray(artifact["empirical_oof_error_distribution"]["oof_errors_sample"], dtype=float)
    if (
        errors.ndim != 1
        or not len(errors)
        or not np.isfinite(errors).all()
        or not math.isfinite(alpha + beta)
    ):
        raise ValueError("mlb_v10_replay_invalid_error_distribution")
    over, under, push = empirical_probabilities(line, delta, alpha, beta, errors)
    return {
        "structural_prediction": asdict(pred),
        "delta": delta,
        "probabilities": {"over": over, "under": under, "push": push},
    }


def build_snapshot(
    artifact: dict[str, Any], features: Any, line: float, observed_at_utc: str
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "sport": "MLB",
        "market_type": "total",
        "model_version": VERSION,
        "artifact": artifact,
        "artifact_hash": artifact_hash(artifact),
        "code_sha256": code_hashes(),
        "event_id": features.event_id,
        "event_start_utc": features.game_start_utc,
        "observed_at_utc": observed_at_utc,
        "features": features.to_dict(),
        "line": line,
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact frozen structural weights, full feature vector, market line and empirical residual mapping; captured through separate v10 shadow runner.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload.update(replay_snapshot(payload))
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("mlb_v10_replay_snapshot_hash_mismatch")
    features = payload["features"]
    if (
        str(row.get("league")).upper() != "MLB"
        or row.get("market_type") != "total"
        or row.get("model_version") != VERSION
        or payload["model_version"] != VERSION
        or row.get("model_artifact_hash") != payload["artifact_hash"]
        or str(row.get("event_id")) != payload["event_id"]
        or features["event_id"] != payload["event_id"]
        or row.get("home_team") != features["home_team"]
        or row.get("away_team") != features["away_team"]
        or row.get("selection") not in {"over", "under"}
        or row.get("line") in (None, "")
        or float(row["line"]) != payload["line"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(features["game_start_utc"]) != parse_utc(payload["event_start_utc"])
        or not parse_utc(features["as_of_utc"])
        <= parse_utc(payload["observed_at_utc"])
        < parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("mlb_v10_replay_decision_identity_mismatch")
    values = replay_snapshot(payload)
    if any(values[k] != payload[k] for k in values):
        raise ValueError("mlb_v10_replay_values_mismatch")
    p, stored = values["probabilities"][row["selection"]], float(row["model_probability"])
    if not math.isfinite(stored) or abs(stored - p) > 2e-6:
        raise ValueError("mlb_v10_replay_probability_mismatch")
    return p
