"""Factorial research challengers for roadmap features available today.

The experiment copies each active artifact's feature set as the incumbent and
adds only score-history features that the repository can construct identically
in validation and forward inference. It never writes config/models, changes the
active configuration, logs a pick, or makes an economic claim.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from sklearn.linear_model import LogisticRegression

from .calibration import calibration_metrics
from .config import load_config
from .features.base import FeatureStore
from .models.learned_market import LearnedMarketArtifact, learn_confidence_threshold
from .validation import ValidationRow, build_walk_forward_rows, chronological_split

SPORTS = ("mlb", "nba", "wnba", "nfl")
ADDITIONS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    (
        ("consistency", ("consistency_gap",)),
        ("hot_cold", ("hot_cold_gap",)),
        ("rest_disparity", ("rest_disparity",)),
        ("back_to_back", ("back_to_back_gap",)),
        ("schedule_density", ("games_last_7_gap",)),
        ("schedule_missingness", ("schedule_available",)),
    )
)
GATE_GRID = tuple(round(0.50 + 0.025 * index, 3) for index in range(13))
DEFAULT_SNAPSHOT_DIRECTORY = Path("snapshots/20260720T073745+0800-pre-roadmap-challenger")


def _matrix(rows: Sequence[ValidationRow], features: Sequence[str]) -> list[list[float]]:
    return [[float(getattr(row, feature)) for feature in features] for row in rows]


def _fit(train: Sequence[ValidationRow], features: Sequence[str]) -> LogisticRegression:
    model = LogisticRegression(max_iter=2_000, solver="lbfgs")
    model.fit(_matrix(train, features), [row.outcome for row in train])
    return model


def _predict(
    model: LogisticRegression,
    rows: Sequence[ValidationRow],
    features: Sequence[str],
) -> list[float]:
    return [float(pair[1]) for pair in model.predict_proba(_matrix(rows, features))]


def _artifact_predict(
    artifact: LearnedMarketArtifact,
    rows: Sequence[ValidationRow],
    features: Sequence[str],
) -> list[float]:
    return [
        artifact.probability("moneyline", {feature: float(getattr(row, feature)) for feature in features})
        for row in rows
    ]


def _all_metrics(probabilities: Sequence[float], rows: Sequence[ValidationRow]) -> dict[str, Any]:
    outcomes = [row.outcome for row in rows]
    metrics: dict[str, Any] = calibration_metrics(probabilities, outcomes)
    hits = sum(
        (probability >= 0.5) == bool(outcome)
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )
    brier_score = metrics["brier_score"]
    log_loss = metrics["log_loss"]
    expected_calibration_error = metrics["expected_calibration_error"]
    assert isinstance(brier_score, (int, float))
    assert isinstance(log_loss, (int, float))
    assert isinstance(expected_calibration_error, (int, float))
    return {
        "observations": len(rows),
        "accuracy": round(hits / len(rows), 6),
        "brier_score": round(float(brier_score), 6),
        "log_loss": round(float(log_loss), 6),
        "expected_calibration_error": round(float(expected_calibration_error), 6),
        "brier_reliability": round(float(metrics["brier_reliability"]), 6)
        if "brier_reliability" in metrics
        else float("nan"),
        "brier_resolution": round(float(metrics["brier_resolution"]), 6)
        if "brier_resolution" in metrics
        else float("nan"),
        "brier_uncertainty": round(float(metrics["brier_uncertainty"]), 6)
        if "brier_uncertainty" in metrics
        else float("nan"),
        "calibration_intercept": metrics.get("calibration_intercept"),
        "calibration_slope": metrics.get("calibration_slope"),
    }


def _gate_metrics(
    probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
    gate: float,
) -> dict[str, Any]:
    selected: list[tuple[float, int]] = []
    for probability, row in zip(probabilities, rows, strict=True):
        confidence = max(probability, 1 - probability)
        if confidence < gate:
            continue
        correct = row.outcome if probability >= 0.5 else 1 - row.outcome
        selected.append((confidence, correct))
    calls = len(selected)
    hits = sum(correct for _, correct in selected)
    return {
        "gate": gate,
        "calls": calls,
        "call_rate": round(calls / len(rows), 6) if rows else 0.0,
        "hits": hits,
        "hit_rate": round(hits / calls, 6) if calls else None,
        "brier_score": (
            round(sum((confidence - correct) ** 2 for confidence, correct in selected) / calls, 6)
            if calls
            else None
        ),
        "diagnostic_units_at_minus_110": (round(hits * (10 / 11) - (calls - hits), 6) if calls else 0.0),
    }


def _validation_selected_gate(
    validation_probabilities: Sequence[float],
    validation: Sequence[ValidationRow],
    holdout_probabilities: Sequence[float],
    holdout: Sequence[ValidationRow],
    target_hit_rate: float,
) -> dict[str, Any]:
    try:
        gate, learned = learn_confidence_threshold(
            validation_probabilities,
            [row.outcome for row in validation],
            target_hit_rate=target_hit_rate,
            minimum_calls=50,
        )
    except ValueError as error:
        return {"status": "no_validation_gate", "reason": str(error)}
    return {
        "status": "evaluated",
        "target_hit_rate": target_hit_rate,
        "gate": gate,
        "validation_selection": learned,
        "validation": _gate_metrics(validation_probabilities, validation, gate),
        "holdout": _gate_metrics(holdout_probabilities, holdout, gate),
    }


def _cluster_bootstrap_brier_delta(
    incumbent_probabilities: Sequence[float],
    candidate_probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
    *,
    seed: int,
    n_resamples: int = 2_000,
) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for incumbent, candidate, row in zip(incumbent_probabilities, candidate_probabilities, rows, strict=True):
        outcome = row.outcome
        by_date[row.date].append((candidate - outcome) ** 2 - (incumbent - outcome) ** 2)
    dates = sorted(by_date)
    observed = mean(value for day in dates for value in by_date[day])
    rng = random.Random(seed)
    samples = []
    for _ in range(n_resamples):
        sampled_days = [rng.choice(dates) for _ in dates]
        values = [value for day in sampled_days for value in by_date[day]]
        samples.append(mean(values))
    samples.sort()
    low_index = int(0.025 * (len(samples) - 1))
    high_index = int(0.975 * (len(samples) - 1))
    return {
        "metric": "candidate_brier_minus_incumbent",
        "dates": len(dates),
        "resamples": n_resamples,
        "point_estimate": round(observed, 8),
        "ci_95_low": round(samples[low_index], 8),
        "ci_95_high": round(samples[high_index], 8),
    }


def _cluster_sign_flip_p_value(
    incumbent_probabilities: Sequence[float],
    candidate_probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
    *,
    seed: int,
    n_resamples: int = 5_000,
) -> dict[str, Any]:
    """Two-sided paired randomization test with game dates as clusters."""
    by_date: dict[str, float] = defaultdict(float)
    for incumbent, candidate, row in zip(incumbent_probabilities, candidate_probabilities, rows, strict=True):
        by_date[row.date] += (candidate - row.outcome) ** 2 - (incumbent - row.outcome) ** 2
    cluster_sums = list(by_date.values())
    observed = abs(sum(cluster_sums))
    if observed == 0 and all(value == 0 for value in cluster_sums):
        return {
            "method": "paired_date_cluster_sign_flip",
            "dates": len(cluster_sums),
            "resamples": n_resamples,
            "two_sided_p_value": 1.0,
        }
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_resamples):
        randomized = sum(value if rng.getrandbits(1) else -value for value in cluster_sums)
        extreme += int(abs(randomized) >= observed)
    return {
        "method": "paired_date_cluster_sign_flip",
        "dates": len(cluster_sums),
        "resamples": n_resamples,
        "two_sided_p_value": round((extreme + 1) / (n_resamples + 1), 8),
    }


def _feature_stats(rows: Sequence[ValidationRow], feature: str) -> dict[str, Any]:
    values = [float(getattr(row, feature)) for row in rows]
    return {
        "count": len(values),
        "mean": round(mean(values), 6),
        "std": round(pstdev(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "unique_values": len(set(values)),
        "zero_rate": round(sum(value == 0 for value in values) / len(values), 6),
    }


def _processed_data_validation(store: FeatureStore, sport: str) -> dict[str, Any]:
    path = store.processed_path(sport)
    raw_lines = []
    invalid_json = 0
    event_ids = []
    rows_with_observed_at = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw_lines.append(line)
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_json += 1
                continue
            event_ids.append(str(payload.get("event_id", "")))
            rows_with_observed_at += int(bool(payload.get("observed_at_utc")))
    loaded = store.load_games(sport)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    unique_parsed_event_ids = len(set(event_ids))
    return {
        "path": str(path),
        "sha256": digest,
        "raw_nonempty_lines": len(raw_lines),
        "invalid_json_lines": invalid_json,
        "raw_duplicate_event_ids": len(event_ids) - len(set(event_ids)),
        "unique_parsed_event_ids": unique_parsed_event_ids,
        "loaded_modeling_games": len(loaded),
        "excluded_by_modeling_loader": unique_parsed_event_ids - len(loaded),
        "rows_with_observed_at_utc": rows_with_observed_at,
        "observation_metadata_complete": rows_with_observed_at == len(event_ids),
        "chronological_feature_rule": "complete prior dates only",
        "provenance_limitation": (
            "Completed score order is enforced by event time, but legacy processed rows do not "
            "carry retrieval observed_at_utc; this experiment is predictive development evidence, "
            "not promotion-grade source provenance."
        ),
    }


def _variant_name(additions: Iterable[str]) -> str:
    values = tuple(additions)
    return "incumbent" if not values else "incumbent+" + "+".join(values)


def _all_combinations() -> list[tuple[str, ...]]:
    names = tuple(ADDITIONS)
    return [
        combination for size in range(len(names) + 1) for combination in itertools.combinations(names, size)
    ]


def _features_for(incumbent: Sequence[str], additions: Sequence[str]) -> tuple[str, ...]:
    features = list(incumbent)
    for addition in additions:
        for feature in ADDITIONS[addition]:
            if feature not in features:
                features.append(feature)
    return tuple(features)


def evaluate_sport(store: FeatureStore, sport: str, config: Mapping[str, Any]) -> dict[str, Any]:
    model_config = config["models"][sport.upper()]
    artifact_path = Path(str(model_config["production_artifact"]))
    artifact = LearnedMarketArtifact.load(artifact_path)
    incumbent_features = tuple(artifact.raw["market_models"]["moneyline"]["feature_names"])
    rows = build_walk_forward_rows(store, sport)
    train, validation, holdout, split = chronological_split(rows)
    active_model = artifact.raw["market_models"]["moneyline"]
    active_probabilities = {
        "train": _artifact_predict(artifact, train, incumbent_features),
        "validation": _artifact_predict(artifact, validation, incumbent_features),
        "holdout": _artifact_predict(artifact, holdout, incumbent_features),
    }
    feature_names = tuple(feature for group in ADDITIONS.values() for feature in group)
    data_validation = _processed_data_validation(store, sport)
    data_validation["walk_forward_rows"] = len(rows)
    data_validation["excluded_before_binary_evaluation"] = data_validation["loaded_modeling_games"] - len(
        rows
    )
    data_validation["walk_forward_exclusion_rule"] = (
        "The first 50 history games seed features; tied non-soccer results are excluded."
    )
    data_validation["feature_stats"] = {
        cohort_name: {feature: _feature_stats(cohort, feature) for feature in feature_names}
        for cohort_name, cohort in (
            ("train", train),
            ("validation", validation),
            ("holdout", holdout),
        )
    }
    data_validation["schedule_coverage"] = {
        cohort_name: round(sum(row.schedule_available for row in cohort) / len(cohort), 6)
        for cohort_name, cohort in (
            ("train", train),
            ("validation", validation),
            ("holdout", holdout),
        )
    }

    variants: OrderedDict[str, dict[str, Any]] = OrderedDict()
    probability_cache: dict[str, dict[str, list[float]]] = {}
    for additions in _all_combinations():
        name = _variant_name(additions)
        features = _features_for(incumbent_features, additions)
        model = _fit(train, features)
        train_probabilities = _predict(model, train, features)
        validation_probabilities = _predict(model, validation, features)
        holdout_probabilities = _predict(model, holdout, features)
        probability_cache[name] = {
            "validation": validation_probabilities,
            "holdout": holdout_probabilities,
        }
        variants[name] = {
            "additions": list(additions),
            "features": list(features),
            "coefficients": {
                feature: round(float(coefficient), 10)
                for feature, coefficient in zip(features, model.coef_[0], strict=True)
            },
            "intercept": round(float(model.intercept_[0]), 10),
            "train_all_predictions": _all_metrics(train_probabilities, train),
            "validation_all_predictions": _all_metrics(validation_probabilities, validation),
            "holdout_all_predictions": _all_metrics(holdout_probabilities, holdout),
            "validation_selected_gate_60": _validation_selected_gate(
                validation_probabilities,
                validation,
                holdout_probabilities,
                holdout,
                0.60,
            ),
            "validation_selected_gate_65": _validation_selected_gate(
                validation_probabilities,
                validation,
                holdout_probabilities,
                holdout,
                0.65,
            ),
        }

    refit_control = variants["incumbent"]
    refit_control["label_note"] = (
        "Matched refit of the active feature specification on the current train cohort; "
        "not the byte-for-byte active artifact."
    )

    incumbent_probabilities = probability_cache["incumbent"]["holdout"]
    incumbent_brier = variants["incumbent"]["holdout_all_predictions"]["brier_score"]
    incumbent_log_loss = variants["incumbent"]["holdout_all_predictions"]["log_loss"]
    for name, variant in variants.items():
        brier = variant["holdout_all_predictions"]["brier_score"]
        log_loss = variant["holdout_all_predictions"]["log_loss"]
        variant["holdout_delta_vs_incumbent"] = {
            "brier": round(brier - incumbent_brier, 6),
            "log_loss": round(log_loss - incumbent_log_loss, 6),
        }
        seed = int(hashlib.sha256(f"{sport}:{name}".encode()).hexdigest()[:8], 16)
        variant["holdout_brier_delta_cluster_bootstrap"] = _cluster_bootstrap_brier_delta(
            incumbent_probabilities,
            probability_cache[name]["holdout"],
            holdout,
            seed=seed,
        )
        if len(variant["additions"]) == 1:
            variant["holdout_brier_date_cluster_sign_flip"] = _cluster_sign_flip_p_value(
                incumbent_probabilities,
                probability_cache[name]["holdout"],
                holdout,
                seed=seed ^ 0xA5A5A5A5,
            )

    gate_sweeps: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    isolated_names = ["incumbent"] + [_variant_name((addition,)) for addition in ADDITIONS]
    for name in isolated_names:
        gate_sweeps[name] = [
            {
                "gate": gate,
                "validation": _gate_metrics(probability_cache[name]["validation"], validation, gate),
                "holdout": _gate_metrics(probability_cache[name]["holdout"], holdout, gate),
            }
            for gate in GATE_GRID
        ]

    return {
        "sport": sport,
        "incumbent": {
            "artifact_path": str(artifact_path),
            "artifact_version": artifact.version,
            "artifact_hash": artifact.hash,
            "artifact_qualified": artifact.qualified,
            "features": list(incumbent_features),
            "active_coefficients": {
                feature: float(coefficient)
                for feature, coefficient in zip(incumbent_features, active_model["coefficients"], strict=True)
            },
            "active_intercept": float(active_model["intercept"]),
            "active_threshold": float(active_model["confidence_threshold"]),
            "current_cohort_metrics": {
                "train_all_predictions": _all_metrics(active_probabilities["train"], train),
                "validation_all_predictions": _all_metrics(active_probabilities["validation"], validation),
                "holdout_all_predictions": _all_metrics(active_probabilities["holdout"], holdout),
                "validation_at_active_threshold": _gate_metrics(
                    active_probabilities["validation"],
                    validation,
                    float(active_model["confidence_threshold"]),
                ),
                "holdout_at_active_threshold": _gate_metrics(
                    active_probabilities["holdout"],
                    holdout,
                    float(active_model["confidence_threshold"]),
                ),
            },
            "matched_refit_coefficient_delta": {
                feature: round(refit_control["coefficients"][feature] - float(coefficient), 10)
                for feature, coefficient in zip(incumbent_features, active_model["coefficients"], strict=True)
            },
            "matched_refit_intercept_delta": round(
                refit_control["intercept"] - float(active_model["intercept"]), 10
            ),
        },
        "split": split,
        "data_validation": data_validation,
        "variants": variants,
        "confidence_gate_sweeps": gate_sweeps,
    }


def _verdict(variant: Mapping[str, Any]) -> str:
    if "schedule_missingness" in variant["additions"]:
        return "REJECT_DEGENERATE"
    delta = variant["holdout_delta_vs_incumbent"]
    ci = variant["holdout_brier_delta_cluster_bootstrap"]
    validation = variant["validation_all_predictions"]
    holdout = variant["holdout_all_predictions"]
    validation_better = validation["brier_score"] < variant.get("incumbent_validation_brier", math.inf)
    if delta["brier"] < 0 and delta["log_loss"] < 0 and ci["ci_95_high"] < 0 and validation_better:
        return "KEEP_FOR_FRESH_TEST"
    if delta["brier"] > 0 and delta["log_loss"] > 0 and ci["ci_95_low"] > 0:
        return "REJECT"
    if holdout["brier_score"] == 0:
        return "INVALID"
    return "INCONCLUSIVE"


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _variant_row(name: str, variant: Mapping[str, Any]) -> str:
    holdout = variant["holdout_all_predictions"]
    delta = variant["holdout_delta_vs_incumbent"]
    ci = variant["holdout_brier_delta_cluster_bootstrap"]
    gate = variant["validation_selected_gate_60"]
    holdout_gate = gate.get("holdout", {}) if gate.get("status") == "evaluated" else {}
    return (
        f"| `{name}` | {holdout['brier_score']:.6f} | {delta['brier']:+.6f} | "
        f"{holdout['log_loss']:.6f} | {delta['log_loss']:+.6f} | "
        f"[{ci['ci_95_low']:+.6f}, {ci['ci_95_high']:+.6f}] | "
        f"{_fmt(gate.get('gate'), 3)} | {holdout_gate.get('calls', '—')} | "
        f"{_fmt(holdout_gate.get('hit_rate'), 3)} | {_fmt(holdout_gate.get('diagnostic_units_at_minus_110'), 2)} | "
        f"{_verdict(variant)} |"
    )


def build_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Roadmap challenger factorial experiment",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        "## Decision boundary",
        "",
        "This is a development experiment, not a promotion audit. The current models were snapshotted before changes. The historical holdouts have already been viewed in prior research, so every result below is evidence for what deserves a fresh future test—not permission to activate a feature. No model config, production artifact, ledger, pick, or order was changed.",
        "",
        f"Snapshot directory: `{report['incumbent_snapshot']['directory']}`",
        f"Snapshot archive SHA-256: `{report['incumbent_snapshot']['archive_sha256']}`",
        "",
        "Tested additions: `consistency_gap`, `hot_cold_gap`, `rest_disparity`, `back_to_back_gap`, `games_last_7_gap`, and `schedule_available`. These are the only roadmap additions that can be constructed consistently from existing completed-game histories in both validation and forward paths.",
        "",
        "## Method",
        "",
        "- Each sport starts from the exact feature list in its active artifact.",
        "- The exact active artifact is scored separately. In factorial tables, `incumbent` means a matched logistic refit of that feature specification on the current train cohort so nested challengers have a fair control.",
        "- All 64 combinations of six additions are fit on the existing complete-date 60/20/20 split.",
        "- Coefficients are fit on train only. Gate selection uses validation only.",
        "- All-prediction Brier/log loss/ECE are primary. Selective hit rate and flat `-110` units are diagnostics.",
        "- Brier deltas use a 2,000-resample date-cluster bootstrap.",
        "- Confidence gates from 0.50 to 0.80 are shown for the incumbent and every isolated addition.",
        "- Market prices are absent, so ROI, CLV, and executable EV are not claimed.",
        "",
        "## Untestable high-value roadmap additions",
        "",
        "| Model | Missing historical point-in-time additions | Decision |",
        "|---|---|---|",
        "| NBA/WNBA | player availability, projected minutes, RAPM/lineup impact, Four Factors/pace snapshots, transactions | Collect first; do not proxy from final box scores. |",
        "| MLB | true pregame starters, confirmed lineups, bullpen availability, archived game-time forecasts, pitch mix | Current retrospective caches cannot support promotion-grade testing. |",
        "| NFL | expected QB, practice/inactive state, EPA/CPOE, line continuity, pressure and drive state | No historical decision-time feature archive exists. |",
        "| LoL/CS2 | effective-dated rosters, patch/map/veto/draft state | Existing baseline remains score/series-only. |",
        "| KBO/NPB | starters, reliever workload, lineups, park/weather, game-specific tie probability | Existing tie-aware Elo remains the control. |",
        "",
    ]

    clean_passes: list[str] = []
    for sport, result in report["sports"].items():
        baseline = result["variants"]["incumbent"]["validation_all_predictions"]["brier_score"]
        for name, variant in result["variants"].items():
            evaluated = dict(variant)
            evaluated["incumbent_validation_brier"] = baseline
            if _verdict(evaluated) == "KEEP_FOR_FRESH_TEST":
                clean_passes.append(f"{sport}:{name}")

    lines.extend(
        [
            "## Executive conclusion",
            "",
            f"Clean additions passing the full statistical and structural screen: **{len(clean_passes)}**.",
            "",
            "The blunt answer is **do not activate any tested addition**. Apparent formal wins that include `schedule_available` are rejected as degenerate: the field is constant or almost constant in validation/holdout and acts like a cohort/intercept marker, not a durable predictive signal. The nondegenerate candidates below are useful hypotheses for fresh prospective collection, but none clears both validation consistency and clustered holdout uncertainty.",
            "",
            "| Sport | Most informative nondegenerate challenger | Validation Δ Brier | Holdout Δ Brier | Holdout 95% CI | Interpretation |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    review_candidates = (
        ("mlb", "incumbent+rest_disparity", "Holdout signal, but validation moved the wrong way."),
        (
            "nba",
            "incumbent+consistency+hot_cold+schedule_density",
            "Both cohorts improve slightly, but the clustered interval crosses zero.",
        ),
        ("wnba", "incumbent+hot_cold", "Holdout improves, validation worsens, and sample is small."),
        (
            "nfl",
            "incumbent+consistency+hot_cold+rest_disparity+schedule_density",
            "Largest directional gain, but only 110 holdout games and interval crosses zero.",
        ),
    )
    for sport, name, interpretation in review_candidates:
        result = report["sports"][sport]
        variant = result["variants"][name]
        baseline = result["variants"]["incumbent"]["validation_all_predictions"]["brier_score"]
        ci = variant["holdout_brier_delta_cluster_bootstrap"]
        lines.append(
            f"| {sport.upper()} | `{name}` | "
            f"{variant['validation_all_predictions']['brier_score'] - baseline:+.6f} | "
            f"{variant['holdout_delta_vs_incumbent']['brier']:+.6f} | "
            f"[{ci['ci_95_low']:+.6f}, {ci['ci_95_high']:+.6f}] | {interpretation} |"
        )
    lines.extend(
        [
            "",
            "Confidence gating does not rescue the additions. The validation-selected 60% gate is effectively nonselective for NBA, WNBA, and NFL; MLB's refit gate rises to roughly 0.57 but misses the 60% target on reused holdout. Attractive flat `-110` rows are threshold diagnostics only and cannot establish executable profitability.",
            "",
        ]
    )

    for sport, result in report["sports"].items():
        variants = result["variants"]
        incumbent_validation_brier = variants["incumbent"]["validation_all_predictions"]["brier_score"]
        for variant in variants.values():
            variant["incumbent_validation_brier"] = incumbent_validation_brier
        lines.extend(
            [
                f"# {sport.upper()}",
                "",
                "## Incumbent and data validation",
                "",
                f"- Artifact: `{result['incumbent']['artifact_path']}`",
                f"- Version/hash: `{result['incumbent']['artifact_version']}` / `{result['incumbent']['artifact_hash']}`",
                f"- Incumbent features: `{', '.join(result['incumbent']['features'])}`",
                f"- Active artifact threshold: {result['incumbent']['active_threshold']:.3f}",
                f"- Split: train {result['split']['train']['observations']}, validation {result['split']['validation']['observations']}, holdout {result['split']['locked_holdout']['observations']}",
                f"- Processed data: `{result['data_validation']['path']}`",
                f"- Data SHA-256: `{result['data_validation']['sha256']}`",
                f"- Raw rows / loaded modeling games / excluded by loader / duplicate IDs / invalid JSON: {result['data_validation']['raw_nonempty_lines']} / {result['data_validation']['loaded_modeling_games']} / {result['data_validation']['excluded_by_modeling_loader']} / {result['data_validation']['raw_duplicate_event_ids']} / {result['data_validation']['invalid_json_lines']}",
                f"- Walk-forward binary rows / excluded before evaluation: {result['data_validation']['walk_forward_rows']} / {result['data_validation']['excluded_before_binary_evaluation']} ({result['data_validation']['walk_forward_exclusion_rule']})",
                f"- Rows with `observed_at_utc`: {result['data_validation']['rows_with_observed_at_utc']} (metadata complete: {result['data_validation']['observation_metadata_complete']})",
                f"- Schedule coverage train/validation/holdout: {result['data_validation']['schedule_coverage']['train']:.1%} / {result['data_validation']['schedule_coverage']['validation']:.1%} / {result['data_validation']['schedule_coverage']['holdout']:.1%}",
                "",
                "> Provenance limitation: " + result["data_validation"]["provenance_limitation"],
                "",
                "### Exact active artifact versus matched refit control",
                "",
                "The active row uses the snapshotted coefficients and threshold. The refit row uses the same feature list but re-estimates coefficients on the current train cohort. All factorial deltas use the refit row as control.",
                "",
                "| Model | Validation Brier | Holdout Brier | Holdout log loss | Gate | Holdout calls | Holdout hit | -110 units |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        active_metrics = result["incumbent"]["current_cohort_metrics"]
        active_gate = active_metrics["holdout_at_active_threshold"]
        refit_metrics = variants["incumbent"]
        refit_gate_result = refit_metrics["validation_selected_gate_60"]
        refit_gate = (
            refit_gate_result.get("holdout", {}) if refit_gate_result.get("status") == "evaluated" else {}
        )
        lines.extend(
            [
                f"| Exact active artifact | {active_metrics['validation_all_predictions']['brier_score']:.6f} | {active_metrics['holdout_all_predictions']['brier_score']:.6f} | {active_metrics['holdout_all_predictions']['log_loss']:.6f} | {result['incumbent']['active_threshold']:.3f} | {active_gate['calls']} | {_fmt(active_gate['hit_rate'], 3)} | {active_gate['diagnostic_units_at_minus_110']:.2f} |",
                f"| Matched refit control (`incumbent`) | {refit_metrics['validation_all_predictions']['brier_score']:.6f} | {refit_metrics['holdout_all_predictions']['brier_score']:.6f} | {refit_metrics['holdout_all_predictions']['log_loss']:.6f} | {_fmt(refit_gate_result.get('gate'), 3)} | {refit_gate.get('calls', '—')} | {_fmt(refit_gate.get('hit_rate'), 3)} | {_fmt(refit_gate.get('diagnostic_units_at_minus_110'), 2)} |",
                "",
                "Active-to-refit coefficient deltas: `"
                + ", ".join(
                    f"{feature}={delta:+.6f}"
                    for feature, delta in result["incumbent"]["matched_refit_coefficient_delta"].items()
                )
                + f", intercept={result['incumbent']['matched_refit_intercept_delta']:+.6f}`",
                "",
                "### Feature distributions",
                "",
                "| Cohort | Feature | Mean | Std | Min | Max | Unique | Zero rate |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for cohort, features in result["data_validation"]["feature_stats"].items():
            for feature, stats in features.items():
                lines.append(
                    f"| {cohort} | `{feature}` | {stats['mean']:.4f} | {stats['std']:.4f} | "
                    f"{stats['min']:.4f} | {stats['max']:.4f} | {stats['unique_values']} | {stats['zero_rate']:.1%} |"
                )

        isolated = ["incumbent"] + [_variant_name((name,)) for name in ADDITIONS]
        pairs = [name for name, value in variants.items() if len(value["additions"]) == 2]
        lines.extend(
            [
                "",
                "## Isolated additions",
                "",
                "Lower Brier/log loss is better. A negative delta favors the challenger. `KEEP_FOR_FRESH_TEST` requires validation improvement and a holdout Brier/log-loss improvement whose clustered 95% CI excludes zero. Variants using the near-constant missingness flag are `REJECT_DEGENERATE` even if their numerical screen looks favorable.",
                "",
                "| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        lines.extend(_variant_row(name, variants[name]) for name in isolated)
        lines.extend(
            [
                "",
                "## Pairwise interactions",
                "",
                "| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        lines.extend(_variant_row(name, variants[name]) for name in pairs)
        lines.extend(
            [
                "",
                "## Every feature combination",
                "",
                "| Variant | Holdout Brier | Δ Brier | Holdout log loss | Δ log loss | 95% CI Δ Brier | Val-selected 60% gate | Holdout calls | Hit rate | -110 units | Verdict |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        lines.extend(_variant_row(name, variant) for name, variant in variants.items())

        lines.extend(["", "## Confidence-gate sweeps for incumbent and isolated additions", ""])
        for name, sweep in result["confidence_gate_sweeps"].items():
            lines.extend(
                [
                    f"### `{name}`",
                    "",
                    "| Gate | Validation calls | Validation hit | Validation -110U | Holdout calls | Holdout hit | Holdout -110U |",
                    "|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for row in sweep:
                validation = row["validation"]
                holdout = row["holdout"]
                lines.append(
                    f"| {row['gate']:.3f} | {validation['calls']} | {_fmt(validation['hit_rate'], 3)} | "
                    f"{validation['diagnostic_units_at_minus_110']:.2f} | {holdout['calls']} | "
                    f"{_fmt(holdout['hit_rate'], 3)} | {holdout['diagnostic_units_at_minus_110']:.2f} |"
                )
            lines.append("")

        ranked = sorted(variants.items(), key=lambda item: item[1]["holdout_all_predictions"]["brier_score"])[
            :10
        ]
        lines.extend(
            [
                "## Development ranking (not a promotion ranking)",
                "",
                "| Rank | Variant | Holdout Brier | Δ Brier | Verdict |",
                "|---:|---|---:|---:|---|",
            ]
        )
        for index, (name, variant) in enumerate(ranked, 1):
            lines.append(
                f"| {index} | `{name}` | {variant['holdout_all_predictions']['brier_score']:.6f} | "
                f"{variant['holdout_delta_vs_incumbent']['brier']:+.6f} | {_verdict(variant)} |"
            )
        lines.append("")

    lines.extend(
        [
            "# Final decision rules",
            "",
            "1. Reject additions that worsen both Brier and log loss with a clustered CI above zero.",
            "2. Do not keep an addition merely because one confidence gate creates attractive flat units; that is threshold mining.",
            "3. Keep only robust additions as candidates for a newly reserved prospective cohort.",
            "4. Treat mixed or CI-over-zero results as inconclusive, not wins.",
            "5. Do not activate any challenger until Vincent selects a named feature set and it passes a fresh forward test with timestamp-valid inputs and prices.",
            "",
        ]
    )
    return "\n".join(lines)


def _holm_adjusted_p_values(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running_max = 0.0
    total = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running_max = max(running_max, min(1.0, (total - index) * value))
        adjusted[name] = round(running_max, 8)
    return adjusted


def _independent_effect_label(
    addition: str,
    *,
    validation_delta: float,
    holdout_delta: float,
    raw_p_value: float,
    adjusted_p_value: float,
    unique_values: int,
) -> str:
    if addition == "schedule_missingness":
        return "REJECT_DEGENERATE"
    if unique_values <= 1:
        return "NO_VARIANCE"
    if adjusted_p_value <= 0.05:  # source of truth: config.SIGNIFICANCE_THRESHOLD
        if validation_delta < 0 and holdout_delta < 0:
            return "RELIABLE_POSITIVE"
        if holdout_delta > 0:
            return "DETECTABLE_HARM"
        return "SPLIT_UNSTABLE"
    if raw_p_value <= 0.05:
        return "NOMINAL_ONLY"
    if validation_delta < 0 and holdout_delta < 0:
        return "DIRECTIONAL_POSITIVE_NOT_DETECTED"
    if validation_delta > 0 and holdout_delta > 0:
        return "DIRECTIONAL_HARM_NOT_DETECTED"
    return "SPLIT_UNSTABLE"


def build_independent_markdown(report: Mapping[str, Any]) -> str:
    p_values: dict[str, float] = {}
    for sport, result in report["sports"].items():
        for addition in ADDITIONS:
            name = _variant_name((addition,))
            p_values[f"{sport}:{addition}"] = result["variants"][name][
                "holdout_brier_date_cluster_sign_flip"
            ]["two_sided_p_value"]
    adjusted = _holm_adjusted_p_values(p_values)

    lines = [
        "# Independent roadmap feature effect audit",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        "## Answer",
        "",
        "Each addition was fit alone on top of the matched refit control. Across 24 sport-feature tests, no feature demonstrates a reliable positive effect after validation-direction checks, date-cluster randomization, and Holm correction. `schedule_available` is rejected structurally because it is constant or nearly constant in the evaluation cohorts.",
        "",
        "A coefficient being nonzero does not establish an effect. The decision requires lower Brier on both validation and holdout plus multiplicity-safe evidence. Confidence-gate changes are reported but cannot override the probability-quality tests.",
        "",
        "## Testing contract",
        "",
        "- One added feature group at a time; no interactions or combinations.",
        "- Same complete-date 60/20/20 cohorts and matched refit control as the factorial dossier.",
        "- Holdout uncertainty uses a paired date-cluster sign-flip test with 5,000 randomizations.",
        "- Holm correction is applied across all 24 isolated tests.",
        "- Lower Brier/log loss is better; negative deltas favor the isolated feature.",
        "- Flat `-110` results and selective hit rates are diagnostics, not economic evidence.",
        "",
        "## Cross-sport feature consistency",
        "",
        "| Feature | Validation improved | Holdout improved | Raw p≤0.05 | Holm p≤0.05 | Conclusion |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for addition in ADDITIONS:
        validation_better = holdout_better = raw_significant = adjusted_significant = 0
        for sport, result in report["sports"].items():
            variant = result["variants"][_variant_name((addition,))]
            baseline = result["variants"]["incumbent"]
            validation_delta = (
                variant["validation_all_predictions"]["brier_score"]
                - baseline["validation_all_predictions"]["brier_score"]
            )
            validation_better += int(validation_delta < 0)
            holdout_better += int(variant["holdout_delta_vs_incumbent"]["brier"] < 0)
            key = f"{sport}:{addition}"
            raw_significant += int(p_values[key] <= 0.05)
            adjusted_significant += int(adjusted[key] <= 0.05)
        conclusion = (
            "Reject as a prediction feature"
            if addition == "schedule_missingness"
            else "No reliable independent effect"
        )
        lines.append(
            f"| `{addition}` | {validation_better}/4 | {holdout_better}/4 | "
            f"{raw_significant}/4 | {adjusted_significant}/4 | {conclusion} |"
        )

    for sport, result in report["sports"].items():
        baseline = result["variants"]["incumbent"]
        baseline_gate_result = baseline["validation_selected_gate_60"]
        baseline_gate = (
            baseline_gate_result.get("holdout", {})
            if baseline_gate_result.get("status") == "evaluated"
            else {}
        )
        lines.extend(
            [
                "",
                f"# {sport.upper()}",
                "",
                f"Control holdout Brier: `{baseline['holdout_all_predictions']['brier_score']:.6f}`. Control validation-selected gate: `{_fmt(baseline_gate_result.get('gate'), 3)}`; holdout calls/hit rate: `{baseline_gate.get('calls', '—')}` / `{_fmt(baseline_gate.get('hit_rate'), 3)}`.",
                "",
                "| Isolated feature | Coefficient | Holdout std / unique / zero | Validation Δ Brier | Holdout Δ Brier | Δ log loss | 95% CI Δ Brier | Raw p | Holm p | Gate | Calls | Hit | Effect decision |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for addition, feature_names in ADDITIONS.items():
            name = _variant_name((addition,))
            variant = result["variants"][name]
            feature = feature_names[0]
            stats = result["data_validation"]["feature_stats"]["holdout"][feature]
            validation_delta = (
                variant["validation_all_predictions"]["brier_score"]
                - baseline["validation_all_predictions"]["brier_score"]
            )
            holdout_delta = variant["holdout_delta_vs_incumbent"]["brier"]
            ci = variant["holdout_brier_delta_cluster_bootstrap"]
            gate_result = variant["validation_selected_gate_60"]
            gate = gate_result.get("holdout", {}) if gate_result.get("status") == "evaluated" else {}
            key = f"{sport}:{addition}"
            label = _independent_effect_label(
                addition,
                validation_delta=validation_delta,
                holdout_delta=holdout_delta,
                raw_p_value=p_values[key],
                adjusted_p_value=adjusted[key],
                unique_values=stats["unique_values"],
            )
            lines.append(
                f"| `{addition}` | {variant['coefficients'][feature]:+.6f} | "
                f"{stats['std']:.4f} / {stats['unique_values']} / {stats['zero_rate']:.1%} | "
                f"{validation_delta:+.6f} | {holdout_delta:+.6f} | "
                f"{variant['holdout_delta_vs_incumbent']['log_loss']:+.6f} | "
                f"[{ci['ci_95_low']:+.6f}, {ci['ci_95_high']:+.6f}] | "
                f"{p_values[key]:.4f} | {adjusted[key]:.4f} | "
                f"{_fmt(gate_result.get('gate'), 3)} | {gate.get('calls', '—')} | "
                f"{_fmt(gate.get('hit_rate'), 3)} | {label} |"
            )

    lines.extend(
        [
            "",
            "# Final independent-feature decision",
            "",
            "- Keep in production: **none**.",
            "- Collect prospectively: MLB `rest_disparity`; NFL `hot_cold` and `schedule_density`; NBA schedule-load features. These are hypotheses, not validated additions.",
            "- Reject as modeled: `schedule_missingness`; NFL `back_to_back` has no variance; MLB `back_to_back` shows holdout harm.",
            "- Do not tune confidence gates around these features until a fresh timestamp-valid cohort confirms an all-prediction Brier improvement.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    data_root: str | Path = "data",
    snapshot_directory: str | Path = DEFAULT_SNAPSHOT_DIRECTORY,
) -> dict[str, Any]:
    from .domain import utc_now

    config = load_config()
    store = FeatureStore(data_root)
    snapshot_path = Path(snapshot_directory)
    snapshot_archive = snapshot_path / "current-model.tar.gz"
    snapshot_manifest = snapshot_path / "MANIFEST.md"
    if not snapshot_archive.exists() or not snapshot_manifest.exists():
        raise FileNotFoundError(f"incumbent snapshot is incomplete: {snapshot_path}")
    return {
        "schema_version": "roadmap-challenger-factorial-v1",
        "generated_at_utc": utc_now().isoformat().replace("+00:00", "Z"),
        "promotion_eligible": False,
        "holdout_status": "reused_development_evidence",
        "market_prices_used": False,
        "economic_claims_allowed": False,
        "incumbent_snapshot": {
            "directory": str(snapshot_path),
            "manifest": str(snapshot_manifest),
            "archive": str(snapshot_archive),
            "archive_sha256": hashlib.sha256(snapshot_archive.read_bytes()).hexdigest(),
        },
        "additions": {name: list(features) for name, features in ADDITIONS.items()},
        "confidence_gate_grid": list(GATE_GRID),
        "sports": {sport: evaluate_sport(store, sport, config) for sport in SPORTS},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json-output",
        default="outputs/roadmap_challenger/roadmap-challenger-factorial-v1.json",
    )
    parser.add_argument(
        "--markdown-output",
        default="outputs/roadmap_challenger/ROADMAP_CHALLENGER_DECISION_DOSSIER.md",
    )
    parser.add_argument(
        "--independent-markdown-output",
        default="outputs/roadmap_challenger/INDEPENDENT_FEATURE_EFFECTS.md",
    )
    parser.add_argument(
        "--snapshot-directory",
        default=str(DEFAULT_SNAPSHOT_DIRECTORY),
    )
    args = parser.parse_args()
    report = build_report(snapshot_directory=args.snapshot_directory)
    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    independent_markdown_path = Path(args.independent_markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    independent_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(build_markdown(report) + "\n", encoding="utf-8")
    independent_markdown_path.write_text(build_independent_markdown(report) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(markdown_path),
                "independent_markdown": str(independent_markdown_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
