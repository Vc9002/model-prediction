"""Audit all 21 registered research candidates using a canonical ledger snapshot.

Run with PYTHONPATH=src:. Output directories must be new. Comparisons are
blocked unless the current incumbent can reproduce its stored decisions.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.research_generation import build_rows, file_hash, load_games, predict_bundle
from model_prediction.research_incumbent_evaluation import (
    POLICY,
    QuoteArchive,
    canonical_hash,
    deduplicate,
    economic_metrics,
    names_match,
    predictive_metrics,
    probability,
    read_records,
    replay,
    simulate,
    timestamp,
    validate_quote,
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def unique_rows(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    conflicts = set()
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row["event_id"])
            if key in result and result[key] != row:
                conflicts.add(key)
            result[key] = row
    for key in conflicts:
        del result[key]
    return result


def paired_rows(
    records: list[dict[str, Any]],
    model: dict[str, Any],
    generation: Path,
    manifest: dict[str, Any],
    archive: QuoteArchive,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, int], dict[str, Any]]:
    directory = generation / "models" / f"{model['sport'].lower()}-{model['market']}"
    predictions = unique_rows(directory / "evaluation_predictions.jsonl")
    feature_path = generation / "features" / f"{model['sport'].lower()}.jsonl"
    features = unique_rows(feature_path)
    audit = manifest["data_audits"][model["sport"]]
    source = Path(audit["source"])
    current_source_hash = file_hash(source)
    source_check = "SOURCE_BYTES_IDENTICAL"
    if current_source_hash != audit["source_sha256"]:
        normalized, _ = load_games(source, model["sport"], manifest["as_of_utc_date_exclusive"])
        rebuilt = {row.event_id: asdict(row) for row in build_rows(normalized, model["sport"])}
        for row in rebuilt.values():
            row["features"] = list(row["features"])
        if rebuilt != features:
            raise ValueError("historical_source_changed_and_normalized_inputs_differ")
        source_check = "SOURCE_BYTES_CHANGED_ALL_NORMALIZED_INPUTS_IDENTICAL"
    games = unique_rows(source)
    ids = sorted(predictions)
    reproduced = predict_bundle(Path(model["artifact"]), np.array([features[i]["features"] for i in ids]))
    if not np.allclose(reproduced, np.array([predictions[i]["prediction"] for i in ids]), atol=1e-12, rtol=0):
        raise ValueError("candidate_saved_prediction_replay_failed")
    paired, economics = [], []
    excluded: Counter[str] = Counter()
    quote_excluded: Counter[str] = Counter()
    earliest: dict[str, dict[str, Any]] = {}
    for row in sorted(records, key=lambda r: (timestamp(r["created_at_utc"]), r["pick_id"])):
        if row["event_id"] in earliest:
            excluded["later_decision_same_event"] += 1
        else:
            earliest[row["event_id"]] = row
    for event_id, row in earliest.items():
        try:
            if event_id not in predictions or event_id not in games:
                raise ValueError("no_exact_candidate_holdout_event")
            if row["result"] not in {"win", "loss"}:
                raise ValueError("push_or_nonbinary_settlement")
            if row["selection"] not in {"home", "away"}:
                raise ValueError("unsupported_selection")
            game, pred = games[event_id], predictions[event_id]
            decision = json.loads(row["decision_payload_json"])
            if timestamp(game["event_start_utc"]) != timestamp(row["event_start_utc"]):
                raise ValueError("source_start_mismatch")
            if timestamp(row["created_at_utc"]) >= timestamp(row["event_start_utc"]):
                raise ValueError("decision_not_pregame")
            if timestamp(row["settled_at_utc"]) <= timestamp(row["event_start_utc"]):
                raise ValueError("settlement_not_post_start")
            if pred["feature_max_event_date"] >= timestamp(row["created_at_utc"]).date().isoformat():
                raise ValueError("candidate_features_not_before_decision")
            home, away = str(game["home_team"]), str(game["away_team"])
            if (
                not names_match(home, str(decision.get("home_team") or ""))
                or not names_match(away, str(decision.get("away_team") or ""))
                or names_match(home, str(decision.get("away_team") or ""))
                or names_match(away, str(decision.get("home_team") or ""))
            ):
                raise ValueError("source_participants_mismatch")
            home_score, away_score = float(game["home_score"]), float(game["away_score"])
            if (
                float(decision["home_score"]) != home_score
                or float(decision["away_score"]) != away_score
                or home_score == away_score
            ):
                raise ValueError("score_or_tie_mismatch")
            won_home = home_score > away_score
            if (row["result"] == "win") != (won_home if row["selection"] == "home" else not won_home):
                raise ValueError("settlement_outcome_mismatch")
            if pred["target"] != float(won_home):
                raise ValueError("candidate_target_mismatch")
            incumbent_p = probability(row["model_probability"])
            if row["selection"] == "away":
                incumbent_p = 1 - incumbent_p
            candidate_p = probability(pred["prediction"][1])
            pair = {
                "event_id": event_id,
                "pick_id": row["pick_id"],
                "date": pred["date"],
                "home_won": won_home,
                "home_team": home,
                "away_team": away,
                "incumbent_home_probability": incumbent_p,
                "candidate_home_probability": candidate_p,
                "created_at_utc": row["created_at_utc"],
                "event_start_utc": row["event_start_utc"],
                "settled_at_utc": row["settled_at_utc"],
                "home_score": home_score,
                "away_score": away_score,
            }
            paired.append(pair)
        except (KeyError, ValueError, TypeError) as exc:
            excluded[str(exc)] += 1
            continue
        try:
            snapshot, linkage = archive.lookup(row)
            side = validate_quote(row, snapshot, home, away)
            economics.append(
                pair
                | {
                    "snapshot": snapshot,
                    "full_record_sha256": canonical_hash(snapshot),
                    "stored_snapshot_hash": row["market_snapshot_hash"],
                    "linkage": linkage,
                    "incumbent": simulate(incumbent_p, won_home, snapshot, side),
                    "candidate": simulate(candidate_p, won_home, snapshot, side),
                }
            )
        except (KeyError, ValueError, TypeError) as exc:
            quote_excluded[str(exc)] += 1
    if file_hash(source) != current_source_hash:
        raise ValueError("historical_source_changed_during_evaluation")
    evidence = {
        "source_check": source_check,
        "training_source_sha256": audit["source_sha256"],
        "evaluation_source_sha256": current_source_hash,
        "frozen_feature_rows": len(features),
        "feature_file_sha256": file_hash(feature_path),
        "candidate_saved_prediction_replay": "PASS",
        "candidate_replay_rows": len(ids),
        "candidate_model_sha256": file_hash(Path(model["artifact"])),
        "candidate_prediction_file_sha256": file_hash(directory / "evaluation_predictions.jsonl"),
    }
    return paired, economics, dict(excluded), dict(quote_excluded), evidence


def run(root: Path, generation: Path, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    # Written before reading outcomes. This is a retrospective research policy,
    # not a claim of prospective preregistration or previously unseen data.
    write_json(output / "policy.json", POLICY | {"written_at_utc": datetime.now(UTC).isoformat()})
    manifest = json.loads((generation / "manifest.json").read_text())
    registry = yaml.safe_load((root / "config/production.yaml").read_text())
    current = {
        (m["sport"], m["market"]): m
        for m in registry["prediction_service"]["models"]
        if m["enabled"] and m["serving_status"] == "production"
    }
    if {(m["sport"], m["market"]): m["incumbent"] for m in manifest["models"]} != {
        key: value["model_id"] for key, value in current.items()
    }:
        raise ValueError("production_registry_changed_since_generation")
    protected = [
        root / "config/production.yaml",
        *sorted((root / "config/models").rglob("*.json")),
        root / "src/model_prediction/models/soccer.py",
        root / "src/model_prediction/models/tennis.py",
    ]
    before = {str(path): file_hash(path) for path in protected}
    records = read_records(database, [m["incumbent"] for m in manifest["models"]])
    write_rows(output / "ledger_snapshot.jsonl", records)
    archive = QuoteArchive(root / "data/odds")
    reports = []
    for model in manifest["models"]:
        key = f"{model['sport'].lower()}-{model['market']}"
        directory = output / key
        directory.mkdir()
        raw = [
            r
            for r in records
            if r["model_id"] == model["incumbent"]
            and str(r["sport"]).upper() == model["sport"]
            and r["market_type"] == model["market"]
        ]
        rows, duplicates = deduplicate(raw)
        artifact = None
        if model["market"] == "moneyline" and model["sport"] in {"MLB", "WNBA", "NBA", "NFL"}:
            artifact = LearnedMarketArtifact.load(
                root / current[(model["sport"], model["market"])]["artifact"]
            )
        parities = [replay(row, artifact) for row in rows]
        write_rows(directory / "replay_rows.jsonl", parities)
        counts = Counter(p["status"] for p in parities)
        exact = [row for row in rows if artifact and row["model_artifact_hash"] == artifact.hash]
        captured = []
        for row in rows:
            try:
                snapshot = json.loads(row.get("feature_payload_json") or "{}").get("model_input_snapshot")
                if snapshot is not None:
                    captured.append(row)
            except (ValueError, TypeError):
                pass
        gate = "NO_DATA" if not rows else "UNREPRODUCIBLE"
        if artifact and exact:
            gate = "PASS" if counts["PASS"] == len(exact) else "FAIL"
        if artifact is None and captured:
            # Historical rows without state stay visible separately. New captured
            # cohorts must all reproduce; never select only passing decisions.
            captured_ids = {row["pick_id"] for row in captured}
            gate = (
                "PASS"
                if all(p["status"] == "PASS" for p in parities if p["pick_id"] in captured_ids)
                else "FAIL"
            )
        if duplicates.get("conflicting_context_rows") or duplicates.get("invalid_context"):
            gate = "FAIL_CONFLICTING_OR_INVALID_CONTEXT"
        report: dict[str, Any] = {
            "sport": model["sport"],
            "market": model["market"],
            "incumbent": model["incumbent"],
            "candidate": model["model_id"],
            "settled_ledger_rows": len(raw),
            "unique_contexts": len(rows),
            "deduplication": duplicates,
            "incumbent_reproduction": gate,
            "replay_counts": dict(counts),
            "exact_current_artifact_contexts": len(exact) if artifact else None,
            "captured_serving_state_contexts": len(captured),
            "capture_adapter_implemented": True,
            "rows_with_stored_features": sum(
                bool(json.loads(r["feature_payload_json"] or "{}").get("features")) for r in rows
            ),
            "rows_with_quote_references": sum(bool(r["market_snapshot_hash"]) for r in rows),
            "predictive": None,
            "economics": economic_metrics([]) | {"status": "NOT_EVALUATED_REPLAY_BLOCKED"},
            "promote": False,
            "verdict": "COMPARISON_BLOCKED",
            "historical_feature_observation_times_verified": False,
        }
        report["replay_scope"] = (
            "Exact stored inputs through the hash-verified current binary learned artifact."
            if artifact
            else "Self-contained serving-state replay adapter; legacy rows without captured state cannot be reconstructed."
        )
        report["blocker"] = {
            "NO_DATA": "No settled canonical-ledger decisions for this exact current model and market.",
            "UNREPRODUCIBLE": "Historical decision inputs/state are absent; implemented capture adapters apply to new decisions only.",
            "FAIL": "Current artifact replay has missing/invalid inputs or probability mismatches.",
            "FAIL_CONFLICTING_OR_INVALID_CONTEXT": "Conflicting or invalid decision contexts require investigation.",
            "PASS": None,
        }[gate]
        if gate == "PASS" and artifact is None:
            report.update(
                verdict="AWAITING_EXACT_MARKET_AWARE_PAIRS",
                blocker="Captured incumbent state reproduces. Exact candidate decision contexts and market-aware economic evidence are required.",
            )
            report["economics"]["status"] = "NOT_EVALUATED_NO_EXACT_MARKET_PAIRS"
        if gate == "PASS" and artifact is not None:
            pairs, economic_rows, excluded, quote_excluded, input_evidence = paired_rows(
                exact, model, generation, manifest, archive
            )
            write_rows(directory / "prediction_pairs.jsonl", pairs)
            write_rows(directory / "economic_rows.jsonl", economic_rows)
            metrics = predictive_metrics(pairs)
            report.update(
                predictive=metrics,
                economics=economic_metrics(economic_rows),
                pair_exclusions=excluded,
                quote_exclusions=quote_excluded,
                input_evidence=input_evidence,
            )
            report["verdict"] = "INSUFFICIENT_PAIRED_EVIDENCE"
            if (
                metrics
                and metrics["events"] >= POLICY["minimum_events"]
                and metrics["dates"] >= POLICY["minimum_dates"]
            ):
                interval = metrics["brier_gain_date_bootstrap"]
                report["verdict"] = (
                    "RETROSPECTIVE_PREDICTIVE_GAIN_ONLY"
                    if interval["lower_95"] > POLICY["minimum_brier_gain"]
                    else "NO_RELIABLE_PREDICTIVE_GAIN"
                )
        write_json(directory / "report.json", report)
        reports.append(report)
        print(key, gate, report["verdict"], flush=True)
    after = {str(path): file_hash(path) for path in protected}
    if after != before:
        raise ValueError("production_artifacts_changed_during_evaluation")
    result = {
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "models": reports,
        "ledger_source": str(database),
        "ledger_snapshot_sha256": file_hash(output / "ledger_snapshot.jsonl"),
        "generation_manifest_sha256": file_hash(generation / "manifest.json"),
        "policy_sha256": file_hash(output / "policy.json"),
        "production_artifacts_unchanged": True,
        "protected_file_hashes": before,
        "profitability_proven": False,
        "promotions": 0,
        "limitations": [
            "Historical features are not verified point in time.",
            "Incumbent-ledger selection limits the sampled opportunity universe.",
            "Single top-of-book hypothetical fills do not establish realized execution.",
            "No future-data or multiple-testing-adjusted superiority claim.",
        ],
    }
    write_json(output / "report.json", result)
    snapshot_dir = output / "code_snapshot"
    snapshot_dir.mkdir()
    for source in (
        root / "scripts/evaluate_research_incumbents.py",
        root / "src/model_prediction/research_incumbent_evaluation.py",
    ):
        shutil.copy2(source, snapshot_dir / source.name)
    lines = [
        "# Incumbent comparison — 2026-09-09",
        "",
        "No candidate is approved for promotion. Failed replay blocks comparison; no missing economics becomes zero.",
        "",
        "| Sport | Market | Settled rows | Unique contexts | Replay | Verdict |",
        "|---|---|---:|---:|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report['sport']} | {report['market']} | {report['settled_ledger_rows']} | "
            f"{report['unique_contexts']} | {report['incumbent_reproduction']} | {report['verdict']} |"
        )
    for report in reports:
        if report["predictive"]:
            lines.extend(
                [
                    "",
                    f"## {report['sport']} {report['market']}",
                    "",
                    "```json",
                    json.dumps(report, indent=2),
                    "```",
                ]
            )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root.resolve(), args.generation.resolve(), args.database.resolve(), args.output.resolve())
