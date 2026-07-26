"""Current-production leave-one-feature-out evaluation.

This runner is deliberately narrow: it freezes every configured production
artifact, reuses the repository's existing chronological split, and compares a
matched refit of the active feature set with one refit per omitted feature.  It
never writes or changes an active model artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from sklearn.linear_model import LogisticRegression

from .config import load_config
from .esports import NeutralElo, _load_matches, _predict
from .features.base import FeatureStore
from .models.learned_market import LearnedMarketArtifact, artifact_hash
from .validation import ValidationRow, build_walk_forward_rows

SCORE_SPORTS = ("mlb", "nba", "wnba", "soccer", "nfl")
ESPORTS_TITLES = ("lol", "cs2", "dota2", "valorant")
MINIMUM_MEANINGFUL_BRIER_DELTA = 0.001
BOOTSTRAP_RESAMPLES = 2_000
RANDOMIZATION_RESAMPLES = 5_000
COEFFICIENT_REPRODUCTION_TOLERANCE = 1e-8
METRIC_REPRODUCTION_TOLERANCE = 1e-6


def _probability_metrics(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, Any]:
    clipped = [min(1 - 1e-9, max(1e-9, float(value))) for value in probabilities]
    correct = sum((value >= 0.5) == bool(outcome) for value, outcome in zip(clipped, outcomes, strict=True))
    return {
        "observations": len(outcomes),
        "accuracy": round(correct / len(outcomes), 6),
        "brier_score": round(mean((value - outcome) ** 2 for value, outcome in zip(clipped, outcomes, strict=True)), 6),
        "log_loss": round(-mean(outcome * math.log(value) + (1 - outcome) * math.log(1 - value) for value, outcome in zip(clipped, outcomes, strict=True)), 6),
    }


def _call_metrics(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    confidence_threshold: float,
) -> dict[str, Any]:
    selected = [
        (max(value, 1 - value), int((value >= 0.5) == bool(outcome)))
        for value, outcome in zip(probabilities, outcomes, strict=True)
        if max(value, 1 - value) >= confidence_threshold
    ]
    calls = len(selected)
    hits = sum(item[1] for item in selected)
    confidences = [item[0] for item in selected]
    correctness = [item[1] for item in selected]
    return {
        "confidence_threshold": round(confidence_threshold, 8),
        "calls": calls,
        "hits": hits,
        "hit_rate": round(hits / calls, 6) if calls else None,
        "brier_score": (
            round(mean((value - outcome) ** 2 for value, outcome in zip(confidences, correctness, strict=True)), 6)
            if calls
            else None
        ),
        "log_loss": (
            round(-mean(outcome * math.log(value) + (1 - outcome) * math.log(1 - value) for value, outcome in zip(confidences, correctness, strict=True)), 6)
            if calls
            else None
        ),
        "diagnostic_units_at_minus_110": round(hits * (10 / 11) - (calls - hits), 6),
    }


def _fit_binary(rows: Sequence[ValidationRow], features: Sequence[str]) -> tuple[Any, dict[str, Any]]:
    outcomes = [row.outcome for row in rows]
    if not features:
        probability = min(1 - 1e-9, max(1e-9, mean(outcomes)))
        return (lambda evaluation: [probability] * len(evaluation)), {
            "features": [],
            "intercept": round(math.log(probability / (1 - probability)), 10),
            "coefficients": {},
        }
    model = LogisticRegression(max_iter=2_000, solver="lbfgs")
    model.fit([[float(getattr(row, feature)) for feature in features] for row in rows], outcomes)

    def predict(evaluation: Sequence[ValidationRow]) -> list[float]:
        matrix = [[float(getattr(row, feature)) for feature in features] for row in evaluation]
        return [float(pair[1]) for pair in model.predict_proba(matrix)]

    return predict, {
        "features": list(features),
        "intercept": round(float(model.intercept_[0]), 10),
        "coefficients": {
            feature: round(float(value), 10)
            for feature, value in zip(features, model.coef_[0], strict=True)
        },
    }


def _paired_uncertainty(
    baseline: Sequence[float],
    candidate: Sequence[float],
    outcomes: Sequence[int],
    dates: Sequence[str],
    *,
    seed: int,
) -> dict[str, Any]:
    clusters: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for base, challenger, outcome, day in zip(baseline, candidate, outcomes, dates, strict=True):
        base = min(1 - 1e-9, max(1e-9, base))
        challenger = min(1 - 1e-9, max(1e-9, challenger))
        brier = (challenger - outcome) ** 2 - (base - outcome) ** 2
        log_loss = -(outcome * math.log(challenger) + (1 - outcome) * math.log(1 - challenger))
        log_loss += outcome * math.log(base) + (1 - outcome) * math.log(1 - base)
        clusters[day].append((brier, log_loss))
    days = sorted(clusters)
    summaries = {
        day: (sum(value[0] for value in clusters[day]), sum(value[1] for value in clusters[day]), len(clusters[day]))
        for day in days
    }
    observed_brier = sum(value[0] for value in summaries.values()) / len(outcomes)
    observed_log_loss = sum(value[1] for value in summaries.values()) / len(outcomes)
    rng = random.Random(seed)
    brier_samples: list[float] = []
    log_loss_samples: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [summaries[rng.choice(days)] for _ in days]
        count = sum(value[2] for value in sample)
        brier_samples.append(sum(value[0] for value in sample) / count)
        log_loss_samples.append(sum(value[1] for value in sample) / count)
    brier_samples.sort()
    log_loss_samples.sort()
    low = int(0.025 * (BOOTSTRAP_RESAMPLES - 1))
    high = int(0.975 * (BOOTSTRAP_RESAMPLES - 1))
    cluster_sums = [summaries[day][0] for day in days]
    observed_sum = abs(sum(cluster_sums))
    extreme = 0
    for _ in range(RANDOMIZATION_RESAMPLES):
        randomized = sum(value if rng.getrandbits(1) else -value for value in cluster_sums)
        extreme += int(abs(randomized) >= observed_sum)
    return {
        "unit": "event_date_cluster",
        "dates": len(days),
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "randomization_resamples": RANDOMIZATION_RESAMPLES,
        "candidate_minus_baseline": {
            "brier_score": round(observed_brier, 8),
            "brier_ci_95": [round(brier_samples[low], 8), round(brier_samples[high], 8)],
            "log_loss": round(observed_log_loss, 8),
            "log_loss_ci_95": [round(log_loss_samples[low], 8), round(log_loss_samples[high], 8)],
        },
        "paired_date_sign_flip_p_value": round((extreme + 1) / (RANDOMIZATION_RESAMPLES + 1), 8),
    }


def _holm(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: item[1])
    result: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * value))
        result[name] = round(running, 8)
    return result


def _provenance(feature: str) -> dict[str, Any]:
    if feature == "park_factor":
        return {"status": "blocked", "reason": "2025-three-year static table is applied retroactively across seasons"}
    if feature == "weather_factor":
        return {"status": "blocked", "reason": "historical weather cache has no forecast issue or observed_at timestamp"}
    return {
        "status": "development_only",
        "reason": "constructed from completed events strictly before the prediction date; legacy source rows lack observed_at_utc",
    }


def _frozen_score_split(
    rows: Sequence[ValidationRow], training: Mapping[str, Any]
) -> tuple[list[ValidationRow], list[ValidationRow], list[ValidationRow], dict[str, Any]]:
    declarations = {
        "train": training["coefficient_fit"],
        "validation": training["threshold_selection"],
        "locked_holdout": training["locked_holdout"],
    }
    cohorts = {
        name: [row for row in rows if declared["start"] <= row.date <= declared["end"]]
        for name, declared in declarations.items()
    }
    for name, cohort in cohorts.items():
        declared = declarations[name]
        actual = {
            "start": cohort[0].date if cohort else None,
            "end": cohort[-1].date if cohort else None,
            "observations": len(cohort),
        }
        if actual != declared:
            raise ValueError(f"frozen split drift for {name}: {actual} != {declared}")
    metadata = {"method": "artifact_frozen_complete_date_60_20_20", **declarations}
    return cohorts["train"], cohorts["validation"], cohorts["locked_holdout"], metadata


def _reproduction_gate(
    artifact: LearnedMarketArtifact,
    baseline_fit: Mapping[str, Any],
    baseline_calls: Mapping[str, Any],
) -> dict[str, Any]:
    active = artifact.raw["market_models"]["moneyline"]
    coefficient_deltas = {
        feature: round(float(baseline_fit["coefficients"][feature]) - float(coefficient), 12)
        for feature, coefficient in zip(active["feature_names"], active["coefficients"], strict=True)
    }
    intercept_delta = round(float(baseline_fit["intercept"]) - float(active["intercept"]), 12)
    qualification = artifact.raw.get("qualification", {})
    stored_log_loss = (qualification.get("calibration") or {}).get("log_loss")
    metric_deltas = {
        "calls": int(baseline_calls["calls"]) - int(qualification.get("calls", -1)),
        "hits": int(baseline_calls["hits"]) - int(qualification.get("hits", -1)),
        "brier_score": (
            round(float(baseline_calls["brier_score"]) - float(qualification["brier_score"]), 12)
            if baseline_calls["brier_score"] is not None and qualification.get("brier_score") is not None
            else None
        ),
        "log_loss": (
            round(float(baseline_calls["log_loss"]) - float(stored_log_loss), 12)
            if baseline_calls["log_loss"] is not None and stored_log_loss is not None
            else None
        ),
    }
    failures = []
    if any(abs(value) > COEFFICIENT_REPRODUCTION_TOLERANCE for value in coefficient_deltas.values()):
        failures.append("coefficient delta exceeds tolerance")
    if abs(intercept_delta) > COEFFICIENT_REPRODUCTION_TOLERANCE:
        failures.append("intercept delta exceeds tolerance")
    if metric_deltas["calls"] != 0 or metric_deltas["hits"] != 0:
        failures.append("holdout calls or hits do not reproduce exactly")
    for metric in ("brier_score", "log_loss"):
        value = metric_deltas[metric]
        if value is None or abs(value) > METRIC_REPRODUCTION_TOLERANCE:
            failures.append(f"holdout {metric} missing or exceeds tolerance")
    return {
        "passed": not failures,
        "coefficient_tolerance": COEFFICIENT_REPRODUCTION_TOLERANCE,
        "metric_tolerance": METRIC_REPRODUCTION_TOLERANCE,
        "coefficient_deltas_matched_refit_minus_artifact": coefficient_deltas,
        "intercept_delta_matched_refit_minus_artifact": intercept_delta,
        "holdout_metric_deltas_matched_refit_minus_artifact": metric_deltas,
        "failures": failures,
    }


def _score_sport(config: Mapping[str, Any], sport: str, store: FeatureStore) -> dict[str, Any]:
    artifact_path = Path(config["models"][sport.upper()]["production_artifact"])
    artifact = LearnedMarketArtifact.load(artifact_path)
    active = artifact.raw["market_models"]["moneyline"]
    features = tuple(active["feature_names"])
    source_path = store.processed_path(sport).resolve()
    if not source_path.exists():
        return {
            "status": "UNTESTABLE_SOURCE_SNAPSHOT_UNIDENTIFIED",
            "artifact_path": str(artifact_path),
            "artifact_version": artifact.version,
            "artifact_hash": artifact.hash,
            "production_features": list(features),
            "source_evidence": {"path": str(source_path), "exists": False},
            "reason": "explicit processed source file does not exist",
        }
    raw_rows = sum(1 for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip())
    loaded_games = store.load_games(sport)
    rows = build_walk_forward_rows(store, sport)
    source_evidence = {
        "path": str(source_path),
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "raw_nonempty_rows": raw_rows,
        "loaded_modeling_games": len(loaded_games),
        "walk_forward_rows": len(rows),
    }
    try:
        train, validation, holdout, split = _frozen_score_split(
            rows, artifact.raw["training"]
        )
    except ValueError as error:
        return {
            "status": "UNTESTABLE_SOURCE_SNAPSHOT_UNIDENTIFIED",
            "artifact_path": str(artifact_path),
            "artifact_version": artifact.version,
            "artifact_hash": artifact.hash,
            "production_features": list(features),
            "declared_training": artifact.raw["training"],
            "source_evidence": source_evidence,
            "reason": str(error),
        }
    outcomes_validation = [row.outcome for row in validation]
    outcomes_holdout = [row.outcome for row in holdout]
    threshold = float(active["confidence_threshold"])
    active_validation = [artifact.probability("moneyline", {feature: float(getattr(row, feature)) for feature in features}) for row in validation]
    active_holdout = [artifact.probability("moneyline", {feature: float(getattr(row, feature)) for feature in features}) for row in holdout]
    baseline_predict, baseline_fit = _fit_binary(train, features)
    baseline_validation = baseline_predict(validation)
    baseline_holdout = baseline_predict(holdout)
    baseline_call_metrics = _call_metrics(
        baseline_holdout, outcomes_holdout, confidence_threshold=threshold
    )
    reproduction = _reproduction_gate(artifact, baseline_fit, baseline_call_metrics)
    if not reproduction["passed"]:
        return {
            "status": "UNTESTABLE_REPRODUCTION_GATE_FAILED",
            "artifact_path": str(artifact_path),
            "artifact_version": artifact.version,
            "artifact_hash": artifact.hash,
            "production_features": list(features),
            "split": split,
            "source_evidence": source_evidence,
            "reproduction_gate": reproduction,
            "reason": "; ".join(reproduction["failures"]),
        }
    leave_one_out: dict[str, Any] = {}
    for feature in features:
        remaining = tuple(name for name in features if name != feature)
        predict, fit = _fit_binary(train, remaining)
        validation_probabilities = predict(validation)
        holdout_probabilities = predict(holdout)
        key = f"{sport}:{feature}"
        uncertainty = _paired_uncertainty(
            baseline_holdout,
            holdout_probabilities,
            outcomes_holdout,
            [row.date for row in holdout],
            seed=int(hashlib.sha256(key.encode()).hexdigest()[:8], 16),
        )
        leave_one_out[feature] = {
            "comparison_key": key,
            "omitted_feature": feature,
            "remaining_features": list(remaining),
            "fit": fit,
            "validation": {
                "all_predictions": _probability_metrics(validation_probabilities, outcomes_validation),
                "at_frozen_production_threshold": _call_metrics(validation_probabilities, outcomes_validation, confidence_threshold=threshold),
            },
            "locked_holdout": {
                "all_predictions": _probability_metrics(holdout_probabilities, outcomes_holdout),
                "at_frozen_production_threshold": _call_metrics(holdout_probabilities, outcomes_holdout, confidence_threshold=threshold),
            },
            "validation_brier_delta": round(_probability_metrics(validation_probabilities, outcomes_validation)["brier_score"] - _probability_metrics(baseline_validation, outcomes_validation)["brier_score"], 6),
            "paired_uncertainty": uncertainty,
            "raw_p_value": uncertainty["paired_date_sign_flip_p_value"],
            "provenance": _provenance(feature),
        }
    return {
        "status": "evaluated",
        "model_family": "logistic_regression",
        "artifact_path": str(artifact_path),
        "artifact_version": artifact.version,
        "artifact_hash": artifact.hash,
        "target": "home_win_binary" if sport != "soccer" else "home_win_vs_draw_or_away_binary",
        "production_features": list(features),
        "split": split,
        "source_evidence": source_evidence,
        "reproduction_gate": reproduction,
        "frozen_threshold": threshold,
        "exact_active_artifact": {
            "validation": _probability_metrics(active_validation, outcomes_validation),
            "locked_holdout": {
                "all_predictions": _probability_metrics(active_holdout, outcomes_holdout),
                "at_frozen_production_threshold": _call_metrics(active_holdout, outcomes_holdout, confidence_threshold=threshold),
            },
        },
        "matched_refit_baseline": {
            "fit": baseline_fit,
            "validation": _probability_metrics(baseline_validation, outcomes_validation),
            "locked_holdout": {
                "all_predictions": _probability_metrics(baseline_holdout, outcomes_holdout),
                "at_frozen_production_threshold": _call_metrics(baseline_holdout, outcomes_holdout, confidence_threshold=threshold),
            },
        },
        "leave_one_out": leave_one_out,
    }


def _esports_model(config: Mapping[str, Any], title: str) -> dict[str, Any]:
    model_config = config["models"][title.upper()]
    artifact_path = Path(model_config["production_artifact"])
    matches_path = Path("data") / "esports" / title / "matches.jsonl"
    expected_version = model_config.get("active_production_version")
    source_not_inspected = {
        "path": matches_path.as_posix(),
        "inspection_status": "not_inspected_due_to_artifact_integrity",
    }
    try:
        decoded = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {
            "status": "UNTESTABLE_ARTIFACT_INTEGRITY",
            "artifact_path": str(artifact_path),
            "artifact_version": None,
            "artifact_hash": None,
            "production_features": ["neutral_elo_rating_difference"],
            "source_evidence": source_not_inspected,
            "artifact_integrity": {
                "passed": False,
                "stored_hash": None,
                "computed_hash": None,
                "expected_title": title,
                "artifact_title": None,
                "expected_version": expected_version,
                "artifact_version": None,
                "failures": [f"artifact JSON invalid or unreadable: {error}"],
            },
            "reason": f"artifact JSON invalid or unreadable: {error}",
        }
    if not isinstance(decoded, Mapping):
        failures = ["artifact JSON root must be an object"]
        return {
            "status": "UNTESTABLE_ARTIFACT_INTEGRITY",
            "artifact_path": str(artifact_path),
            "artifact_version": None,
            "artifact_hash": None,
            "production_features": ["neutral_elo_rating_difference"],
            "source_evidence": source_not_inspected,
            "artifact_integrity": {
                "passed": False,
                "stored_hash": None,
                "computed_hash": None,
                "expected_title": title,
                "artifact_title": None,
                "expected_version": expected_version,
                "artifact_version": None,
                "failures": failures,
            },
            "reason": "; ".join(failures),
        }
    artifact = dict(decoded)
    stored_hash = artifact.get("artifact_hash")
    computed_hash = artifact_hash(artifact)
    artifact_title = artifact.get("title")
    artifact_version = artifact.get("model_version")
    failures = []
    if not stored_hash:
        failures.append("artifact_hash is missing")
    elif stored_hash != computed_hash:
        failures.append("artifact_hash does not match canonical artifact content")
    if artifact_title != title:
        failures.append(f"artifact title {artifact_title!r} does not match configured title {title!r}")
    if artifact_version != expected_version:
        failures.append(
            f"artifact version {artifact_version!r} does not match configured active version {expected_version!r}"
        )
    if failures:
        return {
            "status": "UNTESTABLE_ARTIFACT_INTEGRITY",
            "artifact_path": str(artifact_path),
            "artifact_version": artifact_version,
            "artifact_hash": stored_hash,
            "production_features": ["neutral_elo_rating_difference"],
            "source_evidence": source_not_inspected,
            "artifact_integrity": {
                "passed": False,
                "stored_hash": stored_hash,
                "computed_hash": computed_hash,
                "expected_title": title,
                "artifact_title": artifact_title,
                "expected_version": expected_version,
                "artifact_version": artifact_version,
                "failures": failures,
            },
            "reason": "; ".join(failures),
        }
    if hashlib.sha256(matches_path.read_bytes()).hexdigest() != artifact["matches_sha256"]:
        raise ValueError(f"{title} match data drifted from production artifact")
    rows = _load_matches(matches_path)
    source_evidence = {
        "path": matches_path.as_posix(),
        "sha256": hashlib.sha256(matches_path.read_bytes()).hexdigest(),
        "raw_nonempty_rows": len(rows),
        "loaded_modeling_games": len(rows),
        "walk_forward_rows": len(rows),
    }
    if not isinstance(artifact.get("locked_test"), Mapping):
        return {
            "status": "UNTESTABLE_REPRODUCTION_EVIDENCE_MISSING",
            "artifact_path": str(artifact_path),
            "artifact_version": artifact["model_version"],
            "artifact_hash": artifact["artifact_hash"],
            "production_features": ["neutral_elo_rating_difference"],
            "source_evidence": source_evidence,
            "reproduction_gate": {
                "passed": False,
                "failures": ["active artifact does not pin locked-test metrics and calls"],
            },
            "reason": "active artifact does not pin locked-test metrics and calls",
        }
    train_end = int(len(rows) * 0.60)
    validation_end = int(len(rows) * 0.80)
    train, validation, holdout = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    k = float(artifact["k"])
    validation_book = NeutralElo(k=k, ratings={})
    _predict(validation_book, train)
    validation_predictions = _predict(validation_book, validation)
    holdout_book = NeutralElo(k=k, ratings={})
    _predict(holdout_book, [*train, *validation])
    holdout_predictions = _predict(holdout_book, holdout)
    baseline_validation = [float(row["probability"]) for row in validation_predictions]
    baseline_holdout = [float(row["probability"]) for row in holdout_predictions]
    validation_outcomes = [int(row["outcome"]) for row in validation_predictions]
    holdout_outcomes = [int(row["outcome"]) for row in holdout_predictions]
    validation_intercept = mean(1 if row["winner_id"] == row["team1_id"] else 0 for row in train)
    holdout_intercept = mean(1 if row["winner_id"] == row["team1_id"] else 0 for row in [*train, *validation])
    candidate_validation = [validation_intercept] * len(validation)
    candidate_holdout = [holdout_intercept] * len(holdout)
    threshold = 0.5 + float(artifact["confidence_threshold"])
    feature = "neutral_elo_rating_difference"
    key = f"{title}:{feature}"
    uncertainty = _paired_uncertainty(
        baseline_holdout,
        candidate_holdout,
        holdout_outcomes,
        [row["start_utc"][:10] for row in holdout],
        seed=int(hashlib.sha256(key.encode()).hexdigest()[:8], 16),
    )
    split = {
        "method": "existing_row_order_60_20_20",
        "train": {"start": train[0]["start_utc"], "end": train[-1]["start_utc"], "observations": len(train)},
        "validation": {"start": validation[0]["start_utc"], "end": validation[-1]["start_utc"], "observations": len(validation)},
        "locked_holdout": {"start": holdout[0]["start_utc"], "end": holdout[-1]["start_utc"], "observations": len(holdout)},
    }
    leave_one_out = {
        feature: {
            "comparison_key": key,
            "omitted_feature": feature,
            "remaining_features": [],
            "fit": {"method": "train_cohort_intercept_only", "validation_probability": round(validation_intercept, 10), "holdout_probability": round(holdout_intercept, 10)},
            "validation": {
                "all_predictions": _probability_metrics(candidate_validation, validation_outcomes),
                "at_frozen_production_threshold": _call_metrics(candidate_validation, validation_outcomes, confidence_threshold=threshold),
            },
            "locked_holdout": {
                "all_predictions": _probability_metrics(candidate_holdout, holdout_outcomes),
                "at_frozen_production_threshold": _call_metrics(candidate_holdout, holdout_outcomes, confidence_threshold=threshold),
            },
            "validation_brier_delta": round(_probability_metrics(candidate_validation, validation_outcomes)["brier_score"] - _probability_metrics(baseline_validation, validation_outcomes)["brier_score"], 6),
            "paired_uncertainty": uncertainty,
            "raw_p_value": uncertainty["paired_date_sign_flip_p_value"],
            "provenance": _provenance(feature),
        }
    }
    return {
        "status": "evaluated",
        "model_family": "neutral_series_elo",
        "artifact_path": str(artifact_path),
        "artifact_version": artifact["model_version"],
        "artifact_hash": artifact["artifact_hash"],
        "target": "team1_series_win_binary",
        "production_features": [feature],
        "split": split,
        "source_evidence": source_evidence,
        "frozen_threshold": threshold,
        "exact_active_artifact": {"note": "historical predictions reconstructed point-in-time with frozen artifact K; terminal ratings were not backcast"},
        "matched_refit_baseline": {
            "fit": {"k": k, "initial_rating": artifact["initial_rating"], "home_or_order_advantage": artifact["home_or_order_advantage"]},
            "validation": _probability_metrics(baseline_validation, validation_outcomes),
            "locked_holdout": {
                "all_predictions": _probability_metrics(baseline_holdout, holdout_outcomes),
                "at_frozen_production_threshold": _call_metrics(baseline_holdout, holdout_outcomes, confidence_threshold=threshold),
            },
        },
        "leave_one_out": leave_one_out,
    }


def _decision(result: Mapping[str, Any], adjusted_p_value: float) -> tuple[str, str]:
    provenance = result["provenance"]
    if provenance["status"] == "blocked":
        return "REMOVE CANDIDATE", provenance["reason"]
    delta = result["paired_uncertainty"]["candidate_minus_baseline"]
    low, high = delta["brier_ci_95"]
    validation_delta = float(result["validation_brier_delta"])
    if validation_delta > 0 and delta["brier_score"] >= MINIMUM_MEANINGFUL_BRIER_DELTA and delta["log_loss"] > 0 and low > 0 and adjusted_p_value <= 0.05:
        return "KEEP", "removal worsened validation and holdout proper scores with multiplicity-adjusted paired evidence"
    if validation_delta < 0 and delta["brier_score"] <= -MINIMUM_MEANINGFUL_BRIER_DELTA and delta["log_loss"] < 0 and high < 0 and adjusted_p_value <= 0.05:
        return "REMOVE CANDIDATE", "removal improved validation and holdout proper scores with multiplicity-adjusted paired evidence"
    return "INCONCLUSIVE", "predeclared removal or retention gate not cleared"


def build_report(data_root: str | Path = "data") -> dict[str, Any]:
    config = load_config()
    store = FeatureStore(Path(data_root).resolve())
    models = {sport: _score_sport(config, sport, store) for sport in SCORE_SPORTS}
    models.update({title: _esports_model(config, title) for title in ESPORTS_TITLES})
    raw = {
        result["comparison_key"]: float(result["raw_p_value"])
        for model in models.values()
        if model["status"] == "evaluated"
        for result in model["leave_one_out"].values()
    }
    adjusted = _holm(raw)
    for model in models.values():
        if model["status"] != "evaluated":
            continue
        for result in model["leave_one_out"].values():
            result["holm_adjusted_p_value"] = adjusted[result["comparison_key"]]
            result["decision"], result["decision_reason"] = _decision(result, result["holm_adjusted_p_value"])
    payload: dict[str, Any] = {
        "schema_version": "production-feature-ablation-v1",
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "as_of_date": "2026-07-22",
        "scope": "configured production_artifact entries only; leave one active feature out at a time",
        "explicit_score_data_root": str(Path(data_root).resolve()),
        "promotion_eligible": False,
        "holdout_status": "reused_locked_holdouts_development_evidence",
        "market_prices_used": False,
        "economic_claims_allowed": False,
        "diagnostic_units": "flat one-unit staking at synthetic -110; non-executable",
        "predeclared_gates": {
            "reproduction_prerequisite": "matched full-feature refit must reproduce artifact coefficients/intercept within 1e-8, stored selected-holdout Brier/log loss within 1e-6, and calls/hits exactly",
            "primary_metric": "all-prediction locked-holdout Brier candidate minus matched-refit baseline",
            "minimum_meaningful_absolute_brier_delta": MINIMUM_MEANINGFUL_BRIER_DELTA,
            "keep": "validation delta > 0, holdout Brier delta >= 0.001, holdout log-loss delta > 0, Brier 95% CI > 0, Holm p <= 0.05",
            "remove_candidate": "symmetric evidence favoring removal, or a point-in-time provenance blocker",
            "otherwise": "INCONCLUSIVE",
            "multiplicity": f"Holm family across all {len(raw)} production-feature omissions",
        },
        "models": models,
    }
    payload["artifact_hash"] = artifact_hash(payload)
    return payload


def build_markdown(report: Mapping[str, Any]) -> str:
    decisions = [
        (sport, model, feature, result)
        for sport, model in report["models"].items()
        if model["status"] == "evaluated"
        for feature, result in model["leave_one_out"].items()
    ]
    counts = {name: sum(result["decision"] == name for _, _, _, result in decisions) for name in ("KEEP", "REMOVE CANDIDATE", "INCONCLUSIVE")}
    evaluated_models = sum(model["status"] == "evaluated" for model in report["models"].values())
    lines = [
        "# Production feature ablation — 2026-07-22",
        "",
        "## Bottom line",
        "",
        f"Across {len(decisions)} testable active features in {evaluated_models} reproduced models ({len(report['models'])} configured production artifacts): **{counts['KEEP']} KEEP**, **{counts['REMOVE CANDIDATE']} REMOVE CANDIDATE**, and **{counts['INCONCLUSIVE']} INCONCLUSIVE**.",
        "",
        "This is development evidence, not a promotion or profit study. The locked cohorts have been reused, legacy score rows lack `observed_at_utc`, and no point-in-time executable prices, fees, or CLV were used. Flat `-110` units below are non-executable diagnostics only.",
        "",
        "## Predeclared decision rule",
        "",
        "The primary comparison is leave-one-out minus the matched refit on all locked-holdout predictions. KEEP requires removal to worsen validation Brier, worsen holdout Brier by at least 0.001, worsen holdout log loss, produce a date-cluster 95% Brier interval above zero, and survive Holm adjustment at 0.05. REMOVE CANDIDATE is symmetric, or follows directly from a point-in-time provenance blocker. Everything else is INCONCLUSIVE.",
        "",
    ]
    untestable = [(sport, model) for sport, model in report["models"].items() if model["status"] != "evaluated"]
    if untestable:
        lines.extend([
            "## Untestable production models",
            "",
            "These models were excluded from inference and multiplicity adjustment because the source or required reproduction evidence was not uniquely established.",
            "A model is excluded when the full-feature refit does not reproduce the artifact or the artifact does not pin the holdout evidence required to test reproduction.",
            "",
            "| Model | Status | Explicit source | SHA-256 | Raw / loaded / walk-forward rows | Reason |",
            "|---|---|---|---|---:|---|",
        ])
        for sport, model in untestable:
            source = model["source_evidence"]
            counts_text = f"{source.get('raw_nonempty_rows', '—')} / {source.get('loaded_modeling_games', '—')} / {source.get('walk_forward_rows', '—')}"
            lines.append(f"| {sport.upper()} | `{model['status']}` | `{source['path']}` | `{source.get('sha256', '—')}` | {counts_text} | {model['reason']} |")
        lines.append("")
    lines.extend([
        "## Feature decisions",
        "",
        "| Model | Active feature omitted | Decision | Val Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for sport, _, feature, result in decisions:
        delta = result["paired_uncertainty"]["candidate_minus_baseline"]
        ci = delta["brier_ci_95"]
        lines.append(
            f"| {sport.upper()} | `{feature}` | **{result['decision']}** | {result['validation_brier_delta']:+.6f} | {delta['brier_score']:+.6f} | {delta['log_loss']:+.6f} | [{ci[0]:+.6f}, {ci[1]:+.6f}] | {result['raw_p_value']:.4f} | {result['holm_adjusted_p_value']:.4f} |"
        )
    for sport, model in report["models"].items():
        if model["status"] != "evaluated":
            continue
        baseline = model["matched_refit_baseline"]
        holdout = baseline["locked_holdout"]
        calls = holdout["at_frozen_production_threshold"]
        reproduction = model["reproduction_gate"]
        coefficient_deltas = reproduction["coefficient_deltas_matched_refit_minus_artifact"]
        max_coefficient_delta = max(abs(value) for value in coefficient_deltas.values())
        metric_deltas = reproduction["holdout_metric_deltas_matched_refit_minus_artifact"]
        lines.extend([
            "",
            f"## {sport.upper()} — `{model['artifact_version']}`",
            "",
            f"Split: {model['split']['train']['observations']} train / {model['split']['validation']['observations']} validation / {model['split']['locked_holdout']['observations']} locked holdout. Active features: " + ", ".join(f"`{item}`" for item in model["production_features"]) + ".",
            f"Source: `{model['source_evidence']['path']}`; SHA-256 `{model['source_evidence']['sha256']}`; raw / loaded / walk-forward rows: {model['source_evidence']['raw_nonempty_rows']} / {model['source_evidence']['loaded_modeling_games']} / {model['source_evidence']['walk_forward_rows']}.",
            f"Reproduction gate: **PASS**. Maximum absolute coefficient delta `{max_coefficient_delta:.12g}`; intercept delta `{reproduction['intercept_delta_matched_refit_minus_artifact']:+.12g}`; calls / hits deltas `{metric_deltas['calls']}` / `{metric_deltas['hits']}`; Brier / log-loss deltas `{metric_deltas['brier_score']:+.12g}` / `{metric_deltas['log_loss']:+.12g}`. Tolerances: coefficients/intercept `{reproduction['coefficient_tolerance']}`, metrics `{reproduction['metric_tolerance']}`.",
            "",
            "| Baseline | Observations | Accuracy | Brier | Log loss | Calls | Hit rate | -110 units |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            f"| Matched refit | {holdout['all_predictions']['observations']} | {holdout['all_predictions']['accuracy']:.4f} | {holdout['all_predictions']['brier_score']:.6f} | {holdout['all_predictions']['log_loss']:.6f} | {calls['calls']} | {calls['hit_rate'] if calls['hit_rate'] is not None else '—'} | {calls['diagnostic_units_at_minus_110']:.2f} |",
            "",
        ])
        for feature, result in model["leave_one_out"].items():
            metric = result["locked_holdout"]["all_predictions"]
            called = result["locked_holdout"]["at_frozen_production_threshold"]
            lines.append(
                f"- Omit `{feature}`: {metric['observations']} observations, {metric['accuracy']:.2%} accuracy, Brier {metric['brier_score']:.6f}, log loss {metric['log_loss']:.6f}; {called['calls']} calls, {called['hit_rate'] if called['hit_rate'] is not None else '—'} hit rate, {called['diagnostic_units_at_minus_110']:+.2f} diagnostic units. **{result['decision']}** — {result['decision_reason']}"
            )
    lines.extend([
        "",
        "## Multiplicity and economic boundary",
        "",
        f"Holm correction covers all {len(decisions)} feature omissions. These reused holdouts can rank removal hypotheses, but they cannot certify a promoted model. No ROI, EV, profitability, or tradability claim is made; that requires point-in-time executable asks on both sides, fees/friction, and CLV on a fresh prospective cohort.",
        "",
        f"Reproducibility hash: `{report['artifact_hash']}`.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", default="config/models/production-feature-ablation-2026-07-22.json")
    parser.add_argument("--markdown-output", default="docs/PRODUCTION_FEATURE_ABLATION_2026-07-22.md")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()
    report = build_report(args.data_root)
    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(build_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
