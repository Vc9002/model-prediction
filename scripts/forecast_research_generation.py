"""Capture and replay an isolated generation forecast from explicit fixtures.

Usage: PYTHONPATH=src:. .venv/bin/python scripts/forecast_research_generation.py
  --generation outputs/research/generation_20260909_v1_verified
  --fixtures path/to/fixtures.jsonl --output outputs/research/new_capture

Each fixture declares sport, market, event_id, timezone-qualified event_start_utc,
and the same participant IDs used by the historical store. See research_forward.
The command freezes sources and artifacts into a new output directory; it never
logs production picks. Partial failures remain visible and return exit code 2.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.research_forward import forecast, replay_forecast
from model_prediction.research_generation import digest, file_hash, load_games, source_path


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def capture(source: Path, target: Path) -> dict[str, Any]:
    before = source.stat()
    data = source.read_bytes()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("source_changed_during_capture")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(data)
    return {
        "source": str(source.resolve()),
        "archive": str(target.resolve()),
        "source_sha256": file_hash(target),
        "captured_at_utc": datetime.now(UTC).isoformat(),
    }


def run(root: Path, generation: Path, fixtures: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        return _run_owned(root, generation, fixtures, output)
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run_owned(root: Path, generation: Path, fixtures: Path, output: Path) -> dict[str, Any]:
    capture(fixtures, output / "fixtures.jsonl")
    capture(generation / "manifest.json", output / "generation_manifest.json")
    manifest = json.loads((output / "generation_manifest.json").read_text())
    models = {(r["sport"], r["market"]): r for r in manifest["models"]}
    requests = [
        json.loads(line) for line in (output / "fixtures.jsonl").read_text().splitlines() if line.strip()
    ]
    if not requests:
        raise ValueError("empty_fixture_batch")
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    histories: dict[str, Any] = {}
    artifacts: dict[tuple[str, str], Path] = {}
    results = []
    seen = set()
    for raw in requests:
        sport, market = str(raw.get("sport", "")), str(raw.get("market", ""))
        key = (sport, market)
        identity = (sport, market, raw.get("event_id"), raw.get("line"))
        try:
            if identity in seen:
                raise ValueError("duplicate_fixture_contract")
            seen.add(identity)
            if key not in models or models[key].get("status") != "TRAINED_RESEARCH_ONLY":
                raise ValueError("unknown_or_untrained_generation_model")
            if key not in artifacts:
                directory = generation / "models" / f"{sport.lower()}-{market}"
                destination = output / "models" / directory.name
                model_path = destination / "model.joblib"
                capture(directory / "model.joblib", model_path)
                capture(directory / "recipe.json", destination / "recipe.json")
                if file_hash(model_path) != models[key]["artifact_sha256"]:
                    raise ValueError("generation_manifest_artifact_mismatch")
                artifacts[key] = model_path
            source_key = (sport, "nrfi" if market == "nrfi" else "games")
            if source_key not in sources:
                source = (
                    root / "data/mlb_statsapi/game_snapshots.jsonl"
                    if market == "nrfi"
                    else source_path(root, sport)
                )
                archived = output / "sources" / f"{sport.lower()}-{source_key[1]}.jsonl"
                sources[source_key] = capture(source, archived)
                if market != "nrfi":
                    games, audit = load_games(archived, sport, datetime.now(UTC).date().isoformat())
                    histories[sport] = games
                    sources[source_key]["normalized_games_sha256"] = digest([asdict(g) for g in games])
                    sources[source_key]["data_audit"] = audit
            observed = datetime.now(UTC).isoformat()
            record = forecast(
                artifacts[key],
                raw,
                sport,
                market,
                observed,
                games=histories.get(sport, []),
                source_evidence=sources[source_key],
                nrfi_snapshot_path=Path(sources[source_key]["archive"]) if market == "nrfi" else None,
            )
            if datetime.fromisoformat(raw["event_start_utc"]) <= datetime.now(UTC):
                raise ValueError("event_started_before_capture_completed")
            replay_forecast(record, artifacts[key])
            record = {"status": "CAPTURED_REPLAY_PASS", "prediction": record}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            record = {
                "status": "NO_PREDICTION",
                "sport": sport,
                "market": market,
                "event_id": raw.get("event_id"),
                "reason": str(exc),
            }
        results.append(record)
        with (output / "predictions.jsonl").open("a") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")
    counts = Counter(r["status"] for r in results)
    coverage = []
    for sport, market in models:
        matching = [
            r
            for r in results
            if (r.get("prediction", r).get("sport"), r.get("prediction", r).get("market")) == (sport, market)
        ]
        coverage.append(
            {
                "sport": sport,
                "market": market,
                "requested": len(matching),
                "captured": sum(r["status"] == "CAPTURED_REPLAY_PASS" for r in matching),
                "status": "NO_FIXTURE_REQUESTED"
                if not matching
                else "CAPTURED"
                if all(r["status"] == "CAPTURED_REPLAY_PASS" for r in matching)
                else "PARTIAL_OR_FAILED",
            }
        )
    code_root = Path(__file__).resolve().parents[1]
    report: dict[str, Any] = {
        "status": "COMPLETE" if counts["NO_PREDICTION"] == 0 else "PARTIAL",
        "counts": dict(counts),
        "coverage": coverage,
        "sources": list(sources.values()),
        "production_ledger_written": False,
        "promote": False,
        "historical_source_observation_times_verified": False,
        "accuracy": None,
        "profitability": None,
        "code_sha256": {
            str(p.relative_to(code_root)): file_hash(p)
            for p in [
                Path(__file__).resolve(),
                code_root / "src/model_prediction/research_forward.py",
                code_root / "src/model_prediction/research_generation.py",
                code_root / "src/model_prediction/joint_score_research.py",
            ]
        },
    }
    for name in report["code_sha256"]:
        capture(code_root / name, output / "code_snapshot" / name)
    write_json(output / "report.json", report)
    write_json(output / "RUN_STATUS.json", {"status": report["status"]})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.root, args.generation, args.fixtures, args.output)
    print(json.dumps({"status": report["status"], "counts": report["counts"]}))
    raise SystemExit(0 if report["status"] == "COMPLETE" else 2)
