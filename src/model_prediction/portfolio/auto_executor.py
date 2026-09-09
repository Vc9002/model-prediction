"""Automated Polymarket Execution Engine.

Executes forecast picks with strict safety gates:
1. Whitelist of empirical positive-EV models (Tennis, Soccer, WNBA ML, CS2/LoL Gated).
2. Explicit model and sport/market blocks, including all MLB moneylines.
3. Configurable unit value sizing (default 1U = $0.50 / 50 cents).
4. Point-in-time pregame verification (now < event_start_utc) and live quote freshness (< 5 min).
5. Hard daily spending limit and per-game exposure caps.
6. Single-order deduplication per pick_id against the append-only audit log.
7. Real-money live execution when enabled, with persistent dashboard ON/OFF control.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("model_prediction.auto_executor")

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
EASTERN = ZoneInfo("America/New_York")

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
    # soccer-poisson-dc-v1 removed 2026-09-07 -- see EXPLICIT_BLACKLIST_MODELS.
    "soccer-poisson-dc-v2",
    "soccer-total-v1",
    "wnba-elo-trend-lr-v4",
    "wnba-spread-margin-v2",
    "wnba-total-margin-v2",
    "cs2-tiered-elo-v6",
    "lol-tiered-elo-v6",
)

EXPLICIT_BLACKLIST_MODELS: tuple[str, ...] = (
    "measured-edge-totals-v3",  # MLB totals (degraded, negative CLV vs market close)
    "measured-edge-margin-v3",  # MLB run line (negative EV)
    "wnba-spread-margin-v1",  # Legacy WNBA spread (severe miscalibration)
    "wnba-total-margin-v1",  # Legacy WNBA totals (uncalibrated static SD)
    "mlb-nrfi-v1",  # Legacy MLB NRFI
    "cfb-total-v1",  # CFB total (uncalibrated)
    # All MLB pulled 2026-09-02: mlb-elo-trend-lr-v8 shipped via operator
    # override despite qualified:false and a documented validation-Brier
    # regression vs its own incumbent (holdout number that promoted it was
    # exactly the "peeking at holdout" pattern docs/AGENTS.md warns against).
    # Live Auto-Buyer record confirmed it: 43% win rate / -$1.00 vs a claimed
    # 60.8% holdout hit rate. v7 also never cleared the qualification gate.
    # mlb-structural-v10-frozen and mlb-nrfi-v2 pulled alongside it pending
    # the same qualification review, not because they were shown bad live.
    "mlb-elo-trend-lr-v8",
    "mlb-structural-v10-frozen",
    "mlb-nrfi-v2",
    # Pulled 2026-09-07: same miscalibration signature as the MLB pull above.
    # Live Auto-Buyer record: 19 settled bets, 6W-13L (31.6% actual) against
    # a claimed average 60.3% win probability -- a 29-point overconfidence
    # gap. Worse on its own highest-conviction bets (>=65% claimed): 28.6%
    # actual (2W-5L) vs 78.5% claimed, i.e. confidence is inversely
    # informative on this model's strongest calls. Realized P&L -$49.23,
    # the sole reason the whole Auto-Buyer book looked roughly breakeven
    # instead of solidly profitable (ex-soccer P&L: +$48.30). The other two
    # whitelisted soccer models (soccer-poisson-dc-v2, soccer-total-v1)
    # have placed zero live bets, so this is not a soccer-as-a-sport
    # problem -- it's this one model.
    "soccer-poisson-dc-v1",
)

AUTO_BUYER_STATE_FILE = DATA / "auto_buyer_state.json"
# Paper-mode picks are recorded through the same record/read/settle functions
# as live trades, just rooted at this separate directory -- never mixed into
# the real-money ledger, so paper and live P&L can never contaminate each other.
PAPER_AUTO_BUYER_DATA_ROOT = DATA / "paper_auto_buyer"
AUTO_BUYER_MODES = frozenset({"paper", "live"})
DEFAULT_AUTO_BUYER_MODE = "paper"
EXECUTABLE_AUTO_BUYER_MARKET_TYPES = frozenset({"moneyline", "spread", "total", "nrfi"})
ESPORTS_LEAGUES = frozenset({"cs2", "lol", "dota2", "valorant", "r6", "cod", "ow", "rl"})
DISABLED_AUTO_BUYER_SPORT_MARKETS = frozenset({("mlb", "moneyline")})
DEFAULT_AUTO_BUYER_UNIT_VALUE_USD = 0.50
DEFAULT_AUTO_BUYER_MAX_EDGE = 0.20
DEFAULT_MAX_DAILY_SPEND_UNITS = 50.0
DEFAULT_MAX_GAME_STAKE_UNITS = 5.0


@dataclass(frozen=True)
class AutoExecutionConfig:
    unit_value_usd: float = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD
    min_edge: float = 0.035  # Minimum +3.5% edge over market ask
    max_edge: float = DEFAULT_AUTO_BUYER_MAX_EDGE  # Maximum +20.0% edge (outlier/anomaly sanity ceiling)
    max_daily_spend_usd: float = 25.0  # Daily budget cap
    max_game_stake_usd: float = 2.50  # Max dollars per game/pick
    min_shares: float = 1.0  # Polymarket minimum share size
    whitelisted_models: tuple[str, ...] = DEFAULT_WHITELIST_MODELS
    blacklisted_models: tuple[str, ...] = EXPLICIT_BLACKLIST_MODELS
    disabled_sport_markets: tuple[tuple[str, str], ...] = ()
    execute_live: bool = False  # Dry-run by default unless enabled/requested
    mode: str = DEFAULT_AUTO_BUYER_MODE  # "paper" (simulated fills, recorded) or "live" (real orders)
    max_event_lookahead_hours: float = 24.0  # How far out an event may start and still be buyable
    max_daily_loss_usd: float | None = None  # Drawdown circuit breaker; None = disabled
    edge_scaled_sizing_enabled: bool = False  # Off by default: scales stake by edge within the per-game cap
    edge_scaling_min_fraction: float = (
        0.5  # Stake fraction of the per-game cap used at min_edge, when enabled
    )


@dataclass
class AutoExecutionResult:
    total_evaluated: int = 0
    rejected_not_open: int = 0
    whitelisted_count: int = 0
    rejected_not_whitelisted: int = 0
    rejected_blacklist: int = 0
    rejected_future_slate: int = 0
    rejected_started: int = 0
    rejected_stale_quote: int = 0
    rejected_unmapped_market: int = 0
    rejected_unsupported_market: int = 0
    rejected_disabled_sport_market: int = 0
    rejected_closed_market: int = 0
    rejected_low_edge: int = 0
    rejected_unrealistic_edge: int = 0
    rejected_budget: int = 0
    rejected_dedup: int = 0
    rejected_min_topup: int = 0
    rejected_daily_loss_breaker: int = 0
    submitted_orders: list[dict[str, Any]] = field(default_factory=list)
    dry_run_orders: list[dict[str, Any]] = field(default_factory=list)
    total_spend_usd: float = 0.0
    snapshot_captures: list[dict[str, Any]] = field(default_factory=list)


def _auto_buyer_sport_market(row: dict[str, Any]) -> tuple[str, str]:
    """Return a normalized sport/market pair for categorical execution gates."""
    model_id = str(row.get("model_id") or row.get("model_version") or "").lower()
    sport = str(row.get("league") or row.get("sport") or "").strip().lower()
    if not sport and model_id.startswith("mlb-"):
        sport = "mlb"

    market_type = str(row.get("market_type") or "").strip().lower().replace("_", "")
    if market_type == "ml":
        market_type = "moneyline"
    return sport, market_type


def _is_disabled_auto_buyer_sport_market(
    row: dict[str, Any],
    disabled_markets: tuple[tuple[str, str], ...] | frozenset[tuple[str, str]] = (),
) -> bool:
    target = _auto_buyer_sport_market(row)
    if target in DISABLED_AUTO_BUYER_SPORT_MARKETS:
        return True
    return bool(disabled_markets and target in disabled_markets)


def _capture_missing_active_snapshot_slates(
    picks: list[dict[str, Any]],
    *,
    config: AutoExecutionConfig,
    now: datetime,
    data_root: Path | str = DATA,
    client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Capture absent sport/date snapshot files needed by the active 24-hour slate.

    The daily forecast captures its requested calendar date, while esports forecasts
    can legitimately include games after midnight Eastern. A live Auto-Buyer cycle
    must discover those next-day contracts before calling the local fail-closed
    mapper. Existing files are never rewritten or broadly refreshed here.
    """
    if not config.execute_live:
        return []

    root = Path(data_root)
    eastern = ZoneInfo("America/New_York")
    missing: set[tuple[str, str]] = set()
    for row in picks:
        model_id = str(row.get("model_id") or row.get("model_version") or "")
        if model_id in config.blacklisted_models or model_id not in config.whitelisted_models:
            continue
        if _is_disabled_auto_buyer_sport_market(row, config.disabled_sport_markets):
            continue
        if row.get("status") != "open":
            continue
        market_type = str(row.get("market_type") or "").lower()
        if market_type and market_type not in EXECUTABLE_AUTO_BUYER_MARKET_TYPES:
            continue
        try:
            event_start = parse_utc(str(row.get("event_start_utc") or ""))
        except (TypeError, ValueError):
            continue
        seconds_until_start = (event_start - now).total_seconds()
        if seconds_until_start <= 0 or seconds_until_start > 24 * 3600:
            continue
        raw_sport = str(row.get("league") or row.get("sport") or "").lower()
        sport = "esports" if raw_sport in ESPORTS_LEAGUES else raw_sport
        if not sport:
            continue
        game_date = event_start.astimezone(eastern).date().isoformat()
        snapshot_path = root / "odds" / sport / game_date / "polymarket_snapshots.jsonl"
        if not snapshot_path.exists() or snapshot_path.stat().st_size == 0:
            missing.add((sport, game_date))

    if not missing:
        return []

    from model_prediction.data_sources.polymarket_us import (
        PolymarketUSClient,
        capture_slate_snapshots,
    )

    market_client = client or PolymarketUSClient()
    capture = capture_fn or capture_slate_snapshots
    results: list[dict[str, Any]] = []
    for sport, game_date in sorted(missing):
        try:
            slate = market_client.sport_slate(
                sport,
                date.fromisoformat(game_date),
                "America/New_York",
            )
            capture_result = capture(market_client, slate.events, root, game_date)
            results.append(
                {
                    "sport": sport,
                    "game_date": game_date,
                    "status": capture_result.get("status"),
                    "captured": int(capture_result.get("captured") or 0),
                    "league_errors": dict(slate.errors),
                }
            )
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as error:
            results.append(
                {
                    "sport": sport,
                    "game_date": game_date,
                    "status": "error",
                    "captured": 0,
                    "error": str(error)[:200],
                }
            )
    return results


