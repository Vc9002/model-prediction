"""Read-only coverage of serving champions and today's canonical ledger records.

Run after daily: python scripts/ledger_model_coverage.py --run scratch/run.json.
Archived observations, prior versions and mirror tiers are reported separately.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .domain import RETIRED_STATUSES
from .runtime_paths import RuntimePaths

ROUTES = {
    ("MLB", "moneyline"): ("mlb", "mlb-elo-trend-lr-v8"),
    ("MLB", "spread"): ("mlb_totals", "mlb-structural-runline-v4"),
    ("MLB", "total"): ("mlb_totals", "mlb-structural-v10-frozen"),
    ("MLB", "nrfi"): ("mlb_nrfi", "mlb-nrfi-v4"),
}
for _sport in ("NBA", "WNBA", "NFL"):
    for _market, _suffix in (("moneyline", ""), ("spread", "_spread"), ("total", "_total")):
        ROUTES[(_sport, _market)] = (_sport.lower() + _suffix, None)
for _sport in (
    "SOCCER",
    "TENNIS",
    "CS2",
    "DOTA2",
    "LOL",
    "VALORANT",
    "RAINBOW_SIX",
    "KBO",
    "NPB",
    "HOCKEY",
    "DARTS",
):
    ROUTES[(_sport, "moneyline")] = (_sport.lower(), None)
for _market in ("moneyline", "spread", "total"):
    ROUTES[("NCAAF", _market)] = ("ncaaf", None)
    ROUTES[("NCAAB", _market)] = ("ncaab", "ncaab-tempo-v2")
    ROUTES[("EUROLEAGUE", _market)] = ("euroleague", "eurolg-tempo-v2")
    ROUTES[("NBL", _market)] = ("nbl", "nbl-tempo-v2")
for _sport in ("HOCKEY", "TENNIS", "SOCCER", "CS2", "DOTA2", "LOL", "VALORANT", "RAINBOW_SIX"):
    for _market in ("spread", "total"):
        ROUTES[(_sport, _market)] = (_sport.lower(), None)


def build_report(root: Path, database: Path, day: str, run: dict) -> dict:
    production = yaml.safe_load((root / "config/production.yaml").read_text(encoding="utf-8"))
    configured = yaml.safe_load((root / "config/model.yaml").read_text(encoding="utf-8"))["models"]
    champions = production["prediction_service"]["champions"]
    forecasts = run.get("step2_3_forecast_and_log", {})
    eastern = ZoneInfo("America/New_York")
    start = datetime.combine(date.fromisoformat(day), time(), eastern)
    end = start + timedelta(days=1)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = [
            dict(r)
            for r in connection.execute(
                "SELECT model_id,market_type,event_start_utc,event_id,selection,line,ledger_tier,status,reason_code,created_at_utc,decision FROM ledger_records"
            )
        ]
    finally:
        connection.close()
    today = []
    for row in records:
        if row["status"] in RETIRED_STATUSES:
            continue
        try:
            when = datetime.fromisoformat(row["event_start_utc"])
        except (AttributeError, ValueError):
            continue
        if start <= when < end:
            today.append(row)
    lanes = []
    for sport, markets in champions.items():
        for market, model in markets.items():
            route, expected = ROUTES.get((sport, market), (None, None))
            result = forecasts.get(route, {})
            matching = [r for r in today if r["model_id"] == model and r["market_type"] == market]
            counts = dict(Counter(r["ledger_tier"] for r in matching))
            generated = [
                r
                for r in records
                if r["model_id"] == model
                and r["market_type"] == market
                and r["status"] not in RETIRED_STATUSES
                and start <= datetime.fromisoformat(r["created_at_utc"]) < end
            ]
            generated_counts = dict(Counter(r["ledger_tier"] for r in generated))
            flat_events = {str(r["event_id"]) for r in matching if r["ledger_tier"] == "flat"}
            main_events = {str(r["event_id"]) for r in matching if r["ledger_tier"] == "main"}
            priced = next(
                (
                    result[key]
                    for key in ("priced_contracts", "candidates", "priced")
                    if isinstance(result.get(key), list)
                ),
                [],
            )
            priced = [row for row in priced if isinstance(row, dict)]
            dated_priced = []
            for row in priced:
                if row.get("event_start_utc"):
                    try:
                        when = datetime.fromisoformat(row["event_start_utc"])
                    except (TypeError, ValueError):
                        continue
                    if not start <= when < end:
                        continue
                dated_priced.append(row)
            priced_events = {
                str(r["event_id"])
                for r in dated_priced
                if r.get("event_id") is not None and r.get("market_type", market) == market
            }
            missing_flat = sorted(priced_events - flat_events)
            main_without_flat = sorted(main_events - flat_events)
            if not route:
                state = "missing_route"
            elif expected and expected != model:
                state = "champion_route_mismatch"
            elif result.get("status") in {"error", "failed"} or result.get("errors"):
                state = "forecast_error"
            elif missing_flat or main_without_flat:
                state = "missing_flat_records"
            elif counts:
                state = "recorded"
            elif generated_counts:
                state = "recorded_for_other_event_date"
            elif priced and not dated_priced:
                state = "priced_for_other_event_date"
            elif route not in forecasts:
                state = "not_exercised"
            elif any(
                result.get(key) == 0
                for key in (
                    "scheduled_games",
                    "total_events_discovered",
                    "scheduled_events",
                    "events_count",
                    "event_count",
                    "events",
                )
            ):
                state = "no_scheduled_events"
            elif result.get("skipped") and all(r.get("reason") == "event_started" for r in result["skipped"]):
                state = "all_events_started"
            elif result.get("market_candidates") == 0:
                state = "no_matched_markets"
            elif result.get("priced_count") == 0:
                state = "no_priced_contracts"
            else:
                state = "no_records_review_reasons"
            lanes.append(
                {
                    "sport": sport,
                    "market": market,
                    "model_id": model,
                    "route": route,
                    "status": state,
                    "ledger_counts": counts,
                    "generated_today_ledger_counts": generated_counts,
                    "flat_coverage": {
                        "recorded_games": len(flat_events),
                        "priced_games": len(priced_events),
                        "off_date_priced_count": len(priced) - len(dated_priced),
                        "missing_priced_event_ids": missing_flat,
                        "main_only": bool(main_without_flat),
                        "main_without_flat_event_ids": main_without_flat,
                    },
                    "reasons": dict(Counter(r["reason_code"] for r in matching)),
                    "source_exclusions": dict(
                        Counter(
                            str(row.get("reason", "unspecified"))
                            for key in ("no_calls", "skipped", "unmatched", "unmatched_events")
                            for row in (result.get(key) if isinstance(result.get(key), list) else [])
                            if isinstance(row, dict)
                        )
                    ),
                    "forecast": {
                        k: result[k]
                        for k in (
                            "status",
                            "reason",
                            "scheduled_games",
                            "scheduled_events",
                            "events_count",
                            "no_calls",
                            "total_events_discovered",
                            "priced_count",
                            "market_candidates",
                            "logged",
                            "unmatched",
                            "skipped",
                            "errors",
                        )
                        if k in result
                    },
                }
            )
    extra = [
        {
            "sport": sport,
            "model_id": cfg.get("active_production_version"),
            "status": "configured_without_champion",
        }
        for sport, cfg in configured.items()
        if cfg.get("active_production_version") and sport not in champions
    ]
    identities = Counter(
        (r["model_id"], r["market_type"], r["event_id"], r["selection"], r["line"], r["ledger_tier"])
        for r in today
    )
    main_non_calls = sum(row["ledger_tier"] == "main" and row["decision"] != "CALL" for row in today)
    missing_flat_lanes = sum(row["status"] == "missing_flat_records" for row in lanes)
    broken_routes = any(
        row["status"] in {"forecast_error", "missing_route", "champion_route_mismatch"} for row in lanes
    )
    return {
        "status": "error" if main_non_calls or missing_flat_lanes or broken_routes else "ok",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "date_et": day,
        "lanes": lanes,
        "configured_without_champion": extra,
        "summary": dict(Counter(r["status"] for r in lanes)),
        "duplicate_active_contract_groups": sum(n > 1 for n in identities.values()),
        "main_non_call_records": main_non_calls,
        "missing_flat_lanes": missing_flat_lanes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--date", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/health/ledger_model_coverage.json"))
    args = parser.parse_args()
    report = build_report(
        Path(__file__).resolve().parents[2],
        args.database or RuntimePaths.resolve().ledgers_db,
        args.date,
        json.loads(args.run.read_text(encoding="utf-8-sig")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"]))
    for row in report["lanes"]:
        print(row["sport"], row["market"], row["model_id"], row["status"], row["ledger_counts"])
    for row in report["configured_without_champion"]:
        print("UNREGISTERED", row["sport"], row["model_id"])


if __name__ == "__main__":
    main()
