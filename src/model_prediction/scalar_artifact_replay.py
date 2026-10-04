"""Replay exact NRFI and WNBA spread/total artifact calculations."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash
from .production_registry import compute_artifact_hash

SCHEMA = "scalar-artifact-replay-v1"


def engine_hash(method: str) -> str:
    name = {
        "first_inning_logistic": "mlb_first_inning.py",
        "margin_normal": "basketball.py",
        "total_normal": "basketball.py",
    }.get(method)
    if name is None:
        raise ValueError("unsupported_scalar_replay_method")
    return hashlib.sha256((Path(__file__).parent / "models" / name).read_bytes()).hexdigest()


def replay_snapshot(payload: dict[str, Any]) -> dict[str, float]:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("scalar_snapshot_hash_mismatch")
    artifact = payload["artifact"]
    method = artifact["method"]
    if (
        compute_artifact_hash(artifact) != artifact.get("artifact_hash")
        or artifact["artifact_hash"] != payload["artifact_hash"]
    ):
        raise ValueError("scalar_replay_artifact_hash_mismatch")
    if payload["engine_source_sha256"] != engine_hash(method):
        raise ValueError("scalar_replay_engine_hash_mismatch")
    if method == "first_inning_logistic":
        features = payload["features"]
        logit = float(artifact["intercept"])
        for name, coef, mean, scale in zip(
            artifact["feature_names"],
            artifact["coef"],
            artifact["scaler_mean"],
            artifact["scaler_scale"],
            strict=True,
        ):
            value = float(features[name])
            if not all(math.isfinite(float(x)) for x in (value, coef, mean, scale)) or scale <= 0:
                raise ValueError("invalid_scalar_replay_feature")
            logit += coef * ((value - mean) / scale)
        p = 1 / (1 + math.exp(-max(-20.0, min(20.0, logit))))
        return {"nrfi": round(p, 4), "yrfi": round(1 - p, 4)}
    from .models.basketball import _normal_cdf

    inputs = payload["inference_inputs"]
    mean, sd, line = (float(inputs[key]) for key in ("mean", "sd", "line"))
    if not math.isfinite(mean + sd + line) or sd <= 0:
        raise ValueError("invalid_scalar_replay_distribution")
    if sd != float(artifact["margin_sd" if method == "margin_normal" else "total_sd"]):
        raise ValueError("scalar_replay_artifact_parameter_mismatch")
    below = _normal_cdf(line, mean, sd)
    values = (
        {"away": below, "home": 1 - below}
        if method == "margin_normal"
        else {"under": below, "over": 1 - below}
    )
    return {name: round(value, 6) for name, value in values.items()}


def build_snapshot(
    artifact: dict[str, Any],
    features: dict[str, Any],
    inference_inputs: dict[str, Any],
    *,
    event_id: str,
    event_start_utc: str,
    observed_at_utc: str,
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "artifact": artifact,
        "artifact_hash": artifact["artifact_hash"],
        "model_version": artifact["model_version"],
        "engine_source_sha256": engine_hash(artifact["method"]),
        "features": features,
        "inference_inputs": inference_inputs,
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "observed_at_utc": observed_at_utc,
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact final classifier/distribution inputs and artifact. Upstream historical feature construction not certified.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    replay_snapshot(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    values = replay_snapshot(payload)
    artifact = payload["artifact"]
    method = artifact["method"]
    market = {"first_inning_logistic": "nrfi", "margin_normal": "spread", "total_normal": "total"}[method]
    if (
        row.get("model_version") != artifact["model_version"]
        or payload["model_version"] != artifact["model_version"]
        or row.get("model_artifact_hash") != artifact["artifact_hash"]
        or str(row.get("league")).lower() != artifact["sport"]
        or row.get("market_type") != market
        or row.get("selection") not in values
        or str(row.get("event_id")) != payload["event_id"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("scalar_replay_decision_identity_mismatch")
    line = (
        0.5
        if market == "nrfi"
        else payload["inference_inputs"]["line"]
        * (-1 if market == "spread" and row["selection"] == "home" else 1)
    )
    if float(row["line"]) != line:
        raise ValueError("scalar_replay_line_mismatch")
    stored = float(row["model_probability"])
    if not math.isfinite(stored) or abs(stored - values[row["selection"]]) > 2e-6:
        raise ValueError("scalar_replay_probability_mismatch")
    return values[row["selection"]]