AUTO_BUYER_CYCLE_HISTORY_PATH = DATA / "logs" / "auto_buyer_cycle_history.jsonl"


def _append_auto_buyer_cycle_history(
    run_summary: dict[str, Any],
    history_path: Path | str = AUTO_BUYER_CYCLE_HISTORY_PATH,
) -> None:
    """Append one trimmed row per cycle so funnel/rejection trends are queryable directly.

    Previously the only way to see how a cycle's rejection breakdown compared to prior
    cycles was to parse the raw operational log and ledger by hand. ``orders`` (the full
    per-order payload) is dropped here -- the ledger jsonl is already the durable record
    of what was bought -- so this file stays a lightweight, append-only funnel history.
    """
    row = {k: v for k, v in run_summary.items() if k not in ("orders", "snapshot_captures", "reconciliation")}
    path = Path(history_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        logger.warning("Failed to append auto_buyer_cycle_history.jsonl: %s", exc)


def _today_auto_buyer_totals(data_root: Path | str = DATA) -> tuple[float, float]:
    """Sum today's (ET calendar day) already-executed spend and realized P&L.

    Computed fresh from the ledger rather than reset per call, so the daily-spend
    cap and the drawdown circuit breaker both apply across a full calendar day's
    worth of cycles -- not just whichever cycle happens to be running right now.
    """
    from model_prediction.portfolio.auto_buyer_ledger import read_auto_buyer_ledger

    today = utc_now().astimezone(EASTERN).date()
    spend = 0.0
    realized_pnl = 0.0
    j_path = Path(data_root) / "auto_buyer_ledger.jsonl"
    for row in read_auto_buyer_ledger(jsonl_path=j_path):
        executed_raw = row.get("executed_at_utc")
        if executed_raw:
            try:
                if parse_utc(str(executed_raw)).astimezone(EASTERN).date() == today:
                    spend += float(row.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass

        settled_raw = row.get("settled_at_utc")
        result = str(row.get("result") or "").lower()
        if settled_raw and result in ("win", "loss", "push"):
            try:
                if parse_utc(str(settled_raw)).astimezone(EASTERN).date() == today:
                    realized_pnl += float(row.get("pnl_usd") or 0.0)
            except (TypeError, ValueError):
                pass

    return round(spend, 2), round(realized_pnl, 2)


def _parse_disabled_sport_markets(state: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Parse the persisted ``disabled_sport_markets`` state entry into sport/market pairs.

    Single source of truth for both production config-builders (``run_auto_buyer_cycle``
    and ``AutoPolymarketBuyer.__init__``'s fallback) so a categorical block like the
    soccer/MLB disable can't silently apply on one path and not the other.
    """
    disabled_parsed: list[tuple[str, str]] = []
    for entry in state.get("disabled_sport_markets") or []:
        if isinstance(entry, str) and ":" in entry:
            sp, mk = entry.split(":", 1)
            disabled_parsed.append((sp.strip().lower(), mk.strip().lower()))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            disabled_parsed.append((str(entry[0]).strip().lower(), str(entry[1]).strip().lower()))
    return tuple(disabled_parsed)


def load_auto_buyer_state() -> dict[str, Any]:
    """Load persistent auto-buyer configuration and runtime toggle state."""
    default_state = {
        "enabled": False,
        "mode": DEFAULT_AUTO_BUYER_MODE,
        "unit_value_usd": DEFAULT_AUTO_BUYER_UNIT_VALUE_USD,
        "min_edge": 0.035,
        "max_edge": DEFAULT_AUTO_BUYER_MAX_EDGE,
        "max_daily_spend_units": DEFAULT_MAX_DAILY_SPEND_UNITS,
        "max_game_stake_units": DEFAULT_MAX_GAME_STAKE_UNITS,
        "whitelist_models": list(DEFAULT_WHITELIST_MODELS),
        "blacklist_models": list(EXPLICIT_BLACKLIST_MODELS),
        "disabled_sport_markets": [
            f"{sport}:{market}" for sport, market in sorted(DISABLED_AUTO_BUYER_SPORT_MARKETS)
        ],
        "last_run": None,
        "last_daily_date": None,
    }
    data: dict[str, Any] = {}
    if AUTO_BUYER_STATE_FILE.exists():
        try:
            data = json.loads(AUTO_BUYER_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    state = {**default_state, **data}
    normalized_mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
    state["mode"] = normalized_mode if normalized_mode in AUTO_BUYER_MODES else DEFAULT_AUTO_BUYER_MODE
    try:
        unit_value = float(state.get("unit_value_usd") or DEFAULT_AUTO_BUYER_UNIT_VALUE_USD)
    except (TypeError, ValueError):
        unit_value = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD
    if not math.isfinite(unit_value) or unit_value <= 0:
        unit_value = DEFAULT_AUTO_BUYER_UNIT_VALUE_USD

    try:
        max_edge = float(state.get("max_edge") or DEFAULT_AUTO_BUYER_MAX_EDGE)
    except (TypeError, ValueError):
        max_edge = DEFAULT_AUTO_BUYER_MAX_EDGE
    if not math.isfinite(max_edge) or max_edge <= 0:
        max_edge = DEFAULT_AUTO_BUYER_MAX_EDGE
    state["max_edge"] = max_edge

    def _unit_limit(unit_key: str, legacy_usd_key: str, default_units: float) -> float:
        raw_units = data.get(unit_key)
        if raw_units is None and data.get(legacy_usd_key) is not None:
            try:
                raw_units = float(data[legacy_usd_key]) / unit_value
            except (TypeError, ValueError, ZeroDivisionError):
                raw_units = None
        try:
            parsed = float(raw_units if raw_units is not None else default_units)
        except (TypeError, ValueError):
            parsed = default_units
        return parsed if math.isfinite(parsed) and parsed > 0 else default_units

    daily_units = _unit_limit(
        "max_daily_spend_units",
        "max_daily_spend_usd",
        DEFAULT_MAX_DAILY_SPEND_UNITS,
    )
    game_units = _unit_limit(
        "max_game_stake_units",
        "max_game_stake_usd",
        DEFAULT_MAX_GAME_STAKE_UNITS,
    )
    state["unit_value_usd"] = unit_value
    state["max_daily_spend_units"] = daily_units
    state["max_game_stake_units"] = game_units
    state["max_daily_spend_usd"] = round(daily_units * unit_value, 2)
    state["max_game_stake_usd"] = round(game_units * unit_value, 2)
    return state


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


def set_auto_buyer_mode(raw_mode: Any) -> dict[str, Any]:
    """Switch the Auto-Buyer between "paper" (simulated, recorded fills) and
    "live" (real Polymarket orders). Never places or cancels a real order by
    itself -- it only changes which path future cycles take."""
    mode = str(raw_mode or "").strip().lower()
    if mode not in AUTO_BUYER_MODES:
        raise ValueError(f"Auto-Buyer mode must be one of {sorted(AUTO_BUYER_MODES)}")

    state = load_auto_buyer_state()
    previous = state.get("mode", DEFAULT_AUTO_BUYER_MODE)
    state["mode"] = mode
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_mode_updated",
            "auto_buyer.mode",
            {"previous_mode": previous, "mode": mode, "source": "dashboard"},
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return state


def set_auto_buyer_unit_value(raw_value: Any) -> dict[str, Any]:
    """Persist the dollar value used for future Auto-Buyer order sizing."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError("Auto-Buyer 1U must be a dollar amount") from error
    if not math.isfinite(value) or not 0.01 <= value <= 100_000:
        raise ValueError("Auto-Buyer 1U must be between $0.01 and $100,000.00")

    state = load_auto_buyer_state()
    previous = float(state.get("unit_value_usd") or DEFAULT_AUTO_BUYER_UNIT_VALUE_USD)
    state["unit_value_usd"] = round(value, 2)
    daily_units = float(state.get("max_daily_spend_units") or DEFAULT_MAX_DAILY_SPEND_UNITS)
    game_units = float(state.get("max_game_stake_units") or DEFAULT_MAX_GAME_STAKE_UNITS)
    state["max_daily_spend_usd"] = round(daily_units * state["unit_value_usd"], 2)
    state["max_game_stake_usd"] = round(game_units * state["unit_value_usd"], 2)
    state["updated_at_utc"] = iso_utc(utc_now())
    save_auto_buyer_state(state)
    try:
        AuditLog(DATA / "audit.jsonl").append(
            "auto_buyer_unit_value_updated",
            "auto_buyer.unit_value_usd",
            {
                "previous_usd": previous,
                "unit_value_usd": state["unit_value_usd"],
                "max_game_stake_units": game_units,
                "max_game_stake_usd": state["max_game_stake_usd"],
                "max_daily_spend_units": daily_units,
                "max_daily_spend_usd": state["max_daily_spend_usd"],
                "source": "dashboard",
            },
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return {
        "status": "ok",
        "previous_unit_value_usd": previous,
        "unit_value_usd": state["unit_value_usd"],
        "max_game_stake_units": game_units,
        "max_game_stake_usd": state["max_game_stake_usd"],
        "max_daily_spend_units": daily_units,
        "max_daily_spend_usd": state["max_daily_spend_usd"],
        "note": "Applies only to future Auto-Buyer orders; unit-based game/day caps scale with it and historical rows retain their recorded unit value.",
    }


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
    mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
    if mode not in AUTO_BUYER_MODES:
        mode = DEFAULT_AUTO_BUYER_MODE
    state["mode"] = mode
    cycle_enabled = bool(state.get("enabled", False))
    # Explicit execution can request a one-off live cycle, but can never
    # override PAPER mode. Changing the saved mode is a separate action.
    should_execute = mode == "live" and (
        cycle_enabled if execute_override is None else bool(execute_override)
    )

    # Reconcile resting orders, update fills, and cancel stale/moved orders before evaluating new picks
    reconciliation_summary: dict[str, Any] = {}
    if should_execute:
        try:
            from .auto_buyer_ledger import (
                reconcile_pending_auto_buyer_fallbacks,
                settle_auto_buyer_ledger,
            )

            reconciliation_summary = reconcile_pending_auto_buyer_fallbacks()
            settle_auto_buyer_ledger()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError) as exc:
            logger.warning("Pre-cycle resting order reconciliation or settlement failed: %s", exc)
    elif cycle_enabled and mode == "paper":
        try:
            from .auto_buyer_ledger import settle_auto_buyer_ledger

            settle_auto_buyer_ledger(data_root=PAPER_AUTO_BUYER_DATA_ROOT)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError) as exc:
            logger.warning("Pre-cycle paper-ledger settlement failed: %s", exc)

    whitelisted = state.get("whitelist_models")
    blacklisted = state.get("blacklist_models")

    config = AutoExecutionConfig(
        unit_value_usd=float(state.get("unit_value_usd", 0.50)),
        min_edge=float(state.get("min_edge", 0.035)),
        max_edge=float(state.get("max_edge", DEFAULT_AUTO_BUYER_MAX_EDGE)),
        max_daily_spend_usd=float(state.get("max_daily_spend_usd", 250.0)),
        max_game_stake_usd=float(state.get("max_game_stake_usd", 25.0)),
        max_event_lookahead_hours=float(state.get("max_event_lookahead_hours", 6.0)),
        execute_live=should_execute,
        mode=mode,
        whitelisted_models=tuple(whitelisted) if whitelisted is not None else DEFAULT_WHITELIST_MODELS,
        blacklisted_models=tuple(blacklisted) if blacklisted is not None else EXPLICIT_BLACKLIST_MODELS,
        disabled_sport_markets=_parse_disabled_sport_markets(state),
        max_daily_loss_usd=(
            float(state["max_daily_loss_usd"])
            if state.get("max_daily_loss_usd") is not None
            else round(float(state.get("max_daily_spend_usd", 250.0)) * 0.5, 2)
        ),
        edge_scaled_sizing_enabled=bool(state.get("edge_scaled_sizing_enabled", False)),
        edge_scaling_min_fraction=float(state.get("edge_scaling_min_fraction", 0.5)),
    )

    buyer = AutoPolymarketBuyer(config=config)
    res = buyer.evaluate_and_execute()

    if not should_execute and cycle_enabled and mode == "paper":
        # Paper fills are recorded as immediately, fully filled at the requested
        # limit price -- there's no real order book to rest on, so "what would
        # have happened" is the simulated fact here. Persisted at this layer
        # (not inside AutoPolymarketBuyer) so direct/unit-test construction of
        # the buyer never has a filesystem side effect.
        from .auto_buyer_ledger import record_auto_buy_execution

        PAPER_AUTO_BUYER_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        for preview in res.dry_run_orders:
            pick_id = str(preview.get("pick_id") or "")
            paper_order_id = f"PAPER-{pick_id}" if pick_id else f"PAPER-{preview.get('market_slug', '')}"
            paper_payload = {
                **preview,
                "order_id": paper_order_id,
                "order_ids": [paper_order_id],
                "fill_known": True,
            }
            try:
                record_auto_buy_execution(
                    paper_payload,
                    order_id=paper_order_id,
                    order_state="ORDER_STATE_FILLED",
                    pick_row=paper_payload,
                    jsonl_path=PAPER_AUTO_BUYER_DATA_ROOT / "auto_buyer_ledger.jsonl",
                    xlsx_path=PAPER_AUTO_BUYER_DATA_ROOT / "auto_buyer_picks.xlsx",
                )
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                logger.warning("Failed to record paper trade for %s: %s", pick_id, exc)

    if should_execute:
        run_mode_label = "LIVE_EXECUTION"
    elif cycle_enabled and mode == "paper":
        run_mode_label = "PAPER_TRADING"
    else:
        run_mode_label = "DISABLED_OR_DRY_RUN"

    run_summary = {
        "executed_at_utc": iso_utc(utc_now()),
        "forecast_date": forecast_date,
        "mode": run_mode_label,
        "auto_buyer_mode": mode,
        "total_evaluated": res.total_evaluated,
        "rejected_not_open": res.rejected_not_open,
        "whitelisted_count": res.whitelisted_count,
        "rejected_not_whitelisted": res.rejected_not_whitelisted,
        "rejected_blacklist": res.rejected_blacklist,
        "rejected_future_slate": res.rejected_future_slate,
        "rejected_started": res.rejected_started,
        "rejected_stale_quote": res.rejected_stale_quote,
        "rejected_unmapped_market": res.rejected_unmapped_market,
        "rejected_unsupported_market": res.rejected_unsupported_market,
        "rejected_disabled_sport_market": res.rejected_disabled_sport_market,
        "rejected_closed_market": res.rejected_closed_market,
        "rejected_low_edge": res.rejected_low_edge,
        "rejected_unrealistic_edge": res.rejected_unrealistic_edge,
        "rejected_budget": res.rejected_budget,
        "rejected_dedup": res.rejected_dedup,
        "rejected_min_topup": res.rejected_min_topup,
        "rejected_daily_loss_breaker": res.rejected_daily_loss_breaker,
        "total_spend_usd": res.total_spend_usd,
        "orders_count": len(res.submitted_orders) if should_execute else len(res.dry_run_orders),
        "orders": res.submitted_orders if should_execute else res.dry_run_orders,
        "snapshot_captures": res.snapshot_captures,
        "reconciliation": reconciliation_summary,
    }

    _append_auto_buyer_cycle_history(run_summary)

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
        data_root: Path | str | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            state = load_auto_buyer_state()
            whitelisted = state.get("whitelist_models")
            blacklisted = state.get("blacklist_models")
            state_mode = str(state.get("mode") or DEFAULT_AUTO_BUYER_MODE).lower()
            if state_mode not in AUTO_BUYER_MODES:
                state_mode = DEFAULT_AUTO_BUYER_MODE
            state_should_execute = bool(state.get("enabled", False)) and state_mode == "live"
            self.config = AutoExecutionConfig(
                unit_value_usd=float(state.get("unit_value_usd") or DEFAULT_AUTO_BUYER_UNIT_VALUE_USD),
                min_edge=float(state.get("min_edge") or 0.035),
                max_edge=float(state.get("max_edge") or DEFAULT_AUTO_BUYER_MAX_EDGE),
                max_daily_spend_usd=float(state.get("max_daily_spend_usd") or 250.0),
                max_game_stake_usd=float(state.get("max_game_stake_usd") or 25.0),
                max_event_lookahead_hours=float(state.get("max_event_lookahead_hours") or 6.0),
                execute_live=state_should_execute,
                mode=state_mode,
                whitelisted_models=tuple(whitelisted)
                if whitelisted is not None
                else DEFAULT_WHITELIST_MODELS,
                blacklisted_models=tuple(blacklisted)
                if blacklisted is not None
                else EXPLICIT_BLACKLIST_MODELS,
                disabled_sport_markets=_parse_disabled_sport_markets(state),
                max_daily_loss_usd=(
                    float(state["max_daily_loss_usd"])
                    if state.get("max_daily_loss_usd") is not None
                    else round(float(state.get("max_daily_spend_usd") or 250.0) * 0.5, 2)
                ),
                edge_scaled_sizing_enabled=bool(state.get("edge_scaled_sizing_enabled", False)),
                edge_scaling_min_fraction=float(state.get("edge_scaling_min_fraction", 0.5)),
            )
        self.data_root = Path(data_root) if data_root else DATA
        self.paper_data_root = self.data_root / "paper_auto_buyer"
        self.audit = audit or AuditLog(self.data_root / "audit.jsonl")
        self._live_quote_fn = live_quote_fn

    def _build_bought_index(self) -> dict[str, Any]:
        """Index all executed, submitted, and held positions to prevent double-buying."""
        bought_pick_ids: set[str] = set()
        bought_market_sides: set[tuple[str, str]] = set()
        bought_event_selections: set[tuple[str, str]] = set()
        held_slugs: set[str] = set()

        partially_filled: dict[str, dict[str, Any]] = {}
        partially_filled_pick_ids: set[str] = set()
        partially_filled_market_sides: set[tuple[str, str]] = set()
        partially_filled_event_selections: set[tuple[str, str]] = set()
        partially_filled_slugs: set[str] = set()

        # 1. Audit logs (self.audit, data_root / audit.jsonl, data_root / audit_log.jsonl)
        audit_paths = [self.audit.path, self.data_root / "audit.jsonl", self.data_root / "audit_log.jsonl"]
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
        poly_ledger_file = self.data_root / "polymarket_picks.xlsx"
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

        # 3b. Dedicated Auto-Buyer Ledger, live and paper (both must be indexed
        # so paper mode never re-buys a pick it already recorded a paper fill
        # for, and switching modes mid-day can't double-buy the same pick).
        auto_ledger_files = [
            self.data_root / "auto_buyer_ledger.jsonl",
            self.paper_data_root / "auto_buyer_ledger.jsonl",
        ]
        for auto_ledger_file in auto_ledger_files:
            if not auto_ledger_file.exists():
                continue
            try:
                from .auto_buyer_ledger import read_auto_buyer_ledger

                for r in read_auto_buyer_ledger(auto_ledger_file):
                    status = str(r.get("status") or "").lower()
                    order_st = str(r.get("order_state") or "").upper()
                    result_st = str(r.get("result") or "").lower()
                    shares_cnt = float(r.get("shares") or 0.0)
                    if (
                        status in {"expired", "cancelled", "rejected"}
                        or (
                            order_st
                            in {"ORDER_STATE_CANCELED", "ORDER_STATE_EXPIRED", "ORDER_STATE_REJECTED"}
                            and shares_cnt == 0
                        )
                        or (result_st == "void" and shares_cnt == 0)
                    ):
                        continue

                    pid = str(r.get("pick_id") or "")
                    slug = str(r.get("market_slug") or "")
                    side = str(r.get("token_side") or "").lower()
                    ev_id = str(r.get("event_id") or "")
                    sel = str(r.get("selection") or "").lower()
                    is_reconciled = bool(r.get("fallback_reconciled", True))

                    # Detect partially filled orders whose resting fallbacks are not active
                    if (
                        status == "open"
                        and order_st == "ORDER_STATE_PARTIALLY_FILLED"
                        and is_reconciled
                        and shares_cnt > 0
                    ):
                        filled_cost = float(r.get("cost_usd") or 0.0)
                        p_info = {
                            "pick_id": pid,
                            "market_slug": slug,
                            "token_side": side,
                            "event_id": ev_id,
                            "selection": sel,
                            "filled_cost_usd": filled_cost,
                            "filled_shares": shares_cnt,
                            "model_units": float(r.get("model_units") or r.get("units") or 1.0),
                            "unit_value_usd": float(r.get("unit_value_usd") or self.config.unit_value_usd),
                            "existing_order_ids": list(r.get("order_ids") or [r.get("order_id")]),
                            "entry_price": float(r.get("entry_price") or 0.0),
                            "ledger_row": r,
                        }
                        if pid:
                            partially_filled[pid] = p_info
                            partially_filled_pick_ids.add(pid)
                        if slug and side:
                            partially_filled_market_sides.add((slug, side))
                        if ev_id and sel:
                            partially_filled_event_selections.add((ev_id, sel))
                        if slug:
                            partially_filled_slugs.add(slug)
                        continue

                    if pid:
                        bought_pick_ids.add(pid)
                    if r.get("order_id"):
                        bought_pick_ids.add(str(r["order_id"]))
                    if slug and side:
                        bought_market_sides.add((slug, side))
                    if ev_id and sel:
                        bought_event_selections.add((ev_id, sel))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # 4. Live Polymarket US account positions
        if self.config.execute_live and os.environ.get("POLYMARKET_KEY_ID") and self._live_quote_fn is None:
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

        # Exclude partially filled positions from hard lockouts so they remain eligible for remainder top-up
        bought_pick_ids.difference_update(partially_filled_pick_ids)
        bought_market_sides.difference_update(partially_filled_market_sides)
        bought_event_selections.difference_update(partially_filled_event_selections)
        held_slugs.difference_update(partially_filled_slugs)

        return {
            "pick_ids": bought_pick_ids,
            "market_sides": bought_market_sides,
            "event_selections": bought_event_selections,
            "held_slugs": held_slugs,
            "partially_filled": partially_filled,
            "partially_filled_market_sides": partially_filled_market_sides,
            "partially_filled_event_selections": partially_filled_event_selections,
            "partially_filled_slugs": partially_filled_slugs,
        }

    def _get_partial_fill_info(
        self,
        pick_id: str,
        market_slug: str | None = None,
        token_side: str | None = None,
        event_id: str | None = None,
        selection: str | None = None,
        bought_index: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Lookup existing partial fill metadata for an open pick to size remainder."""
        index = bought_index if bought_index is not None else self._build_bought_index()
        pf_map: dict[str, dict[str, Any]] = index.get("partially_filled") or {}
        if pick_id and pick_id in pf_map:
            return pf_map[pick_id]
        if market_slug and token_side:
            target_slug = str(market_slug)
            target_side = str(token_side).lower()
            for info in pf_map.values():
                if (
                    info.get("market_slug") == target_slug
                    and str(info.get("token_side") or "").lower() == target_side
                ):
                    return info
        if event_id and selection:
            target_ev = str(event_id)
            target_sel = str(selection).lower()
            for info in pf_map.values():
                if (
                    str(info.get("event_id") or "") == target_ev
                    and str(info.get("selection") or "").lower() == target_sel
                ):
                    return info
        return None

    def _is_already_bought(
        self,
        pick_id: str,
        market_slug: str | None = None,
        token_side: str | None = None,
        event_id: str | None = None,
        selection: str | None = None,
        bought_index: dict[str, Any] | None = None,
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

        if picks is None:
            from model_prediction.dashboard.picks import _parse_research_picks, read_flat_picks, read_picks

            main_picks = read_picks()
            flat_picks = read_flat_picks()
            gated_picks = _parse_research_picks(gated=True)

            seen_pick_keys: set[Any] = set()
            combined_picks: list[dict[str, Any]] = []

            for p in main_picks + gated_picks + flat_picks:
                p_id = str(p.get("pick_id") or "")
                ev_id = str(p.get("event_id") or f"{p.get('away_team')}@{p.get('home_team')}")
                sel = str(p.get("selection") or "").lower()
                m_ver = str(p.get("model_id") or p.get("model_version") or "")

                key = (ev_id, sel, m_ver) if (ev_id and sel) else p_id
                if key in seen_pick_keys:
                    continue
                seen_pick_keys.add(key)
                combined_picks.append(p)

            picks = combined_picks

        now = utc_now()
        result = AutoExecutionResult()

        totals_root = (
            self.paper_data_root
            if self.config.mode == "paper" and not self.config.execute_live
            else self.data_root
        )
        today_spend_usd, today_realized_pnl_usd = _today_auto_buyer_totals(totals_root)

        # Drawdown circuit breaker: once today's realized loss crosses the configured
        # threshold, stop considering new buys for the rest of the calendar day.
        # Reconciliation/settlement of already-open positions happens separately in
        # run_auto_buyer_cycle, before this method is called, so it is unaffected.
        if self.config.max_daily_loss_usd is not None and today_realized_pnl_usd <= -abs(
            self.config.max_daily_loss_usd
        ):
            result.total_evaluated = len(picks)
            result.rejected_daily_loss_breaker = len(picks)
            return result

        if self._live_quote_fn is None:
            result.snapshot_captures = _capture_missing_active_snapshot_slates(
                picks,
                config=self.config,
                now=now,
            )
        # Seeded from today's already-executed spend so the "daily" cap enforces a
        # real per-calendar-day ceiling across every cycle, not just this one.
        current_spend = today_spend_usd
        bought_index = self._build_bought_index()

        for row in picks:
            result.total_evaluated += 1
            pick_id = str(row.get("pick_id") or "")
            model_id = str(row.get("model_id") or row.get("model_version") or "")
            event_id = str(row.get("event_id") or "")
            selection = str(row.get("selection") or "").lower()

            # 1. Status filter (must be open). Checked first, ahead of every other
            # gate: the pick-source files (main/flat/gated/research) are append-only
            # and accumulate settled/closed history alongside live picks, so most
            # rows on any given cycle are already-resolved noise rather than genuine
            # candidates. Filtering that out first keeps every other counter below
            # (blacklist, whitelist, disabled-sport-market, etc.) reflecting the real
            # live-candidate funnel instead of being diluted by historical rows.
            if row.get("status") != "open":
                result.rejected_not_open += 1
                continue

            # 2. Blacklist filter
            if model_id in self.config.blacklisted_models:
                result.rejected_blacklist += 1
                continue

            # 3. Whitelist filter
            if model_id not in self.config.whitelisted_models:
                result.rejected_not_whitelisted += 1
                continue

            result.whitelisted_count += 1

            # Operator-level categorical block. This deliberately takes precedence
            # over persisted model whitelists so renamed or replacement MLB models
            # cannot restore moneyline execution without a reviewed code change.
            if _is_disabled_auto_buyer_sport_market(row, self.config.disabled_sport_markets):
                result.rejected_disabled_sport_market += 1
                continue

            # Only markets with a proven contract representation may reach mapping.
            # In particular, an NRFI/YRFI model must never be attached to a full-game
            # total merely to increase volume when the exchange exposes no first-inning
            # contract for the event.
            market_type = str(row.get("market_type") or "").lower()
            if market_type and market_type not in EXECUTABLE_AUTO_BUYER_MARKET_TYPES:
                result.rejected_unsupported_market += 1
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

            # Block games scheduled beyond the active slate window. Bounded to the
            # scheduler's own run cadence (daily cycle runs every 6h) by default, so
            # an event isn't bought hours before it can be re-evaluated against a
            # fresher quote/model run, and isn't left unbought because it was out of
            # range on every cycle before it started.
            if (event_start - now).total_seconds() > self.config.max_event_lookahead_hours * 3600:
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
            age_sec = float(quote.get("age_seconds") or 999999)
            if not quote.get("fresh", False) and age_sec > 300 and self._live_quote_fn is None:
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
                        age_sec = 0.0
                except (
                    httpx.HTTPError,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RuntimeError,
                    ExecutionGateError,
                ):
                    pass

            if not quote.get("fresh", False) and age_sec > 300:
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
            if self.config.max_edge is not None and edge > self.config.max_edge:
                result.rejected_unrealistic_edge += 1
                continue

            # Sizing: units * unit_value_usd
            raw_units = float(row.get("units") or row.get("display_units") or 1.0)
            target_spend = raw_units * self.config.unit_value_usd
            target_spend = min(target_spend, self.config.max_game_stake_usd)

            # Optional edge-scaled sizing (off by default -- see edge_scaled_sizing_enabled).
            # Scales the already-capped stake linearly between edge_scaling_min_fraction
            # (at min_edge) and 1.0 (at max_edge), so a bare-minimum-edge pick risks less
            # than a near-max-edge one. Never increases stake above the per-game cap above.
            if self.config.edge_scaled_sizing_enabled:
                edge_ceiling = (
                    self.config.max_edge if self.config.max_edge is not None else self.config.min_edge + 0.20
                )
                edge_span = max(1e-9, edge_ceiling - self.config.min_edge)
                edge_fraction = max(0.0, min(1.0, (edge - self.config.min_edge) / edge_span))
                min_fraction = min(1.0, max(0.0, self.config.edge_scaling_min_fraction))
                scale = min_fraction + (1.0 - min_fraction) * edge_fraction
                target_spend = round(target_spend * scale, 2)

            partial_info = self._get_partial_fill_info(
                pick_id=pick_id,
                market_slug=market_slug,
                token_side=token_side,
                event_id=event_id,
                selection=selection,
                bought_index=bought_index,
            )

            is_topup = False
            filled_cost = 0.0
            if partial_info is not None:
                filled_cost = float(partial_info.get("filled_cost_usd") or 0.0)
                remaining_spend = round(target_spend - filled_cost, 2)
                if remaining_spend < 0.50:
                    result.rejected_min_topup += 1
                    continue
                spend_for_order = remaining_spend
                is_topup = True
            else:
                spend_for_order = target_spend

            limit_price = max(0.01, min(0.99, round(clob_ask, 2)))

            # Compute shares: spend_for_order / limit_price, minimum min_shares
            shares = max(self.config.min_shares, round(spend_for_order / limit_price, 2))
            actual_cost = round(shares * limit_price, 2)

            if is_topup and (filled_cost + actual_cost) > target_spend + 0.10:
                shares = max(
                    self.config.min_shares, math.floor((target_spend - filled_cost) / limit_price * 100) / 100
                )
                actual_cost = round(shares * limit_price, 2)
                if actual_cost < 0.50:
                    result.rejected_min_topup += 1
                    continue

            # Daily budget check
            if current_spend + actual_cost > self.config.max_daily_spend_usd:
                result.rejected_budget += 1
                continue

            ticket_pick_id = pick_id
            if is_topup and partial_info is not None:
                existing_oids = partial_info.get("existing_order_ids") or []
                topup_suffix = f"topup_{len(existing_oids)}" if len(existing_oids) > 1 else "topup"
                ticket_pick_id = f"{pick_id}_{topup_suffix}"

            ticket = OrderTicket(
                market_slug=market_slug,
                token_side=token_side,
                action="buy",
                order_type="limit_ioc",
                price=limit_price,
                size_shares=shares,
                pick_id=ticket_pick_id,
                estimated_cost_usd=actual_cost,
                maximum_cost_usd=actual_cost + 0.10,
                authorization_type="auto_whitelisted_model",
                ioc_fallback_resting=True,
            )

            order_payload = {
                "pick_id": pick_id,
                "ticket_pick_id": ticket_pick_id,
                "is_topup": is_topup,
                "target_spend": target_spend,
                "model_id": model_id,
                "market_slug": market_slug,
                "token_side": token_side,
                "sport": row.get("sport") or row.get("league"),
                "selection": row.get("selection"),
                "limit_price": limit_price,
                "shares": shares,
                "cost_usd": actual_cost,
                "unit_value_usd": self.config.unit_value_usd,
                "edge": round(edge, 4),
                "event_start_utc": event_start_str,
                "timestamp_utc": iso_utc(now),
                # Carried through so a preview/paper fill recorded one layer up
                # (run_auto_buyer_cycle) has everything settlement needs without
                # re-touching the original pick row.
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "market_type": row.get("market_type"),
                "model_probability": row.get("model_probability"),
                "market_probability": row.get("market_probability") or row.get("market_implied_probability"),
                "event_id": row.get("event_id"),
                "line": row.get("line"),
            }

            # Update bought_index to prevent intra-batch double buys
            bought_index["pick_ids"].add(pick_id)
            bought_index["pick_ids"].add(ticket_pick_id)
            bought_index["market_sides"].add((market_slug, token_side))
            if event_id and selection:
                bought_index["event_selections"].add((event_id, selection))
            bought_index["held_slugs"].add(market_slug)
            if "partially_filled" in bought_index and pick_id in bought_index["partially_filled"]:
                del bought_index["partially_filled"][pick_id]

            if not self.config.execute_live:
                # NOTE: this is a pure in-memory preview -- it never touches disk.
                # Paper-mode persistence happens one layer up, in
                # run_auto_buyer_cycle(), so constructing a buyer directly (as
                # every unit test does) can never have a filesystem side effect.
                result.dry_run_orders.append(
                    {
                        "status": "paper_filled" if self.config.mode == "paper" else "dry_run_preview",
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
            exec_row["pick_id"] = ticket_pick_id
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
                filled_shares = float(sub.get("filled_size_shares") or 0.0)
                filled_cost = float(sub.get("estimated_filled_cost_usd") or 0.0)
                fill_known = bool(sub.get("fill_known", True))
                executed_payload = {
                    **order_payload,
                    "requested_shares": shares,
                    "requested_cost_usd": actual_cost,
                    "shares": filled_shares,
                    "cost_usd": filled_cost,
                    "order_ids": sub.get("order_ids") or [oid],
                    "fallback_order_id": sub.get("fallback_order_id"),
                    "fallback_status": sub.get("fallback_status"),
                    "fallback_resting_shares": sub.get("fallback_resting_shares"),
                    "fill_known": fill_known,
                }
                result.submitted_orders.append(
                    {
                        "status": sub.get("status", "submitted"),
                        "order_id": oid,
                        "order_state": sub.get("order_state"),
                        **executed_payload,
                    }
                )
                current_spend += filled_cost
                result.total_spend_usd = round(current_spend, 2)

                # Record directly to dedicated Auto-Buyer Ledger and Logger
                # -- the confirmed *filled* quantity/cost, not the requested
                # ones: an IOC can partially or never fill, and any
                # unfilled remainder is now resting separately (see
                # ioc_fallback_resting) rather than assumed complete here.
                # Record a zero-fill primary when a fallback order is resting,
                # or when the primary fill is unknown, so a later
                # reconcile_pending_auto_buyer_fallbacks() pass can restate it
                # from the exchange instead of leaving a fully untracked live
                # position.
                try:
                    from model_prediction.portfolio.auto_buyer_ledger import (
                        record_auto_buy_execution,
                    )

                    if filled_shares > 0 or executed_payload.get("fallback_order_id") or not fill_known:
                        record_auto_buy_execution(
                            order_payload=executed_payload,
                            order_id=oid,
                            order_state=str(sub.get("order_state") or "FILLED").upper(),
                            pick_row=row,
                            jsonl_path=self.data_root / "auto_buyer_ledger.jsonl",
                            xlsx_path=self.data_root / "auto_buyer_picks.xlsx",
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
