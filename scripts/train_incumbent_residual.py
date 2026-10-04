"""One fixed retrospective experiment; refuses to overwrite prior outputs."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from model_prediction.incumbent_residual import date_split, fit_offset, predict_artifact, predict_offset
from model_prediction.models.learned_market import LearnedMarketArtifact, artifact_hash
from model_prediction.research_generation import FEATURE_NAMES, file_hash
from model_prediction.research_incumbent_evaluation import (
    canonical_hash,
    economic_metrics,
    predictive_metrics,
    replay,
    simulate,
    validate_quote,
)


def run(root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    policy = {
        "written_at_utc": datetime.now(UTC).isoformat(),
        "objective": "Preserve v8 probability and test regularized incremental corrections.",
        "split": "60/20/20 complete UTC dates: train/select/test; no post-hoc calibration",
        "variants": ["identity", "offset_penalty_0.1", "offset_penalty_1.0", "offset_penalty_10.0"],
        "selection": "minimum mean log loss on selection dates; identity wins exact ties",
        "prior_test_exposure": "All source dates were examined in the previous comparison. Development only.",
        "promotion": False,
    }
    (output / "policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    comparison = root / "outputs/research/incumbent_comparison_20260909_verified"
    generation = root / "outputs/research/generation_20260909_v1_verified"
    artifact_path = root / "config/models/mlb-elo-trend-lr-v8.json"
    artifact_file_hash = file_hash(artifact_path)
    artifact = LearnedMarketArtifact.load(artifact_path)
    source_report = json.loads((comparison / "report.json").read_text())
    snapshot_path = comparison / "ledger_snapshot.jsonl"
    if file_hash(snapshot_path) != source_report["ledger_snapshot_sha256"]:
        raise ValueError("canonical_snapshot_hash_mismatch")
    records = {r["pick_id"]: r for r in map(json.loads, snapshot_path.open())}
    pairs = sorted(
        map(json.loads, (comparison / "mlb-moneyline/prediction_pairs.jsonl").open()),
        key=lambda row: (row["date"], row["event_id"]),
    )
    features_path = generation / "features/mlb.jsonl"
    source_model_report = next(
        r for r in source_report["models"] if r["sport"] == "MLB" and r["market"] == "moneyline"
    )
    if source_model_report["incumbent_reproduction"] != "PASS":
        raise ValueError("source_comparison_reproduction_not_passed")
    if file_hash(features_path) != source_model_report["input_evidence"]["feature_file_sha256"]:
        raise ValueError("generic_feature_file_hash_mismatch")
    generic = {r["event_id"]: r for r in map(json.loads, features_path.open())}
    names = artifact.raw["market_models"]["moneyline"]["feature_names"]
    if len({row["event_id"] for row in pairs}) != len(pairs):
        raise ValueError("duplicate_event_in_training_pairs")
    base, matrix, parity = [], [], []
    for row in pairs:
        stored = records[row["pick_id"]]
        target_home = stored["result"] == ("win" if stored["selection"] == "home" else "loss")
        if (
            stored["event_id"] != row["event_id"]
            or stored["market_type"] != "moneyline"
            or stored["selection"] not in {"home", "away"}
            or stored["result"] not in {"win", "loss"}
            or row["home_won"] != target_home
            or generic[row["event_id"]]["outcome"] != int(target_home)
            or generic[row["event_id"]]["date"] != row["date"]
            or generic[row["event_id"]]["feature_max_event_date"] >= row["date"]
        ):
            raise ValueError("residual_pair_context_or_target_mismatch")
        audit = replay(stored, artifact)
        if audit["status"] != "PASS":
            raise ValueError("incumbent_reproduction_failed")
        parity.append(audit)
        f = json.loads(stored["feature_payload_json"])["features"]
        base.append(artifact.probability("moneyline", {n: float(f[n]) for n in names}))
        matrix.append([float(f[n]) for n in names] + generic[row["event_id"]]["features"])
    x, p, y = np.array(matrix), np.array(base), np.array([r["home_won"] for r in pairs], dtype=float)
    dates = [r["date"] for r in pairs]
    masks = date_split(dates)
    train, select, test = masks["train"], masks["select"], masks["test"]
    variants = {"identity": {"kind": "identity"}}
    for penalty in (0.1, 1.0, 10.0):
        variants[f"offset_penalty_{penalty}"] = fit_offset(p[train], x[train], y[train], penalty)
    losses = {}
    for name, parameters in variants.items():
        prediction = predict_offset(parameters, p[select], x[select])
        losses[name] = float(
            -np.mean(y[select] * np.log(prediction) + (1 - y[select]) * np.log1p(-prediction))
        )
    chosen = min(losses, key=losses.__getitem__)
    payload = {
        "model_id": "mlb-moneyline-incumbent-residual-20260909-v2",
        "method": "incumbent_offset_logit_v1",
        "parameters": variants[chosen],
        "incumbent_artifact": artifact.raw,
        "feature_names": [f"incumbent:{n}" for n in names] + [f"generic:{n}" for n in FEATURE_NAMES],
        "selected_variant": chosen,
        "splits": {
            name: {
                "n": len(ids),
                "dates": len({dates[i] for i in ids}),
                "start": dates[ids[0]],
                "end": dates[ids[-1]],
            }
            for name, ids in masks.items()
        },
        "status": "RETROSPECTIVE_DEVELOPMENT_ONLY",
        "promote": False,
    }
    payload["artifact_hash"] = artifact_hash(payload)
    model_path = output / "model.json"
    model_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    loaded = json.loads(model_path.read_text())
    if loaded["artifact_hash"] != artifact_hash(loaded):
        raise ValueError("candidate_artifact_hash_mismatch")
    prediction = predict_artifact(model_path, x[test])
    if not np.array_equal(prediction, predict_offset(variants[chosen], p[test], x[test])):
        raise ValueError("candidate_serialization_parity_failed")
    evaluated = [
        pairs[i] | {"incumbent_home_probability": float(p[i]), "candidate_home_probability": float(pred)}
        for i, pred in zip(test, prediction, strict=True)
    ]
    quotes = {
        r["event_id"]: r for r in map(json.loads, (comparison / "mlb-moneyline/economic_rows.jsonl").open())
    }
    economics = []
    for row in evaluated:
        if row["event_id"] not in quotes:
            continue
        quote = quotes[row["event_id"]]
        if canonical_hash(quote["snapshot"]) != quote["full_record_sha256"]:
            raise ValueError("frozen_economic_quote_hash_mismatch")
        record = records[row["pick_id"]]
        side = validate_quote(record, quote["snapshot"], row["home_team"], row["away_team"])
        economics.append(
            row
            | {
                "snapshot": quote["snapshot"],
                "incumbent": simulate(
                    row["incumbent_home_probability"], row["home_won"], quote["snapshot"], side
                ),
                "candidate": simulate(
                    row["candidate_home_probability"], row["home_won"], quote["snapshot"], side
                ),
            }
        )
    metrics = predictive_metrics(evaluated)
    assert metrics is not None
    interval = metrics["brier_gain_date_bootstrap"]
    report = {
        "model_id": payload["model_id"],
        "selected_variant": chosen,
        "selection_log_loss": losses,
        "splits": payload["splits"],
        "incumbent_reproduction": {"status": "PASS", "contexts": len(parity)},
        "predictive": metrics,
        "economics": economic_metrics(economics),
        "verdict": "DEVELOPMENT_GAIN_ONLY" if interval["lower_95"] > 0.002 else "NO_RELIABLE_GAIN",
        "confirmation_status": "NOT_FRESH_CONFIRMATION",
        "promote": False,
        "source_ledger_snapshot_sha256": file_hash(snapshot_path),
        "model_sha256": file_hash(model_path),
        "generic_feature_file_sha256": file_hash(features_path),
        "policy_sha256": file_hash(output / "policy.json"),
        "source_files_sha256": {
            str(path): file_hash(path)
            for path in (
                comparison / "report.json",
                comparison / "mlb-moneyline/prediction_pairs.jsonl",
                comparison / "mlb-moneyline/economic_rows.jsonl",
            )
        },
        "historical_source_observation_times_verified": False,
    }
    for name, rows in (
        ("incumbent_replay", parity),
        ("evaluation_predictions", evaluated),
        ("economic_rows", economics),
    ):
        (output / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows)
        )
    if file_hash(artifact_path) != artifact_file_hash:
        raise ValueError("incumbent_artifact_changed")
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path.cwd(), args.output)
