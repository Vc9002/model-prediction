"""Matchup-specific serving state for all five tiered-Elo esports incumbents."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "esports-elo-replay-v1"
TITLES = {"CS2", "DOTA2", "LOL", "VALORANT", "RAINBOW_SIX"}


def code_hashes() -> dict[str, str]:
    root = Path(__file__).parent
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("esports.py", "features/elo_ratings.py")
    }


def capture_inputs(book: Any, team_ids: list[str], reference_date: datetime) -> dict[str, Any]:
    if len(team_ids) != 2 or team_ids[0] == team_ids[1]:
        raise ValueError("invalid_esports_replay_teams")
    state = asdict(book)
    # Prediction does not depend on other teams' state. Preserve absence too:
    # an unseen team must replay the engine's explicit neutral-prior behavior.
    for name in ("ratings", "last_match_utc", "games_played"):
        state[name] = {key: value for key, value in state[name].items() if key in team_ids}
    return json.loads(
        json.dumps(
            {
                "book_state": state,
                "team_ids": team_ids,
                "reference_date": reference_date.isoformat(),
                "code_sha256": code_hashes(),
            },
            allow_nan=False,
        )
    )


def replay_inputs(inputs: dict[str, Any]) -> dict[str, float]:
    from .esports import NeutralElo

    if inputs["code_sha256"] != code_hashes():
        raise ValueError("esports_replay_code_mismatch")
    ids = inputs["team_ids"]
    if len(ids) != 2 or ids[0] == ids[1]:
        raise ValueError("esports_replay_team_mismatch")
    book = NeutralElo(**inputs["book_state"])
    p = book.probability(ids[0], ids[1], parse_utc(inputs["reference_date"]))
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("esports_replay_invalid_probability")
    return {"home": round(p, 6), "away": round(1 - p, 6)}


def build_snapshot(contract: dict[str, Any]) -> dict[str, Any]:
    from .research_io import identity_key

    inputs = contract["inference_inputs"]
    if inputs["team_ids"] != contract["source_team_ids"]:
        raise ValueError("esports_replay_source_identity_mismatch")
    probabilities = replay_inputs(inputs)
    names = contract["teams"]
    if len(names) != 2 or identity_key(names[0]) == identity_key(names[1]):
        raise ValueError("esports_replay_ambiguous_names")
    for side in contract["sides"]:
        key = identity_key(side["team"])
        if key not in {identity_key(name) for name in names}:
            raise ValueError("esports_replay_unmatched_side")
        selection = "home" if key == identity_key(names[0]) else "away"
        if abs(float(side["model_probability"]) - probabilities[selection]) > 1e-12:
            raise ValueError("esports_replay_served_probability_mismatch")
    book = inputs["book_state"]
    ids = inputs["team_ids"]
    payload = {
        "schema": SCHEMA,
        "sport": contract["title"].upper(),
        "market_type": "moneyline",
        "model_version": contract["model_version"],
        "artifact_hash": contract["artifact_hash"],
        "event_id": contract["event_id"],
        "event_start_utc": contract["event_start_utc"],
        "observed_at_utc": inputs["reference_date"],
        "home_team": names[0],
        "away_team": names[1],
        "inference_inputs": inputs,
        "probabilities": probabilities,
        "features": {
            "home_elo": book["ratings"].get(ids[0], 1500.0),
            "away_elo": book["ratings"].get(ids[1], 1500.0),
            "home_history_count": book["games_played"].get(ids[0], 0),
            "away_history_count": book["games_played"].get(ids[1], 0),
        },
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact serving book state, calibration, temperature, inactivity reference and thin-history adjustments; training provenance not certified.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("esports_replay_snapshot_hash_mismatch")
    if (
        payload["sport"] not in TITLES
        or not payload["model_version"].startswith(payload["sport"].lower() + "-tiered-elo-")
        or str(row.get("league")).upper() != payload["sport"]
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
        or parse_utc(payload["observed_at_utc"]) != parse_utc(payload["inference_inputs"]["reference_date"])
    ):
        raise ValueError("esports_replay_decision_identity_mismatch")
    probabilities = replay_inputs(payload["inference_inputs"])
    stored = float(row["model_probability"])
    if (
        probabilities != payload["probabilities"]
        or not math.isfinite(stored)
        or abs(stored - probabilities[row["selection"]]) > 2e-6
    ):
        raise ValueError("esports_replay_decision_probability_mismatch")
    return probabilities[row["selection"]]
