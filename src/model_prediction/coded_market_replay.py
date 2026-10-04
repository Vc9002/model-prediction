"""Exact serving replay for soccer Poisson-DC and tennis surface Elo moneylines."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .domain import parse_utc
from .learned_replay import snapshot_hash

SCHEMA = "coded-moneyline-replay-v1"
VERSIONS = {"SOCCER": "soccer-poisson-dc-v1", "TENNIS": "tennis-surface-elo-v1"}


def code_hashes(sport: str) -> dict[str, str]:
    files = [f"models/{sport.lower()}.py"]
    if sport == "TENNIS":
        files.append("features/elo_ratings.py")
    return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in files}


def capture_inputs(sport: str, values: dict[str, Any]) -> dict[str, Any]:
    if sport not in VERSIONS:
        raise ValueError("coded_replay_unsupported_sport")
    return json.loads(
        json.dumps({"sport": sport, "code_sha256": code_hashes(sport), **values}, allow_nan=False)
    )


def replay_inputs(inputs: dict[str, Any]) -> dict[str, float]:
    sport = inputs["sport"]
    if sport not in VERSIONS or inputs["code_sha256"] != code_hashes(sport):
        raise ValueError("coded_replay_code_identity_mismatch")
    if sport == "SOCCER":
        from .models.soccer import SoccerModel

        rates = [float(inputs[k]) for k in ("home_rate", "away_rate")]
        if any(not math.isfinite(rate) or rate <= 0 for rate in rates):
            raise ValueError("coded_replay_invalid_goal_rates")
        matrix = SoccerModel().score_matrix(*rates)
        size = len(matrix)
        home = sum(matrix[h][a] for h in range(size) for a in range(h))
        away = sum(matrix[h][a] for a in range(size) for h in range(a))
        return {"home": round(home, 6), "away": round(away, 6), "draw": round(1 - home - away, 6)}
    from .models.tennis import TennisModel

    players, surface = inputs["players"], inputs["surface"]
    if len(players) != 2 or players[0] == players[1] or not surface:
        raise ValueError("coded_replay_invalid_players")
    overall = dict(zip(players, inputs["overall_ratings"], strict=True))
    by_surface = dict(zip(((p, surface) for p in players), inputs["surface_ratings"], strict=True))
    counts = dict(zip(((p, surface) for p in players), inputs["surface_counts"], strict=True))
    if any(not math.isfinite(v) for v in (*overall.values(), *by_surface.values())) or any(
        isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts.values()
    ):
        raise ValueError("coded_replay_invalid_elo_state")
    p = TennisModel().match_probability(overall, by_surface, counts, players[0], players[1], surface)
    # The producer rounds to six decimals, then the priced contract to four.
    return {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}


def build_snapshot(contract: dict[str, Any], artifact_hash: str, observed_at_utc: str) -> dict[str, Any]:
    inputs = contract["inference_inputs"]
    sport = inputs["sport"]
    values = replay_inputs(inputs)
    if artifact_hash != inputs["code_sha256"][f"models/{sport.lower()}.py"]:
        raise ValueError("coded_replay_artifact_identity_mismatch")
    if contract["market_type"] != "moneyline" or contract["model_version"] != VERSIONS[sport]:
        raise ValueError("coded_replay_contract_identity_mismatch")
    if abs(values[contract["selection"]] - float(contract["model_probability"])) > 1e-12:
        raise ValueError("coded_replay_serving_mismatch")
    payload = {
        "schema": SCHEMA,
        "sport": sport,
        "market_type": "moneyline",
        "model_version": contract["model_version"],
        "artifact_hash": artifact_hash,
        "event_id": contract["event_id"],
        "event_start_utc": contract["event_start_utc"],
        "home_team": contract["home_team"],
        "away_team": contract["away_team"],
        "observed_at_utc": observed_at_utc,
        "inference_inputs": inputs,
        "probabilities": values,
        "features": contract["feature_basis"],
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact full-precision serving state and rounding; upstream historical observation times are not certified.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("coded_replay_snapshot_hash_mismatch")
    sport = payload["sport"]
    if (
        sport not in VERSIONS
        or str(row.get("league")).upper() != sport
        or payload["model_version"] != VERSIONS[sport]
        or row.get("model_version") != VERSIONS[sport]
        or payload["inference_inputs"]["sport"] != sport
        or row.get("model_artifact_hash") != payload["artifact_hash"]
        or payload["artifact_hash"]
        != payload["inference_inputs"]["code_sha256"][f"models/{sport.lower()}.py"]
        or str(row.get("event_id")) != payload["event_id"]
        or row.get("market_type") != "moneyline"
        or row.get("selection") not in {"home", "away", "draw"}
        or row.get("line") not in (None, "")
        or row.get("home_team") != payload["home_team"]
        or row.get("away_team") != payload["away_team"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("coded_replay_decision_identity_mismatch")
    if sport == "TENNIS" and payload["inference_inputs"]["players"] != [
        payload["away_team"],
        payload["home_team"],
    ]:
        raise ValueError("coded_replay_player_orientation_mismatch")
    values = replay_inputs(payload["inference_inputs"])
    if values != payload["probabilities"]:
        raise ValueError("coded_replay_probability_map_mismatch")
    value, stored = values[row["selection"]], float(row["model_probability"])
    if not math.isfinite(stored) or abs(value - stored) > 2e-6:
        raise ValueError("coded_replay_probability_mismatch")
    return value
