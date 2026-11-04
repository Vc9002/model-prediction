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
        "legacy_indicative_metrics": {key: _metrics(value) for key, value in sorted(grouped.items())},
        "limitations": [
            "Legacy v1 has no archived executable quote lineage; its recorded market prices are indicative.",
            "Quote-verified rows are selected calls, not all priced sides or the whole scheduled slate.",
            "Model versions occur on different dates; pooled performance is not a simultaneous model comparison.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
