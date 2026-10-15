"""Multi-Sport Market Quote Backfill with Depth Liquidity Ingestion.

Ingests orderbook snapshots from data/odds/<sport>/*/polymarket_snapshots.jsonl
into MarketQuoteWarehouse (data/market_quotes.db).

Key improvements:
1. Multi-sport coverage: soccer, nfl, nba, ncaaf, tennis, wnba, esports, kbo, npb, mlb.
2. Depth liquidity calculation: captures top-of-book executable depth in USD:
   depth_liquidity_usd = round(bid * bid_size + ask * ask_size, 2).
3. Resilient field extraction across market types (moneyline, spread, total, nrfi, team_win).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import MarketQuote


def calculate_depth_liquidity(
    long_side: dict[str, Any], short_side: dict[str, Any] | None = None
) -> float | None:
    """Calculate executable top-of-book liquidity in USD."""
    depth_usd = 0.0
    bid = float(long_side.get("bid") or 0.0)
    bid_size = float(long_side.get("bid_size") or 0.0)
    ask = float(long_side.get("ask") or 0.0)
    ask_size = float(long_side.get("ask_size") or 0.0)

    if bid > 0 and bid_size > 0:
        depth_usd += bid * bid_size
    if ask > 0 and ask_size > 0:
        depth_usd += ask * ask_size

    if short_side:
        s_bid = float(short_side.get("bid") or 0.0)
        s_bid_size = float(short_side.get("bid_size") or 0.0)
        s_ask = float(short_side.get("ask") or 0.0)
        s_ask_size = float(short_side.get("ask_size") or 0.0)
        if s_bid > 0 and s_bid_size > 0:
            depth_usd += s_bid * s_bid_size
        if s_ask > 0 and s_ask_size > 0:
            depth_usd += s_ask * s_ask_size

    return round(depth_usd, 2) if depth_usd > 0 else None


def backfill_multi_sport_quotes(
    sports: list[str] | None = None,
    batch_size: int = 5000,
    limit_files_per_sport: int | None = None,
) -> dict[str, Any]:
    db_path = PROJECT_ROOT / "data" / "market_quotes.db"
    warehouse = MarketQuoteWarehouse(db_path=db_path)

    odds_root = PROJECT_ROOT / "data" / "odds"
    available_sports = [p.name for p in odds_root.iterdir() if p.is_dir() and p.name not in {"historical"}]

    target_sports = sports or sorted(available_sports)
    total_ingested = 0
    sport_stats = defaultdict(lambda: {"quotes": 0, "with_depth": 0, "files": 0})

    for sport in target_sports:
        sport_dir = odds_root / sport
        if not sport_dir.exists():
            continue

        files = sorted(sport_dir.glob("*/polymarket_snapshots.jsonl"))
        if limit_files_per_sport:
            files = files[-limit_files_per_sport:]

        sport_stats[sport]["files"] = len(files)
        quotes_batch: list[MarketQuote] = []

        for sfile in files:
            try:
                with open(sfile, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)

                        eid = row.get("event_slug") or row.get("event_id")
                        if not eid:
                            continue

                        long_side = row.get("long") or {}
                        short_side = row.get("short") or {}
                        long_bid = long_side.get("bid")
                        long_ask = long_side.get("ask")
                        long_mid = long_side.get("midpoint")
                        short_mid = short_side.get("midpoint")

                        no_vig_prob = None
                        if long_mid is not None and short_mid is not None and (long_mid + short_mid) > 0:
                            no_vig_prob = round(long_mid / (long_mid + short_mid), 6)

                        depth_liq = calculate_depth_liquidity(long_side, short_side)
                        if depth_liq and depth_liq > 0:
                            sport_stats[sport]["with_depth"] += 1

                        mtype = str(row.get("market_type") or "moneyline").lower()
                        selection = str(
                            long_side.get("description")
                            or row.get("team")
                            or ("Over" if mtype == "total" else "Home")
                        )

                        q = MarketQuote(
                            event_id=str(eid),
                            sport=sport.lower(),
                            market_type=mtype,
                            selection=selection,
                            source=str(row.get("provider") or "polymarket_us").lower(),
                            observed_at_utc=str(
                                row.get("observed_at_utc")
                                or row.get("transact_time_utc")
                                or datetime.now(UTC).isoformat()
                            ),
                            line=row.get("line"),
                            best_bid=long_bid,
                            best_ask=long_ask,
                            no_vig_probability=no_vig_prob,
                            depth_liquidity_usd=depth_liq,
                        )
                        quotes_batch.append(q)

                        if len(quotes_batch) >= batch_size:
                            warehouse.record_quotes_batch(quotes_batch)
                            total_ingested += len(quotes_batch)
                            sport_stats[sport]["quotes"] += len(quotes_batch)
                            quotes_batch.clear()

            except (OSError, json.JSONDecodeError):
                continue

        if quotes_batch:
            warehouse.record_quotes_batch(quotes_batch)
            total_ingested += len(quotes_batch)
            sport_stats[sport]["quotes"] += len(quotes_batch)
            quotes_batch.clear()

    warehouse.close()

    result = {
        "status": "success",
        "total_quotes_ingested": total_ingested,
        "sports": dict(sport_stats),
        "database_file": str(db_path),
    }

    out_file = PROJECT_ROOT / "outputs" / "research" / "multi_sport_quote_backfill_summary.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"Ingested {total_ingested} quotes across {len(sport_stats)} sports:")
    for s, st in sport_stats.items():
        print(f"  {s}: {st['quotes']} quotes ({st['with_depth']} with depth > $0) from {st['files']} files")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", nargs="*", default=None)
    parser.add_argument("--limit-files", type=int, default=None)
    args = parser.parse_args()

    backfill_multi_sport_quotes(sports=args.sports, limit_files_per_sport=args.limit_files)
