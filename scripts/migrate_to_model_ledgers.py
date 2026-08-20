#!/usr/bin/env python3
"""Backfill data/model_ledgers/<model-id>.xlsx from every existing ledger.

Operator directive, 2026-08-02: one ledger per MODEL IDENTITY, not one
shared file per sport/routing-destination (Main/Flat/Research/Gated
Research). Those four destinations no longer mean anything under the new
"no classification" architecture -- they were about which curated ledger a
row got routed to, which this migration collapses away: every real-world
decision lands in exactly one row, in its model's own ledger, regardless of
which old destination(s) it happened to be logged to.

Read-only against the source ledgers -- nothing in data/main/*.xlsx,
data/flat/*.xlsx, data/research/*.xlsx, or data/gated_research/*.xlsx is
modified or deleted. Output only ever goes to data/model_ledgers/*.xlsx,
which this script owns exclusively; safe to re-run (existing prediction_ids
are skipped, not duplicated -- see --dry-run to preview without writing).

New-schema fields with no equivalent in the old rows (model_projection,
input_cutoff_utc, input_availability, missing_inputs, source_lineage's
per-input detail) are left blank for migrated rows -- they were never
recorded historically and are not invented here.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/migrate_to_model_ledgers.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.ledger import PickLedger
from model_prediction.model_ledger import ModelLedger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = PROJECT_ROOT / "data"
MODEL_LEDGERS_DIR = DATA / "model_ledgers"

# (league, market_type) -> model identity. SOCCER intentionally maps every
# market_type to the same identity: one Poisson/Dixon-Coles model produces
# both moneyline and total predictions, not two separate models.
MODEL_ID_BY_LEAGUE_AND_MARKET: dict[tuple[str, str], str] = {
    ("MLB", "moneyline"): "mlb-moneyline-elo-trend-lr",
    ("MLB", "spread"): "mlb-spread-measured-edge",
    ("MLB", "total"): "mlb-total-measured-edge",
    ("NBA", "moneyline"): "nba-moneyline-elo-trend-lr",
    ("NBA", "spread"): "nba-spread-baseline",
    ("NBA", "total"): "nba-total-score-ridge",
    ("WNBA", "moneyline"): "wnba-moneyline-elo-trend-lr",
    ("WNBA", "spread"): "wnba-spread-baseline",
    ("WNBA", "total"): "wnba-total-score-ridge",
    ("NFL", "moneyline"): "nfl-moneyline-elo-trend-lr",
    ("NFL", "spread"): "nfl-spread-baseline",
    ("NFL", "total"): "nfl-total-score-ridge",
    ("SOCCER", "moneyline"): "soccer-poisson-dc",
    ("SOCCER", "total"): "soccer-poisson-dc",
    ("SOCCER", "btts"): "soccer-poisson-dc",
    ("TENNIS", "moneyline"): "tennis-surface-elo",
    ("LOL", "moneyline"): "lol-tiered-elo",
    ("CS2", "moneyline"): "cs2-tiered-elo",
    ("DOTA2", "moneyline"): "dota2-tiered-elo",
    ("VALORANT", "moneyline"): "valorant-tiered-elo",
    ("RAINBOW_SIX", "moneyline"): "rainbow-six-tiered-elo",
    ("KBO", "moneyline"): "kbo-tie-aware-elo",
    ("NPB", "moneyline"): "npb-tie-aware-elo",
}


def _source_ledger_paths() -> list[Path]:
    # Main/Flat were single shared files (data/picks.xlsx, data/flat_picks.xlsx)
    # until the 2026-08-03 per-sport split (see main_ledgers.py); this now
    # reads their replacements, data/main/<sport>.xlsx and data/flat/<sport>.xlsx.
    paths = sorted((DATA / "main").glob("*.xlsx"))
    paths.extend(sorted((DATA / "flat").glob("*.xlsx")))
    paths.extend(sorted((DATA / "research").glob("*.xlsx")))
    paths.extend(sorted((DATA / "gated_research").glob("*.xlsx")))
    return [path for path in paths if path.exists()]


def _dedupe_key(row: dict) -> tuple[str, str, str, str, str, str]:
    """Same identity notion as ledger.py's _market_duplicate_key, extended
    with model_id (computed by the caller) -- one real-world decision, not
    one row-per-old-routing-destination."""
    line = row.get("line", "")
    try:
        line = str(abs(float(line))) if line else ""
    except ValueError:
        pass
    return (
        row["model_id"],
        row.get("event_id", ""),
        row.get("market_type", ""),
        line,
        str(row.get("sportsbook", "")).casefold(),
        row.get("model_version", ""),
    )


def _to_new_schema_record(old_row: dict, model_id: str, source_path: Path) -> dict:
    return {
        "model_id": model_id,
        "model_version": old_row.get("model_version", ""),
        "artifact_hash": old_row.get("model_artifact_hash", ""),
        "code_revision": old_row.get("code_revision", ""),
        "feature_schema_version": old_row.get("feature_schema_version", ""),
        "event_id": old_row.get("event_id", ""),
        "market_type": old_row.get("market_type", ""),
        "selection": old_row.get("selection", ""),
        "line": old_row.get("line", ""),
        "model_probability": old_row.get("model_probability", ""),
        "model_uncertainty": old_row.get("model_uncertainty", ""),
        "decision_price": old_row.get("decision_raw_implied_probability")
        or old_row.get("market_implied_probability", ""),
        "market_no_vig_probability": old_row.get("decision_no_vig_probability", ""),
        "model_market_difference": old_row.get("edge", ""),
        "observed_at_utc": old_row.get("observed_at_utc", ""),
        "event_start_utc": old_row.get("event_start_utc", ""),
        "status": old_row.get("status", ""),
        "result": old_row.get("result", ""),
        "closing_price": old_row.get("closing_raw_implied_probability", ""),
        "probability_clv": old_row.get("probability_clv", ""),
        "pnl_units": old_row.get("pnl_units", ""),
        "settled_at_utc": old_row.get("settled_at_utc", ""),
        "source_lineage": (
            f"migrated_from={source_path.relative_to(PROJECT_ROOT)};"
            f"original_pick_id={old_row.get('pick_id', '')};"
            f"original_record_type={old_row.get('record_type', '')}"
        ),
    }


def migrate(*, dry_run: bool) -> dict:
    unmapped: dict[tuple[str, str], int] = {}
    by_model: dict[str, dict[tuple, dict]] = {}
    total_source_rows = 0

    for source_path in _source_ledger_paths():
        for row in PickLedger(source_path).rows():
            total_source_rows += 1
            key = (str(row.get("league", "")).upper(), str(row.get("market_type", "")))
            model_id = MODEL_ID_BY_LEAGUE_AND_MARKET.get(key)
            if model_id is None:
                unmapped[key] = unmapped.get(key, 0) + 1
                continue
            new_row = _to_new_schema_record(row, model_id, source_path)
            new_row["_original_pick_id"] = row.get("pick_id", "")
            new_row["_original_status"] = row.get("status", "")
            dedupe_key = _dedupe_key(new_row)
            bucket = by_model.setdefault(model_id, {})
            existing = bucket.get(dedupe_key)
            # Prefer a settled copy over an open one when the same real
            # decision was logged to more than one old destination ledger
            # (e.g. both Main and Flat) -- settled carries the real outcome.
            if existing is None or (
                new_row["_original_status"] == "settled" and existing["_original_status"] != "settled"
            ):
                bucket[dedupe_key] = new_row

    written = 0
    skipped_existing = 0
    per_model_counts: dict[str, int] = {}
    for model_id, bucket in sorted(by_model.items()):
        ledger_path = MODEL_LEDGERS_DIR / f"{model_id}.xlsx"
        ledger = ModelLedger(ledger_path)
        existing_ids = {row["prediction_id"] for row in ledger.rows()} if ledger_path.exists() else set()
        count = 0
        for new_row in bucket.values():
            prediction_id = new_row.pop("_original_pick_id")
            new_row.pop("_original_status")
            if prediction_id in existing_ids:
                skipped_existing += 1
                continue
            if not dry_run:
                ledger.append_prediction(new_row, prediction_id=prediction_id or None)
            written += 1
            count += 1
        per_model_counts[model_id] = count

    return {
        "dry_run": dry_run,
        "total_source_rows_scanned": total_source_rows,
        "written": written,
        "skipped_already_migrated": skipped_existing,
        "unmapped_league_market_combinations": {
            f"{league}/{market}": n for (league, market), n in unmapped.items()
        },
        "per_model_written": per_model_counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would happen without writing")
    args = parser.parse_args()
    import json

    print(json.dumps(migrate(dry_run=args.dry_run), indent=2, sort_keys=True))
