"""Capture research-only prices after frozen forecasts, with no order or ledger writes.

Adapters cover MLB NRFI, CFB, WNBA derivatives, KBO/NPB and soccer.
Unsupported, ambiguous, stale, or unavailable
quotes remain explicit. A batch makes one book request per mapped forecast.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from model_prediction.data_sources.polymarket_us import PolymarketUSClient
from model_prediction.research_forward import rebuild_forecast
from model_prediction.research_generation import digest, file_hash
from model_prediction.research_market_pricing import TYPES, matching_event, matching_market
from model_prediction.research_pricing import (
    PRICING_POLICY,
    matching_nrfi_event,
    price_forecast,
)
from scripts.forecast_research_generation import capture, write_json


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rebuild(directory: Path, prediction: dict[str, Any]) -> dict[str, float]:
    p = prediction
    model = [
        m
        for m in json.loads((directory / "generation_manifest.json").read_text())["models"]
        if (m["sport"], m["market"]) == (p["sport"], p["market"])
    ]
    if len(model) != 1 or (model[0]["model_id"], model[0]["artifact_sha256"]) != (
        p["model_id"],
        p["model_file_sha256"],
    ):
        raise ValueError("pricing_generation_identity_mismatch")
    return rebuild_forecast(
        p,
        directory / "models" / f"{p['sport'].lower()}-{p['market']}" / "model.joblib",
        directory / "sources" / f"{p['sport'].lower()}-{'nrfi' if p['market'] == 'nrfi' else 'games'}.jsonl",
    )


def seal(output: Path, report: dict[str, Any]) -> dict[str, Any]:
    report["files"] = {
        str(p.relative_to(output)): file_hash(p)
        for p in sorted(output.rglob("*"))
        if p.is_file() and p not in {output / "RUN_STATUS.json", output / "report.json"}
    }
    write_json(output / "report.json", report)
    write_json(
        output / "RUN_STATUS.json", {"status": "COMPLETE", "report_sha256": file_hash(output / "report.json")}
    )
    return report


def freeze_archive(source: Path, target: Path) -> dict[str, Any]:
    state = json.loads((source / "RUN_STATUS.json").read_text())
    if state["status"] != "COMPLETE" or state["report_sha256"] != file_hash(source / "report.json"):
        raise ValueError("pricing_archive_report_hash_mismatch")
    report = json.loads((source / "report.json").read_text())
    for name, expected in report["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("pricing_archive_unsafe_path")
        if capture(source / name, target / name)["source_sha256"] != expected:
            raise ValueError("pricing_archive_file_hash_mismatch")
    if capture(source / "report.json", target / "report.json")["source_sha256"] != state["report_sha256"]:
        raise ValueError("pricing_report_changed_during_archive")
    capture(source / "RUN_STATUS.json", target / "RUN_STATUS.json")
    return report


def run(forecasts: Path, output: Path, client: PolymarketUSClient | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "RUN_STATUS.json", {"status": "RUNNING"})
    try:
        return _run(forecasts, output, client or PolymarketUSClient())
    except Exception as exc:
        write_json(output / "RUN_STATUS.json", {"status": "FAILED", "reason": str(exc)})
        raise


def _run(forecasts: Path, output: Path, client: PolymarketUSClient) -> dict[str, Any]:
    archive = output / "forecasts"
    for name in ("predictions.jsonl", "generation_manifest.json"):
        capture(forecasts / name, archive / name)
    predictions = rows(archive / "predictions.jsonl")
    if not predictions:
        raise ValueError("empty_forecasts")
    results, seen = [], set()
    inventories: dict[str, list[dict[str, Any]]] = {}
    for row in predictions:
        p = row.get("prediction", row)
        identity = (p["sport"], p["market"], p.get("fixture", p).get("event_id"), p.get("line"))
        base = {"sport": identity[0], "market": identity[1], "event_id": identity[2]}
        quote: dict[str, Any] = {}
        verified_forecast = False
        try:
            if identity in seen:
                raise ValueError("duplicate_forecast_contract")
            seen.add(identity)
            if row["status"] != "CAPTURED_REPLAY_PASS":
                results.append(base | {"status": "NO_FORECAST", "reason": row.get("reason")})
                continue
            base["forecast_hash"] = p["snapshot_hash"]
            nrfi = (p["sport"], p["market"]) == ("MLB", "nrfi")
            if not nrfi and (p["sport"], p["market"]) not in TYPES:
                results.append(base | {"status": "UNPRICED", "reason": "pricing_adapter_unavailable"})
                continue
            relative = Path("models") / f"{p['sport'].lower()}-{p['market']}"
            for name in ("model.joblib", "recipe.json"):
                if not (archive / relative / name).exists():
                    capture(forecasts / relative / name, archive / relative / name)
            source = Path("sources") / f"{p['sport'].lower()}-{'nrfi' if nrfi else 'games'}.jsonl"
            if not (archive / source).exists():
                capture(forecasts / source, archive / source)
            rebuild(archive, p)
            verified_forecast = True
            if datetime.fromisoformat(p["raw_fixture"]["event_start_utc"]) <= datetime.now(UTC):
                raise ValueError("pricing_event_already_started")
            league = p["raw_fixture"]["league"] if p["sport"] == "SOCCER" else p["sport"]
            if league not in inventories:
                inventories[league] = client.events(league)
                write_json(output / f"discovery_{league.lower()}.json", inventories[league])
            inventory = inventories[league]
            quote = {
                "provider": "polymarket_us_public_gateway",
                "request_started_at_utc": datetime.now(UTC).isoformat(),
                "discovery_hash": digest(inventory),
            }
            matching = [
                e
                for e in inventory
                if (matching_nrfi_event(p["raw_fixture"], e) if nrfi else matching_event(p, e))
            ]
            if len(matching) != 1:
                raise ValueError("pricing_event_missing_or_ambiguous")
            event = client.event(matching[0]["slug"])
            quote["event"] = event
            if not (matching_nrfi_event(p["raw_fixture"], event) if nrfi else matching_event(p, event)):
                raise ValueError("pricing_detail_event_mismatch")
            markets = [
                m
                for m in event["markets"]
                if (
                    m.get("sportsMarketType") == "baseball_team_first_inning_run"
                    if nrfi
                    else matching_market(p, event, m)
                )
            ]
            if len(markets) != 1:
                raise ValueError("pricing_market_missing_or_ambiguous")
            quote["market"] = client.market(markets[0]["slug"])
            quote["book"] = client.book(markets[0]["slug"])
            quote["observed_at_utc"] = datetime.now(UTC).isoformat()
            quote["pricing_decision_at_utc"] = datetime.now(UTC).isoformat()
            priced = price_forecast(p, quote)
            results.append(base | {"status": "PRICED_REPLAY_PASS", "pricing": priced})
        except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError) as exc:
            results.append(
                base
                | {
                    "status": "UNPRICED" if verified_forecast else "INTEGRITY_FAILURE",
                    "reason": str(exc),
                    "attempt_evidence": quote,
                }
            )
        # Persist every attempt immediately; interrupted runs retain their evidence.
        (output / "pricing_rows.jsonl").write_text(
            "".join(json.dumps(r, allow_nan=False) + "\n" for r in results)
        )
    # Include terminal no-forecast/unsupported rows, which skip the request path.
    (output / "pricing_rows.jsonl").write_text(
        "".join(json.dumps(r, allow_nan=False) + "\n" for r in results)
    )
    root = Path(__file__).resolve().parents[1]
    for name in (
        "scripts/capture_research_prices.py",
        "src/model_prediction/research_pricing.py",
        "src/model_prediction/research_market_pricing.py",
        "src/model_prediction/research_soccer_pricing.py",
        "src/model_prediction/data_sources/polymarket_us.py",
        "src/model_prediction/data_sources/cfb_data.py",
        "src/model_prediction/international_baseball.py",
        "src/model_prediction/research_pairing.py",
    ):
        capture(root / name, output / "code" / Path(name).name)
    priced_records = [r["pricing"] for r in results if r["status"] == "PRICED_REPLAY_PASS"]
    return seal(
        output,
        {
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "policy": PRICING_POLICY,
            "counts": dict(Counter(r["status"] for r in results)),
            "reasons": dict(Counter(r["reason"] for r in results if "reason" in r)),
            "calls": sum(p["choice"]["called"] for p in priced_records),
            "pnl_usd": None,
            "actual_fills": None,
            "promote": False,
            "limitations": [
                "Prices belong to a later decision than the forecast.",
                "This is a candidate-only simulation, not a canonical incumbent comparison or an actual fill.",
                "Completed games only; void settlement is not inferred.",
                "Historical feature observation availability remains unverified.",
            ],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.forecasts, args.output)
    print(json.dumps({k: report[k] for k in ("counts", "reasons", "calls")}, indent=2))
    return 2 if any(report["counts"].get(s) for s in ("UNPRICED", "INTEGRITY_FAILURE", "NO_FORECAST")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
