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


def _normalize_league(lg: str) -> str:
    s = lg.upper().replace(" ", "_").replace("-", "_")
    if s in ("CSGO", "COUNTER_STRIKE", "COUNTER_STRIKE_2"):
        return "CS2"
    if s in ("LEAGUE_OF_LEGENDS", "LEAGUEOFLEGENDS"):
        return "LOL"
    if s in ("R6", "RAINBOW6", "RAINBOW_6", "R6_SIEGE"):
        return "RAINBOW_SIX"
    if s in ("DOTA", "DOTA_2"):
        return "DOTA2"
    if s in ("ATP", "WTA"):
        return "TENNIS"
    return s


def _normalize_name(name: str) -> str:
    return " ".join(
        name.casefold().replace(".", "").replace("-", " ").replace("'", "").replace("_", " ").split()
    )


def _team_matches(team_name: str, side_description: str) -> bool:
    team = _normalize_name(team_name)
    description = _normalize_name(side_description)
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


def _lookup_model_prob(
    league: str,
    home_desc: str,
    away_desc: str,
    event_start_utc: str | None = None,
    market_type: str = "moneyline",
) -> float | None:
    picks = _get_logged_picks()
    lg_norm = _normalize_league(league)
    target_date = event_start_utc[:10] if event_start_utc else None

    candidates: list[tuple[int, str, float]] = []
    for p in picks:
        p_lg = _normalize_league(str(p.get("league") or ""))
        if p_lg != lg_norm:
            continue
        p_mtype = str(p.get("market_type") or "moneyline").lower()
        if p_mtype != market_type.lower():
            continue

        p_home = str(p.get("home_team") or "")
        p_away = str(p.get("away_team") or "")
        if not p_home or not p_away:
            continue

        p_date = str(p.get("event_start_utc") or "")[:10]

        match_direct = _team_matches(p_home, home_desc) and _team_matches(p_away, away_desc)
        match_reverse = _team_matches(p_away, home_desc) and _team_matches(p_home, away_desc)

        if match_direct or match_reverse:
            prob = p.get("model_probability")
            if prob is not None:
                try:
                    val = float(prob)
                    sel = str(p.get("selection") or "").lower()
                    if match_direct:
                        # home_desc is p_home
                        p_win = val if sel == "home" else (1.0 - val)
                    else:
                        # home_desc is p_away
                        p_win = (1.0 - val) if sel == "home" else val

                    score = 0
                    if target_date and p_date == target_date:
                        score += 10
                    candidates.append((score, str(p.get("created_at_utc") or ""), p_win))
                except (ValueError, TypeError):
                    pass

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]
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

    def parse_snapshot_line(self, line: str, require_model: bool = True) -> DispatchRequest | None:
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
        p_model = _lookup_model_prob(
            league, home_or_a, away_or_b, event_start_utc=event_start_utc, market_type=market_type
        )
        if require_model and p_model is None:
            return None

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
        require_model: bool = True,
    ) -> list[PolymarketOrderDecision]:
        """Scan a specific polymarket_snapshots.jsonl file with market deduplication."""
        p = Path(snapshot_path)
        if not p.exists():
            return []

        latest_by_market: dict[str, DispatchRequest] = {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                req = self.parse_snapshot_line(line, require_model=require_model)
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
        require_model: bool = True,
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
                    req = self.parse_snapshot_line(line, require_model=require_model)
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
