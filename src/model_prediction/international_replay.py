"""Exact KBO/NPB Elo replay with win/draw probabilities and settlement values."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "international-elo-replay-v1"


def code_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in ("international_baseball.py", "features/elo_ratings.py")
    }


def capture_inputs(
    book: Any, away_id: str, home_id: str, tie_method: str, base_tie_rate: float
) -> dict[str, Any]:
    state = asdict(book)
    state["ratings"] = {key: value for key, value in state["ratings"].items() if key in {away_id, home_id}}
    # These fields are update-only, but retaining any matchup entries makes
    # the exact serving object's state explicit without other teams' histories.
    for key in ("_last_active", "_games_played"):
        if state[key] is not None:
            state[key] = {k: v for k, v in state[key].items() if k in {away_id, home_id}}
    return json.loads(
        json.dumps(
            {
                "book_state": state,
                "away_id": away_id,
                "home_id": home_id,
                "tie_method": tie_method,
                "base_tie_rate": base_tie_rate,
                "code_sha256": code_hashes(),
            },
            allow_nan=False,
        )
    )


def replay_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    from .international_baseball import HomeElo, _game_tie_probability, tie_aware_fair_values

    if inputs["code_sha256"] != code_hashes() or inputs["away_id"] == inputs["home_id"]:
        raise ValueError("international_replay_identity_mismatch")
    if inputs["tie_method"] not in {"flat", "elo_gap"} or not 0 <= inputs["base_tie_rate"] <= 1:
        raise ValueError("international_replay_invalid_tie_policy")
    book = HomeElo(**inputs["book_state"])
    p_home = book.decisive_home_probability(inputs["away_id"], inputs["home_id"])
    tie = _game_tie_probability(
        inputs["away_id"], inputs["home_id"], book, inputs["tie_method"], inputs["base_tie_rate"]
    )
    if not math.isfinite(p_home + tie) or not 0 <= p_home <= 1 or not 0 <= tie <= 1:
        raise ValueError("international_replay_invalid_probability")
    away_fair, home_fair = tie_aware_fair_values(p_home, tie)
    return {
        "settlement_values": {"away": round(away_fair, 6), "home": round(home_fair, 6)},
        "outcome_probabilities": {"away": (1 - tie) * (1 - p_home), "draw": tie, "home": (1 - tie) * p_home},
        "draw_settlement_value": 0.5,
    }


def build_snapshot(contract: dict[str, Any], observed_at_utc: str) -> dict[str, Any]:
    from .research_io import identity_key

    inputs = contract["inference_inputs"]
    values = replay_inputs(inputs)
    names = {identity_key(contract["home_team"]): "home", identity_key(contract["away_team"]): "away"}
    if len(names) != 2:
        raise ValueError("international_replay_ambiguous_teams")
    for side in contract["sides"]:
        selection = names[identity_key(side["team"])]
        if abs(float(side["model_fair_settlement_value"]) - values["settlement_values"][selection]) > 1e-12:
            raise ValueError("international_replay_serving_mismatch")
    payload = {
        "schema": SCHEMA,
        "sport": contract["league"].upper(),
        "market_type": "moneyline",
        "model_version": contract["model_version"],
        "artifact_hash": contract["artifact_hash"],
        "event_id": contract["event_id"],
        "event_start_utc": contract["event_start_utc"],
        "home_team": contract["home_team"],
        "away_team": contract["away_team"],
        "observed_at_utc": observed_at_utc,
        "inference_inputs": inputs,
        **values,
        "features": {
            "home_elo": inputs["book_state"]["ratings"][inputs["home_id"]],
            "away_elo": inputs["book_state"]["ratings"][inputs["away_id"]],
            "home_advantage": inputs["book_state"]["home_advantage"],
            "base_tie_rate": inputs["base_tie_rate"],
        },
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact Elo and tie model; logged value is expected contract settlement, not outright win probability.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("international_replay_hash_mismatch")
    if (
        payload["sport"] not in {"KBO", "NPB"}
        or str(row.get("league")).upper() != payload["sport"]
        or payload["model_version"] != payload["sport"].lower() + "-tie-aware-elo-v2"
        or row.get("model_version") != payload["model_version"]
        or row.get("model_artifact_hash") != payload["artifact_hash"]
        or str(row.get("event_id")) != payload["event_id"]
        or row.get("market_type") != "moneyline"
        or row.get("selection") not in {"home", "away"}
        or row.get("line") not in (None, "")
        or row.get("home_team") != payload["home_team"]
        or row.get("away_team") != payload["away_team"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("international_replay_decision_identity_mismatch")
    values = replay_inputs(payload["inference_inputs"])
    if any(values[key] != payload[key] for key in values):
        raise ValueError("international_replay_values_mismatch")
    stored = float(row["model_probability"])
    value = values["settlement_values"][row["selection"]]
    if not math.isfinite(stored) or abs(stored - value) > 2e-6:
        raise ValueError("international_replay_probability_mismatch")
    return value
