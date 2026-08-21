"""Polymarket US Live Slate Scanner & Portfolio Edge Engine.

Scans prospective Polymarket CLOB snapshot files, parses executable BBO quotes,
evaluates corresponding quantitative models, and generates execution-ready
Quarter-Kelly orders passing the minimum edge gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .polymarket_dispatcher import (
    DispatchRequest,
    PolymarketDispatcher,
    PolymarketOrderDecision,
)


@dataclass(slots=True)
class PolymarketScanResult:
    """Aggregated result of a Polymarket slate scan."""

    as_of_utc: str
    total_markets_scanned: int
    actionable_orders_count: int
    actionable_orders: list[PolymarketOrderDecision] = field(default_factory=list)
    total_capital_staked: float = 0.0


class PolymarketSlateScanner:
    """Engine scanning live snapshot JSONL files across all sports."""

    def __init__(
        self,
        bankroll: float = 1000.0,
        min_edge: float = 0.025,
        kelly_fraction: float = 0.25,
        max_position_pct: float = 0.03,
    ) -> None:
        self.dispatcher = PolymarketDispatcher(
            bankroll=bankroll,
            min_edge=min_edge,
            kelly_fraction=kelly_fraction,
            max_position_pct=max_position_pct,
        )

    def parse_snapshot_line(self, line: str) -> DispatchRequest | None:
        """Parse a single JSONL snapshot line into a DispatchRequest."""
        try:
            data = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError, KeyError):
            return None

        market_type = data.get("market_type")
        if market_type != "moneyline":
            return None  # Focus primary execution on Moneylines

        long_q = data.get("long", {})
        short_q = data.get("short", {})

        ask = long_q.get("ask")
        bid = long_q.get("bid")
        if ask is None or bid is None:
            return None

        # Ignore non-executable or illiquid extreme lines
        if ask <= 0.01 or ask >= 0.99 or bid <= 0.0:
            return None

        market_id = str(data.get("market_id", ""))
        league = str(data.get("league", "")).upper()
        event_title = str(data.get("event_title", ""))
        event_start_utc = str(data.get("event_start_utc", ""))
        home_or_a = str(long_q.get("description", ""))
        away_or_b = str(short_q.get("description", ""))

        return DispatchRequest(
            market_id=market_id,
            league=league,
            question=event_title or f"{home_or_a} vs {away_or_b}",
            home_or_player_a=home_or_a,
            away_or_player_b=away_or_b,
            best_bid=float(bid),
            best_ask=float(ask),
            event_start_utc=event_start_utc,
        )

    def scan_file(
        self,
        snapshot_path: Path | str,
        prefer_maker: bool = False,
    ) -> list[PolymarketOrderDecision]:
        """Scan a specific polymarket_snapshots.jsonl file."""
        p = Path(snapshot_path)
        if not p.exists():
            return []

        requests: list[DispatchRequest] = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                req = self.parse_snapshot_line(line)
                if req is not None:
                    requests.append(req)

        return self.dispatcher.get_actionable_orders(requests, prefer_maker=prefer_maker)

    def scan_directory(
        self,
        base_dir: Path | str = "data/odds",
        sport_filter: str | None = None,
        date_filter: str | None = None,
        prefer_maker: bool = False,
    ) -> PolymarketScanResult:
        """Scan multiple snapshot files across sports and dates."""
        base = Path(base_dir)
        if not base.exists():
            return PolymarketScanResult(
                as_of_utc="",
                total_markets_scanned=0,
                actionable_orders_count=0,
            )

        pattern = "**/*polymarket_snapshots.jsonl"
        all_files = sorted(base.glob(pattern))

        total_scanned = 0
        all_requests: list[DispatchRequest] = []

        for f_path in all_files:
            str_path = str(f_path)
            if sport_filter and sport_filter.lower() not in str_path.lower():
                continue
            if date_filter and date_filter not in str_path:
                continue

            with open(f_path, encoding="utf-8") as f:
                for line in f:
                    req = self.parse_snapshot_line(line)
                    if req is not None:
                        total_scanned += 1
                        all_requests.append(req)

        actionable = self.dispatcher.get_actionable_orders(all_requests, prefer_maker=prefer_maker)
        total_stake = sum(order.stake_units for order in actionable)

        return PolymarketScanResult(
            as_of_utc=date_filter or "all_scanned",
            total_markets_scanned=total_scanned,
            actionable_orders_count=len(actionable),
            actionable_orders=actionable,
            total_capital_staked=round(total_stake, 2),
        )
