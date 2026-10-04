"""Audit and rebuild NRFI using completed outcomes and team-specific histories."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import build_first_inning_ledger, compute_first_inning_priors
from model_prediction.research_generation import (
    ResearchRow,
    cluster_interval,
    digest,
    file_hash,
    partition,
    predict_bundle,
    train_candidate,
)
from model_prediction.research_nrfi import FAMILY, FEATURE_NAMES, MODEL_ID, build_rows, load_snapshots
from scripts.forecast_research_generation import capture, write_json


def run(reference: Path, source: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        report = _run(reference, source, output)
        write_json(output / "report.json", report)
        write_json(
            output / "RUN_STATUS.json",
            {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")},
        )
        return report
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run(reference: Path, source: Path, output: Path) -> dict[str, Any]:
    write_json(
        output / "policy.json",
        {
            "theory": "remove invalid labels, mixed opponent batter pools and train/serve cutoff mismatch",
            "selection": "existing five classifier variants on original selection dates",
            "calibration": "separate original dates",
            "embargo": "prior UTC dates strictly before target date minus one day",
            "minimum_vector_brier_gain": 0.004,
            "historical_publication_times_verified": False,
            "historical_starter_selection": "actual postgame starter; pregame availability not certified",
            "reference": "research v1, not production incumbent",
            "economics": None,
            "promote": False,
        },
    )
    capture(source, output / "snapshots.jsonl")
    for name in ("model.joblib", "recipe.json", "evaluation_predictions.jsonl"):
        capture(reference / name, output / "reference" / name)
    recipe = json.loads((output / "reference/recipe.json").read_text())
    as_of = max(e["date"] for e in read_rows(output / "reference/evaluation_predictions.jsonl"))
    legacy = sorted(
        [
            ResearchRow(
                str(r.game_pk),
                r.game_start_utc[:10],
                tuple(float(r.features[n]) for n in recipe["features"]),
                int(r.nrfi),
                0.0,
                float(r.runs_1st_total),
                "UNVERIFIED_LEGACY_FEATURE_BUILDER",
            )
            for r in build_first_inning_ledger(output / "snapshots.jsonl", priors=recipe["source"]["priors"])
            if r.game_start_utc[:10] <= as_of
        ],
        key=lambda r: (r.date, r.event_id),
    )
    if digest([asdict(r) for r in legacy]) != recipe["dataset_hash"]:
        raise ValueError("legacy_feature_reconstruction_mismatch")
    by_id = {r.event_id: r for r in legacy}
    saved = read_rows(output / "reference/evaluation_predictions.jsonl")
    if [r["event_id"] for r in saved] != recipe["split_event_ids"]["test"]:
        raise ValueError("reference_test_identity_mismatch")
    reference_prediction = predict_bundle(
        output / "reference/model.joblib", np.asarray([by_id[r["event_id"]].features for r in saved])
    )
    errors = np.max(np.abs(reference_prediction - np.asarray([r["prediction"] for r in saved])), axis=1)
    if float(errors.max()) > 1e-12:
        raise ValueError("reference_reproduction_failed")
    write_json(
        output / "reference_reproduction.json",
        [
            {"event_id": r["event_id"], "max_absolute_error": float(e), "status": "PASS"}
            for r, e in zip(saved, errors, strict=True)
        ],
    )
    snapshots, audit = load_snapshots(output / "snapshots.jsonl")
    snapshots = [r for r in snapshots if parse_utc(r["game_start_utc"]).date().isoformat() <= as_of]
    clean_path = output / "clean_snapshots.jsonl"
    clean_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in snapshots))
    accepted_ids = {str(r["game_pk"]) for r in snapshots}
    audit["excluded_reference_rows"] = [
        {
            "event_id": r.event_id,
            "date": r.date,
            "partition": next(k for k, ids in recipe["split_event_ids"].items() if r.event_id in ids),
            "legacy_label_nrfi": r.outcome,
        }
        for r in legacy
        if r.event_id not in accepted_ids
    ]
    write_json(output / "source_audit.json", audit)
    train_end = max(by_id[e].date for e in recipe["split_event_ids"]["train"])
    priors = compute_first_inning_priors(
        clean_path, end_utc=parse_utc(train_end + "T00:00:00Z") + timedelta(days=1)
    )
    data = sorted(build_rows(snapshots, priors), key=lambda r: (r.date, r.event_id))
    fixed_ids = {k: [e for e in ids if e in accepted_ids] for k, ids in recipe["split_event_ids"].items()}
    partition(data, split_event_ids=fixed_ids)
    (output / "features.jsonl").write_text("".join(json.dumps(asdict(r)) + "\n" for r in data))
    model_dir = output / "models/mlb-nrfi"
    report = train_candidate(
        data,
        "MLB",
        "nrfi",
        recipe["incumbent_model_id"],
        model_dir,
        FEATURE_NAMES,
        {
            "source_sha256": file_hash(output / "snapshots.jsonl"),
            "clean_source_sha256": file_hash(clean_path),
            "priors": priors,
            "prior_training_end": train_end,
            "historical_observation_times_verified": False,
        },
        model_id_override=MODEL_ID,
        research_family=FAMILY,
        split_event_ids=fixed_ids,
    )
    candidates = read_rows(model_dir / "evaluation_predictions.jsonl")
    controls = {r["event_id"]: reference_prediction[i] for i, r in enumerate(saved)}
    p = np.asarray([r["prediction"] for r in candidates])
    q = np.asarray([controls[r["event_id"]] for r in candidates])
    y = np.asarray([int(r["target"]) for r in candidates])
    onehot = np.eye(2)[y]
    loss, reference_loss = ((p - onehot) ** 2).sum(1), ((q - onehot) ** 2).sum(1)
    interval = cluster_interval(reference_loss - loss, [r["date"] for r in candidates])
    metrics = {
        "candidate_accuracy": float((p.argmax(1) == y).mean()),
        "reference_accuracy": float((q.argmax(1) == y).mean()),
        "candidate_vector_brier": float(loss.mean()),
        "reference_vector_brier": float(reference_loss.mean()),
        "candidate_log_loss": float(-np.log(p[np.arange(len(y)), y]).mean()),
        "reference_log_loss": float(-np.log(q[np.arange(len(y)), y]).mean()),
        "reference_minus_candidate_vector_brier": interval,
    }
    write_json(output / "manifest.json", {"models": [report], "promote": False})
    root = Path(__file__).resolve().parents[1]
    for name in (
        "scripts/train_clean_nrfi_research.py",
        "src/model_prediction/research_nrfi.py",
        "src/model_prediction/research_generation.py",
        "src/model_prediction/research_forward.py",
    ):
        capture(root / name, output / "code_snapshot" / name)
    return {
        "model": report,
        "source_audit": audit,
        "reference_reproduction": {"rows": len(saved), "max_absolute_error": float(errors.max())},
        "comparison_events": len(candidates),
        "metrics": metrics,
        "verdict": "EXPLORATORY_GAIN_VS_RESEARCH_REFERENCE"
        if interval["lower_95"] > 0.004
        else "NO_RELIABLE_GAIN_VS_RESEARCH_REFERENCE",
        "production_superiority": None,
        "profitability": None,
        "historical_development_only": True,
        "promote": False,
    }


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.reference, args.source, args.output)
    print(
        json.dumps(
            {k: result[k] for k in ("reference_reproduction", "comparison_events", "metrics", "verdict")},
            indent=2,
        )
    )
