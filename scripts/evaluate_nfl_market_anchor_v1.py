"""Read-only date-walk-forward NFL market-anchor comparison on saved Flat calls."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from model_prediction.domain import parse_utc
from model_prediction.models.nfl_market_anchor_v1 import (
    SettledQuote,
    asof_market_weight,
    market_anchored_probability,
)


def _valid_row(row: sqlite3.Row) -> tuple[str, SettledQuote, datetime, datetime] | None:
    if row["status"] != "settled" or row["result"] not in ("win", "loss"):
        return None
    payload = json.loads(row["decision_payload_json"])
    if str(payload.get("market_quote_timestamp_valid")).lower() != "true":
        return None
    if str(payload.get("market_quote_reconstructed")).lower() != "false":
        return None
    if payload.get("market_quote_source") != "polymarket_us":
        return None
    observed = parse_utc(payload["market_quote_observed_at_utc"])
    created = parse_utc(row["created_at_utc"])
    start = parse_utc(payload["event_start_utc"])
    settled = parse_utc(payload["settled_at_utc"])
    if not observed <= created < start:
        return None
    market = row["market_type"]
    quote_key = "decision_no_vig_probability" if market == "moneyline" else "market_implied_probability"
    quote = float(payload[quote_key])
    model = float(row["model_probability"])
    if not 0 < quote < 1 or not 0 < model < 1:
        return None
    event_date = start.astimezone(ZoneInfo("America/New_York")).date()
    return (
        market,
        SettledQuote(event_date, model, quote, int(row["result"] == "win"), settled),
        created,
        settled,
    )


def evaluate(db_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_id, market_type, model_probability, result, status, "
        "created_at_utc, decision_payload_json FROM ledger_records "
        "WHERE sport='nfl' AND ledger_tier='flat' AND market_type IN "
        "('moneyline','spread','total') ORDER BY created_at_utc"
    ).fetchall()
    connection.close()
    latest = {(row["event_id"], row["market_type"]): row for row in rows}
    by_market: dict[str, list[tuple[SettledQuote, datetime, datetime]]] = defaultdict(list)
    excluded: dict[str, int] = defaultdict(int)
    for row in latest.values():
        try:
            valid = _valid_row(row)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            valid = None
        if valid is None:
            excluded[row["market_type"]] += 1
            continue
        market, quote, created, settled = valid
        by_market[market].append((quote, created, settled))
    metrics = {}
    for market, history in by_market.items():
        history.sort(key=lambda item: item[0].event_date)
        evaluated = []
        weights = []
        for row, created, _ in history:
            try:
                weight = asof_market_weight([previous for previous, _, _ in history], row.event_date, created)
            except ValueError as exc:
                if str(exc) != "no_prior_complete_date_evidence":
                    raise
                continue
            weights.append((row.event_date.isoformat(), weight))
            candidate = market_anchored_probability(row.model_probability, row.market_probability, weight)
            evaluated.append((row, candidate))
        if not evaluated:
            continue
        n = len(evaluated)
        metrics[market] = {
            "quote_backed_rows": len(history),
            "walk_forward_rows": n,
            "dates": sorted({day for day, _ in weights}),
            "last_weight": weights[-1][1],
            "incumbent_brier": sum((row.model_probability - row.outcome) ** 2 for row, _ in evaluated) / n,
            "market_brier": sum((row.market_probability - row.outcome) ** 2 for row, _ in evaluated) / n,
            "challenger_brier": sum((candidate - row.outcome) ** 2 for row, candidate in evaluated) / n,
            "incumbent_log_loss": -sum(
                math.log(row.model_probability if row.outcome else 1 - row.model_probability)
                for row, _ in evaluated
            )
            / n,
            "challenger_log_loss": -sum(
                math.log(candidate if row.outcome else 1 - candidate) for row, candidate in evaluated
            )
            / n,
        }
    return {
        "markets": metrics,
        "excluded_rows": dict(excluded),
        "scope": "date-walk-forward retrospective research; quote-backed Flat calls only; no new orders",
        "quote_semantics": "moneyline uses saved no-vig; spread/total use saved raw implied ask, not two-sided no-vig",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.database), indent=2, sort_keys=True))
