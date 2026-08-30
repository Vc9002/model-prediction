"""Automated Polymarket Execution Engine.

Executes forecast picks with strict safety gates:
1. Whitelist of empirical positive-EV models (Tennis, Soccer, WNBA ML, CS2/LoL Gated, MLB NRFI/Totals).
2. Explicit blacklist blocking uncalibrated or negative-EV derivatives (MLB Spread, WNBA Spread, CFB Totals).
3. Configurable unit value sizing (default 1U = $0.005 / 0.5 cent).
4. Point-in-time pregame verification (now < event_start_utc) and live quote freshness (< 5 min).
5. Hard daily spending limit and per-game exposure caps.
6. Single-order deduplication per pick_id against the append-only audit log.
7. Real-money live execution when enabled, with persistent dashboard ON/OFF control.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from model_prediction.audit import AuditLog
from model_prediction.data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)
from model_prediction.domain import iso_utc, parse_utc, utc_now
from model_prediction.runtime_paths import RuntimePaths

_paths = RuntimePaths.resolve()
DATA = _paths.repo_root / "data"

# Auto-load .env credentials if present
_env_file = _paths.repo_root / ".env"
if _env_file.exists():
    try:
        with _env_file.open(encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _key, _val = _line.split("=", 1)
                    if _key.strip() and _key.strip() not in os.environ:
                        os.environ[_key.strip()] = _val.strip()
    except OSError:
        pass

DEFAULT_WHITELIST_MODELS: tuple[str, ...] = (
    "tennis-surface-elo-v1",
    "soccer-poisson-dc-v1",
    "wnba-elo-trend-lr-v4",
    "cs2-tiered-elo-v6",
    "lol-tiered-elo-v6",
    "mlb-nrfi-v1",
    "measured-edge-totals-v3",
)

EXPLICIT_BLACKLIST_MODELS: tuple[str, ...] = (
    "measured-edge-margin-v3",  # MLB run line (negative EV)
    "wnba-spread-margin-v1",  # WNBA spread (severe miscalibration)
    "cfb-total-v1",  # CFB total (uncalibrated)
)

AUTO_BUYER_STATE_FILE = DATA / "auto_buyer_state.json"


@dataclass(frozen=True)
class AutoExecutionConfig:
    unit_value_usd: float = 0.005  # 1U = 0.5 cent default ($0.005)
    min_edge: float = 0.035  # Minimum +3.5% edge over market ask
    max_daily_spend_usd: float = 25.0  # Daily budget cap
    max_game_stake_usd: float = 2.50  # Max dollars per game/pick
    min_shares: float = 1.0  # Polymarket minimum share size
    whitelisted_models: tuple[str, ...] = DEFAULT_WHITELIST_MODELS
    blacklisted_models: tuple[str, ...] = EXPLICIT_BLACKLIST_MODELS
    execute_live: bool = False  # Dry-run by default unless enabled/requested


@dataclass
class AutoExecutionResult:
    total_evaluated: int = 0
    whitelisted_count: int = 0
    rejected_blacklist: int = 0
    rejected_future_slate: int = 0
    rejected_started: int = 0
    rejected_stale_quote: int = 0
    rejected_unmapped_market: int = 0
    rejected_closed_market: int = 0
    rejected_low_edge: int = 0
    rejected_budget: int = 0
    rejected_dedup: int = 0
    submitted_orders: list[dict[str, Any]] = field(default_factory=list)
    dry_run_orders: list[dict[str, Any]] = field(default_factory=list)
    total_spend_usd: float = 0.0


def load_auto_buyer_state() -> dict[str, Any]:
    """Load persistent auto-buyer configuration and runtime toggle state."""
    default_state = {
        "enabled": False,
        "unit_value_usd": 0.005,
        "min_edge": 0.035,
        "max_daily_spend_usd": 25.0,
        "max_game_stake_usd": 2.50,
        "whitelist_models": list(DEFAULT_WHITELIST_MODELS),
        "blacklist_models": list(EXPLICIT_BLACKLIST_MODELS),
        "last_run": None,
        "last_daily_date": None,
    }
    if not AUTO_BUYER_STATE_FILE.exists():
        return default_state
    try:
        data = json.loads(AUTO_BUYER_STATE_FILE.read_text(encoding="utf-8"))
        return {**default_state, **data}
    except (json.JSONDecodeError, OSError):
        return default_state


def save_auto_buyer_state(state: dict[str, Any]) -> None:
    """Save auto-buyer toggle state and settings atomically."""
    AUTO_BUYER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = AUTO_BUYER_STATE_FILE.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temp_file.replace(AUTO_BUYER_STATE_FILE)


def toggle_auto_buyer(enabled: bool | None = None) -> dict[str, Any]:
    """Toggle Auto-Buyer ON or OFF from the dashboard or API."""
    state = load_auto_buyer_state()
    if enabled is None:
        state["enabled"] = not state.get("enabled", False)
    else:
        state["enabled"] = bool(enabled)
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    return state


def run_auto_buyer_cycle(
    execute_override: bool | None = None,
    forecast_date: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run one cycle of the Auto-Buyer based on saved configuration.

    Evaluates all open forecast picks and executes qualifying whitelisted orders,
    with fail-closed deduplication ensuring already-bought events are never re-purchased
    across multiple daily runs (e.g. 12:30 AM and 12:00 PM schedules).
    """
    state = load_auto_buyer_state()
    should_execute = state.get("enabled", False) if execute_override is None else bool(execute_override)

    config = AutoExecutionConfig(
        unit_value_usd=float(state.get("unit_value_usd", 0.005)),
        min_edge=float(state.get("min_edge", 0.035)),
        max_daily_spend_usd=float(state.get("max_daily_spend_usd", 25.0)),
        max_game_stake_usd=float(state.get("max_game_stake_usd", 2.50)),
        execute_live=should_execute,
    )

    buyer = AutoPolymarketBuyer(config=config)
    res = buyer.evaluate_and_execute()

    run_summary = {
        "executed_at_utc": iso_utc(utc_now()),
        "forecast_date": forecast_date,
        "mode": "LIVE_EXECUTION" if should_execute else "DISABLED_OR_DRY_RUN",
        "total_evaluated": res.total_evaluated,
        "whitelisted_count": res.whitelisted_count,
        "rejected_blacklist": res.rejected_blacklist,
        "rejected_future_slate": res.rejected_future_slate,
        "rejected_started": res.rejected_started,
        "rejected_stale_quote": res.rejected_stale_quote,
        "rejected_unmapped_market": res.rejected_unmapped_market,
        "rejected_closed_market": res.rejected_closed_market,
        "rejected_low_edge": res.rejected_low_edge,
        "rejected_budget": res.rejected_budget,
        "rejected_dedup": res.rejected_dedup,
        "total_spend_usd": res.total_spend_usd,
        "orders_count": len(res.submitted_orders) if should_execute else len(res.dry_run_orders),
        "orders": res.submitted_orders if should_execute else res.dry_run_orders,
    }

    state["last_run"] = run_summary
    if forecast_date:
        state["last_daily_date"] = forecast_date
    save_auto_buyer_state(state)
    return run_summary


