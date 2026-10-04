"""Score archived research forecasts against captured completed outcome records.

Existing outputs are immutable. No source is fetched, prediction refitted,
production ledger written, or missing economics replaced with a price proxy.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.research_forward import rebuild_forecast
from model_prediction.research_generation import digest, file_hash, source_path
from model_prediction.research_scoring import outcome_label, proper_score
from scripts.forecast_research_generation import capture, write_json


def run(
    root: Path, forecasts: Path, output: Path, *, outcome_overrides: dict[str, str] | None = None
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        return _run_owned(root, forecasts, output, outcome_overrides or {})
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run_owned(
    root: Path, forecasts: Path, output: Path, outcome_overrides: dict[str, str]
) -> dict[str, Any]:
    forecast_evidence = capture(forecasts / "predictions.jsonl", output / "forecast_records.jsonl")
    capture(forecasts / "generation_manifest.json", output / "generation_manifest.json")
    if (forecasts / "preparation.json").exists():
        capture(forecasts / "preparation.json", output / "preparation.json")
    manifest = json.loads((output / "generation_manifest.json").read_text())
    records = [
        json.loads(line)
        for line in (output / "forecast_records.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if not records:
        raise ValueError("empty_forecast_batch")
    registered = {(m["sport"], m["market"]): m for m in manifest["models"]}
    outcome_sources: dict[tuple[str, str], dict[str, Any]] = {}
    outcome_indices: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    seen, results = set(), []
    for record in records:
        p = record.get("prediction", record)
        sport, market = p["sport"], p["market"]
        base = {"sport": sport, "market": market, "event_id": p.get("fixture", p).get("event_id")}
        if record["status"] != "CAPTURED_REPLAY_PASS":
            results.append(base | {"status": "NO_FORECAST", "reason": record.get("reason")})
            continue
        try:
            registered_model = registered[(sport, market)]
            if (
                p["model_id"] != registered_model["model_id"]
                or p["model_file_sha256"] != registered_model["artifact_sha256"]
            ):
                raise ValueError("generation_manifest_model_mismatch")
            key = (sport, "nrfi" if market == "nrfi" else "games")
            identity = (sport, market, base["event_id"], p["line"])
            if identity in seen:
                raise ValueError("duplicate_forecast_contract")
            seen.add(identity)
            directory = f"{sport.lower()}-{market}"
            model_path = output / "models" / directory / "model.joblib"
            if not model_path.exists():
                for filename in ("model.joblib", "recipe.json"):
                    capture(forecasts / "models" / directory / filename, model_path.with_name(filename))
            input_path = output / "inputs" / f"{sport.lower()}-{key[1]}.jsonl"
            if not input_path.exists():
                capture(forecasts / "sources" / input_path.name, input_path)
            probabilities = rebuild_forecast(p, model_path, input_path)
            if key not in outcome_sources:
                source = (
                    root / "data/mlb_statsapi/game_snapshots.jsonl"
                    if market == "nrfi"
                    else source_path(root, sport)
                )
                if f"{sport}:{key[1]}" in outcome_overrides:
                    source = Path(outcome_overrides[f"{sport}:{key[1]}"])
                destination = output / "outcomes" / input_path.name
                outcome_sources[key] = capture(source, destination)
                index: dict[str, list[dict[str, Any]]] = defaultdict(list)
                with destination.open() as handle:
                    for line in handle:
                        raw = json.loads(line)
                        event = str(raw.get("event_id") or raw.get("match_id") or raw.get("game_id") or "")
                        if market == "nrfi":
                            from model_prediction.research_forward import aware_time

                            event = aware_time(raw["game_start_utc"]).isoformat()
                        index[event].append(raw)
                outcome_indices[key] = index
            evidence = outcome_sources[key]
            event = str(base["event_id"])
            if market == "nrfi":
                from model_prediction.research_forward import aware_time

                event = aware_time(p["raw_fixture"]["event_start_utc"]).isoformat()
                raws = [
                    r
                    for r in outcome_indices[key].get(event, [])
                    if r.get("home", {}).get("team_name") == p["raw_fixture"]["home_team"]
                    and r.get("away", {}).get("team_name") == p["raw_fixture"]["away_team"]
                ]
            else:
                raws = outcome_indices[key].get(event, [])
            if not raws:
                results.append(base | {"status": "AWAITING_OUTCOME", "replay": "PASS"})
                continue
            labels, failures = [], []
            for raw in raws:
                try:
                    labels.append((outcome_label(p, raw, evidence["captured_at_utc"]), raw))
                except (ValueError, KeyError, TypeError) as exc:
                    failures.append(str(exc))
            if labels and any(
                "identity_mismatch" in reason
                or "participant_or_event_mismatch" in reason
                or "start_mismatch" in reason
                or "surface_mismatch" in reason
                for reason in failures
            ):
                raise ValueError("conflicting_outcome_identity")
            if (
                market == "nrfi"
                and len({(raw["first_inning_runs_home"], raw["first_inning_runs_away"]) for _, raw in labels})
                > 1
            ):
                raise ValueError("conflicting_first_inning_scores")
            if not labels:
                results.append(
                    base
                    | {"status": "OUTCOME_UNAVAILABLE", "replay": "PASS", "reasons": sorted(set(failures))}
                )
                continue
            if len({label for label, _ in labels}) != 1 or (
                market == "nrfi"
                and len(
                    {
                        raw.get("event_id")
                        if raw.get("source_schema") == "espn-scoreboard-result-v1"
                        else raw["game_pk"]
                        for _, raw in labels
                    }
                )
                != 1
            ):
                raise ValueError("conflicting_outcome_records")
            # Conflicting full scores must not be hidden by an unchanged win label.
            if market != "nrfi":
                from datetime import timedelta

                from model_prediction.research_generation import normalize_game

                as_of = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
                if len({normalize_game(raw, sport, as_of) for _, raw in labels}) != 1:
                    raise ValueError("conflicting_completed_scores")
            label, raw = labels[0]
            results.append(
                base
                | {
                    "status": "SCORED",
                    "replay": "PASS",
                    "snapshot_hash": p["snapshot_hash"],
                    "outcome_record_hash": digest(raw),
                    "outcome_source": evidence,
                    **proper_score(probabilities, label),
                }
            )
        except (ValueError, KeyError, TypeError, OSError) as exc:
            results.append(base | {"status": "INTEGRITY_FAILURE", "reason": str(exc)})
    with (output / "scored_rows.jsonl").open("x") as handle:
        for row in results:
            handle.write(json.dumps(row, allow_nan=False) + "\n")
    coverage = []
    for model in manifest["models"]:
        matching = [r for r in results if (r["sport"], r["market"]) == (model["sport"], model["market"])]
        scored = [r for r in matching if r["status"] == "SCORED"]
        coverage.append(
            {
                "sport": model["sport"],
                "market": model["market"],
                "counts": dict(Counter(r["status"] for r in matching)),
                "metrics": {
                    key: sum(r[key] for r in scored) / len(scored)
                    for key in ("accuracy", "brier_vector_sum", "log_loss")
                }
                if scored
                else None,
            }
        )
    result = {
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "forecasts": forecast_evidence,
        "counts": dict(Counter(r["status"] for r in results)),
        "coverage": coverage,
        "economics": None,
        "incumbent_superiority": None,
        "promote": False,
        "limitations": [
            "Completed outcome scoring alone does not establish incumbent superiority or profitability.",
            "Vector-sum Brier is twice the scalar Brier convention for binary outcomes.",
            "Source capture proves retained bytes, not historical feature observation provenance.",
        ],
    }
    code_root = Path(__file__).resolve().parents[1]
    result["code_evidence"] = [
        capture(code_root / name, output / "code" / Path(name).name)
        for name in ("scripts/score_research_forward.py", "src/model_prediction/research_scoring.py")
    ]
    write_json(output / "report.json", result)
    write_json(
        output / "RUN_STATUS.json", {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")}
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--outcome-overrides",
        type=Path,
        help="JSON mapping SPORT:games or SPORT:nrfi to explicit outcome JSONL sources",
    )
    args = parser.parse_args()
    result = run(
        args.root,
        args.forecasts,
        args.output,
        outcome_overrides=json.loads(args.outcome_overrides.read_text()) if args.outcome_overrides else None,
    )
    print(json.dumps(result["counts"], indent=2))
    return 2 if result["counts"].get("INTEGRITY_FAILURE") or not result["counts"].get("SCORED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
