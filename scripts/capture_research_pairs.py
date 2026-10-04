"""Prepare frozen candidate inputs, then pair with newly recorded incumbents.

Run `prepare` before a normal incumbent forecast, and `capture` while the
explicit fixtures are still pregame. Both commands only read production state.
Preparation fixes fixtures, artifacts and sources. Capture chooses the first
incumbent decision after preparation, regardless of its predicted probability.
The candidate uses that decision's information cutoff, with its actual later
creation time recorded separately. No retrospective capture is accepted.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.learned_replay import validate_decision_snapshot
from model_prediction.research_forward import aware_time, forecast, normalize_fixture, rebuild_forecast
from model_prediction.research_generation import digest, file_hash, load_games, source_path
from model_prediction.research_incumbent_evaluation import deduplicate
from model_prediction.research_pairing import _a_is_home
from scripts.forecast_research_generation import capture as copy_file
from scripts.forecast_research_generation import write_json

POLICY = "first_incumbent_after_preparation_v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prepare(root: Path, generation: Path, fixtures: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        copied: dict[str, dict[str, Any]] = {}

        def archive(source: Path, name: str) -> None:
            if name not in copied:
                copied[name] = copy_file(source, output / name)

        archive(fixtures, "fixtures.jsonl")
        archive(generation / "manifest.json", "generation_manifest.json")
        manifest = json.loads((output / "generation_manifest.json").read_text())
        models = {(m["sport"], m["market"]): m for m in manifest["models"]}
        requests = rows(output / "fixtures.jsonl")
        if not requests:
            raise ValueError("empty_fixture_batch")
        seen = set()
        for raw in requests:
            sport, market = raw["sport"], raw["market"]
            normalize_fixture(raw, sport, now())
            identity = (sport, market, raw["event_id"], raw.get("line"))
            if identity in seen:
                raise ValueError("duplicate_fixture_contract")
            seen.add(identity)
            model = models[sport, market]
            if model["status"] != "TRAINED_RESEARCH_ONLY":
                raise ValueError("untrained_generation_model")
            key = f"{sport.lower()}-{market}"
            for filename in ("model.joblib", "recipe.json"):
                archive(generation / "models" / key / filename, f"models/{key}/{filename}")
            if file_hash(output / "models" / key / "model.joblib") != model["artifact_sha256"]:
                raise ValueError("generation_manifest_artifact_mismatch")
            kind = "nrfi" if market == "nrfi" else "games"
            source = (
                root / "data/mlb_statsapi/game_snapshots.jsonl"
                if kind == "nrfi"
                else source_path(root, sport)
            )
            archive(source, f"sources/{sport.lower()}-{kind}.jsonl")
        prepared_at = now()
        for raw in requests:
            normalize_fixture(raw, raw["sport"], prepared_at)
        result = {
            "schema": "research-pair-preparation-v1",
            "policy": POLICY,
            "prepared_at_utc": prepared_at,
            "files": copied,
            "fixtures": len(requests),
            "production_ledger_written": False,
            "promote": False,
        }
        write_json(output / "preparation.json", result)
        write_json(
            output / "RUN_STATUS.json",
            {"status": "PREPARED", "sha256": file_hash(output / "preparation.json")},
        )
        return result
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def read_decisions(database: Path, model_ids: list[str], ready: str) -> list[dict[str, Any]]:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        placeholders = ",".join("?" for _ in model_ids)
        found = connection.execute(
            f"SELECT * FROM ledger_records WHERE model_id IN ({placeholders}) "
            "AND julianday(created_at_utc) >= julianday(?) ORDER BY created_at_utc, pick_id, ledger_tier",
            [*model_ids, ready],
        ).fetchall()
    return [dict(r) for r in found]


def capture(prepared: Path, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        report = _capture(prepared, database, output)
        write_json(output / "report.json", report)
        write_json(output / "RUN_STATUS.json", {"status": report["status"]})
        return report
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _capture(prepared: Path, database: Path, output: Path) -> dict[str, Any]:
    copy_file(prepared / "preparation.json", output / "preparation.json")
    preparation = json.loads((output / "preparation.json").read_text())
    if preparation["schema"] != "research-pair-preparation-v1" or preparation["policy"] != POLICY:
        raise ValueError("unsupported_pair_preparation")
    for name, evidence in preparation["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_preparation_path")
        copy_file(prepared / relative, output / relative)
        if file_hash(output / relative) != evidence["source_sha256"]:
            raise ValueError("prepared_input_changed")
        if aware_time(evidence["captured_at_utc"]) > aware_time(preparation["prepared_at_utc"]):
            raise ValueError("prepared_input_time_mismatch")
    manifest = json.loads((output / "generation_manifest.json").read_text())
    models = {(m["sport"], m["market"]): m for m in manifest["models"]}
    records = read_decisions(
        database, [m["incumbent"] for m in models.values()], preparation["prepared_at_utc"]
    )
    with (output / "incumbent_records.jsonl").open("x") as handle:
        for record in records:
            handle.write(json.dumps(record, allow_nan=False) + "\n")
    results = []
    for raw in rows(output / "fixtures.jsonl"):
        sport, market = raw["sport"], raw["market"]
        base = {"sport": sport, "market": market, "event_id": raw["event_id"]}
        try:
            normalize_fixture(raw, sport, now())
            current = [
                r
                for r in records
                if r["model_id"] == models[sport, market]["incumbent"]
                and str(r["sport"]).upper() == sport
                and r["market_type"] == market
                and str(r["event_id"]) == raw["event_id"]
                and aware_time(r["created_at_utc"]) >= aware_time(preparation["prepared_at_utc"])
            ]
            current, duplicates = deduplicate(current)
            if duplicates.get("conflicting_context_rows") or duplicates.get("invalid_context"):
                raise ValueError("conflicting_incumbent_contexts")
            if not current:
                raise ValueError("awaiting_new_incumbent_decision")
            # Do not skip an invalid first decision in favor of a convenient later one.
            record = min(current, key=lambda r: (aware_time(r["created_at_utc"]), r["pick_id"]))
            if record.get("settled_at_utc") or record.get("result") in {"win", "loss", "push"}:
                raise ValueError("incumbent_already_settled")
            snapshot = json.loads(record.get("feature_payload_json") or "{}").get("model_input_snapshot")
            if not snapshot:
                raise ValueError("missing_incumbent_serving_snapshot")
            row = (
                json.loads(record["decision_payload_json"])
                | record
                | {"league": record["sport"], "model_version": record["model_id"]}
            )
            validate_decision_snapshot(snapshot, row)
            observed = snapshot["observed_at_utc"]
            if (
                not aware_time(preparation["prepared_at_utc"])
                <= aware_time(observed)
                <= aware_time(record["created_at_utc"])
                <= aware_time(now())
            ):
                raise ValueError("incumbent_predates_preparation_or_is_future")
            if aware_time(record["event_start_utc"]) != aware_time(raw["event_start_utc"]):
                raise ValueError("pair_start_mismatch")
            _a_is_home({"sport": sport, "raw_fixture": raw}, row)
            if market in {"spread", "total"}:
                line = float(record["line"]) * (
                    -1 if market == "spread" and record["selection"] == "away" else 1
                )
                if raw.get("line") != line:
                    raise ValueError("pair_exact_line_mismatch")
            name = f"sources/{sport.lower()}-{'nrfi' if market == 'nrfi' else 'games'}.jsonl"
            source = output / name
            evidence = preparation["files"][name]
            games = (
                []
                if market == "nrfi"
                else load_games(source, sport, aware_time(observed).date().isoformat())[0]
            )
            if market != "nrfi":
                evidence = evidence | {"normalized_games_sha256": digest([asdict(g) for g in games])}
            model_path = output / "models" / f"{sport.lower()}-{market}" / "model.joblib"
            candidate = forecast(
                model_path,
                raw,
                sport,
                market,
                observed,
                games=games,
                source_evidence=evidence,
                nrfi_snapshot_path=source if market == "nrfi" else None,
            )
            candidate["capture_context"] = {
                "policy": POLICY,
                "preparation_sha256": file_hash(output / "preparation.json"),
                "prepared_at_utc": preparation["prepared_at_utc"],
                "pick_id": record["pick_id"],
                "incumbent_snapshot_hash": snapshot["snapshot_hash"],
                "incumbent_created_at_utc": record["created_at_utc"],
                "prediction_created_at_utc": now(),
            }
            candidate["snapshot_hash"] = digest({k: v for k, v in candidate.items() if k != "snapshot_hash"})
            rebuild_forecast(candidate, model_path, source)
            normalize_fixture(raw, sport, now())
            result = {"status": "CAPTURED_REPLAY_PASS", "prediction": candidate}
        except (KeyError, ValueError, TypeError, OSError) as exc:
            result = base | {"status": "NO_PREDICTION", "reason": str(exc)}
        results.append(result)
        with (output / "predictions.jsonl").open("a") as handle:
            handle.write(json.dumps(result, allow_nan=False) + "\n")
    counts = dict(Counter(r["status"] for r in results))
    return {
        "status": "COMPLETE" if not counts.get("NO_PREDICTION") else "PARTIAL",
        "counts": counts,
        "policy": POLICY,
        "production_ledger_written": False,
        "promote": False,
    }


def observe(
    prepared: Path,
    database: Path,
    output: Path,
    *,
    poll_seconds: float = 30,
    max_wait_seconds: float = 86400,
) -> dict[str, Any]:
    """Bounded read-only observer; retain the first successful pregame capture.

    Capture batches are immutable. Only a changed incumbent context triggers
    another source copy/inference pass. Polling does not rerun production.
    """
    import math

    if not math.isfinite(poll_seconds) or not 1 <= poll_seconds <= 60:
        raise ValueError("poll_seconds_must_be_between_1_and_60")
    if not math.isfinite(max_wait_seconds) or not 1 <= max_wait_seconds <= 86400:
        raise ValueError("max_wait_seconds_must_be_between_1_and_86400")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING", "started_at_utc": now()})
    try:
        preparation = json.loads((prepared / "preparation.json").read_text())
        if preparation["schema"] != "research-pair-preparation-v1" or preparation["policy"] != POLICY:
            raise ValueError("unsupported_pair_preparation")
        # Retain the same layout accepted by score_research_forward.
        copy_file(prepared / "preparation.json", output / "preparation.json")
        for name, evidence in preparation["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid_preparation_path")
            copy_file(prepared / relative, output / relative)
            if file_hash(output / relative) != evidence["source_sha256"]:
                raise ValueError("prepared_input_changed")
            if aware_time(evidence["captured_at_utc"]) > aware_time(preparation["prepared_at_utc"]):
                raise ValueError("prepared_input_time_mismatch")
        requests = rows(output / "fixtures.jsonl")
        models = json.loads((output / "generation_manifest.json").read_text())["models"]
        wanted = {(r["sport"], r["market"], r["event_id"], r.get("line")): r for r in requests}
        completed: dict[tuple[Any, ...], dict[str, Any]] = {}
        last_signature = None
        batches = 0
        deadline = time.monotonic() + max_wait_seconds
        while True:
            records = read_decisions(
                database, [m["incumbent"] for m in models], preparation["prepared_at_utc"]
            )
            event_keys = {
                (r["sport"], r["market"], r["event_id"]) for key, r in wanted.items() if key not in completed
            }
            relevant = [
                r
                for r in records
                if (str(r["sport"]).upper(), r["market_type"], str(r["event_id"])) in event_keys
            ]
            signature = digest(relevant)
            expired = time.monotonic() >= deadline
            late = any(
                aware_time(r["event_start_utc"]) <= aware_time(now())
                for key, r in wanted.items()
                if key not in completed
            )
            if (relevant and signature != last_signature) or expired or late:
                batch = output / "batches" / f"{batches:04d}"
                capture(output, database, batch)
                batches += 1
                for result in rows(batch / "predictions.jsonl"):
                    prediction = result.get("prediction", result)
                    event = prediction.get("fixture", prediction).get("event_id")
                    identities = [
                        key for key in wanted if key[:3] == (prediction["sport"], prediction["market"], event)
                    ]
                    for key in identities:
                        if key in completed:
                            continue
                        if result["status"] == "CAPTURED_REPLAY_PASS" and prediction["line"] == key[3]:
                            completed[key] = result
                        elif (
                            result["status"] == "NO_PREDICTION"
                            and result.get("reason") != "awaiting_new_incumbent_decision"
                        ):
                            # Frozen inputs and the first incumbent are binding.
                            completed[key] = result
                        elif expired or aware_time(wanted[key]["event_start_utc"]) <= aware_time(now()):
                            completed[key] = {
                                "status": "NO_PREDICTION",
                                "sport": key[0],
                                "market": key[1],
                                "event_id": key[2],
                                "reason": "observation_deadline_reached"
                                if expired and not late
                                else result.get("reason", "fixture_not_future"),
                            }
                # Publish only completed results; never overwrite a captured
                # prediction with a later model decision or a postgame refusal.
                write_path = output / "predictions.jsonl.tmp"
                write_path.write_text(
                    "".join(
                        json.dumps(completed[key], allow_nan=False) + "\n"
                        for key in wanted
                        if key in completed
                    )
                )
                write_path.replace(output / "predictions.jsonl")
            last_signature = signature
            counts = dict(Counter(r["status"] for r in completed.values()))
            finished = len(completed) == len(wanted)
            report = {
                "status": "COMPLETE"
                if finished and not counts.get("NO_PREDICTION")
                else "PARTIAL"
                if finished
                else "OBSERVING",
                "counts": counts,
                "pending": len(wanted) - len(completed),
                "batches": batches,
                "policy": POLICY,
                "last_checked_at_utc": now(),
                "production_ledger_written": False,
                "promote": False,
            }
            write_json(output / "report.json", report)
            write_json(
                output / "RUN_STATUS.json",
                {"status": report["status"], "last_checked_at_utc": report["last_checked_at_utc"]},
            )
            if finished:
                return report
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    prep.add_argument("--generation", type=Path, required=True)
    prep.add_argument("--fixtures", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--prepared", type=Path, required=True)
    cap.add_argument("--database", type=Path, required=True)
    cap.add_argument("--output", type=Path, required=True)
    watch = sub.add_parser("observe")
    watch.add_argument("--prepared", type=Path, required=True)
    watch.add_argument("--database", type=Path, required=True)
    watch.add_argument("--output", type=Path, required=True)
    watch.add_argument("--poll-seconds", type=float, default=30)
    watch.add_argument("--max-wait-seconds", type=float, default=86400)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root, args.generation, args.fixtures, args.output)
        print(json.dumps({"status": "PREPARED", "fixtures": result["fixtures"]}))
    else:
        result = (
            observe(
                args.prepared,
                args.database,
                args.output,
                poll_seconds=args.poll_seconds,
                max_wait_seconds=args.max_wait_seconds,
            )
            if args.command == "observe"
            else capture(args.prepared, args.database, args.output)
        )
        print(json.dumps(result))
        raise SystemExit(0 if result["status"] == "COMPLETE" else 2)
