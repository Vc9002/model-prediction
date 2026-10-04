"""Score exact pregame paired archives even when live ledger rows were removed.

All returns are simulations of previously recorded choices. Canonical removal
is reported separately; no removed row is relabeled settled or written back.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.research_generation import digest, file_hash
from model_prediction.research_incumbent_evaluation import POLICY, deduplicate, timestamp
from model_prediction.research_pairing import first_bound_record, reproduce_pair, simulate_contracts
from model_prediction.research_quote_contracts import executable_probabilities, verify_embedded_quote
from model_prediction.research_scoring import outcome_label, proper_score
from scripts.capture_research_pairs import read_decisions
from scripts.evaluate_captured_pairs import rows
from scripts.forecast_research_generation import capture, write_json


def economics(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [p for p in pairs if p.get("economics")]
    result: dict[str, Any] = {
        "status": "HYPOTHETICAL_ONLY" if eligible else "NO_ELIGIBLE_QUOTES",
        "event_markets": len(eligible),
        "distinct_events": len({(p["sport"], p["event_id"]) for p in eligible}),
        "actual_fills": None,
        "clv": None,
        "profitability_proven": False,
        "confidence_interval": None,
        "uncertainty_reason": "insufficient independent dates; no reliable profitability inference",
    }
    for name in ("incumbent", "candidate"):
        trades = [p["economics"][name] for p in eligible]
        cost, pnl = sum(t["cost_usd"] for t in trades), sum(t["pnl_usd"] for t in trades)
        result[name] = {
            "calls": sum(t["called"] for t in trades),
            "cost_usd": cost if trades else None,
            "pnl_usd": pnl if trades else None,
            "roi": pnl / cost if cost else None,
            "fee_usd": sum(t["fee_usd"] for t in trades) if trades else None,
        }
    return result


def run(root: Path, audit: Path, scores: Path, database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        report = _run(root, audit, scores, database, output)
        write_json(output / "report.json", report)
        write_json(
            output / "RUN_STATUS.json",
            {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")},
        )
        return report
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run(root: Path, audit: Path, scores: Path, database: Path, output: Path) -> dict[str, Any]:
    for name in (
        "report.json",
        "policy.json",
        "audit_rows.jsonl",
        "ledger_snapshot.jsonl",
        "preparation.json",
        "predictions.jsonl",
        "generation_manifest.json",
    ):
        capture(audit / name, output / "pregame" / name)
    before = output / "pregame"
    report = json.loads((before / "report.json").read_text())
    policy = json.loads((before / "policy.json").read_text())
    if (
        report["audit_rows_sha256"] != file_hash(before / "audit_rows.jsonl")
        or report["ledger_snapshot_sha256"] != file_hash(before / "ledger_snapshot.jsonl")
        or report["policy"] != policy
        or any(policy.get(k) != v for k, v in POLICY.items() if k != "version")
        or policy.get("version") != "pregame_paired_quote_audit_20260911_v1"
    ):
        raise ValueError("pregame_archive_or_policy_hash_mismatch")
    prep = json.loads((before / "preparation.json").read_text())
    for name, evidence in prep["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid_preparation_path")
        if not (before / relative).exists():
            capture(audit / relative, before / relative)
        if file_hash(before / relative) != evidence["source_sha256"]:
            raise ValueError("prepared_input_hash_mismatch")
    capture(scores / "scored_rows.jsonl", output / "scored_rows.jsonl")
    scored = {r["snapshot_hash"]: r for r in rows(output / "scored_rows.jsonl") if r["status"] == "SCORED"}
    candidates = {
        r["prediction"]["snapshot_hash"]: r["prediction"]
        for r in rows(before / "predictions.jsonl")
        if r["status"] == "CAPTURED_REPLAY_PASS"
    }
    manifest = json.loads((before / "generation_manifest.json").read_text())
    models = {(m["sport"], m["market"]): m for m in manifest["models"]}
    originals = rows(before / "ledger_snapshot.jsonl")
    current = read_decisions(database, [m["incumbent"] for m in models.values()], prep["prepared_at_utc"])
    (output / "current_ledger_snapshot.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in current)
    )
    current_by_id = {r["pick_id"]: r for r in current}
    pairs, failures = [], []
    seen = set()
    for prior in rows(before / "audit_rows.jsonl"):
        if prior["audit_status"] not in {"VERIFIED_PREGAME_CHOICES", "REPRODUCED_NO_VERIFIED_QUOTE"}:
            continue
        try:
            candidate = candidates[prior["candidate_snapshot_hash"]]
            sport, market = candidate["sport"], candidate["market"]
            event = candidate["fixture"]["event_id"]
            identity = (sport, market, event)
            if identity in seen:
                raise ValueError("duplicate_event_market")
            seen.add(identity)
            model = models[sport, market]
            context = candidate["capture_context"]
            if (
                context["preparation_sha256"] != file_hash(before / "preparation.json")
                or context["policy"] != prep["policy"]
                or context["prepared_at_utc"] != prep["prepared_at_utc"]
                or candidate["model_id"] != model["model_id"]
                or candidate["model_file_sha256"] != model["artifact_sha256"]
            ):
                raise ValueError("candidate_preparation_mismatch")
            eligible, duplicates = deduplicate(
                [
                    r
                    for r in originals
                    if r["model_id"] == model["incumbent"]
                    and str(r["sport"]).upper() == sport
                    and r["market_type"] == market
                    and str(r["event_id"]) == event
                    and timestamp(r["created_at_utc"]) >= timestamp(prep["prepared_at_utc"])
                ]
            )
            if duplicates.get("conflicting_context_rows") or duplicates.get("invalid_context"):
                raise ValueError("conflicting_incumbent_contexts")
            record = first_bound_record(eligible, originals, context)
            if record.get("settled_at_utc") or not timestamp(
                context["prediction_created_at_utc"]
            ) <= timestamp(report["audited_at_utc"]) < timestamp(record["event_start_utc"]):
                raise ValueError("archive_not_pregame")
            source = f"{sport.lower()}-{'nrfi' if market == 'nrfi' else 'games'}.jsonl"
            paired = reproduce_pair(
                record,
                candidate,
                before / f"models/{sport.lower()}-{market}/model.joblib",
                before / "sources" / source,
            )
            if any(paired[k] != prior[k] for k in paired):
                raise ValueError("pregame_pair_reproduction_mismatch")
            score = scored[candidate["snapshot_hash"]]
            outcome_path = output / "outcomes" / source
            if not outcome_path.exists():
                capture(scores / "outcomes" / source, outcome_path)
            if file_hash(outcome_path) != score["outcome_source"]["source_sha256"]:
                raise ValueError("outcome_source_hash_mismatch")
            found = {digest(r): r for r in rows(outcome_path) if digest(r) == score["outcome_record_hash"]}
            if len(found) != 1:
                raise ValueError("outcome_record_missing_or_ambiguous")
            label = outcome_label(
                candidate, next(iter(found.values())), score["outcome_source"]["captured_at_utc"]
            )
            paired.update(
                outcome=label,
                incumbent_score=proper_score(paired["incumbent_probabilities"], label),
                candidate_score=proper_score(paired["candidate_probabilities"], label),
                outcome_source=score["outcome_source"],
                outcome_record_hash=score["outcome_record_hash"],
                canonical_record_status_at_evaluation=current_by_id.get(record["pick_id"], {}).get(
                    "status", "MISSING"
                ),
                settlement_basis="independent archived completed scoreboard; not canonical ledger settlement",
            )
            if prior["audit_status"] == "VERIFIED_PREGAME_CHOICES":
                evidence = verify_embedded_quote(record, candidate, prior["quote_evidence"])
                pregame, settled = {}, {}
                for name in ("incumbent", "candidate"):
                    p = executable_probabilities(paired[name + "_probabilities"], evidence)
                    pregame[name] = simulate_contracts(p, None, evidence["contracts"])
                    settled[name] = simulate_contracts(p, label, evidence["contracts"])
                if pregame != prior["choices"]:
                    raise ValueError("pregame_policy_choices_changed")
                paired["economics"] = settled
                paired["quote_evidence"] = evidence
            else:
                paired["economics_unavailable_reason"] = prior["reason"]
            pairs.append(paired)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            failures.append(
                {"candidate_snapshot_hash": prior.get("candidate_snapshot_hash"), "reason": str(exc)}
            )
    (output / "paired_rows.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in pairs)
    )
    write_json(output / "failures.json", failures)
    summaries = []
    for (sport, market), model in models.items():
        subset = [p for p in pairs if p["sport"] == sport and p["market"] == market]
        metrics = (
            {
                name: {
                    metric: sum(p[name + "_score"][metric] for p in subset) / len(subset)
                    for metric in ("accuracy", "brier_vector_sum", "log_loss")
                }
                for name in ("incumbent", "candidate")
            }
            if subset
            else None
        )
        summaries.append(
            {
                "sport": sport,
                "market": market,
                "candidate": model["model_id"],
                "pairs": len(subset),
                "metrics": metrics,
                "economics": economics(subset),
                "verdict": "INSUFFICIENT_PAIRED_EVIDENCE" if subset else "NO_PAIRED_EVIDENCE",
                "promote": False,
            }
        )
    for name in (
        "scripts/evaluate_archived_research_pairs.py",
        "src/model_prediction/research_quote_contracts.py",
        "src/model_prediction/research_pairing.py",
    ):
        capture(root / name, output / "code_snapshot" / name)
    return {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "models": summaries,
        "pairs": len(pairs),
        "distinct_events": len({(p["sport"], p["event_id"]) for p in pairs}),
        "failures": failures,
        "canonical_record_status_counts": dict(
            Counter(p["canonical_record_status_at_evaluation"] for p in pairs)
        ),
        "economics": economics(pairs),
        "paired_rows_sha256": file_hash(output / "paired_rows.jsonl"),
        "profitability_proven": False,
        "promote": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = run(args.root, args.audit, args.scores, args.database, args.output)
    print(json.dumps({k: result[k] for k in ("pairs", "failures", "economics")}, indent=2))
    raise SystemExit(2 if result["failures"] or not result["pairs"] else 0)
