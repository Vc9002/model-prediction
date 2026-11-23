"""Exact NCAAF serving replay, including the simulator's advancing RNG state."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from .domain import parse_utc
from .learned_replay import snapshot_hash
from .models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine

SCHEMA = "cfb-joint-replay-v1"
VERSIONS = {
    "moneyline": (
        "cfb-structural-v5.2",
        "cfb-structural-v5",
        "cfb-structural-v4",
        "cfb-structural-v3",
        "cfb-structural-v2",
        "college-football-v1",
    ),
    "spread": (
        "cfb-structural-v5.2",
        "cfb-structural-v5",
        "cfb-structural-v4",
        "cfb-structural-v3",
        "cfb-structural-v2",
        "cfb-spread-v1",
    ),
    "total": (
        "cfb-structural-v5.2",
        "cfb-structural-v5",
        "cfb-structural-v4",
        "cfb-structural-v3",
        "cfb-structural-v2",
        "cfb-total-v1",
    ),
}


# Simulated (home, away) score arrays by engine parameters, RNG state and score
# means. A replay restores the recorded RNG state, so the draws are a pure
# function of that key; the spread and total lines only threshold them. One
# game's contracts share one key and each was replayed at slate build, snapshot
# build, snapshot validation and every ledger write: 93% of the 128 s NCAAF
# slate build on 2026-11-14. Two 200,000-draw arrays are 3.2 MB, hence the
# small bound. The memo lives here, not in models/cfb_distribution.py, because
# that file's hash is stamped into every stored snapshot (engine_hash).
_SCORES_CACHE: OrderedDict[str, tuple[np.ndarray, np.ndarray]] = OrderedDict()
_SCORES_CACHE_LOCK = threading.Lock()
_SCORES_CACHE_MAX_ENTRIES = 16


class _MemoizedReplayEngine(CFBJointDistributionEngine):
    """A replay engine whose score simulation is served from ``_SCORES_CACHE``."""

    def simulate_joint_scores(self, mu_home: float, mu_away: float) -> tuple[np.ndarray, np.ndarray]:
        key = json.dumps(
            [
                self.distribution_type.value,
                self.margin_sd,
                self.total_sd,
                self.score_correlation,
                self.nb_dispersion,
                self.n_simulations,
                self.rng.bit_generator.state,
                mu_home,
                mu_away,
            ],
            sort_keys=True,
        )
        with _SCORES_CACHE_LOCK:
            cached = _SCORES_CACHE.get(key)
            if cached is not None:
                _SCORES_CACHE.move_to_end(key)
                return cached
        home_scores, away_scores = super().simulate_joint_scores(mu_home, mu_away)
        # Shared between callers from here on; a write would corrupt later replays.
        home_scores.setflags(write=False)
        away_scores.setflags(write=False)
        with _SCORES_CACHE_LOCK:
            _SCORES_CACHE[key] = (home_scores, away_scores)
            while len(_SCORES_CACHE) > _SCORES_CACHE_MAX_ENTRIES:
                _SCORES_CACHE.popitem(last=False)
        return home_scores, away_scores


# cfb-structural-v5.2's total-market shrink, recorded in inference_inputs only when the artifact sets it
# (so v2-v5 snapshots keep their exact key set). Kept here, not in models/cfb_distribution.py, whose bytes
# are every stored snapshot's engine identity; serving and replay call the one helper below on the same
# unrounded engine output, so a v5.2 row replays to its served probability bit for bit.
TOTAL_SLOPE_KEY = "total_probability_logit_slope"


def validate_total_slope(slope: Any) -> float:
    value = float(slope)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("cfb_replay_invalid_total_slope")
    return value


def shrink_total_probabilities(
    p_over: float, p_under: float, p_push: float, slope: float
) -> tuple[float, float]:
    """(P(over), P(under)) with the no-push over share moved to ``sigmoid(slope * logit(share))``.

    Intercept 0, so no over/under direction is introduced. The push mass is left as the engine
    priced it (it is 0 on half-point lines, where the pair stays exactly complementary and
    ``slope = 0`` gives exactly 0.5 / 0.5); on integer lines the pair sums to ``1 - p_push``.
    """
    slope = validate_total_slope(slope)
    decided = 1.0 - p_push
    total = p_over + p_under
    if total <= 0.0 or decided <= 0.0:
        return p_over, p_under
    share = p_over / total
    if slope == 0.0:
        shrunk = 0.5
    elif share <= 0.0 or share >= 1.0:
        shrunk = share
    else:
        shrunk = 1.0 / (1.0 + math.exp(-slope * math.log(share / (1.0 - share))))
    over = shrunk * decided
    return over, decided - over


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
    total_probability_logit_slope: float | None = None,
) -> dict[str, Any]:
    shrink = (
        {}
        if total_probability_logit_slope is None
        else {TOTAL_SLOPE_KEY: validate_total_slope(total_probability_logit_slope)}
    )
    return json.loads(
        json.dumps(
            {
                **shrink,
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
    engine = _MemoizedReplayEngine(**parameters)
    engine.rng.bit_generator.state = inputs["rng_state_before"]
    result = engine.compute_market_probabilities(
        mu_home=inputs["mu_home"],
        mu_away=inputs["mu_away"],
        spread_home_line=inputs["spread_home_line"],
        total_line=inputs["total_line"],
    )
    p_over, p_under = result.p_over, result.p_under
    if TOTAL_SLOPE_KEY in inputs:
        # Validated for every market so a damaged slope fails closed wherever it is replayed.
        slope = validate_total_slope(inputs[TOTAL_SLOPE_KEY])
        if market == "total":
            p_over, p_under = shrink_total_probabilities(p_over, p_under, result.p_push_total, slope)
    values = (
        {"home": result.p_home_win, "away": result.p_away_win}
        if market == "moneyline"
        else {"home": result.p_home_cover, "away": result.p_away_cover, "push": result.p_push_spread}
        if market == "spread"
        else {"over": p_over, "under": p_under, "push": result.p_push_total}
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
    allowed_versions = VERSIONS.get(market, ())
    if (
        payload["model_version"] not in allowed_versions
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
