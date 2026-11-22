"""Replayable decision snapshot for ``nba-anchor-v10`` Flat rows.

The ledger replays every stored ``model_input_snapshot_json`` before it accepts a row
(``learned_replay.validate_decision_snapshot`` dispatches on ``schema``). This schema stores the
anchored-pricing inputs and recomputes the served probability from them:

    mean = anchor_mean + max(0, lambda) * (model_mean - anchor_mean)
    P(low side) = Phi((contract_line - mean) / sigma)     low side = away (spread) / under (total)

so a stored row can always be re-derived from its own components. Upstream anchor captures are
referenced by observed time, not certified here.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Any

from .domain import parse_utc

SCHEMA = "nba-anchor-v10-replay-v1"
_LOW_SIDE = {"spread": "away", "total": "under"}
_REPLAY_KEYS = ("anchor_mean", "model_mean", "lambda", "sigma", "contract_line")


def snapshot_hash(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def replay_probability(payload: dict[str, Any]) -> float:
    """Served probability of ``payload['side']`` recomputed from the stored components."""
    values = {key: float(payload[key]) for key in _REPLAY_KEYS}
    if not all(math.isfinite(v) for v in values.values()) or values["sigma"] <= 0:
        raise ValueError("nba_anchor_v10_invalid_components")
    mean = values["anchor_mean"] + max(0.0, values["lambda"]) * (values["model_mean"] - values["anchor_mean"])
    p_low = NormalDist().cdf((values["contract_line"] - mean) / values["sigma"])
    return p_low if payload["side"] == _LOW_SIDE[payload["market"]] else 1.0 - p_low


def build_snapshot(
    components: dict[str, Any], *, event_id: str, event_start_utc: str, observed_at_utc: str
) -> dict[str, Any]:
    payload = {
        **components,
        "schema": SCHEMA,
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "observed_at_utc": observed_at_utc,
        "features": {key: components[key] for key in _REPLAY_KEYS},
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    if abs(replay_probability(payload) - float(payload["probability"])) > 2e-6:
        raise ValueError("nba_anchor_v10_replay_probability_mismatch")
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("nba_anchor_v10_snapshot_hash_mismatch")
    if (
        payload["event_id"] != str(row.get("event_id"))
        or payload["model_version"] != row.get("model_version")
        or payload["market"] != row.get("market_type")
        or payload["side"] != str(row.get("selection", "")).lower()
        or str(row.get("league", "")).casefold() != "nba"
        or parse_utc(payload["event_start_utc"]) != parse_utc(str(row.get("event_start_utc")))
    ):
        raise ValueError("nba_anchor_v10_identity_mismatch")
    probability = replay_probability(payload)
    stored = row.get("model_probability")
    if stored not in (None, "") and abs(probability - float(stored)) > 2e-6:
        raise ValueError("nba_anchor_v10_probability_mismatch")
    return probability
