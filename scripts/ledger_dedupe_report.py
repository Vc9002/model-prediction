"""Read-only settled-P&L report that counts each pick once.

Re-forecasts before commit b77a563d wrote several settled rows for the same pick
(same event, market, selection, line and model) with different probability, price
and size. This report keeps one row per key -- the latest by default, matching the
rule b77a563d applies to new picks -- and prints raw versus deduplicated totals.

It never writes: the database is opened read-only and the hash-chained ledger
history is left exactly as stored.

Usage:
    python scripts/ledger_dedupe_report.py [--db PATH] [--tier main] [--sport ncaaf]
                                           [--keep latest|first] [--list-duplicates]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_DB = r"E:\model-prediction-runtime\ledgers\ledgers.db"
KEY_COLUMNS = ("model_id", "event_id", "market_type", "selection", "line")


def settled_rows(conn: sqlite3.Connection, tier: str, sport: str | None) -> list[dict[str, Any]]:
    query = (
        "SELECT pick_id, model_id, event_id, market_type, selection, line, units, result, "
        "pnl_units, created_at_utc FROM ledger_records "
        "WHERE ledger_tier = ? AND result IS NOT NULL AND pnl_units IS NOT NULL"
    )
    params: list[Any] = [tier]
    if sport:
        query += " AND sport = ?"
        params.append(sport)
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query, params)]


def pick_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in KEY_COLUMNS)


def dedupe(rows: list[dict[str, Any]], keep: str = "latest") -> list[dict[str, Any]]:
    """One row per pick key: earliest or latest by created_at_utc (pick_id breaks ties)."""
    if keep not in {"latest", "first"}:
        raise ValueError("keep must be 'latest' or 'first'")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[pick_key(row)].append(row)
    chosen = []
    for group in groups.values():
        group.sort(key=lambda r: (r["created_at_utc"], r["pick_id"]))
        chosen.append(group[-1] if keep == "latest" else group[0])
    return chosen


def totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    staked = sum(float(r["units"] or 0) for r in rows)
    units = sum(float(r["pnl_units"] or 0) for r in rows)
    decided = [r for r in rows if r["result"] in ("win", "loss")]
    wins = sum(1 for r in decided if r["result"] == "win")
    return {
        "rows": len(rows),
        "units": units,
        "staked": staked,
        "roi": units / staked if staked else 0.0,
        "hit": wins / len(decided) if decided else 0.0,
    }


def report(rows: list[dict[str, Any]], keep: str, list_duplicates: bool) -> str:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[row["model_id"]].append(row)
    lines = [
        f"{'model':<24}{'rows':>6}{'picks':>7}{'dups':>6}{'raw U':>9}{'dedup U':>9}{'ROI':>8}{'hit':>7}",
    ]
    for model, model_rows in sorted(by_model.items()):
        kept = dedupe(model_rows, keep)
        raw, deduped = totals(model_rows), totals(kept)
        lines.append(
            f"{model:<24}{raw['rows']:>6}{deduped['rows']:>7}{raw['rows'] - deduped['rows']:>6}"
            f"{raw['units']:>+9.2f}{deduped['units']:>+9.2f}{deduped['roi']:>+8.1%}{deduped['hit']:>7.1%}"
        )
    if list_duplicates:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[pick_key(row)].append(row)
        lines.append("")
        lines.append("Duplicate keys (model, event, market, selection, line): rows -> kept")
        for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
            if len(group) > 1:
                kept_row = dedupe(group, keep)[0]
                lines.append(f"  {key}: {len(group)} rows -> kept {kept_row['pick_id']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--tier", default="main")
    parser.add_argument("--sport")
    parser.add_argument("--keep", choices=("latest", "first"), default="latest")
    parser.add_argument("--list-duplicates", action="store_true")
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"ledger database not found: {db}", file=sys.stderr)
        return 2
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        rows = settled_rows(conn, args.tier, args.sport)
    print(report(rows, args.keep, args.list_duplicates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
