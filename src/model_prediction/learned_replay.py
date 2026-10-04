"""Self-contained inference evidence for learned moneyline decisions.

This records the serving calculation, including post-model transforms. It
does not certify historical source availability or authorize a trade.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .domain import parse_utc
from .models.learned_market import LearnedMarketArtifact
from .wnba_availability_evaluation import adjust_home_probability

SCHEMA = "learned-moneyline-replay-v1"


def snapshot_hash(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def build_snapshot(
    artifact: LearnedMarketArtifact,
    features: dict[str, float],
    *,
    event_id: str,
    observed_at_utc: str,
    event_start_utc: str,
    base_home_probability: float,
    served_home_probability: float,
    adjustment: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "event_id": event_id,
        "observed_at_utc": observed_at_utc,
        "event_start_utc": event_start_utc,
        "model_version": artifact.version,
        "artifact_hash": artifact.hash,
        "artifact": artifact.raw,
        "features": {
            name: features[name] for name in artifact.raw["market_models"]["moneyline"]["feature_names"]
        },
        "base_home_probability": base_home_probability,
        "served_home_probability": served_home_probability,
        "adjustment": adjustment,
        "historical_source_observation_times_verified": False,
    }
    # Copy mutable producer dictionaries and reject NaN before persistence.
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    replay_snapshot(payload)
    return payload


def replay_snapshot(payload: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("invalid_learned_replay_snapshot")
    if parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"]):
        raise ValueError("replay_observation_not_pregame")
    for name in ("base_home_probability", "served_home_probability"):
        value = float(payload[name])
        if not math.isfinite(value) or not 0 < value < 1:
            raise ValueError("invalid_replay_probability")
    artifact = LearnedMarketArtifact(payload["artifact"])
    if payload.get("artifact_hash") != artifact.hash or payload.get("model_version") != artifact.version:
        raise ValueError("replay_artifact_identity_mismatch")
    features = payload["features"]
    if not features or not all(math.isfinite(float(v)) for v in features.values()):
        raise ValueError("invalid_replay_features")
    base = artifact.probability("moneyline", features)
    if abs(base - float(payload["base_home_probability"])) > 1e-12:
        raise ValueError("replay_base_probability_mismatch")
    adjustment = payload["adjustment"]
    if adjustment["method"] == "identity":
        if adjustment.get("applied") is not False:
            raise ValueError("invalid_identity_adjustment")
        served = base
    elif adjustment["method"] == "wnba_probit_v1":
        gap, sigma = float(adjustment["points_gap"]), float(adjustment["margin_sigma"])
        threshold = float(adjustment["minimum_delta"])
        if (
            artifact.sport != "wnba"
            or not math.isfinite(gap + sigma + threshold)
            or sigma <= 0
            or threshold < 0
        ):
            raise ValueError("invalid_probit_adjustment")
        proposed = adjust_home_probability(base, gap, sigma)
        applied = abs(proposed - base) >= threshold
        if adjustment.get("applied") is not applied:
            raise ValueError("replay_adjustment_policy_mismatch")
        served = proposed if applied else base
    else:
        raise ValueError("unsupported_replay_adjustment")
    if abs(served - float(payload["served_home_probability"])) > 1e-12:
        raise ValueError("replay_served_probability_mismatch")
    return served


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") == "mlb-v10-structural-replay-v1":
        from .mlb_v10_replay import validate_decision_snapshot as validate_v10

        return validate_v10(payload, row)
    if payload.get("schema") == "mlb-margin-replay-v1":
        from .mlb_margin_replay import validate_decision_snapshot as validate_margin

        return validate_margin(payload, row)
    if payload.get("schema") == "coded-moneyline-replay-v1":
        from .coded_market_replay import validate_decision_snapshot as validate_coded

        return validate_coded(payload, row)
    if payload.get("schema") == "international-elo-replay-v1":
        from .international_replay import validate_decision_snapshot as validate_international

        return validate_international(payload, row)
    if payload.get("schema") == "scalar-artifact-replay-v1":
        from .scalar_artifact_replay import validate_decision_snapshot as validate_scalar

        return validate_scalar(payload, row)
    if payload.get("schema") == "esports-elo-replay-v1":
        from .esports_replay import validate_decision_snapshot as validate_esports

        return validate_esports(payload, row)
    if payload.get("schema") == "cfb-joint-replay-v1":
        from .cfb_replay import validate_decision_snapshot as validate_cfb

        return validate_cfb(payload, row)
    served = replay_snapshot(payload)
    if (
        payload["artifact_hash"] != row.get("model_artifact_hash")
        or payload["model_version"] != row.get("model_version")
        or str(payload["artifact"]["sport"]).casefold() != str(row.get("league")).casefold()
        or payload["event_id"] != row.get("event_id")
        or parse_utc(payload["event_start_utc"]) != parse_utc(str(row.get("event_start_utc")))
        or row.get("market_type") != "moneyline"
        or row.get("selection") not in {"home", "away"}
    ):
        raise ValueError("learned replay snapshot does not match decision identity")
    probability = served if row["selection"] == "home" else 1 - served
    stored = float(row["model_probability"])
    if not math.isfinite(stored) or abs(probability - stored) > 0.000002:
        raise ValueError("learned replay snapshot does not match decision probability")
    return served
