"""Freeze pregame candidate/incumbent choices from exact retained quote evidence.

Reads canonical decisions without writing them. Requires preparation and complete
reproduction of both models. Outcomes are deliberately absent from this audit;
settled comparison remains the responsibility of evaluate_captured_pairs.py.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.research_generation import file_hash
from model_prediction.research_incumbent_evaluation import POLICY, deduplicate, timestamp
from model_prediction.research_pairing import first_bound_record, reproduce_pair, simulate_contracts
from model_prediction.research_quote_contracts import PairedQuoteArchive, executable_probabilities
from scripts.capture_research_pairs import POLICY as CAPTURE_POLICY
from scripts.capture_research_pairs import read_decisions
from scripts.evaluate_captured_pairs import rows
from scripts.forecast_research_generation import capture, write_json


def run(root: Path, captures: Path, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        report = _run(root, captures, database, output)
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise
    write_json(output / "report.json", report)
    write_json(
        output / "RUN_STATUS.json", {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")}
    )
    return report


def _run(root: Path, captures: Path, database: Path, output: Path) -> dict[str, Any]:
    audited = datetime.now(UTC).isoformat()
    policy = POLICY | {
        "version": "pregame_paired_quote_audit_20260911_v1",
        "choice": "one highest positive expected return per dollar contract per model per event-market",
        "outcomes": "unobserved; all realized P&L and performance metrics null",
        "portfolio": "independent per-market simulations; no aggregate bankroll or independence claim",
        "promotion": False,
    }
    write_json(output / "policy.json", policy)
    capture(captures / "preparation.json", output / "preparation.json")
    prep = json.loads((output / "preparation.json").read_text())
    if prep["schema"] != "research-pair-preparation-v1" or prep["policy"] != CAPTURE_POLICY:
        raise ValueError("unsupported_pair_preparation")
    for name, evidence in prep["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_preparation_path")
        capture(captures / relative, output / relative)
        if file_hash(output / relative) != evidence["source_sha256"]:
            raise ValueError("prepared_input_hash_mismatch")
        if timestamp(evidence["captured_at_utc"]) > timestamp(prep["prepared_at_utc"]):
            raise ValueError("source_captured_after_preparation")
    capture(captures / "predictions.jsonl", output / "predictions.jsonl")
    manifest = json.loads((output / "generation_manifest.json").read_text())
    models = {(m["sport"], m["market"]): m for m in manifest["models"]}
    records = read_decisions(database, [m["incumbent"] for m in models.values()], prep["prepared_at_utc"])
    (output / "ledger_snapshot.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    )
    archive = PairedQuoteArchive(root)
    results = []
    seen = set()
    for raw in rows(output / "predictions.jsonl"):
        if raw["status"] != "CAPTURED_REPLAY_PASS":
            results.append(raw | {"audit_status": "NO_CANDIDATE"})
            continue
        candidate = raw["prediction"]
        sport, market = candidate["sport"], candidate["market"]
        event = candidate["fixture"]["event_id"]
        result: dict[str, Any] = {
            "sport": sport,
            "market": market,
            "event_id": event,
            "candidate_snapshot_hash": candidate["snapshot_hash"],
        }
        try:
            identity = (sport, market, event)
            if identity in seen:
                raise ValueError("duplicate_candidate_event_market")
            seen.add(identity)
            model = models[sport, market]
            context = candidate["capture_context"]
            source = f"sources/{sport.lower()}-{'nrfi' if market == 'nrfi' else 'games'}.jsonl"
            model_name = f"models/{sport.lower()}-{market}/model.joblib"
            if (
                context["preparation_sha256"] != file_hash(output / "preparation.json")
                or context["policy"] != prep["policy"]
                or context["prepared_at_utc"] != prep["prepared_at_utc"]
                or candidate["model_id"] != model["model_id"]
                or candidate["model_file_sha256"] != model["artifact_sha256"]
                or candidate["model_file_sha256"] != prep["files"][model_name]["source_sha256"]
                or candidate["source_evidence"]["source_sha256"] != prep["files"][source]["source_sha256"]
            ):
                raise ValueError("candidate_preparation_mismatch")
            current, duplicates = deduplicate(
                [
                    r
                    for r in records
                    if r["model_id"] == model["incumbent"]
                    and str(r["sport"]).upper() == sport
                    and r["market_type"] == market
                    and str(r["event_id"]) == event
                ]
            )
            if duplicates.get("conflicting_context_rows") or duplicates.get("invalid_context"):
                raise ValueError("conflicting_incumbent_contexts")
            if not current:
                raise ValueError("no_incumbent_decision")
            record = first_bound_record(current, records, context)
            if timestamp(context["prediction_created_at_utc"]) > timestamp(audited):
                raise ValueError("candidate_created_after_audit")
            if (
                record.get("settled_at_utc")
                or record.get("result") in {"win", "loss", "push"}
                or timestamp(audited) >= timestamp(record["event_start_utc"])
            ):
                raise ValueError("pregame_audit_window_closed")
            result.update(reproduce_pair(record, candidate, output / model_name, output / source))
            result["audit_status"] = "REPRODUCED_NO_VERIFIED_QUOTE"
        except (KeyError, ValueError, TypeError, OSError) as exc:
            result.update(audit_status="REPRODUCTION_BLOCKED", reason=str(exc))
            results.append(result)
            continue
        try:
            evidence = archive.contracts(record, candidate)
            result["quote_evidence"] = evidence
            result["choices"] = {
                name: simulate_contracts(
                    executable_probabilities(result[name + "_probabilities"], evidence),
                    None,
                    evidence["contracts"],
                )
                for name in ("incumbent", "candidate")
            }
            result["audit_status"] = "VERIFIED_PREGAME_CHOICES"
        except (KeyError, ValueError, TypeError, OSError) as exc:
            result["reason"] = str(exc)
        results.append(result)
    (output / "audit_rows.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in results)
    )
    for name in (
        "scripts/audit_research_pair_quotes.py",
        "src/model_prediction/research_pairing.py",
        "src/model_prediction/research_quote_contracts.py",
        "src/model_prediction/research_incumbent_evaluation.py",
        "src/model_prediction/data_sources/mlb_market_odds.py",
    ):
        capture(root / name, output / "code_snapshot" / name)
    verified = [r for r in results if r["audit_status"] == "VERIFIED_PREGAME_CHOICES"]
    return {
        "audited_at_utc": audited,
        "counts": dict(Counter(r["audit_status"] for r in results)),
        "reasons": dict(Counter(r["reason"] for r in results if "reason" in r)),
        "verified_event_markets": len(verified),
        "verified_distinct_events": len({(r["sport"], r["event_id"]) for r in verified}),
        "hypothetical_calls": {
            name: sum(r["choices"][name]["called"] for r in verified) for name in ("incumbent", "candidate")
        },
        "source_ledger": str(database),
        "ledger_snapshot_sha256": file_hash(output / "ledger_snapshot.jsonl"),
        "audit_rows_sha256": file_hash(output / "audit_rows.jsonl"),
        "policy": policy,
        "pnl_usd": None,
        "roi": None,
        "accuracy": None,
        "profitability_proven": False,
        "orders_submitted": False,
        "promote": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = run(args.root, args.captures, args.database, args.output)
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("counts", "reasons", "verified_event_markets", "hypothetical_calls")
            },
            indent=2,
        )
    )
    return 0 if report["verified_event_markets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
