#!/usr/bin/env python3
"""CLI script to run automated Polymarket buying linked to daily model picks."""

from __future__ import annotations

import argparse
import json
import sys

from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoPolymarketBuyer,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated Polymarket Buyer with safe risk gates and fractional unit sizing."
    )
    parser.add_argument(
        "--unit-value",
        type=float,
        default=0.005,
        help="Dollar value per 1 Unit (default: 0.005 = 0.5 cents per unit).",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.035,
        help="Minimum required model edge (default: 0.035 = +3.5%% edge).",
    )
    parser.add_argument(
        "--daily-budget",
        type=float,
        default=25.0,
        help="Hard cap on total daily spend in USD (default: $25.00).",
    )
    parser.add_argument(
        "--max-stake",
        type=float,
        default=2.50,
        help="Hard cap on single game/pick stake in USD (default: $2.50).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Submit real live orders to Polymarket US API. If omitted, runs in paper/dry-run mode.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output execution report in raw JSON format.",
    )
    args = parser.parse_args()

    config = AutoExecutionConfig(
        unit_value_usd=args.unit_value,
        min_edge=args.min_edge,
        max_daily_spend_usd=args.daily_budget,
        max_game_stake_usd=args.max_stake,
        execute_live=args.execute,
    )

    buyer = AutoPolymarketBuyer(config=config)
    result = buyer.evaluate_and_execute()

    if args.json:
        out = {
            "mode": "LIVE_EXECUTION" if args.execute else "PAPER_DRY_RUN",
            "unit_value_usd": args.unit_value,
            "min_edge": args.min_edge,
            "total_evaluated": result.total_evaluated,
            "whitelisted_count": result.whitelisted_count,
            "rejected_blacklist": result.rejected_blacklist,
            "rejected_low_edge": result.rejected_low_edge,
            "rejected_started": result.rejected_started,
            "rejected_budget": result.rejected_budget,
            "rejected_dedup": result.rejected_dedup,
            "total_spend_usd": result.total_spend_usd,
            "orders": result.submitted_orders if args.execute else result.dry_run_orders,
        }
        print(json.dumps(out, indent=2))
        return 0

    mode_str = (
        "*** LIVE REAL-MONEY EXECUTION ***" if args.execute else "[PAPER DRY-RUN PREVIEW] (No orders placed)"
    )
    print("=" * 90)
    print(f" AUTOMATED POLYMARKET BUYER | Mode: {mode_str}")
    print(
        f" Unit Value: ${args.unit_value:.4f} (0.5¢/U) | Min Edge: {args.min_edge:.1%} | Daily Budget: ${args.daily_budget:.2f}"
    )
    print("=" * 90)

    print(f"Picks Scanned:        {result.total_evaluated}")
    print(f"Whitelisted Evaluated:{result.whitelisted_count}")
    print(f"Rejected (Blacklist): {result.rejected_blacklist} (MLB/WNBA Spreads, CFB Totals)")
    print(f"Rejected (Low Edge):  {result.rejected_low_edge} (< {args.min_edge:.1%} edge)")
    print(f"Rejected (Started):   {result.rejected_started} (Game in progress/past)")
    print(f"Rejected (Dedup):     {result.rejected_dedup} (Already executed)")
    print(f"Rejected (Budget):    {result.rejected_budget} (Exceeds daily ${args.daily_budget:.2f})")
    print("-" * 90)

    orders = result.submitted_orders if args.execute else result.dry_run_orders
    if not orders:
        print("No qualified picks passed all edge, timing, and whitelist gates today.")
        print("=" * 90)
        return 0

    print(f"Actionable Orders: {len(orders)} | Total Planned Spend: ${result.total_spend_usd:.2f}")
    print("-" * 90)
    header = f"{'Pick ID':<18} | {'Sport':<8} | {'Model':<24} | {'Limit':<7} | {'Shares':<7} | {'Cost':<7} | {'Edge':<7}"
    print(header)
    print("-" * 90)
    for o in orders:
        row = (
            f"{o['pick_id']:<18} | "
            f"{o.get('sport') or ''!s:<8} | "
            f"{o.get('model_id') or ''!s:<24} | "
            f"{o['limit_price'] * 100:>5.1f}¢ | "
            f"{o['shares']:>7g} | "
            f"${o['cost_usd']:>5.2f} | "
            f"{o['edge'] * 100:>+5.1f}%%"
        )
        print(row)
    print("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
