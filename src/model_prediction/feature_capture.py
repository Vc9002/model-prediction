"""Feature-capture snapshots for markets without an exact serving replay.

Exact replay adapters (``coded_market_replay``, ``esports_replay``, ...) re-derive
the served probability from stored inputs, but only exist for the markets
someone wrote one for. A pick logged without any snapshot leaves its feature
payload empty (``unavailable_not_recorded``). This schema records the model's
inputs next to the served probability with an integrity hash, and is explicit
that it is NOT an exact replay: ``replay_verified`` is always False, so
nothing that requires exact replay (pairing, incumbent evaluation) accepts it.
"""

from __future__ import annotations

import json
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "feature-capture-v1"
REPLAY_SCOPE = (
    "Recorded model inputs and served probability only; the probability is not re-derived "
    "from them, so this snapshot is not an exact replay."
)


def flatten_features(*sources: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten dict sources to one level of JSON-safe values; later sources win."""
    features: dict[str, Any] = {}
    for source in sources:
        for key, value in (source or {}).items():
            if isinstance(value, dict):
                for inner_key, inner in value.items():
                    features[f"{key}.{inner_key}"] = _json_safe(inner)
            else:
                features[str(key)] = _json_safe(value)
    return {key: value for key, value in features.items() if value is not None}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def build_snapshot(
    *,
    sport: str,
    market_type: str,
    model_version: str,
    event_id: str,
    event_start_utc: str,
    home_team: str,
    away_team: str,
    selection: str,
    line: float | None,
    probability: float,
    observed_at_utc: str,
    features: dict[str, Any],
    artifact_hash: str = "",
    serving_probability_exact: bool = True,
) -> dict[str, Any]:
    if not features:
        raise ValueError("feature_capture_no_features")
    payload = {
        "schema": SCHEMA,
        "sport": sport,
        "market_type": market_type,
        "model_version": model_version,
        "artifact_hash": artifact_hash,
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "home_team": home_team,
        "away_team": away_team,
        "selection": selection.lower(),
        "line": line,
        "observed_at_utc": observed_at_utc,
        "probability": float(probability),
        "serving_probability_exact": serving_probability_exact,
        "features": features,
        "replay_verified": False,
        "replay_scope": REPLAY_SCOPE,
    }
    payload = _json_safe(payload)
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def contract_snapshot_json(
    contract: dict[str, Any],
    *,
    sport: str,
    selection: str,
    line: float | None,
    probability: float,
    observed_at_utc: str,
    artifact_hash: str = "",
) -> str | None:
    """Snapshot JSON for a priced contract, or None when it carries no usable inputs.

    Capture is best-effort: a contract without recordable inputs is logged
    without a snapshot, exactly as before, rather than blocking the pick.
    """
    try:
        features = flatten_features(contract.get("feature_basis"), contract.get("inference_inputs"))
        if not features:
            return None
        return json.dumps(
            build_snapshot(
                sport=sport,
                market_type=str(contract["market_type"]),
                model_version=str(contract["model_version"]),
                event_id=str(contract["event_id"]),
                event_start_utc=str(contract["event_start_utc"]),
                home_team=str(contract["home_team"]),
                away_team=str(contract["away_team"]),
                selection=selection,
                line=line,
                probability=probability,
                observed_at_utc=observed_at_utc,
                features=features,
                artifact_hash=artifact_hash,
            ),
            sort_keys=True,
            allow_nan=False,
        )
    except (KeyError, ValueError, TypeError):
        return None


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("replay_verified") is not False:
        raise ValueError("feature_capture_schema_mismatch")
    if payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("feature_capture_hash_mismatch")
    if not isinstance(payload.get("features"), dict) or not payload["features"]:
        raise ValueError("feature_capture_no_features")
    if (
        payload["event_id"] != str(row.get("event_id"))
        or payload["model_version"] != row.get("model_version")
        or payload["market_type"] != row.get("market_type")
        or payload["selection"] != str(row.get("selection", "")).lower()
        or str(payload["sport"]).casefold() != str(row.get("league", "")).casefold()
        or parse_utc(payload["event_start_utc"]) != parse_utc(str(row.get("event_start_utc")))
    ):
        raise ValueError("feature_capture_identity_mismatch")
    probability = float(payload["probability"])
    if not 0 < probability < 1:
        raise ValueError("feature_capture_invalid_probability")
    if (
        payload.get("serving_probability_exact", True)
        and row.get("model_probability") not in (None, "")
        and abs(probability - float(row["model_probability"])) > 1e-6
    ):
        raise ValueError("feature_capture_probability_mismatch")
    parse_utc(payload["observed_at_utc"])
    return probability
