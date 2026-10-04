"""Fit a new joint NCAAF candidate against the exact archived research v1 cohort.

The reference is explicitly research v1, not the production incumbent. No
historical contract lines or prices are invented. Production superiority and
profitability remain unavailable without captured incumbent/market evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np

from model_prediction.joint_score_research import fit, probabilities, score_samples
from model_prediction.research_generation import (
    FEATURE_NAMES,
    cluster_interval,
    digest,
    file_hash,
    predict_bundle,
)
from scripts.forecast_research_generation import capture, write_json


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(generation: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        result = _run(generation, output)
        write_json(output / "report.json", result)
        write_json(output / "RUN_STATUS.json", {"status": "COMPLETE"})
        return result
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run(generation: Path, output: Path) -> dict[str, Any]:
    write_json(
        output / "policy.json",
        {
            "model": "joint standardized ridge home/away means plus paired held-out residuals",
            "alphas": [1, 10, 100],
            "selection": "mean home/away score MAE",
            "partitions": "exact archived v1 complete-date partitions",
            "prior_test_exposure": "reused historical development; not fresh confirmation",
            "reference": "research generation v1, not production incumbent",
            "economics": None,
            "promote": False,
        },
    )
    capture(generation / "features/ncaaf.jsonl", output / "features.jsonl")
    data = rows(output / "features.jsonl")
    x = np.asarray([r["features"] for r in data])
    actual = np.asarray([[(r["total"] + r["margin"]) / 2, (r["total"] - r["margin"]) / 2] for r in data])
    ids = {r["event_id"]: i for i, r in enumerate(data)}
    if len(ids) != len(data) or any(r["feature_max_event_date"] >= r["date"] for r in data):
        raise ValueError("invalid_archived_feature_cohort")
    references, replay, incumbent_ids = {}, [], {}
    masks = None
    for market in ("moneyline", "spread", "total"):
        root = output / "reference" / market
        for name in ("model.joblib", "recipe.json", "evaluation_predictions.jsonl"):
            capture(generation / "models" / f"ncaaf-{market}" / name, root / name)
        recipe = json.loads((root / "recipe.json").read_text())
        incumbent_ids[market] = recipe["incumbent_model_id"]
        if (
            recipe["dataset_hash"] != digest(data)
            or recipe["features"] != list(FEATURE_NAMES)
            or recipe["model_file_sha256"] != file_hash(root / "model.joblib")
        ):
            raise ValueError("reference_artifact_or_feature_cohort_mismatch")
        current_masks = {
            key: [ids[event] for event in events] for key, events in recipe["split_event_ids"].items()
        }
        if masks is not None and current_masks != masks:
            raise ValueError("reference_partition_mismatch")
        masks = current_masks
        saved = rows(root / "evaluation_predictions.jsonl")
        if [r["event_id"] for r in saved] != recipe["split_event_ids"]["test"]:
            raise ValueError("reference_test_identity_mismatch")
        if market == "moneyline":
            prediction = predict_bundle(root / "model.joblib", x[masks["test"]])
        else:
            bundle = joblib.load(root / "model.joblib")
            if bundle["recipe"] != {k: v for k, v in recipe.items() if k != "model_file_sha256"}:
                raise ValueError("reference_recipe_mismatch")
            prediction = bundle["model"].predict(x[masks["test"]]) + float(np.median(recipe["residuals"]))
        error = float(np.max(np.abs(prediction - np.asarray([r["prediction"] for r in saved]))))
        if error > 1e-12:
            raise ValueError("research_reference_reproduction_failed")
        replay.append({"market": market, "status": "PASS", "rows": len(saved), "max_absolute_error": error})
        references[market] = prediction
    assert masks is not None
    # Verify date ordering and non-overlap, not just names of stored partitions.
    previous = ""
    for name in ("train", "select", "calibrate", "test"):
        dates = [data[i]["date"] for i in masks[name]]
        if min(dates) <= previous:
            raise ValueError("nonchronological_partitions")
        previous = max(dates)
    write_json(output / "reference_reproduction.json", replay)
    tr, se, ca, te = (masks[name] for name in ("train", "select", "calibrate", "test"))
    artifact = fit(x[tr], actual[tr], x[se], actual[se], x[ca], actual[ca], list(FEATURE_NAMES))
    write_json(output / "model.json", artifact)
    registrations = []
    for market, model_id in artifact["model_ids"].items():
        directory = output / "models" / f"ncaaf-{market}"
        directory.mkdir(parents=True, exist_ok=False)
        recipe = {
            "model_id": model_id,
            "research_family": "ncaaf_joint_score_v2",
            "market": market,
            "features": list(FEATURE_NAMES),
            "joint_score_artifact": artifact,
            "class_labels": ["b_win", "a_win"] if market == "moneyline" else None,
            "classes": [0, 1] if market == "moneyline" else None,
            "target": market,
            "source": {
                "archived_features_sha256": file_hash(output / "features.jsonl"),
                "historical_source_observation_times_verified": False,
            },
        }
        model_path = directory / "model.joblib"
        joblib.dump({"model": None, "recipe": recipe}, model_path)
        write_json(directory / "recipe.json", recipe | {"model_file_sha256": file_hash(model_path)})
        registrations.append(
            {
                "sport": "NCAAF",
                "market": market,
                "model_id": model_id,
                "incumbent": incumbent_ids[market],
                "status": "TRAINED_RESEARCH_ONLY",
                "artifact_sha256": file_hash(model_path),
            }
        )
    write_json(
        output / "manifest.json",
        {"models": registrations, "historical_development_only": True, "promote": False},
    )
    restored = json.loads((output / "model.json").read_text())
    candidate_ml = probabilities(restored, x[te], "moneyline")
    if not np.array_equal(candidate_ml, probabilities(artifact, x[te], "moneyline")):
        raise ValueError("joint_serialization_prediction_mismatch")
    samples = score_samples(restored, x[te])
    margin_samples = samples[:, :, 0] - samples[:, :, 1]
    predictions = {
        "moneyline": candidate_ml,
        "spread": np.asarray([np.median(m[m != 0]) for m in margin_samples]),
        "total": np.asarray(
            [np.median(s.sum(1)[m != 0]) for s, m in zip(samples, margin_samples, strict=True)]
        ),
    }
    dates = [data[i]["date"] for i in te]
    y = np.asarray([data[i]["outcome"] for i in te])
    metrics = {}
    for market, candidate in predictions.items():
        reference = references[market]
        metric: dict[str, Any]
        if market == "moneyline":
            cand_losses = (candidate[:, 1] - y) ** 2
            ref_losses = (reference[:, 1] - y) ** 2
            metric = {
                "candidate_accuracy": float((candidate.argmax(1) == y).mean()),
                "reference_accuracy": float((reference.argmax(1) == y).mean()),
                "candidate_brier": float(cand_losses.mean()),
                "reference_brier": float(ref_losses.mean()),
                "candidate_log_loss": float(
                    -np.log(np.clip(candidate[np.arange(len(y)), y], 1e-12, 1)).mean()
                ),
                "reference_log_loss": float(
                    -np.log(np.clip(reference[np.arange(len(y)), y], 1e-12, 1)).mean()
                ),
            }
        else:
            target = np.asarray([data[i]["margin" if market == "spread" else "total"] for i in te])
            cand_losses, ref_losses = np.abs(candidate - target), np.abs(reference - target)
            metric = {
                "candidate_mae": float(cand_losses.mean()),
                "reference_mae": float(ref_losses.mean()),
                "contract_probability_metrics": None,
                "reason": "exact historical contract lines unavailable",
            }
        metrics[market] = metric | {
            "reference_minus_candidate_loss": cluster_interval(ref_losses - cand_losses, dates)
        }
    (output / "evaluation_predictions.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "event_id": data[i]["event_id"],
                    "date": data[i]["date"],
                    "candidate_moneyline": candidate_ml[j].tolist(),
                    "reference_moneyline": references["moneyline"][j].tolist(),
                    "candidate_margin": float(predictions["spread"][j]),
                    "candidate_total": float(predictions["total"][j]),
                    "actual_home": float(actual[i, 0]),
                    "actual_away": float(actual[i, 1]),
                }
            )
            + "\n"
            for j, i in enumerate(te)
        )
    )
    root = Path(__file__).resolve().parents[1]
    for name in ("scripts/train_cfb_joint_research.py", "src/model_prediction/joint_score_research.py"):
        capture(root / name, output / "code_snapshot" / name)
    return {
        "status": "TRAINED_RESEARCH_ONLY",
        "model_ids": artifact["model_ids"],
        "artifact_sha256": file_hash(output / "model.json"),
        "artifact_hash": artifact["artifact_hash"],
        "selected_alpha": artifact["selected_alpha"],
        "splits": {
            k: {"events": len(v), "dates": len({data[i]["date"] for i in v})} for k, v in masks.items()
        },
        "reference_reproduction": replay,
        "reference_is_production_incumbent": False,
        "serialization_parity": "PASS",
        "metrics": metrics,
        "calibration_residual_covariance": artifact["residual_covariance"],
        "raw_sample_tie_fraction": float((margin_samples == 0).mean()),
        "historical_development_only": True,
        "production_incumbent_superiority": None,
        "economics": None,
        "promote": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.generation, args.output), indent=2))
