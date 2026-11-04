"""Read-only quote-verified and indicative NCAAF calibration audit."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/research/ncaaf_market_calibration_2026-11-04.json"
LEGACY = {"college-football-v1", "cfb-spread-v1", "cfb-total-v1"}


def _metrics(rows: list[sqlite3.Row]) -> dict:
    scored = [row for row in rows if row["result"] in ("win", "loss")]
    if not scored:
        return {"calls": len(rows), "scored": 0}
    outcome = [float(row["result"] == "win") for row in scored]
    model = [float(row["model_probability"]) for row in scored]
    market = [float(row["market_probability"]) for row in scored]
    return {
        "calls": len(rows),
        "scored": len(scored),
        "pnl_units": round(sum(row["pnl_units"] or 0 for row in rows), 4),
        "staked_units": round(sum(row["units"] or 0 for row in rows), 4),
        "actual_win_rate": sum(outcome) / len(scored),
        "mean_model_probability": sum(model) / len(scored),
        "mean_recorded_price": sum(market) / len(scored),
        "model_brier": sum((p - y) ** 2 for p, y in zip(model, outcome, strict=True)) / len(scored),
        "market_brier": sum((p - y) ** 2 for p, y in zip(market, outcome, strict=True)) / len(scored),
        "model_log_loss": -sum(
            y * math.log(max(1e-6, min(1 - 1e-6, p))) + (1 - y) * math.log(max(1e-6, min(1 - 1e-6, 1 - p)))
            for p, y in zip(model, outcome, strict=True)
        )
        / len(scored),
        "market_log_loss": -sum(
            y * math.log(max(1e-6, min(1 - 1e-6, p))) + (1 - y) * math.log(max(1e-6, min(1 - 1e-6, 1 - p)))
            for p, y in zip(market, outcome, strict=True)
        )
        / len(scored),
    }


def _line_size(row: sqlite3.Row) -> str:
    if row["market_type"] == "moneyline" or row["line"] is None:
        return "none"
    line = abs(float(row["line"]))
    if row["market_type"] == "total":
        return "under_45" if line < 45 else "45_to_55" if line < 55 else "55_plus"
    return "under_7" if line < 7 else "7_to_21" if line <= 21 else "over_21"


def _favorite_status(row: sqlite3.Row) -> str:
    if row["market_type"] == "moneyline":
        return "favorite" if float(row["market_probability"]) >= 0.5 else "underdog"
    if row["market_type"] == "spread" and row["line"] is not None:
        return "favorite_laying" if float(row["line"]) < 0 else "underdog_getting"
    return "not_applicable"


def _week_index(event_ids: set[str]) -> dict[str, int]:
    path = ROOT / "data/processed/ncaaf/games.jsonl"
    if not path.is_file():
        return {}
    games = [
        game
        for line in path.read_text(encoding="utf-8").splitlines()
        if (game := json.loads(line)).get("event_id") in event_ids
    ]
    weeks = {str(game["event_id"]): int(game["week"]) for game in games if game.get("week") is not None}
    raw_dates = {
        str(game["raw_source"]).rsplit(":", 1)[-1]
        for game in games
        if game.get("week") is None and game.get("raw_source")
    }
    for day in raw_dates:
        raw_path = ROOT / "data/raw/ncaaf" / day / "scores_ncaaf.json"
        if not raw_path.is_file():
            continue
        board = json.loads(raw_path.read_text(encoding="utf-8"))
        for event in board.get("events", []):
            week = (event.get("week") or {}).get("number")
            if week is not None and event.get("id"):
                weeks[str(event["id"])] = int(week)
    return weeks


def _dimensions(rows: list[sqlite3.Row], weeks: dict[str, int]) -> dict[str, dict]:
    dimensions: dict[str, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        week = weeks.get(str(row["event_id"]))
        values = {
            "market": str(row["market_type"]),
            "favorite_status": _favorite_status(row),
            "line_size": _line_size(row),
            "season_week": str(week) if week is not None else "unknown",
            "model_version": str(row["model_id"]),
        }
        for dimension, value in values.items():
            dimensions[dimension][value].append(row)
    return {
        dimension: {value: _metrics(group) for value, group in sorted(groups.items())}
        for dimension, groups in sorted(dimensions.items())
    }


def _prospective_packet_status() -> dict:
    root = RuntimePaths.resolve(repo_root=ROOT).runtime_root / "research/college_contract_candidates/ncaaf"
    paths = list(root.glob("*/*.json")) if root.is_dir() else []
    latest_by_day: dict[str, dict] = {}
    rejected = 0
    for path in paths:
        try:
            packet = json.loads(path.read_text(encoding="utf-8"))
            rows = packet["rows"]
            summary = packet["summary"]
            valid_rows = all(
                row.get("record_hash")
                == hashlib.sha256(
                    json.dumps(
                        {key: value for key, value in row.items() if key != "record_hash"},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                for row in rows
            )
            digest = hashlib.sha256(
                json.dumps(
                    {"summary": summary, "row_hashes": [row["record_hash"] for row in rows]},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if not valid_rows or digest != packet.get("packet_hash") or digest != path.stem:
                rejected += 1
                continue
            day = str(summary["day"])
            decision_time = max((str(row["decision_at_utc"]) for row in rows), default="")
            prior = latest_by_day.get(day)
            if prior is None or decision_time > prior["decision_time"]:
                latest_by_day[day] = {"decision_time": decision_time, "packet": packet}
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            rejected += 1
    completed = {
        str(game["event_id"])
        for line in (ROOT / "data/processed/ncaaf/games.jsonl").read_text(encoding="utf-8").splitlines()
        if (game := json.loads(line)).get("status", "completed") == "completed"
    }
    selected_rows = [row for item in latest_by_day.values() for row in item["packet"]["rows"]]
    complete = [
        row for row in selected_rows if row["quote_reason"] == "matched" and row["model_reason"] == "scored"
    ]
    settled = [row for row in complete if str(row.get("model_event_id")) in completed]
    return {
        "packet_files": len(paths),
        "rejected_packet_files": rejected,
        "latest_days": len(latest_by_day),
        "listed_contracts": len(selected_rows),
        "quote_and_model_complete": len(complete),
        "settled_quote_and_model_complete": len(settled),
        "status": "awaiting_later_common_contract_cohort",
        "new_calibration_fitted": False,
        "new_gate_tested": False,
        "stakes_changed": False,
    }


def _archive_index(path: Path) -> dict[str, dict]:
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        records[digest] = row
    return records


def audit() -> dict:
    database = RuntimePaths.resolve(repo_root=ROOT).ledgers_db
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ledger_records WHERE sport='ncaaf' AND ledger_tier='main' AND status='settled'"
    ).fetchall()
    archives = {}
    quote_verified = []
    rejected = []
    ages_hours = []
    for row in rows:
        digest = row["market_snapshot_hash"]
        if not digest:
            continue
        path = Path(str(row["market_snapshot_archive_path"] or ""))
        if not path.is_file():
            rejected.append({"pick_id": row["pick_id"], "reason": "archive_missing"})
            continue
        if path not in archives:
            archives[path] = _archive_index(path)
        quote = archives[path].get(digest)
        if quote is None:
            rejected.append({"pick_id": row["pick_id"], "reason": "hash_not_in_archive"})
            continue
        observed = parse_utc(str(quote["observed_at_utc"]))
        start = parse_utc(str(row["event_start_utc"]))
        created = parse_utc(str(row["created_at_utc"]))
        asks = [float((quote.get(side) or {}).get("ask") or 0) for side in ("long", "short")]
        if not (
            quote.get("provider") == "polymarket_us"
            and quote.get("timestamp_valid") is True
            and quote.get("reconstructed") is False
            and quote.get("market_state") == "MARKET_STATE_OPEN"
            and quote.get("market_type") == row["market_type"]
            and observed <= created < start
            and all(0 < ask < 1 for ask in asks)
            and min(abs(ask - float(row["market_probability"])) for ask in asks) < 0.01
        ):
            rejected.append({"pick_id": row["pick_id"], "reason": "quote_contract_or_timing_mismatch"})
            continue
        if row["line"] is not None and not math.isclose(
            abs(float(row["line"])), abs(float(quote["line"])), abs_tol=1e-6
        ):
            rejected.append({"pick_id": row["pick_id"], "reason": "line_mismatch"})
            continue
        quote_verified.append(row)
        ages_hours.append((created - observed).total_seconds() / 3600)
    legacy = [row for row in rows if row["model_id"] in LEGACY]
    grouped = defaultdict(list)
    for row in legacy:
        grouped[f"{row['reason_code']}:{row['market_type']}"].append(row)
    quoted_groups = defaultdict(list)
    for row in quote_verified:
        quoted_groups[f"{row['model_id']}:{row['market_type']}"].append(row)
    result = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(database),
        "all_main_settled": len(rows),
        "quote_hash_present": sum(bool(row["market_snapshot_hash"]) for row in rows),
        "quote_verified": len(quote_verified),
        "quote_rejected": rejected,
        "quote_age_hours_median": sorted(ages_hours)[len(ages_hours) // 2] if ages_hours else None,
        "quote_verified_metrics": {key: _metrics(value) for key, value in sorted(quoted_groups.items())},
        "quote_verified_dimensions": _dimensions(
            quote_verified, _week_index({str(row["event_id"]) for row in quote_verified})
        ),
        "prospective_common_contract_status": _prospective_packet_status(),
        "legacy_indicative_metrics": {key: _metrics(value) for key, value in sorted(grouped.items())},
        "limitations": [
            "Legacy v1 has no archived executable quote lineage; its recorded market prices are indicative.",
            "Quote-verified rows are selected calls, not all priced sides or the whole scheduled slate.",
            "Model versions occur on different dates; pooled performance is not a simultaneous model comparison.",
            "No new calibration or gate is fitted: the quote-verified sample has no simultaneous v4 common-contract cohort.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
