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
VERSIONS = {"SOCCER": "soccer-poisson-dc-v1", "TENNIS": "tennis-surface-elo-v4"}
ALLOWED_VERSIONS = {
    "SOCCER": {"soccer-poisson-dc-v1", "soccer-poisson-dc-v2", "soccer-poisson-dc-v3"},
    "TENNIS": {
        "tennis-surface-elo-v1",
        "tennis-markov-hierarchical-v2",
        "tennis-learned-v3",
        "tennis-surface-elo-v4",
    },
}


def code_hashes(sport: str, model_version: str | None = None) -> dict[str, str]:
    files = [f"models/{sport.lower()}.py"]
    if sport == "SOCCER" and model_version == "soccer-poisson-dc-v3":
        files.append("models/soccer_v3.py")
    if sport == "TENNIS":
        files.append("features/elo_ratings.py")
    return {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in files}


def capture_inputs(sport: str, values: dict[str, Any]) -> dict[str, Any]:
    if sport not in VERSIONS:
        raise ValueError("coded_replay_unsupported_sport")
    return json.loads(
        json.dumps(
            {"sport": sport, "code_sha256": code_hashes(sport, values.get("model_version")), **values},
            allow_nan=False,
        )
    )


def replay_inputs(inputs: dict[str, Any]) -> dict[str, float]:
    sport = inputs["sport"]
    if sport not in VERSIONS or inputs["code_sha256"] != code_hashes(sport, inputs.get("model_version")):
        raise ValueError("coded_replay_code_identity_mismatch")
    if sport == "SOCCER":
        from .models.soccer import SoccerModel, soccer_model_v2

        rates = [float(inputs[k]) for k in ("home_rate", "away_rate")]
        if any(not math.isfinite(rate) or rate <= 0 for rate in rates):
            raise ValueError("coded_replay_invalid_goal_rates")
        rho = float(inputs["rho"]) if "rho" in inputs and inputs["rho"] is not None else None
        soccer_model = (
            soccer_model_v2() if inputs.get("model_version") == "soccer-poisson-dc-v2" else SoccerModel()
        )
        matrix = (
            soccer_model.score_matrix(rates[0], rates[1], rho=rho)
            if rho is not None
            else soccer_model.score_matrix(rates[0], rates[1])
        )
        size = len(matrix)
        home = sum(matrix[h][a] for h in range(size) for a in range(h))
        away = sum(matrix[h][a] for a in range(size) for h in range(a))
        draw = sum(matrix[i][i] for i in range(size))
        return {
            "home": round(home, 6),
            "away": round(away, 6),
            "draw": round(draw, 6),
            "1X": round(home + draw, 6),
            "X2": round(away + draw, 6),
            "12": round(home + away, 6),
        }

    model_version = inputs.get("model_version")
    if model_version == "tennis-learned-v3":
        from .models.tennis import TennisModel, apply_market_residual

        players = inputs["players"]
        surface = inputs["surface"]
        tour = inputs.get("tour", "ATP")
        overall = dict(zip(players, inputs["overall_ratings"], strict=True))
        by_surface = dict(zip(((p, surface) for p in players), inputs["surface_ratings"], strict=True))
        surface_counts = dict(zip(((p, surface) for p in players), inputs["surface_counts"], strict=True))
        counts = dict(zip(players, inputs.get("counts", [0, 0]), strict=True))
        rest_days = dict(zip(players, inputs.get("rest_days", [14.0, 14.0]), strict=True))
        p = TennisModel(version="tennis-learned-v3").match_probability_v3(
            overall=overall,
            by_surface=by_surface,
            surface_counts=surface_counts,
            counts=counts,
            rest_days=rest_days,
            player_one=players[0],
            player_two=players[1],
            surface=surface,
            tour=tour,
        )
        market_prob = inputs.get("market_probability")
        if market_prob is not None:
            p = apply_market_residual(p, float(market_prob))
        return {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}

    if model_version == "tennis-surface-elo-v4":
        from .models.tennis import TennisModel

        players = inputs["players"]
        surface = inputs["surface"]
        overall = dict(zip(players, inputs["overall_ratings"], strict=True))
        by_surface = dict(zip(((p, surface) for p in players), inputs["surface_ratings"], strict=True))
        surface_counts = dict(zip(((p, surface) for p in players), inputs["surface_counts"], strict=True))
        counts = dict(zip(players, inputs.get("counts", [0, 0]), strict=True))
        recent_form = dict(
            zip(players, inputs.get("recent_form_ratings", inputs["overall_ratings"]), strict=True)
        )
        rest_days = dict(zip(players, inputs.get("rest_days", [14.0, 14.0]), strict=True))
        best_of = int(inputs.get("best_of", 3))
        tennis_model = TennisModel(version="tennis-surface-elo-v4")
        tennis_model.recent_form = recent_form
        p = tennis_model.match_probability_v4(
            overall=overall,
            by_surface=by_surface,
            surface_counts=surface_counts,
            counts=counts,
            player_one=players[0],
            player_two=players[1],
            surface=surface,
            rest_days=rest_days,
            best_of=best_of,
        )
        return {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}

    if model_version == "tennis-markov-hierarchical-v2" or (
        "p_serve_a" in inputs
        and model_version not in ("tennis-surface-elo-v1", "tennis-learned-v3", "tennis-surface-elo-v4")
    ):
        from .models.tennis_derivatives import price_tennis_derivatives

        p_serve_a = float(inputs["p_serve_a"])
        p_serve_b = float(inputs["p_serve_b"])
        best_of = int(inputs.get("best_of", 3))
        deriv = price_tennis_derivatives(p_serve_a=p_serve_a, p_serve_b=p_serve_b, best_of=best_of)
        p = deriv.match_win_a
        return {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}

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
    p = TennisModel(version="tennis-surface-elo-v1").match_probability(
        overall, by_surface, counts, players[0], players[1], surface
    )
    # The producer rounds to six decimals, then the priced contract to four.
    return {"away": round(round(p, 6), 4), "home": round(round(1 - p, 6), 4)}


def build_snapshot(contract: dict[str, Any], artifact_hash: str, observed_at_utc: str) -> dict[str, Any]:
    inputs = contract["inference_inputs"]
    sport = inputs["sport"]
    values = replay_inputs(inputs)
    if artifact_hash != inputs["code_sha256"][f"models/{sport.lower()}.py"]:
        raise ValueError("coded_replay_artifact_identity_mismatch")
    if contract["market_type"] != "moneyline" or contract["model_version"] not in ALLOWED_VERSIONS[sport]:
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
        sport not in ALLOWED_VERSIONS
        or str(row.get("league")).upper() != sport
        or payload["model_version"] not in ALLOWED_VERSIONS[sport]
        or row.get("model_version") != payload["model_version"]
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
