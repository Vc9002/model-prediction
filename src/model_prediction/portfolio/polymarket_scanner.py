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


def _team_matches(team_name: str, side_description: str) -> bool:
    team = " ".join(team_name.casefold().split())
    description = " ".join(side_description.casefold().split())
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (description, team) if len(description) <= len(team) else (team, description)
    return f" {shorter} " in f" {longer} "


_PICKS_INDEX_CACHE: list[dict] | None = None


def _get_logged_picks() -> list[dict]:
    global _PICKS_INDEX_CACHE
    if _PICKS_INDEX_CACHE is not None:
        return _PICKS_INDEX_CACHE
    try:
        from model_prediction.dashboard.picks import (
            _parse_research_picks,
            read_flat_picks,
            read_picks,
        )

        _PICKS_INDEX_CACHE = read_picks() + read_flat_picks() + _parse_research_picks(gated=False)
    except Exception:  # noqa: BLE001
        _PICKS_INDEX_CACHE = []
    return _PICKS_INDEX_CACHE


def _lookup_model_prob(league: str, home_desc: str, away_desc: str) -> float | None:
    picks = _get_logged_picks()
    lg_upper = league.upper()
    for p in picks:
        if str(p.get("league") or "").upper() != lg_upper:
            continue
        p_home = str(p.get("home_team") or "")
        p_away = str(p.get("away_team") or "")
        if not p_home or not p_away:
            continue

        if _team_matches(p_home, home_desc) and _team_matches(p_away, away_desc):
            prob = p.get("model_probability")
            if prob is not None:
                try:
                    val = float(prob)
                    return val if str(p.get("selection") or "").lower() == "home" else (1.0 - val)
                except (ValueError, TypeError):
                    pass
        elif _team_matches(p_away, home_desc) and _team_matches(p_home, away_desc):
            prob = p.get("model_probability")
            if prob is not None:
                try:
                    val = float(prob)
                    return (1.0 - val) if str(p.get("selection") or "").lower() == "home" else val
                except (ValueError, TypeError):
                    pass
    return None


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

        # Look up true calibrated domain model probability
        p_model = _lookup_model_prob(league, home_or_a, away_or_b)

        return DispatchRequest(
            market_id=market_id,
            league=league,
            question=event_title or f"{home_or_a} vs {away_or_b}",
            home_or_player_a=home_or_a,
            away_or_player_b=away_or_b,
            best_bid=float(bid),
            best_ask=float(ask),
            event_start_utc=event_start_utc,
            p_model_override=p_model,
        )

    def scan_file(
        self,
        snapshot_path: Path | str,
        prefer_maker: bool = False,
    ) -> list[PolymarketOrderDecision]:
        """Scan a specific polymarket_snapshots.jsonl file with market deduplication."""
        p = Path(snapshot_path)
        if not p.exists():
            return []

        latest_by_market: dict[str, DispatchRequest] = {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                req = self.parse_snapshot_line(line)
                if req is not None:
                    latest_by_market[req.market_id] = req

        return self.dispatcher.get_actionable_orders(
            list(latest_by_market.values()), prefer_maker=prefer_maker
        )

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

        # Filter files by sport
        if sport_filter and sport_filter.lower() != "all":
            all_files = [f for f in all_files if sport_filter.lower() in str(f).lower()]

        if not all_files:
            return PolymarketScanResult(
                as_of_utc=date_filter or "none",
                total_markets_scanned=0,
                actionable_orders_count=0,
            )

        # If date_filter is not explicitly specified, find the latest date per sport
        target_files = []
        if date_filter and date_filter != "all":
            target_files = [f for f in all_files if date_filter in str(f)]
        else:
            # Group by sport and pick the latest date file for each sport
            by_sport: dict[str, Path] = {}
            for f in all_files:
                parts = f.parts
                # e.g. data/odds/mlb/2026-08-21/polymarket_snapshots.jsonl
                sport_name = parts[-3] if len(parts) >= 3 else "unknown"
                by_sport[sport_name] = f
            target_files = list(by_sport.values())

        latest_by_market: dict[str, DispatchRequest] = {}

        for f_path in target_files:
            with open(f_path, encoding="utf-8") as f:
                for line in f:
                    req = self.parse_snapshot_line(line)
                    if req is not None:
                        latest_by_market[req.market_id] = req

        unique_requests = list(latest_by_market.values())
        actionable = self.dispatcher.get_actionable_orders(unique_requests, prefer_maker=prefer_maker)
        total_stake = sum(order.stake_units for order in actionable)

        as_of = date_filter or (target_files[-1].parent.name if target_files else "latest_slate")

        return PolymarketScanResult(
            as_of_utc=as_of,
            total_markets_scanned=len(unique_requests),
            actionable_orders_count=len(actionable),
            actionable_orders=actionable,
            total_capital_staked=round(total_stake, 2),
        )
