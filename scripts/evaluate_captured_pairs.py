"""Evaluate captured candidates against exact canonical incumbent decisions.

Requires a completed score_research_forward output. Every pair independently
replays both models and joins its archived outcome again. No tolerant event-only
join, missing-state reconstruction, or automatic promotion is allowed.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from model_prediction.research_generation import cluster_interval, digest, file_hash
from model_prediction.research_incumbent_evaluation import (
    POLICY,
    deduplicate,
    economic_metrics,
    read_records,
    timestamp,
)
from model_prediction.research_pairing import compare_pair, first_bound_record, simulate_contracts
from model_prediction.research_quote_contracts import PairedQuoteArchive, executable_probabilities
from scripts.forecast_research_generation import capture, write_json


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(root: Path, scores: Path, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        result = _run(root, scores, database, output)
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise
    write_json(
        output / "RUN_STATUS.json", {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")}
    )
    return result


def _run(root: Path, scores: Path, database: Path, output: Path) -> dict[str, Any]:
    policy = POLICY | {
        "version": "captured_market_pairs_20260911_v2",
        "score": "vector-sum Brier including draw/push classes",
        "minimum_brier_gain": 0.004,
        "settlement": "explicit payout tables; full-game binary moneyline and MLB half-point spread/total",
        "context": "exact event, start, participants, contract line and model observation time",
        "promotion": False,
    }
    write_json(output / "policy.json", policy)
    for name in ("generation_manifest.json", "forecast_records.jsonl", "scored_rows.jsonl"):
        capture(scores / name, output / name)
    manifest = json.loads((output / "generation_manifest.json").read_text())
    preparation = None
    if (scores / "preparation.json").exists():
        capture(scores / "preparation.json", output / "preparation.json")
        preparation = json.loads((output / "preparation.json").read_text())
    candidates = {
        r["prediction"]["snapshot_hash"]: r["prediction"]
        for r in rows(output / "forecast_records.jsonl")
        if r["status"] == "CAPTURED_REPLAY_PASS"
    }
    records = read_records(database, [m["incumbent"] for m in manifest["models"]])
    with (output / "ledger_snapshot.jsonl").open("x") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    archive = PairedQuoteArchive(root)
    scored = rows(output / "scored_rows.jsonl")
    reports, all_pairs, parity = [], [], []
    for model in manifest["models"]:
        sport, market = model["sport"], model["market"]
        current = [
            r
            for r in records
            if r["model_id"] == model["incumbent"]
            and str(r["sport"]).upper() == sport
            and r["market_type"] == market
        ]
        current, duplicate_counts = deduplicate(current)
        earliest: dict[str, dict[str, Any]] = {}
        for record in sorted(current, key=lambda r: (timestamp(r["created_at_utc"]), r["pick_id"])):
            earliest.setdefault(record["event_id"], record)
        excluded: Counter[str] = Counter()
        pairs, economics = [], []
        matching = [r for r in scored if r["sport"] == sport and r["market"] == market]
        for score in matching:
            base = {"sport": sport, "market": market, "event_id": score["event_id"]}
            try:
                if duplicate_counts.get("conflicting_context_rows") or duplicate_counts.get(
                    "invalid_context"
                ):
                    raise ValueError("conflicting_or_invalid_incumbent_contexts")
                if score["status"] != "SCORED":
                    raise ValueError("candidate_outcome_not_scored")
                candidate = candidates[score["snapshot_hash"]]
                if (
                    candidate["model_id"] != model["model_id"]
                    or candidate["model_file_sha256"] != model["artifact_sha256"]
                ):
                    raise ValueError("candidate_generation_identity_mismatch")
                selected_record = earliest.get(score["event_id"])
                context = candidate.get("capture_context")
                if context is not None:
                    source_name = f"sources/{sport.lower()}-{'nrfi' if market == 'nrfi' else 'games'}.jsonl"
                    if (
                        preparation is None
                        or file_hash(output / "preparation.json") != context["preparation_sha256"]
                        or preparation["policy"] != context["policy"]
                        or preparation["prepared_at_utc"] != context["prepared_at_utc"]
                        or preparation["files"][source_name]["source_sha256"]
                        != candidate["source_evidence"]["source_sha256"]
                        or preparation["files"][f"models/{sport.lower()}-{market}/model.joblib"][
                            "source_sha256"
                        ]
                        != candidate["model_file_sha256"]
                    ):
                        raise ValueError("candidate_preparation_mismatch")
                    eligible = [
                        r
                        for r in current
                        if r["event_id"] == score["event_id"]
                        and timestamp(r["created_at_utc"]) >= timestamp(context["prepared_at_utc"])
                    ]
                    selected_record = first_bound_record(eligible, records, context)
                if selected_record is None:
                    raise ValueError("no_incumbent_decision")
                record = selected_record
                if not json.loads(record.get("feature_payload_json") or "{}").get("model_input_snapshot"):
                    raise ValueError("missing_incumbent_serving_snapshot")
                key = f"{sport.lower()}-{market}"
                model_path = output / "models" / key / "model.joblib"
                if not model_path.exists():
                    for filename in ("model.joblib", "recipe.json"):
                        capture(scores / "models" / key / filename, model_path.with_name(filename))
                source_name = f"{sport.lower()}-{'nrfi' if market == 'nrfi' else 'games'}.jsonl"
                features = output / "inputs" / source_name
                outcomes = output / "outcomes" / source_name
                if not features.exists():
                    capture(scores / "inputs" / source_name, features)
                    capture(scores / "outcomes" / source_name, outcomes)
                if file_hash(outcomes) != score["outcome_source"]["source_sha256"]:
                    raise ValueError("outcome_source_hash_mismatch")
                found = {
                    digest(raw): raw for raw in rows(outcomes) if digest(raw) == score["outcome_record_hash"]
                }
                if len(found) != 1:
                    raise ValueError("outcome_record_missing")
                paired = compare_pair(
                    record,
                    candidate,
                    model_path,
                    features,
                    next(iter(found.values())),
                    score["outcome_source"]["captured_at_utc"],
                )
                pairs.append(paired)
                parity.append(
                    base
                    | {
                        "status": "PASS",
                        "incumbent_snapshot_hash": paired["incumbent_snapshot_hash"],
                        "candidate_snapshot_hash": paired["candidate_snapshot_hash"],
                    }
                )
            except (KeyError, ValueError, TypeError, OSError) as exc:
                reason = str(exc)
                excluded[reason] += 1
                parity.append(base | {"status": "BLOCKED", "reason": reason})
                continue
            try:
                evidence = archive.contracts(record, candidate)
                trades = {
                    name: simulate_contracts(
                        executable_probabilities(paired[name + "_probabilities"], evidence),
                        paired["outcome"],
                        evidence["contracts"],
                    )
                    for name in ("incumbent", "candidate")
                }
                economic_row = paired | trades | {"quote_evidence": evidence}
                economics.append(economic_row)
                paired["economics"] = trades
                paired["quote_evidence"] = evidence
            except (KeyError, ValueError, TypeError, OSError) as exc:
                paired["economics_unavailable_reason"] = str(exc)
        metrics: dict[str, Any] | None = None
        verdict = "NO_EXACT_PAIRED_EVIDENCE"
        if pairs:
            metrics = {"events": len(pairs), "dates": len({p["date"] for p in pairs})}
            for name in ("incumbent", "candidate"):
                metrics[name] = {
                    metric: sum(p[name + "_score"][metric] for p in pairs) / len(pairs)
                    for metric in ("brier_vector_sum", "log_loss", "accuracy")
                }
            interval = cluster_interval(
                np.array(
                    [
                        p["incumbent_score"]["brier_vector_sum"] - p["candidate_score"]["brier_vector_sum"]
                        for p in pairs
                    ]
                ),
                [p["date"] for p in pairs],
            )
            metrics["brier_gain_interval"] = interval
            verdict = "INSUFFICIENT_PAIRED_EVIDENCE"
            if metrics["events"] >= policy["minimum_events"] and metrics["dates"] >= policy["minimum_dates"]:
                verdict = (
                    "PREDICTIVE_GAIN_ONLY"
                    if interval["lower_95"] > policy["minimum_brier_gain"]
                    else "NO_RELIABLE_PREDICTIVE_GAIN"
                )
        reports.append(
            {
                "sport": sport,
                "market": market,
                "incumbent": model["incumbent"],
                "candidate": model["model_id"],
                "candidate_records": len(matching),
                "paired": len(pairs),
                "excluded": dict(excluded),
                "deduplication": duplicate_counts,
                "metrics": metrics,
                "economics": economic_metrics(economics),
                "verdict": verdict,
                "promote": False,
            }
        )
        all_pairs.extend(pairs)
    for name, data in (("paired_rows.jsonl", all_pairs), ("reproduction_rows.jsonl", parity)):
        (output / name).write_text(
            "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in data)
        )
    result = {
        "models": reports,
        "policy": policy,
        "paired": len(all_pairs),
        "profitability_proven": False,
        "promote": False,
        "source_ledger": str(database),
        "ledger_snapshot_sha256": file_hash(output / "ledger_snapshot.jsonl"),
    }
    code = Path(__file__).resolve().parents[1]
    for path in (
        "scripts/evaluate_captured_pairs.py",
        "src/model_prediction/research_pairing.py",
        "src/model_prediction/research_quote_contracts.py",
    ):
        capture(code / path, output / "code_snapshot" / Path(path).name)
    write_json(output / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = run(args.root, args.scores, args.database, args.output)
    print(json.dumps({"paired": report["paired"], "models": len(report["models"])}, indent=2))
    return 0 if report["paired"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
