"""Test recency weighting against reproduced v1 models on fixed development dates.

Fits only training rows, selects on selection dates and calibrates on calibration
dates. Reused test periods are exploratory development, never fresh confirmation.
CFB keeps its separate joint-score experiment; NRFI requires its own source audit.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.base import clone
from sklearn.pipeline import Pipeline

from model_prediction.research_generation import (
    classification_losses,
    cluster_interval,
    digest,
    file_hash,
    predict_bundle,
    temperature_scale,
)
from scripts.forecast_research_generation import capture, write_json

POLICY = {
    "family": "recency_history_v2",
    "half_life_days": [None, 180, 730],
    "estimator": "exact selected v1 estimator architecture and hyperparameters",
    "selection": "unweighted selection log loss or score MAE; reference wins exact ties",
    "weighting": "training only; exp2 negative age over half life; normalized mean one; scaler also weighted",
    "calibration": "separate original calibration partition, unweighted",
    "minimum_gain": "vector Brier 0.004; score MAE one percent of reference MAE",
    "uncertainty": "pointwise date-cluster 95 percent intervals; no multiplicity-adjusted confirmation",
    "test_exposure": "reused historical development dates; not prospective evidence",
    "incumbent": "v1 research reference, not production incumbent",
    "profitability": None,
    "promote": False,
}


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def training_weights(dates: list[str], half_life: int) -> np.ndarray:
    if half_life <= 0 or not dates:
        raise ValueError("invalid_recency_window")
    parsed = [datetime.fromisoformat(d) for d in dates]
    end = max(parsed)
    weights = np.exp2(-np.asarray([(end - d).days for d in parsed], dtype=float) / half_life)
    return weights / weights.mean()


def fit_weighted(model: Any, x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> Any:
    fitted = clone(model)
    params = (
        {f"{name}__sample_weight": weights for name, _ in fitted.steps}
        if isinstance(fitted, Pipeline)
        else {"sample_weight": weights}
    )
    fitted.fit(x, y, **params)
    return fitted


def train_one(generation: Path, output: Path, registration: dict[str, Any]) -> dict[str, Any]:
    sport, market = registration["sport"], registration["market"]
    key = f"{sport.lower()}-{market}"
    folder = output / "models" / key
    folder.mkdir(parents=True, exist_ok=False)
    for name in ("model.joblib", "recipe.json", "evaluation_predictions.jsonl"):
        capture(generation / "models" / key / name, folder / "reference" / name)
    feature_path = output / "features" / f"{sport.lower()}.jsonl"
    if not feature_path.exists():
        capture(generation / "features" / f"{sport.lower()}.jsonl", feature_path)
    data = rows(feature_path)
    recipe = json.loads((folder / "reference/recipe.json").read_text())
    model_path = folder / "reference/model.joblib"
    if (
        recipe["dataset_hash"] != digest(data)
        or recipe["model_file_sha256"] != file_hash(model_path)
        or registration["artifact_sha256"] != file_hash(model_path)
    ):
        raise ValueError("reference_artifact_or_dataset_mismatch")
    bundle = joblib.load(model_path)
    if bundle["recipe"] != {k: v for k, v in recipe.items() if k != "model_file_sha256"}:
        raise ValueError("reference_recipe_mismatch")
    by_id = {r["event_id"]: i for i, r in enumerate(data)}
    if len(by_id) != len(data):
        raise ValueError("duplicate_feature_event")
    masks = {k: [by_id[e] for e in v] for k, v in recipe["split_event_ids"].items()}
    previous = ""
    seen: set[int] = set()
    for name in ("train", "select", "calibrate", "test"):
        indices = masks[name]
        dates = [data[i]["date"] for i in indices]
        if (
            not dates
            or min(dates) <= previous
            or seen.intersection(indices)
            or len(set(indices)) != len(indices)
        ):
            raise ValueError("invalid_chronological_partitions")
        previous = max(dates)
        seen.update(indices)
    if len(seen) != len(data) or any(r["feature_max_event_date"] >= r["date"] for r in data):
        raise ValueError("incomplete_or_leaking_feature_cohort")
    x = np.asarray([r["features"] for r in data], dtype=float)
    classification = recipe["classes"] is not None
    y = np.asarray(
        [r["outcome" if classification else "margin" if market == "spread" else "total"] for r in data]
    )
    tr, se, ca, te = (masks[n] for n in ("train", "select", "calibrate", "test"))
    reference = bundle["model"]
    saved = rows(folder / "reference/evaluation_predictions.jsonl")
    if [r["event_id"] for r in saved] != recipe["split_event_ids"]["test"] or any(
        r["target"] != y[i] or r["date"] != data[i]["date"] for r, i in zip(saved, te, strict=True)
    ):
        raise ValueError("reference_test_identity_mismatch")
    ref = (
        predict_bundle(model_path, x[te])
        if classification
        else reference.predict(x[te]) + float(np.median(recipe["residuals"]))
    )
    errors = np.abs(ref - np.asarray([r["prediction"] for r in saved]))
    if np.max(errors) > 1e-12:
        raise ValueError("research_reference_reproduction_failed")
    (folder / "reference_reproduction.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "event_id": data[i]["event_id"],
                    "status": "PASS",
                    "max_absolute_error": float(np.max(error)),
                }
            )
            + "\n"
            for i, error in zip(te, errors, strict=True)
        )
    )

    def selection_loss(model: Any) -> float:
        return (
            float(classification_losses(y[se], model.predict_proba(x[se]), model.classes_).mean())
            if classification
            else float(np.abs(model.predict(x[se]) - y[se]).mean())
        )

    choices: list[tuple[float, int, int | None, Any]] = [(selection_loss(reference), 0, None, reference)]
    weight_audit = []
    for half_life in (180, 730):
        weights = training_weights([data[i]["date"] for i in tr], half_life)
        fitted = fit_weighted(reference, x[tr], y[tr], weights)
        choices.append((selection_loss(fitted), half_life, half_life, fitted))
        weight_audit.append(
            {
                "half_life_days": half_life,
                "effective_training_rows": float(weights.sum() ** 2 / (weights**2).sum()),
                "minimum_weight": float(weights.min()),
                "maximum_weight": float(weights.max()),
            }
        )
    _, _, selected_half_life, model = min(choices, key=lambda c: (c[0], c[1]))
    temperature = recipe["temperature"]
    residuals = recipe["residuals"]
    if selected_half_life is not None:
        if classification:
            p = model.predict_proba(x[ca])
            opt = minimize_scalar(
                lambda t: classification_losses(y[ca], temperature_scale(p, float(t)), model.classes_).mean(),
                bounds=(0.5, 3.0),
                method="bounded",
            )
            if not opt.success:
                raise ValueError("calibration_failed")
            temperature = float(opt.x)
        else:
            residuals = (y[ca] - model.predict(x[ca])).tolist()
    model_id = f"{sport.lower()}-{market}-recency-research-20260911-v2"
    updated = {k: v for k, v in recipe.items() if k != "model_file_sha256"} | {
        "model_id": model_id,
        "research_family": "recency_history_v2",
        "temperature": temperature,
        "residuals": residuals,
        "selected_half_life_days": selected_half_life,
        "reference_model_id": recipe["model_id"],
        "reference_model_sha256": file_hash(model_path),
        "selection": [{"half_life_days": c[2], "selection_loss": c[0]} for c in choices],
        "recency_policy": POLICY,
        "training_weight_audit": weight_audit,
    }
    # Freeze the chosen model before measuring any candidate evaluation outputs.
    joblib.dump({"model": model, "recipe": updated}, folder / "model.joblib")
    write_json(folder / "recipe.json", updated | {"model_file_sha256": file_hash(folder / "model.joblib")})
    restored = joblib.load(folder / "model.joblib")["model"]
    metrics: dict[str, Any]
    if classification:
        prediction = predict_bundle(folder / "model.joblib", x[te])
        original = temperature_scale(model.predict_proba(x[te]), temperature)
        onehot = np.eye(len(recipe["classes"]))[[recipe["classes"].index(int(v)) for v in y[te]]]
        loss, ref_loss = ((prediction - onehot) ** 2).sum(1), ((ref - onehot) ** 2).sum(1)
        metrics = {
            "candidate_accuracy": float((prediction.argmax(1) == y[te]).mean()),
            "reference_accuracy": float((ref.argmax(1) == y[te]).mean()),
            "candidate_vector_brier": float(loss.mean()),
            "reference_vector_brier": float(ref_loss.mean()),
            "candidate_log_loss": float(classification_losses(y[te], prediction, model.classes_).mean()),
            "reference_log_loss": float(classification_losses(y[te], ref, model.classes_).mean()),
        }
        threshold = 0.004
    else:
        prediction = restored.predict(x[te]) + float(np.median(residuals))
        original = model.predict(x[te]) + float(np.median(residuals))
        loss, ref_loss = np.abs(prediction - y[te]), np.abs(ref - y[te])
        metrics = {
            "candidate_mae": float(loss.mean()),
            "reference_mae": float(ref_loss.mean()),
            "contract_probability_metrics": None,
        }
        threshold = 0.01 * float(ref_loss.mean())
    if not np.array_equal(prediction, original):
        raise ValueError("serialization_prediction_parity_failed")
    interval = cluster_interval(ref_loss - loss, [data[i]["date"] for i in te])
    (folder / "evaluation_predictions.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "event_id": data[i]["event_id"],
                    "date": data[i]["date"],
                    "target": float(y[i]),
                    "prediction": np.asarray(prediction[j]).tolist(),
                    "reference_prediction": np.asarray(ref[j]).tolist(),
                }
            )
            + "\n"
            for j, i in enumerate(te)
        )
    )
    report = {
        "sport": sport,
        "market": market,
        "model_id": model_id,
        "incumbent": registration["incumbent"],
        "status": "TRAINED_RESEARCH_ONLY",
        "artifact_sha256": file_hash(folder / "model.joblib"),
        "selected_half_life_days": selected_half_life,
        "reference_reproduction": "PASS",
        "reference_max_absolute_error": float(np.max(errors)),
        "serialization_parity": "PASS",
        "test_events": len(te),
        "test_dates": len({data[i]["date"] for i in te}),
        "metrics": metrics,
        "reference_minus_candidate_loss": interval,
        "minimum_effect": threshold,
        "verdict": "REFERENCE_RETAINED"
        if selected_half_life is None
        else "EXPLORATORY_GAIN_VS_RESEARCH_REFERENCE"
        if interval["lower_95"] > threshold
        else "NO_RELIABLE_GAIN_VS_RESEARCH_REFERENCE",
        "production_incumbent_superiority": None,
        "economics": None,
        "promote": False,
    }
    write_json(folder / "report.json", report)
    return report


def run(generation: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    write_json(output / "policy.json", POLICY)
    manifest: dict[str, Any] = {"policy": POLICY, "models": [], "promote": False}
    try:
        capture(generation / "manifest.json", output / "reference_manifest.json")
        registrations = json.loads((output / "reference_manifest.json").read_text())["models"]
        for registration in registrations:
            if registration["sport"] == "NCAAF" or registration["market"] == "nrfi":
                continue
            print(f"Training {registration['sport']}/{registration['market']} ...", flush=True)
            report = train_one(generation, output, registration)
            manifest["models"].append(report)
            write_json(output / "manifest.json", manifest)
            print(f"  half-life={report['selected_half_life_days']} {report['verdict']}", flush=True)
        capture(Path(__file__), output / "code_snapshot/train_recency_research.py")
        write_json(
            output / "RUN_STATUS.json",
            {"status": "COMPLETE", "manifest_sha256": file_hash(output / "manifest.json")},
        )
        return manifest
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.generation, args.output)
