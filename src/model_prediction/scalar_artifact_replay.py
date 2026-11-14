"""Replay exact NRFI and WNBA spread/total artifact calculations."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .calibration import load_calibrator_artifact
from .config import PROJECT_ROOT
from .domain import parse_utc
from .learned_replay import snapshot_hash
from .production_registry import compute_artifact_hash
from .runtime_paths import RuntimePaths

SCHEMA = "scalar-artifact-replay-v1"


def engine_hash(method: str) -> str:
    name = {
        "first_inning_logistic": "mlb_first_inning.py",
        "half_inning_logistic": "mlb_first_inning.py",
        "half_inning_interaction": "mlb_first_inning.py",
        "margin_normal": "basketball.py",
        "total_normal": "basketball.py",
        "nfl_structural_v5_margin_engine": "nfl_structural_v5.py",
        "nfl_structural_v5_total_engine": "nfl_structural_v5.py",
        "nfl_spread_structural_v6_engine": "nfl_spread_v6.py",
        "nfl_total_structural_v6_engine": "nfl_total_v6.py",
        "nfl_spread_structural_v7_engine": "nfl_spread_v7.py",
        "nfl_total_structural_v7_engine": "nfl_total_v7.py",
    }.get(method)
    if name is None:
        raise ValueError("unsupported_scalar_replay_method")
    return hashlib.sha256((Path(__file__).parent / "models" / name).read_bytes()).hexdigest()


HISTORICAL_ENGINE_HASHES: dict[str, set[str]] = {
    "first_inning_logistic": {
        "e616f074b8082063684bb6db8c43d3a182f629455165dcc0074d32df98459a47",
    },
    "half_inning_logistic": {
        "e616f074b8082063684bb6db8c43d3a182f629455165dcc0074d32df98459a47",
    },
}


def replay_snapshot(payload: dict[str, Any]) -> dict[str, float]:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != snapshot_hash(payload):
        raise ValueError("scalar_snapshot_hash_mismatch")
    artifact = payload["artifact"]
    method = artifact["method"]
    if (
        compute_artifact_hash(artifact) != artifact.get("artifact_hash")
        or artifact["artifact_hash"] != payload["artifact_hash"]
    ):
        raise ValueError("scalar_replay_artifact_hash_mismatch")
    expected_hash = engine_hash(method)
    if payload["engine_source_sha256"] != expected_hash and payload[
        "engine_source_sha256"
    ] not in HISTORICAL_ENGINE_HASHES.get(method, set()):
        raise ValueError("scalar_replay_engine_hash_mismatch")
    if method == "first_inning_logistic":
        features = payload["features"]
        logit = float(artifact["intercept"])
        for name, coef, mean, scale in zip(
            artifact["feature_names"],
            artifact["coef"],
            artifact["scaler_mean"],
            artifact["scaler_scale"],
            strict=True,
        ):
            value = float(features[name])
            if not all(math.isfinite(float(x)) for x in (value, coef, mean, scale)) or scale <= 0:
                raise ValueError("invalid_scalar_replay_feature")
            logit += coef * ((value - mean) / scale)
        p = 1 / (1 + math.exp(-max(-20.0, min(20.0, logit))))
        return {"nrfi": round(p, 4), "yrfi": round(1 - p, 4)}
    if method == "half_inning_logistic":
        features = payload["features"]
        logit_away = float(artifact["intercept_away"])
        for name, coef, mean, scale in zip(
            artifact["feature_names"],
            artifact["coef_away"],
            artifact["scaler_mean"],
            artifact["scaler_scale"],
            strict=True,
        ):
            value = float(features[name])
            if not all(math.isfinite(float(x)) for x in (value, coef, mean, scale)) or scale <= 0:
                raise ValueError("invalid_scalar_replay_feature")
            logit_away += coef * ((value - mean) / scale)
        p_away0 = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, logit_away))))

        logit_home = float(artifact["intercept_home"])
        for name, coef, mean, scale in zip(
            artifact["feature_names"],
            artifact["coef_home"],
            artifact["scaler_mean"],
            artifact["scaler_scale"],
            strict=True,
        ):
            value = float(features[name])
            if not all(math.isfinite(float(x)) for x in (value, coef, mean, scale)) or scale <= 0:
                raise ValueError("invalid_scalar_replay_feature")
            logit_home += coef * ((value - mean) / scale)
        p_home0 = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, logit_home))))

        p = p_away0 * p_home0
        return {"nrfi": round(p, 4), "yrfi": round(1 - p, 4)}
    if method == "half_inning_interaction":
        from .models.mlb_first_inning import FirstInningGameRow, MLBHalfInningInteractionModel

        model = MLBHalfInningInteractionModel.from_dict(artifact)
        row = FirstInningGameRow(
            None, payload["event_start_utc"], "Home", "Away", "", payload["features"], 0, 0.0
        )
        p = model.predict_p_nrfi(row)
        return {"nrfi": round(p, 4), "yrfi": round(1 - p, 4)}
    from .models.basketball import _normal_cdf

    inputs = payload["inference_inputs"]
    mean, sd, line = (float(inputs[key]) for key in ("mean", "sd", "line"))
    if not math.isfinite(mean + sd + line) or sd <= 0:
        raise ValueError("invalid_scalar_replay_distribution")
    if method == "nfl_structural_v5_margin_engine":
        from .models.nfl_structural_v5 import discrete_spread_prob

        p_home, p_away, _ = discrete_spread_prob(-line, mean, sd)
        tot_spread = p_home + p_away
        if tot_spread > 0:
            p_home /= tot_spread
            p_away /= tot_spread
        return {"away": round(p_away, 6), "home": round(p_home, 6)}
    if method == "nfl_structural_v5_total_engine":
        from .models.nfl_structural_v5 import discrete_total_prob

        p_over, p_under, _ = discrete_total_prob(line, mean, sd)
        tot_total = p_over + p_under
        if tot_total > 0:
            p_over /= tot_total
            p_under /= tot_total
        return {"under": round(p_under, 6), "over": round(p_over, 6)}
    if method == "nfl_spread_structural_v6_engine":
        from .models.nfl_spread_v6 import (
            NFL_KEY_MARGIN_WEIGHTS,
        )
        from .models.nfl_spread_v6 import (
            _normal_cdf as _nfl_cdf,
        )

        cutoff = line
        exp_margin = mean
        margin_sd = sd
        key_number_weight = float(artifact.get("key_number_weight", 0.2))
        min_cover_prob = float(artifact.get("min_cover_prob", 0.35))
        max_cover_prob = float(artifact.get("max_cover_prob", 0.65))

        p_raw_home_cover = 1.0 - _nfl_cdf(cutoff, exp_margin, margin_sd)
        key_bump = 0.0
        for km, kw in NFL_KEY_MARGIN_WEIGHTS.items():
            if cutoff < km <= cutoff + 1.0:
                key_bump += kw * 0.35
            elif cutoff - 1.0 < km <= cutoff:
                key_bump -= kw * 0.35

        p_home_cover = (1.0 - key_number_weight) * p_raw_home_cover + key_number_weight * (
            p_raw_home_cover + key_bump
        )
        p_home_cover = max(min_cover_prob, min(max_cover_prob, p_home_cover))
        p_away_cover = 1.0 - p_home_cover
        return {"away": round(p_away_cover, 6), "home": round(p_home_cover, 6)}
    if method == "nfl_spread_structural_v7_engine":
        from .models.nfl_spread_v7 import (
            NFL_KEY_MARGIN_FREQUENCIES,
        )
        from .models.nfl_spread_v7 import (
            _normal_cdf as _nfl_cdf,
        )

        cutoff = line
        exp_margin = mean
        margin_sd = sd
        key_number_weight = float(artifact.get("key_number_weight", 0.2))
        min_cover_prob = float(artifact.get("min_cover_prob", 0.35))
        max_cover_prob = float(artifact.get("max_cover_prob", 0.65))

        p_raw_home_cover = 1.0 - _nfl_cdf(cutoff, exp_margin, margin_sd)
        key_bump = 0.0
        for km, kw in NFL_KEY_MARGIN_FREQUENCIES.items():
            if cutoff < km <= cutoff + 1.0:
                key_bump += kw * 0.35
            elif cutoff - 1.0 < km <= cutoff:
                key_bump -= kw * 0.35

        p_home_cover = (1.0 - key_number_weight) * p_raw_home_cover + key_number_weight * (
            p_raw_home_cover + key_bump
        )
        p_home_cover = max(min_cover_prob, min(max_cover_prob, p_home_cover))

        if artifact.get("use_scoring_simulation"):
            from .models.nfl_scoring_simulation import solve_bivariate_scoring

            pts_home = round(23.0 + exp_margin / 2.0, 1)
            pts_away = round(23.0 - exp_margin / 2.0, 1)
            sim = solve_bivariate_scoring(
                exp_home_pts=pts_home,
                exp_away_pts=pts_away,
                spread_home_line=-cutoff,
                rho=float(artifact.get("rho", 0.08)),
            )
            sim_weight = float(artifact.get("simulation_weight", 0.2))
            p_home_cover = (1.0 - sim_weight) * p_home_cover + sim_weight * sim.prob_home_cover
            p_home_cover = max(min_cover_prob, min(max_cover_prob, p_home_cover))

        p_away_cover = 1.0 - p_home_cover
        return {"away": round(p_away_cover, 6), "home": round(p_home_cover, 6)}
    if method in ("nfl_total_structural_v6_engine", "nfl_total_structural_v7_engine"):
        from .models.nfl_total_v7 import _normal_cdf as _nfl_cdf

        min_total_prob = float(artifact.get("min_total_prob", 0.35))
        max_total_prob = float(artifact.get("max_total_prob", 0.65))
        p_over = 1.0 - _nfl_cdf(line, mean, sd)
        p_over = max(min_total_prob, min(max_total_prob, p_over))

        if method == "nfl_total_structural_v7_engine" and artifact.get("use_scoring_simulation"):
            from .models.nfl_scoring_simulation import solve_bivariate_scoring

            sim = solve_bivariate_scoring(
                exp_home_pts=mean / 2.0,
                exp_away_pts=mean / 2.0,
                total_line=line,
            )
            p_over = 0.70 * p_over + 0.30 * sim.prob_over
            p_over = max(min_total_prob, min(max_total_prob, p_over))

        p_under = 1.0 - p_over
        return {"under": round(p_under, 6), "over": round(p_over, 6)}

    is_margin = method == "margin_normal"
    expected_sd = float(artifact["margin_sd" if is_margin else "total_sd"])
    if is_margin and artifact.get("dynamic_volatility"):
        # Replay the enabled engine transform from its exact trend inputs;
        # accepting an arbitrary in-range sd would weaken artifact validation.
        consistency = [
            float(payload["features"][key])
            for key in ("volatility_away_consistency", "volatility_home_consistency")
        ]
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in consistency):
            raise ValueError("invalid_scalar_replay_volatility_features")
        avg_c = sum(value or 0.05 for value in consistency) / 2.0
        base_sd = expected_sd
        expected_sd = max(12.0, min(17.5, expected_sd * (1.0 + 0.25 * (1.0 - avg_c / 0.055))))
        # Thin-sample widening, replayed from the captured current-season sample
        # (BasketballModel: sample < 8 => sd >= margin_sd * (1 + 0.5 * (1 - sample / 8))).
        # Snapshots captured before the sample was recorded carry no key and replay as before.
        sample = payload["features"].get("volatility_sample")
        if sample is not None:
            sample = int(sample)
            if sample < 8:
                sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
                expected_sd = max(expected_sd, base_sd * (1.0 + 0.5 * sample_factor))
    if sd != expected_sd:
        raise ValueError("scalar_replay_artifact_parameter_mismatch")
    below = _normal_cdf(line, mean, sd)
    values = {"away": below, "home": 1 - below} if is_margin else {"under": below, "over": 1 - below}
    return {name: round(value, 6) for name, value in values.items()}


def build_snapshot(
    artifact: dict[str, Any],
    features: dict[str, Any],
    inference_inputs: dict[str, Any],
    *,
    event_id: str,
    event_start_utc: str,
    observed_at_utc: str,
) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "artifact": artifact,
        "artifact_hash": artifact["artifact_hash"],
        "model_version": artifact["model_version"],
        "engine_source_sha256": engine_hash(artifact["method"]),
        "features": features,
        "inference_inputs": inference_inputs,
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "observed_at_utc": observed_at_utc,
        "historical_source_observation_times_verified": False,
        "replay_scope": "Exact final classifier/distribution inputs and artifact. Upstream historical feature construction not certified.",
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload["snapshot_hash"] = snapshot_hash(payload)
    replay_snapshot(payload)
    return payload


def validate_decision_snapshot(payload: dict[str, Any], row: dict[str, Any]) -> float:
    values = replay_snapshot(payload)
    artifact = payload["artifact"]
    method = artifact["method"]
    market = {
        "first_inning_logistic": "nrfi",
        "half_inning_logistic": "nrfi",
        "half_inning_interaction": "nrfi",
        "margin_normal": "spread",
        "nfl_structural_v5_margin_engine": "spread",
        "total_normal": "total",
        "nfl_structural_v5_total_engine": "total",
        "nfl_spread_structural_v6_engine": "spread",
        "nfl_spread_structural_v7_engine": "spread",
        "nfl_total_structural_v6_engine": "total",
        "nfl_total_structural_v7_engine": "total",
    }[method]
    if (
        row.get("model_version") != artifact["model_version"]
        or payload["model_version"] != artifact["model_version"]
        or row.get("model_artifact_hash") != artifact["artifact_hash"]
        or str(row.get("league")).lower() != artifact["sport"]
        or row.get("market_type") != market
        or row.get("selection") not in values
        or str(row.get("event_id")) != payload["event_id"]
        or parse_utc(str(row.get("event_start_utc"))) != parse_utc(payload["event_start_utc"])
        or parse_utc(payload["observed_at_utc"]) >= parse_utc(payload["event_start_utc"])
    ):
        raise ValueError("scalar_replay_decision_identity_mismatch")
    line = (
        0.5
        if market == "nrfi"
        else payload["inference_inputs"]["line"]
        * (-1 if market == "spread" and row["selection"] == "home" else 1)
    )
    if float(row["line"]) != line:
        raise ValueError("scalar_replay_line_mismatch")
    expected = values[row["selection"]]
    calibration_method = str(row.get("calibration_method") or "").lower()
    if calibration_method in {"beta", "platt", "temperature", "isotonic"}:
        filename = f"{artifact['model_version']}.{market}_calibrator.json"
        paths = (
            RuntimePaths.resolve(repo_root=PROJECT_ROOT).runtime_root / "calibration" / "models" / filename,
            PROJECT_ROOT / "data" / "calibration" / "models" / filename,
        )
        calibrator = None
        for path in paths:
            if not path.is_file():
                continue
            candidate = load_calibrator_artifact(path)
            metadata = candidate.metadata
            if (
                metadata.artifact_hash == row.get("calibration_artifact_hash")
                and metadata.calibration_method == calibration_method
                and metadata.base_model_version == artifact["model_version"]
            ):
                calibrator = candidate
                break
        if calibrator is None:
            raise ValueError("scalar_replay_calibration_artifact_hash_mismatch")
        expected = calibrator.transform(expected)
    stored = float(row["model_probability"])
    if not math.isfinite(stored) or abs(stored - expected) > 2e-6:
        raise ValueError("scalar_replay_probability_mismatch")
    return expected