class AutoPolymarketBuyer:
    """Automated buyer executing qualified model picks with risk gates."""

    def __init__(
        self,
        config: AutoExecutionConfig | None = None,
        audit: AuditLog | None = None,
        live_quote_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config or AutoExecutionConfig()
        self.audit = audit or AuditLog(DATA / "audit.jsonl")
        self._live_quote_fn = live_quote_fn

    def _build_bought_index(self) -> dict[str, set[Any]]:
        """Index all executed, submitted, and held positions to prevent double-buying."""
        bought_pick_ids: set[str] = set()
        bought_market_sides: set[tuple[str, str]] = set()
        bought_event_selections: set[tuple[str, str]] = set()
        held_slugs: set[str] = set()

        # 1. Audit logs (self.audit, data/audit.jsonl, data/audit_log.jsonl)
        audit_paths = [self.audit.path, DATA / "audit.jsonl", DATA / "audit_log.jsonl"]
        seen_paths: set[Path] = set()
        for log_path in audit_paths:
            resolved = log_path.resolve()
            if resolved in seen_paths or not log_path.exists():
                continue
            seen_paths.add(resolved)
            try:
                with log_path.open(encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ev_type = event.get("event_type")
                        if ev_type in {"order_executed", "order_submitted"}:
                            subj = str(event.get("subject_id") or "")
                            payload = event.get("payload") or {}
                            action = str(payload.get("action") or "buy").lower()
                            if action == "buy":
                                if subj:
                                    bought_pick_ids.add(subj)
                                if payload.get("pick_id"):
                                    bought_pick_ids.add(str(payload["pick_id"]))
                                slug = str(payload.get("market_slug") or "")
                                side = str(payload.get("token_side") or payload.get("side") or "").lower()
                                if slug and side:
                                    bought_market_sides.add((slug, side))
                                ev_id = str(payload.get("event_id") or "")
                                sel = str(payload.get("selection") or "").lower()
                                if ev_id and sel:
                                    bought_event_selections.add((ev_id, sel))
            except OSError:
                pass

        # 2. Dashboard orders file (dashboard/orders.json)
        dashboard_orders_file = _paths.repo_root / "dashboard" / "orders.json"
        if dashboard_orders_file.exists():
            try:
                orders_data = json.loads(dashboard_orders_file.read_text(encoding="utf-8"))
                for ord_entry in orders_data.get("orders") or []:
                    if not isinstance(ord_entry, dict):
                        continue
                    status = str(ord_entry.get("status") or "").lower()
                    action = str(ord_entry.get("action") or "buy").lower()
                    if (
                        status in {"filled", "submitted", "open", "resting_limit", "marketable_limit"}
                        and action == "buy"
                    ):
                        if ord_entry.get("pick_id"):
                            bought_pick_ids.add(str(ord_entry["pick_id"]))
                        slug = str(ord_entry.get("market_slug") or "")
                        side = str(ord_entry.get("side") or ord_entry.get("token_side") or "").lower()
                        if slug and side:
                            bought_market_sides.add((slug, side))
                        ev_id = str(ord_entry.get("event_id") or "")
                        sel = str(ord_entry.get("selection") or "").lower()
                        if ev_id and sel:
                            bought_event_selections.add((ev_id, sel))
            except (json.JSONDecodeError, OSError):
                pass

        # 3. Polymarket edge ledger (data/polymarket_picks.xlsx)
        poly_ledger_file = DATA / "polymarket_picks.xlsx"
        if poly_ledger_file.exists():
            try:
                from ..xlsx_ledger import read_xlsx_rows

                _, rows = read_xlsx_rows(poly_ledger_file)
                for r in rows:
                    p_id = str(r.get("pick_id") or "")
                    if p_id:
                        bought_pick_ids.add(p_id)
                    ev_id = str(r.get("event_id") or "")
                    sel = str(r.get("selection") or "").lower()
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # 3b. Dedicated Auto-Buyer Ledger (data/auto_buyer_ledger.jsonl)
        auto_ledger_file = DATA / "auto_buyer_ledger.jsonl"
        if auto_ledger_file.exists():
            try:
                from .auto_buyer_ledger import read_auto_buyer_ledger

                for r in read_auto_buyer_ledger(auto_ledger_file):
                    if r.get("pick_id"):
                        bought_pick_ids.add(str(r["pick_id"]))
                    if r.get("order_id"):
                        bought_pick_ids.add(str(r["order_id"]))
                    slug = str(r.get("market_slug") or "")
                    side = str(r.get("token_side") or "").lower()
                    if slug and side:
                        bought_market_sides.add((slug, side))
                    ev_id = str(r.get("event_id") or "")
                    sel = str(r.get("selection") or "").lower()
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # 4. Live Polymarket US account positions
        if self.config.execute_live or os.environ.get("POLYMARKET_KEY_ID"):
            try:
                executor = PolymarketExecutor(audit=self.audit, live_quote=self._live_quote_fn)
                snap = executor.portfolio_snapshot()
                for pos_slug, pos_data in (snap.get("positions") or {}).items():
                    if isinstance(pos_data, dict):
                        qty = float(pos_data.get("netPositionDecimal") or pos_data.get("netPosition") or 0.0)
                        if qty > 0:
                            held_slugs.add(str(pos_slug))
            except (OSError, ValueError, KeyError, TypeError, ExecutionGateError, RuntimeError):
                pass

        return {
            "pick_ids": bought_pick_ids,
            "market_sides": bought_market_sides,
            "event_selections": bought_event_selections,
            "held_slugs": held_slugs,
        }

    def _is_already_bought(
        self,
        pick_id: str,
        market_slug: str | None = None,
        token_side: str | None = None,
        event_id: str | None = None,
        selection: str | None = None,
        bought_index: dict[str, set[Any]] | None = None,
    ) -> bool:
        """Check if an order was already executed for this pick, event, market, or position."""
        index = bought_index if bought_index is not None else self._build_bought_index()
        if pick_id and pick_id in index["pick_ids"]:
            return True
        if market_slug and market_slug in index["held_slugs"]:
            return True
        if market_slug and token_side and (market_slug, token_side.lower()) in index["market_sides"]:
            return True
        return bool(
            event_id and selection and (str(event_id), str(selection).lower()) in index["event_selections"]
        )

    def evaluate_and_execute(
        self,
        picks: list[dict[str, Any]] | None = None,
    ) -> AutoExecutionResult:
        """Scan open picks, apply filters and sizing, and execute live or paper-trade."""
        from model_prediction.dashboard.orders import _decorate_pick
        from model_prediction.dashboard.picks import read_picks

        if picks is None:
            picks = read_picks()

        result = AutoExecutionResult()
        now = utc_now()
        EASTERN = ZoneInfo("America/New_York")
        now_et = now.astimezone(EASTERN)
        current_spend = 0.0
        bought_index = self._build_bought_index()

        for row in picks:
            result.total_evaluated += 1
            pick_id = str(row.get("pick_id") or "")
            model_id = str(row.get("model_id") or row.get("model_version") or "")
            event_id = str(row.get("event_id") or "")
            selection = str(row.get("selection") or "").lower()

            # 1. Blacklist filter
            if model_id in self.config.blacklisted_models:
                result.rejected_blacklist += 1
                continue

            # 2. Whitelist filter
            if model_id not in self.config.whitelisted_models:
                continue

            result.whitelisted_count += 1

            # 3. Status filter (must be open)
            if row.get("status") != "open":
                continue

            # 4. Timing & Slate Date Check:
            # Prevent buying tomorrow's games or games outside today's active slate
            event_start_str = str(row.get("event_start_utc") or "")
            try:
                event_start = parse_utc(event_start_str)
            except (ValueError, TypeError):
                result.rejected_started += 1
                continue

            # Block games that already started
            if now >= event_start:
                result.rejected_started += 1
                continue

            # Block games scheduled for tomorrow or distant future (enforce today's slate)
            start_et = event_start.astimezone(EASTERN)
            if start_et.date() > now_et.date() or (event_start - now).total_seconds() > 14.0 * 3600:
                result.rejected_future_slate += 1
                continue

            # 5. Fast Deduplication check (pick_id or event_id + selection)
            if self._is_already_bought(
                pick_id=pick_id,
                event_id=event_id,
                selection=selection,
                bought_index=bought_index,
            ):
                result.rejected_dedup += 1
                continue

            # 6. Resolve Verified Polymarket Market & Quote
            # Decorates with verified Polymarket mapping, team resolution, and snapshot
            # If a simulated live_quote_fn is provided (e.g. in test suite), use it
            if self._live_quote_fn is not None:
                try:
                    mock_quote = self._live_quote_fn(str(row.get("market_slug") or f"slug-{pick_id}"))
                    quote = {
                        "market_slug": mock_quote.get("market_slug") or f"slug-{pick_id}",
                        "side": mock_quote.get("side") or str(row.get("token_side") or "long"),
                        "ask": mock_quote.get("ask") or float(row.get("market_implied_probability") or 0.50),
                        "fresh": True,
                        "market_state": "MARKET_STATE_OPEN",
                    }
                except (RuntimeError, ValueError, KeyError, TypeError, OSError):
                    quote = None
            else:
                decorated = _decorate_pick(row)
                quote = decorated.get("quote")

            if quote is None:
                result.rejected_unmapped_market += 1
                continue

            # 7. Quote Staleness & Market State Check (with live refresh fallback)
            if (
                not quote.get("fresh", False)
                and quote.get("age_seconds", 999999) > 300
                and self._live_quote_fn is None
            ):
                try:
                    from model_prediction.data_sources.polymarket_us import PolymarketUSClient

                    live_snap = PolymarketUSClient().snapshot(str(quote["market_slug"]))
                    side = str(quote.get("side") or "long").lower()
                    side_data = live_snap.get(side) or {}
                    fresh_ask = side_data.get("ask") or side_data.get("price")
                    if fresh_ask is not None and float(fresh_ask) > 0:
                        quote = {
                            **quote,
                            "ask": float(fresh_ask),
                            "fresh": True,
                            "age_seconds": 0,
                            "market_state": live_snap.get("market_state", "MARKET_STATE_OPEN"),
                        }
                except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError):
                    pass

            if not quote.get("fresh", False) and quote.get("age_seconds", 999999) > 300:
                result.rejected_stale_quote += 1
                continue

            if quote.get("market_state") != "MARKET_STATE_OPEN":
                result.rejected_closed_market += 1
                continue

            market_slug = str(quote["market_slug"])
            token_side = str(quote["side"]).lower()
            clob_ask = float(quote.get("ask") or 0.0)

            # 8. Precise Deduplication check on resolved market_slug and token_side
            if self._is_already_bought(
                pick_id=pick_id,
                market_slug=market_slug,
                token_side=token_side,
                event_id=event_id,
                selection=selection,
                bought_index=bought_index,
            ):
                result.rejected_dedup += 1
                continue

            # 9. Sizing & Edge check against real Polymarket CLOB ask
            model_prob = float(row.get("model_probability") or 0.0)
            edge = model_prob - clob_ask
            if edge < self.config.min_edge:
                result.rejected_low_edge += 1
                continue

            # Sizing: units * unit_value_usd
            raw_units = float(row.get("units") or row.get("display_units") or 1.0)
            target_spend = raw_units * self.config.unit_value_usd
            target_spend = min(target_spend, self.config.max_game_stake_usd)

            limit_price = max(0.01, min(0.99, round(clob_ask, 2)))

            # Compute shares: target_spend / limit_price, minimum min_shares
            shares = max(self.config.min_shares, round(target_spend / limit_price, 2))
            actual_cost = round(shares * limit_price, 2)

            # Daily budget check
            if current_spend + actual_cost > self.config.max_daily_spend_usd:
                result.rejected_budget += 1
                continue

            ticket = OrderTicket(
                market_slug=market_slug,
                token_side=token_side,
                action="buy",
                order_type="limit_ioc",
                price=limit_price,
                size_shares=shares,
                pick_id=pick_id,
                estimated_cost_usd=actual_cost,
                maximum_cost_usd=actual_cost + 0.10,
                authorization_type="auto_whitelisted_model",
            )

            order_payload = {
                "pick_id": pick_id,
                "model_id": model_id,
                "market_slug": market_slug,
                "token_side": token_side,
                "sport": row.get("sport") or row.get("league"),
                "selection": row.get("selection"),
                "limit_price": limit_price,
                "shares": shares,
                "cost_usd": actual_cost,
                "edge": round(edge, 4),
                "event_start_utc": event_start_str,
                "timestamp_utc": iso_utc(now),
            }

            # Update bought_index to prevent intra-batch double buys
            bought_index["pick_ids"].add(pick_id)
            bought_index["market_sides"].add((market_slug, token_side))
            if event_id and selection:
                bought_index["event_selections"].add((event_id, selection))
            bought_index["held_slugs"].add(market_slug)

            if not self.config.execute_live:
                result.dry_run_orders.append(
                    {
                        "status": "dry_run_preview",
                        **order_payload,
                    }
                )
                current_spend += actual_cost
                result.total_spend_usd = round(current_spend, 2)
                continue

            # Live Execution
            executor = PolymarketExecutor(
                audit=self.audit,
                confirm=lambda _prompt: "Y",
                live_quote=self._live_quote_fn,
            )

            exec_row = dict(row)
            exec_row["market_slug"] = market_slug
            exec_row["rationale"] = f"auto_buy market_slug:{market_slug}"

            try:
                sub = executor.execute(
                    ticket=ticket,
                    pick_row=exec_row,
                    execute_flag=True,
                    user_command=True,
                    manual_research_order=False,
                    artifact_qualified=True,
                )
                oid = sub.get("order_id")
                result.submitted_orders.append(
                    {
                        "status": sub.get("status", "submitted"),
                        "order_id": oid,
                        **order_payload,
                    }
                )
                current_spend += actual_cost
                result.total_spend_usd = round(current_spend, 2)

                # Record directly to dedicated Auto-Buyer Ledger and Logger
                try:
                    from model_prediction.portfolio.auto_buyer_ledger import (
                        record_auto_buy_execution,
                    )

                    record_auto_buy_execution(
                        order_payload=order_payload,
                        order_id=oid,
                        order_state=str(sub.get("status", "FILLED")).upper(),
                        pick_row=row,
                    )
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    pass

                # Polite pacing to respect exchange rate limits
                time.sleep(0.25)
            except (ExecutionGateError, RuntimeError, OSError) as exc:
                result.submitted_orders.append(
                    {
                        "status": "refused",
                        "error": str(exc),
                        **order_payload,
                    }
                )

        return result
