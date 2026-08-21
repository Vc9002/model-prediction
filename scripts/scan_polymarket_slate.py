#!/usr/bin/env python3
"""CLIscript to scan live Polymarket US odds files and print Quarter-Kelly edge tickets."""

from __future__ import annotations

import argparse
import json
import sys

from model_prediction.portfolio.polymarket_scanner import PolymarketSlateScanner


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Polymarket US live quotes for actionable model edges.")
    parser.add_argument("--base-dir", default="data/odds", help="Base directory containing odds snapshots.")
    parser.add_argument(
        "--sport", default=None, help="Filter by sport/league slug (e.g. mlb, esports, kbo, tennis, wnba)."
    )
    parser.add_argument("--date", default=None, help="Filter by date string YYYY-DM-DD.")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Total bankroll for Kelly bet sizing.")
    parser.add_argument(
        "--min-edge", type=float, default=0.025, help="Minimum edge threshold (default: 0.025 for +2.5%%)."
    )
    parser.add_argument(
        "--maker", action="store_true", help="Format orders as inside-spread maker limit orders."
    )
    parser.add_argument("--json", action="store_true", help="Output results in raw JSON format.")
    args = parser.parse_args()

    scanner = PolymarketSlateScanner(
        bankroll=args.bankroll,
        min_edge=args.min_edge,
    )

    result = scanner.scan_directory(
        base_dir=args.base_dir,
        sport_filter=args.sport,
        date_filter=args.date,
        prefer_maker=args.maker,
    )

    if args.json:
        out = {
            "total_markets_scanned": result.total_markets_scanned,
            "actionable_orders_count": result.actionable_orders_count,
            "total_capital_staked": result.total_capital_staked,
            "orders": [
                {
                    "market_id": o.market_id,
                    "side": o.side,
                    "order_price": o.order_price,
                    "model_probability": o.model_probability,
                    "market_price": o.market_price,
                    "edge": o.edge,
                    "ev_pct": o.expected_value_pct,
                    "stake_units": o.stake_units,
                    "kelly_fraction": o.kelly_fraction_recommended,
                    "is_maker": o.is_maker,
                    "reason": o.reason,
                }
                for o in result.actionable_orders
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    print("=" * 100)
    print(
        f" POLYMARKET US LIVE%DGE SCANNER | Total Scanned: {result.total_markets_scanned} | Actionable: {result.actionable_orders_count} | Bankroll: ${args.bankroll:.2f}"
    )
    print("=" * 100)

    if not result.actionable_orders:
        print("No actionable edges found passing the minimum +2.5% edge filter.")
        print("=" * 100)
        return 0

    header = (
        f"{'Market ID':<10} | "
        f"{'Side':<8} | "
        f"{'Order Price':<11} | "
        f"{'Model Prob':<10} | "
        f"{'Edge':<8} | "
        f"{'EV%':<7} | "
        f"{'Stake ($)':<9} | "
        f"{'Type':<8}"
    )
    print(header)
    print("-" * 100)

    for o in result.actionable_orders:
        order_type = "MAKER" if o.is_maker else "TAKER"
        row = (
            f"{o.market_id:<10} | "
            f"{o.side:<8} | "
            f"{o.order_price * 100:>9.1f}¢ | "
            f"{o.model_probability * 100:>8.1f}% | "
            f"{o.edge * 100:>+6.1f}% | "
            f"{o.expected_value_pct:>+5.1f}% | "
            f"${o.stake_units:>7.2f} | "
            f"{order_type:<8}"
        )
        print(row)

    print("-" * 100)
    print(
        f"Total Actionable Orders: {len(result.actionable_orders)} | Total Capital Recommended: ${result.total_capital_staked:.2f}"
    )
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
