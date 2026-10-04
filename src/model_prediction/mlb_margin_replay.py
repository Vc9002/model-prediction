"""Measured Edge spread replay from exact features, formula, artifact, and seed inputs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .domain import MarketType, parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "mlb-margin-replay-v1"


def code_hash() -> str:
    return hashlib.sha256((Path(__file__).parent / "models/mlb.py").read_bytes()).hexdigest()


def replay_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    from .models.mlb import (
        FormulaSpec,
        MLBGameFeatures,
        PitcherForm,
        TeamForm,
        canonical_mlb_artifact_hash,
        derive_market_distribution,
        estimate_runs,
        simulate_game,
    )

    if payload["schema"] != SCHEMA or payload["code_sha256"] != code_hash():
        raise ValueError("mlb_margin_replay_code_mismatch")
    artifact = payload["artifact"]
    if (
        artifact.get("artifact_hash") != canonical_mlb_artifact_hash(artifact)
        or artifact["model_version"] != "measured-edge-margin-v3"
    ):
        raise ValueError("mlb_margin_replay_artifact_mismatch")
    features = dict(payload["features"])
    for side in ("away", "home"):
        form = dict(features[side + "_form"])
        for key in ("runs_scored", "runs_allowed"):
            form[key] = tuple(form[key])
        features[side + "_form"] = TeamForm(**form)
        features[side + "_starter"] = PitcherForm(**features[side + "_starter"])
    features["source_snapshot_ids"] = tuple(features["source_snapshot_ids"])
    f = MLBGameFeatures(**features)
    spec = FormulaSpec(**payload["formula_spec"])
    line = float(payload["away_spread_line"])
    if not math.isfinite(line) or line * 2 != round(line * 2):
        raise ValueError("mlb_margin_replay_invalid_line")
    estimate = estimate_runs(f, spec)
    simulation = simulate_game(f, estimate, spec)
    distribution = derive_market_distribution(simulation, MarketType.SPREAD, line)
    raw = {
        "away": distribution.first_win_probability,
        "home": distribution.second_win_probability,
        "push": distribution.push_probability,
    }
    calibrated = {
        side: round(float(artifact["scale"]) * raw[side] + float(artifact["offset"]), 6)
        for side in ("away", "home")
    }
    if any(not 0 < v < 1 for v in calibrated.values()):
        raise ValueError("mlb_margin_replay_invalid_calibration")
    # The incumbent calibrates each side independently; do not normalize them
    # or mistake the calibrated pair for a coherent three-way distribution.
    return {"raw_probabilities": raw, "served_probabilities": calibrated, "run_estimate": asdict(estimate)}


def build_snapshot(
    features: Any,
    spec: Any,
    artifact: dict[str, Any],
    away_line: float,
    distribution: Any,
    observed_at_utc: str,
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "sport": "MLB",
        "market_type": "spread",
        "model_version": artifact["model_version"],
        "artifact_hash": artifact["artifact_hash"],
        "artifact": artifact,
        "code_sha256": code_hash(),
        "features": asdict(features),
        "formula_spec": asdict(spec),
        "away_spread_line": away_line,
        "event_id": features.event_id,
        "event_start_utc": features.event_start_utc,
        "observed_at_utc": observed_at_utc,
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact features, formula and deterministic Monte Carlo seed inputs; independently calibrated sides are not a joint distribution.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    values = replay_snapshot(payload)
    if values["raw_probabilities"] != {
        "away": distribution.first_win_probability,
        "home": distribution.second_win_probability,
        "push": distribution.push_probability,
    }:
        raise ValueError("mlb_margin_replay_serving_mismatch")
    payload.update(values)
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("mlb_margin_replay_snapshot_hash_mismatch")
    side = row.get("selection")
    if (
        str(row.get("league")).upper() != "MLB"
        or row.get("market_type") != "spread"
        or side not in {"away", "home"}
        or row.get("model_version") != payload["model_version"]
        or row.get("model_artifact_hash") != payload["artifact_hash"]
        or payload["artifact_hash"] != payload["artifact"]["artifact_hash"]
        or payload["model_version"] != payload["artifact"]["model_version"]
        or str(row.get("event_id")) != payload["event_id"]
        or payload["features"]["event_id"] != payload["event_id"]
        or row.get("away_team") != payload["features"]["away_team"]
        or row.get("home_team") != payload["features"]["home_team"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["features"]["event_start_utc"]) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
        or parse_utc(payload["features"]["decision_timestamp_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("mlb_margin_replay_decision_identity_mismatch")
    line = payload["away_spread_line"] if side == "away" else -payload["away_spread_line"]
    if row.get("line") in (None, "") or float(row["line"]) != line:
        raise ValueError("mlb_margin_replay_line_mismatch")
    values = replay_snapshot(payload)
    if any(values[k] != payload[k] for k in values):
        raise ValueError("mlb_margin_replay_values_mismatch")
    p, stored = values["served_probabilities"][side], float(row["model_probability"])
    if not math.isfinite(stored) or abs(p - stored) > 2e-6:
        raise ValueError("mlb_margin_replay_probability_mismatch")
    return p
