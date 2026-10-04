"""Grade immutable later pricing decisions using explicit completed outcome rows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.research_forward import aware_time
from model_prediction.research_generation import digest
from model_prediction.research_pairing import simulate_contracts
from model_prediction.research_pricing import SUPPORTED_POLICIES, replay_price
from model_prediction.research_scoring import outcome_label
from scripts.capture_research_prices import freeze_archive, rebuild, rows, seal
from scripts.forecast_research_generation import capture, write_json


def run(prices: Path, outcomes: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        return _run(prices, outcomes, output)
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run(prices: Path, outcomes: Path, output: Path) -> dict[str, Any]:
    archived = output / "pricing_archive"
    report = freeze_archive(prices, archived)
    if report["policy"] not in SUPPORTED_POLICIES.values():
        raise ValueError("pricing_policy_mismatch")
    forecasts = rows(archived / "forecasts/predictions.jsonl")
    indexed = {
        r["prediction"]["snapshot_hash"]: r["prediction"]
        for r in forecasts
        if r["status"] == "CAPTURED_REPLAY_PASS"
    }
    evidence = capture(outcomes, output / "outcomes.jsonl")
    raw_outcomes = rows(output / "outcomes.jsonl")
    results, seen = [], set()
    for row in rows(archived / "pricing_rows.jsonl"):
        base = {k: row[k] for k in ("sport", "market", "event_id")}
        if row["status"] != "PRICED_REPLAY_PASS":
            results.append(base | {"status": row["status"], "reason": row.get("reason")})
            continue
        try:
            p = indexed[row["forecast_hash"]]
            if (p["sport"], p["market"], p["fixture"]["event_id"]) != tuple(base.values()):
                raise ValueError("pricing_row_identity_mismatch")
            identity = (p["sport"], p["market"], p["fixture"]["event_id"], p["line"])
            if identity in seen:
                raise ValueError("duplicate_priced_contract")
            seen.add(identity)
            probabilities = rebuild(archived / "forecasts", p)
            priced = row["pricing"]
            if priced["policy_hash"] != digest(report["policy"]):
                raise ValueError("pricing_row_policy_mismatch")
            choice = replay_price(p, priced)
            if aware_time(priced["quote"]["pricing_decision_at_utc"]) > aware_time(
                report["completed_at_utc"]
            ):
                raise ValueError("pricing_report_chronology")
            matching = [
                r
                for r in raw_outcomes
                if str(r.get("event_id") or r.get("match_id") or r.get("game_id") or "")
                == p["fixture"]["event_id"]
            ]
            if not matching:
                results.append(base | {"status": "AWAITING_OUTCOME", "pnl_usd": None})
                continue
            labels = [outcome_label(p, raw, evidence["captured_at_utc"]) for raw in matching]
            if p["sport"] == "SOCCER" and any(raw.get("completed_periods") != 2 for raw in matching):
                raise ValueError("soccer_regulation_result_unverified")
            fields = (
                ("first_inning_runs_home", "first_inning_runs_away")
                if p["market"] == "nrfi"
                else ("home_score", "away_score")
            )
            if len(set(labels)) != 1 or len({tuple(r[k] for k in fields) for r in matching}) != 1:
                raise ValueError("conflicting_pricing_outcomes")
            settled = simulate_contracts(
                priced.get("pricing_probabilities", probabilities), labels[0], priced["contracts"]
            )
            # Every decision field must remain exactly as recorded before start.
            if any(settled[k] != v for k, v in choice.items() if k not in {"pnl_usd", "settlement_value"}):
                raise ValueError("pricing_choice_changed_on_settlement")
            results.append(
                base
                | {
                    "status": "SCORED",
                    "pricing_hash": priced["pricing_hash"],
                    "outcome": labels[0],
                    "outcome_hash": digest(matching[0]),
                    "simulation": settled,
                }
            )
        except (ValueError, KeyError, TypeError, OSError) as exc:
            results.append(base | {"status": "INTEGRITY_FAILURE", "reason": str(exc)})
    (output / "scored_rows.jsonl").write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in results))
    scored = [r["simulation"] for r in results if r["status"] == "SCORED"]
    cost = sum(r["cost_usd"] for r in scored)
    pnl = sum(r["pnl_usd"] for r in scored) if scored else None
    capture(root := Path(__file__).resolve(), output / "code" / root.name)
    return seal(
        output,
        {
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "counts": dict(Counter(r["status"] for r in results)),
            "outcome_source": evidence,
            "calls": sum(r["called"] for r in scored),
            "distinct_scored_games": len(
                {(r["sport"], r["event_id"]) for r in results if r["status"] == "SCORED"}
            ),
            "simulation": {
                "cost_usd": cost if scored else None,
                "pnl_usd": pnl,
                "roi": pnl / cost if cost and pnl is not None else None,
                "fees_usd": sum(r["fee_usd"] for r in scored) if scored else None,
            },
            "actual_fills": None,
            "incumbent_superiority": None,
            "promote": False,
            "limitations": report["limitations"]
            + ["No return confidence claim from small or incomplete cohorts."],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--outcomes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.prices, args.outcomes, args.output)
    print(json.dumps({k: report[k] for k in ("counts", "calls", "simulation")}, indent=2))
    return 2 if report["counts"].get("INTEGRITY_FAILURE") else 0


if __name__ == "__main__":
    raise SystemExit(main())
