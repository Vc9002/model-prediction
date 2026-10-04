"""Exact NCAAF serving replay, including the simulator's advancing RNG state."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash
from .models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine

SCHEMA = "cfb-joint-replay-v1"
VERSIONS = {"moneyline": "college-football-v1", "spread": "cfb-spread-v1", "total": "cfb-total-v1"}


def engine_hash() -> str:
    return hashlib.sha256(
        Path(__file__).with_name("models").joinpath("cfb_distribution.py").read_bytes()
    ).hexdigest()


def capture_inputs(
    engine: CFBJointDistributionEngine,
    *,
    mu_home: float,
    mu_away: float,
    spread_home_line: float,
    total_line: float,
) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {
                "engine_source_sha256": engine_hash(),
                "parameters": {
                    "distribution_type": engine.distribution_type.value,
                    "margin_sd": engine.margin_sd,
                    "total_sd": engine.total_sd,
                    "score_correlation": engine.score_correlation,
                    "nb_dispersion": engine.nb_dispersion,
                    "n_simulations": engine.n_simulations,
                },
                "rng_state_before": engine.rng.bit_generator.state,
                "mu_home": mu_home,
                "mu_away": mu_away,
                "spread_home_line": spread_home_line,
                "total_line": total_line,
            },
            allow_nan=False,
        )
    )


def replay_inputs(inputs: dict[str, Any], market: str) -> dict[str, float]:
    if market not in VERSIONS or inputs.get("engine_source_sha256") != engine_hash():
        raise ValueError("cfb_replay_engine_identity_mismatch")
    parameters = dict(inputs["parameters"])
    parameters["distribution_type"] = CFBDistributionType(parameters["distribution_type"])
    if parameters["n_simulations"] <= 0 or parameters["n_simulations"] > 1_000_000:
        raise ValueError("cfb_replay_invalid_simulation_count")
    for key in ("mu_home", "mu_away", "spread_home_line", "total_line"):
        if not math.isfinite(float(inputs[key])):
            raise ValueError("cfb_replay_invalid_input")
    if min(inputs["mu_home"], inputs["mu_away"]) <= 0:
        raise ValueError("cfb_replay_invalid_score_mean")
    engine = CFBJointDistributionEngine(**parameters)
    engine.rng.bit_generator.state = inputs["rng_state_before"]
    result = engine.compute_market_probabilities(
        mu_home=inputs["mu_home"],
        mu_away=inputs["mu_away"],
        spread_home_line=inputs["spread_home_line"],
        total_line=inputs["total_line"],
    )
    values = (
        {"home": result.p_home_win, "away": result.p_away_win}
        if market == "moneyline"
        else {"home": result.p_home_cover, "away": result.p_away_cover, "push": result.p_push_spread}
        if market == "spread"
        else {"over": result.p_over, "under": result.p_under, "push": result.p_push_total}
    )
    return {key: round(value, 6) for key, value in values.items()}


def build_snapshot(contract: dict[str, Any], *, model_hash: str, observed_at_utc: str) -> dict[str, Any]:
    inputs = contract["inference_inputs"]
    payload = {
        "schema": SCHEMA,
        "event_id": contract["event_id"],
        "event_start_utc": contract["event_start_utc"],
        "observed_at_utc": observed_at_utc,
        "sport": "NCAAF",
        "market_type": contract["market_type"],
        "model_version": contract["model_version"],
        "artifact_hash": model_hash,
        "features": contract["feature_basis"],
        "inference_inputs": inputs,
        "probabilities": replay_inputs(inputs, contract["market_type"]),
        "historical_source_observation_times_verified": False,
        "replay_scope": "Full precision score means, exact engine parameters and original RNG state; upstream feature construction not certified.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    validate_decision_snapshot(payload, {**contract, "league": "NCAAF", "model_artifact_hash": model_hash})
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("cfb_replay_snapshot_hash_mismatch")
    market = payload["market_type"]
    if (
        payload["model_version"] != VERSIONS.get(market)
        or row.get("model_version") != payload["model_version"]
        or row.get("model_artifact_hash") != payload["artifact_hash"]
        or str(row.get("league")).upper() != "NCAAF"
        or payload.get("sport") != "NCAAF"
        or row.get("market_type") != market
        or str(row.get("event_id")) != payload["event_id"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("cfb_replay_decision_identity_mismatch")
    probabilities = replay_inputs(payload["inference_inputs"], market)
    if probabilities != payload["probabilities"]:
        raise ValueError("cfb_replay_probability_mismatch")
    selection = row["selection"]
    if selection not in ({"home", "away"} if market != "total" else {"over", "under"}):
        raise ValueError("cfb_replay_invalid_selection")
    if market != "moneyline":
        expected_line = (
            payload["inference_inputs"]["total_line"]
            if market == "total"
            else (payload["inference_inputs"]["spread_home_line"] * (1 if selection == "home" else -1))
        )
        if float(row["line"]) != expected_line:
            raise ValueError("cfb_replay_line_mismatch")
    stored = float(row["model_probability"])
    if not math.isfinite(stored) or abs(stored - probabilities[selection]) > 2e-6:
        raise ValueError("cfb_replay_selected_probability_mismatch")
    return probabilities[selection]
