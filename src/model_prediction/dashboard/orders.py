"""Dashboard orders module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from urllib.parse import quote

import yaml

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None


from model_prediction.dashboard.backtests import (
    _pick_quote,
)
from model_prediction.dashboard.common import (
    _CACHE,
    _CACHE_LOCK,
    _MARKET_QUESTION_CACHE,
    _MARKET_QUESTION_LOCK,
    _ORDER_LOCK,
    _ORDER_PREVIEWS,
    _PICKS_CACHE,
    ARCHIVE_FILE,
    DASH_DIR,
    DATA,
    EASTERN,
    GATEWAY,
    ORDERS_FILE,
    PORTFOLIO_HISTORY_FILE,
    ROOT,
    SPORTS,
    _cached,
    _config_payload,
    _log,
    _manual_research_eligibility,
    _number,
    _read_json,
    _resolve_runner,
    _runner_env,
    _set_unit_value_usd,
    _today,
    _unit_value_usd,
)
from model_prediction.dashboard.picks import (
    _find_pick_by_id,
    _main_ledger_paths,
    _parse_research_picks,
    read_flat_picks,
    read_picks,
)

# ── SECTION: Orders & Execution ─────────────────────────────────────


def _load_orders() -> dict:
    payload = _read_json(ORDERS_FILE) or {}
    orders = payload.get("orders") if isinstance(payload, dict) else None
    rows = list(orders) if isinstance(orders, list) else []
    repaired = False
    # Older dashboard builds mixed the CLI confirmation prompt into stdout.
    # The exchange could accept an order and return an ID, while json.loads()
    # rejected the combined prompt + JSON and locally recorded it as refused.
    # Recover those durable exchange acknowledgements so a refresh cannot offer
    # the same model order a second time.
    for row in rows:
        if row.get("status") != "refused" or not isinstance(row.get("error"), str):
            continue
        decoded = _decode_command_output(row["error"])
        if decoded.get("status") == "submitted" and decoded.get("order_id"):
            row.update(
                status="submitted",
                order_id=str(decoded["order_id"]),
                order_state=decoded.get("order_state"),
                error=None,
            )
            repaired = True
    result = {"orders": rows}
    if repaired:
        _save_orders(result)
    return result


def _save_orders(payload: dict) -> None:
    DASH_DIR.mkdir(exist_ok=True)
    temporary = ORDERS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, ORDERS_FILE)


def _decode_command_output(raw: str) -> dict:
    """Decode a CLI JSON result even when an interactive prompt precedes it."""
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    decoder = json.JSONDecoder()
    best: dict = {}
    for index, character in enumerate(str(raw)):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(str(raw)[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not str(raw)[index + end :].strip():
            best = value
            break
    return best


def _latest_order_for_pick(row: dict, quote: dict | None, orders: dict | None = None) -> dict | None:
    """Find an order across equivalent model-version rows for the same contract side."""
    orders = (orders or _load_orders())["orders"]
    pick_id = str(row.get("pick_id") or "")
    direct = [order for order in orders if str(order.get("pick_id") or "") == pick_id]
    if direct:
        return direct[-1]
    if quote is None:
        return None
    equivalent = [
        order
        for order in orders
        if order.get("status") == "submitted"
        and order.get("market_slug") == quote.get("market_slug")
        and order.get("side") == quote.get("side")
    ]
    return equivalent[-1] if equivalent else None


def _filled_entry_for_pick(
    row: dict, orders: dict | None = None, portfolio_history: dict | None = None
) -> dict | None:
    """Exchange-backed entry price for a filled dashboard BUY, in pick-side terms."""
    pick_id = str(row.get("pick_id") or "")
    filled = [
        order
        for order in (orders or _load_orders())["orders"]
        if str(order.get("pick_id") or "") == pick_id
        and order.get("action", "buy") == "buy"
        and (order.get("status") == "filled" or _number(order.get("cum_quantity"), 0) > 0)
    ]
    if not filled:
        return None
    order = filled[-1]
    limit_price = _number(order.get("price"), None)
    side = str(order.get("side") or "")
    slug = str(order.get("market_slug") or "")
    submitted = str(order.get("submitted_at_utc") or "")
    quantity = _number(order.get("cum_quantity") or order.get("size_shares"), None)

    # Portfolio trades use the exchange's YES/long coordinate even for a NO
    # fill. Match the fill and convert it back to the outcome actually bought.
    candidates = []
    for activity in (portfolio_history or _load_portfolio_history())["activities"]:
        if activity.get("type") != "trade" or activity.get("market_slug") != slug:
            continue
        occurred = str(activity.get("occurred_at_utc") or "")
        if submitted and occurred and occurred < submitted:
            continue
        trade_quantity = _number(activity.get("quantity"), None)
        if quantity is not None and trade_quantity is not None and abs(quantity - trade_quantity) > 0.01:
            continue
        raw_price = _number(activity.get("exchange_price", activity.get("price")), None)
        if raw_price is None or not 0 < raw_price < 1:
            continue
        selected_price = 1 - raw_price if side == "short" else raw_price
        if limit_price is not None and selected_price > limit_price + 0.0001:
            continue
        candidates.append((occurred, selected_price, str(activity.get("activity_id") or "")))
    if candidates:
        _, price, activity_id = min(candidates)
        return {
            "price": round(price, 6),
            "basis": "exchange_trade",
            "side": side,
            "market_slug": slug,
            "activity_id": activity_id,
        }
    if limit_price is None:
        return None
    return {
        "price": limit_price,
        "basis": "filled_order_limit",
        "side": side,
        "market_slug": slug,
    }


def _dashboard_order_status(exchange_state: str | None) -> str:
    state = str(exchange_state or "").upper()
    return {
        "ORDER_STATE_FILLED": "filled",
        "ORDER_STATE_CANCELED": "canceled",
        "ORDER_STATE_REPLACED": "replaced",
        "ORDER_STATE_REJECTED": "rejected",
        "ORDER_STATE_EXPIRED": "expired",
    }.get(state, "submitted")


def _reconcile_orders() -> None:
    """Replace local submission state with the exchange's current order state.

    Real race fixed 2026-08-02: this used to read+write orders.json without
    holding _ORDER_LOCK, unlike submit_order/preview_position_sell/etc.
    Called from dashboard_picks() on essentially every /api/picks request,
    so it could run concurrently with a real order submission -- read a
    stale snapshot (before the new order was appended), then write that
    stale snapshot back after submit_order's own locked append completed,
    silently erasing the just-submitted order record. Held under the lock
    now, matching every other read-modify-write of this file.
    """
    with _ORDER_LOCK:
        _reconcile_orders_locked()


def _reconcile_orders_locked() -> None:
    payload = _load_orders()
    active = [
        order for order in payload["orders"] if order.get("status") == "submitted" and order.get("order_id")
    ]
    if not active:
        return
    order_ids = sorted({str(order["order_id"]) for order in active})

    def fetch_order_states() -> dict:
        try:
            command = _resolve_runner() + ["order-status"]
            for order_id in order_ids:
                command.extend(("--order-id", order_id))
            process = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                env=_runner_env(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        raw = process.stdout if process.returncode == 0 else process.stderr
        return _decode_command_output(raw)

    result = _cached("order-states:" + ",".join(order_ids), 10, fetch_order_states)
    if result.get("status") != "live":
        return
    by_id = {str(item.get("order_id")): item for item in result.get("orders", []) if item.get("order_id")}
    changed = False
    for order in active:
        snapshot = by_id.get(str(order["order_id"]))
        if snapshot is None:
            continue
        exchange_state = snapshot.get("order_state")
        updates = {
            "order_state": exchange_state,
            "status": _dashboard_order_status(exchange_state),
            "cum_quantity": snapshot.get("cum_quantity"),
            "leaves_quantity": snapshot.get("leaves_quantity"),
            "last_checked_at_utc": result.get("observed_at_utc"),
        }
        if any(order.get(key) != value for key, value in updates.items()):
            order.update(updates)
            changed = True
    if changed:
        _save_orders(payload)


def _event_already_started(row: dict) -> bool:
    event_start = str(row.get("event_start_utc") or "")
    try:
        game_time = datetime.fromisoformat(event_start)
    except (ValueError, TypeError):
        return False
    return game_time < datetime.now(UTC)


def _order_readiness(row: dict, quote: dict | None) -> tuple[bool, str]:
    if row.get("status") != "open":
        return False, "pick is not open"
    if row.get("record_type") == "QUALIFIED_SHADOW_CALL":
        if float(row.get("units") or 0) <= 0:
            return False, "qualified pick has no authorized units"
    elif row.get("record_type") == "RESEARCH_OBSERVATION":
        eligible, reason = _manual_research_eligibility(row)
        if not eligible:
            return False, reason
        if not _suggested_units(row):
            return False, "manual order has no authorized unit cap"
    else:
        return False, "unsupported ledger record type"
    if quote is None:
        return False, "no exact executable Polymarket US market mapping"
    if not quote.get("fresh"):
        return False, "market quote is older than 5 minutes; scan prices first"
    if quote.get("market_state") != "MARKET_STATE_OPEN":
        return False, "market is not open"
    # Block orders on past games
    if _event_already_started(row):
        return False, "game has already started"
    missing = [name for name in ("POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY") if not os.environ.get(name)]
    if missing:
        return False, f"missing {' and '.join(missing)}"
    return True, "ready"


def _net_position_quantity(slug: str, portfolio_history: dict) -> float | None:
    """Net shares still held on a market, from cached (no live call) activity history.

    Prefers the exchange's own settlement record when one exists: a
    "settlement" activity's after_quantity is ground truth for a resolved
    market, and holds even when a position went through several buy/sell
    round-trips beforehand. Only when the market hasn't settled does this
    fall back to summing trades -- the exchange reports cost_basis_usd/
    realized_pnl_usd on trades that close or reduce a position (null on
    pure opens), so opening-quantity minus closing-quantity approximates net
    exposure for a still-open, never-settled position. That fallback is a
    heuristic, not ground truth: multiple round-trips on the same market
    (sell then rebuy) can make it wrong, which is exactly why the settlement
    record is checked first.
    """
    activities = [a for a in portfolio_history.get("activities", []) if a.get("market_slug") == slug]
    if not activities:
        return None
    settlements = [a for a in activities if a.get("type") == "settlement"]
    if settlements:
        latest = max(settlements, key=lambda a: str(a.get("occurred_at_utc") or ""))
        after = latest.get("after_quantity")
        if after is not None:
            return _number(after, 0.0)
    trades = [a for a in activities if a.get("type") == "trade"]
    if not trades:
        return None
    net = 0.0
    for trade in trades:
        quantity = _number(trade.get("quantity"), 0.0)
        closing = trade.get("cost_basis_usd") is not None or trade.get("realized_pnl_usd") is not None
        net += -quantity if closing else quantity
    return net


def _decorate_pick(
    row: dict,
    orders: dict | None = None,
    portfolio_history: dict | None = None,
    archived_ids: set[str] | None = None,
) -> dict:
    quote = _pick_quote(row)
    ready, reason = _order_readiness(row, quote)
    order = _latest_order_for_pick(row, quote, orders)
    manual, _ = _manual_research_eligibility(row)
    filled_entry = _filled_entry_for_pick(row, orders, portfolio_history)
    position_closed = False
    if order and order.get("action", "buy") == "buy" and order.get("status") == "filled":
        slug = str(order.get("market_slug") or "")
        if slug:
            net = _net_position_quantity(slug, portfolio_history or _load_portfolio_history())
            position_closed = net is not None and abs(net) < 1e-6
    display_units = (
        _number(row.get("units")) or _number(row.get("research_score_units")) or _suggested_units(row) or 0
    )
    display_pnl = _number(row.get("pnl_units")) or _number(row.get("research_pnl_units"))
    # Fallback: compute P&L from american_odds when research_pnl_units is
    # absent. Confirmed 2026-08-01: this never fires against real data --
    # every row settle() ever touches already has a real pnl_units/
    # research_pnl_units -- it's a defensive net for malformed/legacy rows
    # only. Deliberately NOT importing pricing.profit_units here (this file
    # has zero dependencies on the model_prediction package by design, kept
    # runnable standalone); this formula must instead be kept in exact sync
    # with pricing.profit_units by hand -- see
    # tests/test_dashboard_server.py's
    # test_pnl_fallback_formula_matches_pricing_profit_units, which fails
    # loudly if the two ever diverge.
    if display_pnl == 0 and row.get("result") in ("win", "loss") and row.get("american_odds"):
        try:
            odds = int(row["american_odds"])
            if odds > 0:
                display_pnl = display_units * odds / 100
            else:
                display_pnl = display_units * 100 / abs(odds)
            if row["result"] == "loss":
                display_pnl = -display_units
        except (ValueError, TypeError):
            pass
    return {
        **row,
        # Preserve ledger facts in the API. A Research NO_CALL must remain
        # units=0 instead of looking like a sized Gated call merely because
        # the dashboard can calculate a hypothetical display size.
        "display_units": display_units,
        "display_pnl_units": display_pnl,
        "quote": quote,
        "order": order,
        "filled_entry": filled_entry,
        "position_closed": position_closed,
        "buy_ready": ready,
        "buy_block_reason": reason,
        "unit_value_usd": _unit_value_usd(),
        "order_authorization": ("manual_research_override" if manual else "qualified_model"),
        "archived": str(row.get("pick_id") or "")
        in (archived_ids if archived_ids is not None else set(_load_archive().get("pick_ids", []))),
    }


def _pick_identity(row: dict) -> tuple[str, ...]:
    """Canonical dashboard identity, independent of model version or logged price."""
    event = str(row.get("event_id") or "").strip()
    if not event:
        event = "|".join(
            str(row.get(key) or "").strip().casefold()
            for key in ("event_start_utc", "away_team", "home_team")
        )
    line = row.get("line")
    try:
        line_value = f"{float(line):g}" if line not in (None, "") else ""
    except (TypeError, ValueError):
        line_value = str(line or "").strip().casefold()
    return (
        str(row.get("league") or "").strip().casefold(),
        event,
        str(row.get("market_type") or "").strip().casefold(),
        str(row.get("selection") or "").strip().casefold(),
        line_value,
        str(row.get("period") or row.get("horizon") or "").strip().casefold(),
    )


def _dedupe_picks(rows: list[dict]) -> list[dict]:
    """Keep the latest ledger observation for each actual bet shown in the UI."""
    latest: dict[tuple[str, ...], tuple[str, int, dict]] = {}
    for index, row in enumerate(rows):
        rank = (str(row.get("created_at_utc") or ""), index, row)
        key = _pick_identity(row)
        if key not in latest or rank[:2] >= latest[key][:2]:
            latest[key] = rank
    return [item[2] for item in sorted(latest.values(), key=lambda item: item[1])]


def dashboard_picks() -> list[dict]:
    """Latest unique picks with persistent local-clear and order state attached."""
    _reconcile_orders()
    archived = set(_load_archive()["pick_ids"])
    orders, portfolio_history = _load_orders(), _load_portfolio_history()
    # No RESEARCH_ONLY_LEAGUES filter needed here anymore: read_picks() only
    # ever sources from data/main/<sport>.xlsx for _MAIN_LEDGER_SPORTS
    # (mlb/wnba/soccer/tennis), so it's now physically impossible for a
    # research-only league's row to appear here. The old filter was
    # actively wrong for soccer and tennis specifically -- both have real
    # Main-ledger rows since their 2026-08-02/08-03 promotion, but
    # RESEARCH_ONLY_LEAGUES was never updated and was silently hiding them.
    return [
        {
            **_decorate_pick(row, orders, portfolio_history),
            "archived": str(row.get("pick_id")) in archived,
            "suggested_paper_units": _suggested_units(row),
        }
        for row in _dedupe_picks(read_picks())
    ]


def preview_order(payload: dict) -> dict:
    action = str(payload.get("action") or "buy").lower()
    if action not in ("buy", "sell"):
        return {"status": "refused", "error": "action must be buy or sell"}
    pick_id = str(payload.get("pick_id") or "")
    row = _find_pick_by_id(pick_id)
    if row is None:
        return {"status": "refused", "error": "unknown pick id"}
    decorated = _decorate_pick(row)
    quote = decorated["quote"]
    if quote is None:
        return {"status": "refused", "error": "no executable quote for this contract"}
    # Buys require the buy-readiness gate. Sells are exits and only require an
    # executable quote (you can always try to close a position you hold).
    if action == "buy" and not decorated["buy_ready"]:
        return {"status": "refused", "error": decorated["buy_block_reason"]}
    try:
        raw_price = float(payload.get("price"))
        size_shares = float(payload.get("size_shares"))
    except (TypeError, ValueError):
        return {"status": "refused", "error": "price and shares must be numeric"}
    # Validate the tick on the RAW input; rounding first would silently accept
    # (and change) a sub-cent price the user never confirmed.
    if not 0.01 <= raw_price <= 0.99 or abs(raw_price * 100 - round(raw_price * 100)) > 1e-8:
        return {"status": "refused", "error": "limit price must be a 0.01 tick from 0.01 to 0.99"}
    price = round(raw_price, 2)
    if not 0 < size_shares <= 100000:
        return {"status": "refused", "error": "shares must be greater than 0 and at most 100,000"}
    estimated_cost = round(price * size_shares, 2)
    manual = row.get("record_type") == "RESEARCH_OBSERVATION"

    if action == "sell":
        # A resting SELL limit must sit AT OR ABOVE the current bid (post-only:
        # do not cross into the bid). No dollar cost cap — a sell returns
        # capital. Proceeds are informational. Sells are otherwise less
        # restricted than buys (you can always try to close a position you
        # hold) -- but _pick_quote never returns a snapshot observed at or
        # after event_start_utc, so once a game has started the only
        # available quote is a frozen pregame snapshot that can never
        # update again. Validating a resting sell's "don't cross the bid"
        # check against that frozen number would be actively misleading,
        # not just imprecise, so this one case is still blocked.
        if _event_already_started(row):
            return {"status": "refused", "error": "game has already started; quote can no longer update"}
        bid = quote.get("bid")
        if bid is not None and price <= float(bid):
            return {
                "status": "refused",
                "error": (
                    f"resting sell price must be above the current bid {float(bid):.2f}; "
                    "crossing orders are blocked"
                ),
            }
        maximum_cost = None
    else:
        # Buy path: limit below model probability for manual research and keep
        # the authorized unit cap. A limit at/above the ask becomes an IOC
        # marketable limit; a lower limit remains post-only GTC.
        execution_config = (_config_payload().get("execution") or {}) if manual else {}
        if (
            manual
            and execution_config.get("manual_research_require_positive_edge", True)
            and price >= float(row.get("model_probability") or 0)
        ):
            return {
                "status": "refused",
                "error": (
                    f"manual buy limit {price:.2f} must stay below the model probability "
                    f"{float(row.get('model_probability') or 0):.2f}"
                ),
            }
        authorized_units = _suggested_units(row) if manual else float(row.get("units") or 0)
        maximum_cost = round(float(authorized_units or 0) * _unit_value_usd(), 2)
        if estimated_cost > maximum_cost + 0.005:
            return {
                "status": "refused",
                "error": (
                    f"order cost ${estimated_cost:.2f} exceeds this pick's "
                    f"{float(row.get('units') or 0):g}U cap (${maximum_cost:.2f})"
                ),
            }
    ask = float(quote["ask"]) if action == "buy" else None
    marketable = action == "buy" and price >= ask
    order_type = "limit_ioc" if marketable else "limit_gtc"
    nonce = secrets.token_urlsafe(24)
    ticket = {
        "nonce": nonce,
        "pick_id": pick_id,
        "action": action,
        "market_slug": quote["market_slug"],
        "side": quote["side"],
        "price": price,
        "size_shares": size_shares,
        "units": round(estimated_cost / _unit_value_usd(), 4),
        "unit_value_usd": _unit_value_usd(),
        "estimated_cost_usd": estimated_cost,
        "estimated_proceeds_usd": estimated_cost if action == "sell" else None,
        "maximum_cost_usd": maximum_cost,
        "order_type": order_type,
        "execution_mode": "marketable_limit" if marketable else "resting_limit",
        "reference_ask": ask,
        "manual_research_order": manual,
        "created_at": time.time(),
        "expires_at": time.time() + 300,
    }
    with _ORDER_LOCK:
        _ORDER_PREVIEWS[nonce] = ticket
    return {"status": "preview", **ticket}


def submit_order(payload: dict) -> dict:
    nonce = str(payload.get("nonce") or "")
    with _ORDER_LOCK:
        ticket = _ORDER_PREVIEWS.pop(nonce, None)
    if ticket is None or time.time() > float(ticket["expires_at"]):
        return {"status": "refused", "error": "order preview expired; preview it again"}
    row = _find_pick_by_id(ticket["pick_id"])
    if row is None:
        return {"status": "refused", "error": "pick disappeared before submission"}
    quote = _pick_quote(row)
    if quote is None or quote["market_slug"] != ticket["market_slug"]:
        return {"status": "refused", "error": "market changed; preview the order again"}
    action = ticket.get("action", "buy")
    if action == "sell":
        # See the matching comment in preview_order: a game already in
        # progress has no live quote to validate against (_pick_quote only
        # ever returns pregame snapshots), so re-check here too rather than
        # trust that preview-time state still holds at submission time.
        if _event_already_started(row):
            return {"status": "refused", "error": "game has already started; quote can no longer update"}
        bid = quote.get("bid")
        if bid is not None and ticket["price"] <= float(bid):
            return {"status": "refused", "error": "bid moved above your limit; preview the sell again"}
    else:
        ready, reason = _order_readiness(row, quote)
        if not ready:
            return {"status": "refused", "error": reason}
        ask = float(quote["ask"])
        if ticket.get("order_type") == "limit_ioc":
            if ticket["price"] < ask:
                return {
                    "status": "refused",
                    "error": (
                        f"current ask moved to {ask:.2f}, above your {ticket['price']:.2f} "
                        "buy cap; preview the order again"
                    ),
                }
        elif ticket["price"] >= ask:
            return {
                "status": "refused",
                "error": "ask moved down through your resting limit; preview the order again",
            }
    command = _resolve_runner() + [
        "execute",
        "--pick-id",
        ticket["pick_id"],
        "--size-shares",
        str(ticket["size_shares"]),
        "--price",
        str(ticket["price"]),
        "--side",
        ticket["side"],
        "--action",
        action,
        "--order-type",
        ticket.get("order_type", "limit_gtc"),
        "--market-slug",
        ticket["market_slug"],
        "--execute",
    ]
    if ticket.get("manual_research_order"):
        command.append("--manual-research-order")
    process = subprocess.run(
        command,
        cwd=ROOT,
        input="Y\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=_runner_env(),
        check=False,
    )
    raw = process.stdout if process.returncode == 0 else process.stderr
    result = _decode_command_output(raw)
    if not result:
        result = {"status": "refused", "error": raw[-1000:] or "order command failed"}
    record = {
        **ticket,
        "nonce": None,
        "status": result.get("status", "refused"),
        "order_id": result.get("order_id"),
        "order_state": result.get("order_state"),
        "exchange_price": result.get("exchange_price"),
        "price_basis": "selected_outcome_probability",
        "exchange_price_basis": "long_side_probability",
        "submitted_at_utc": datetime.now(UTC).isoformat(),
        "error": result.get("error"),
    }
    with _ORDER_LOCK:
        orders = _load_orders()
        orders["orders"].append(record)
        _save_orders(orders)
    with _CACHE_LOCK:
        _CACHE.clear()
    return {**result, "pick_id": ticket["pick_id"]}


def _live_bbo(market_slug: str) -> dict | None:
    """Fetch a fresh BBO for one market slug from the public gateway."""
    try:
        from model_prediction.data_sources.polymarket_us import PolymarketUSClient

        return PolymarketUSClient().snapshot(market_slug)
    except Exception:  # noqa: BLE001 - any failure => no quote, caller handles
        return None


def preview_position_sell(payload: dict) -> dict:
    """Preview a resting SELL limit against a held live exchange position."""
    slug = str(payload.get("market_slug") or "")
    side = str(payload.get("side") or "long")
    if not slug or side not in ("long", "short"):
        return {"status": "refused", "error": "market_slug and side (long|short) are required"}
    try:
        raw_price = float(payload.get("price"))
        size_shares = float(payload.get("size_shares"))
    except (TypeError, ValueError):
        return {"status": "refused", "error": "price and shares must be numeric"}
    if not 0.01 <= raw_price <= 0.99 or abs(raw_price * 100 - round(raw_price * 100)) > 1e-8:
        return {"status": "refused", "error": "limit price must be a 0.01 tick from 0.01 to 0.99"}
    price = round(raw_price, 2)
    if not 0 < size_shares <= 1_000_000:
        return {"status": "refused", "error": "shares must be greater than 0"}
    portfolio = live_portfolio_view()
    position = next(
        (
            item
            for item in (portfolio.get("open") or {}).get("positions", [])
            if item.get("market_slug") == slug and item.get("side") == side
        ),
        None,
    )
    if portfolio.get("status") != "live" or position is None:
        return {"status": "refused", "error": "live position could not be verified"}
    held = _number(position.get("available_quantity"), 0.0)
    if size_shares > held + 1e-9:
        return {
            "status": "refused",
            "error": f"cannot sell {size_shares:g} shares; only {held:g} are available",
        }
    snapshot = _live_bbo(slug)
    bid = None
    if snapshot:
        bid = (snapshot.get(side) or {}).get("bid")
    if bid is not None and price <= float(bid):
        return {
            "status": "refused",
            "error": (
                f"resting sell price must be above the current {side} bid {float(bid):.2f}; "
                "crossing orders are blocked"
            ),
        }
    nonce = secrets.token_urlsafe(24)
    ticket = {
        "nonce": nonce,
        "kind": "position_sell",
        "market_slug": slug,
        "side": side,
        "price": price,
        "size_shares": size_shares,
        "estimated_proceeds_usd": round(price * size_shares, 2),
        "current_bid": bid,
        "verified_available_quantity": held,
        "created_at": time.time(),
        "expires_at": time.time() + 300,
    }
    with _ORDER_LOCK:
        _ORDER_PREVIEWS[nonce] = ticket
    return {"status": "preview", **ticket}


def submit_position_sell(payload: dict) -> dict:
    nonce = str(payload.get("nonce") or "")
    with _ORDER_LOCK:
        ticket = _ORDER_PREVIEWS.pop(nonce, None)
    if ticket is None or ticket.get("kind") != "position_sell" or time.time() > float(ticket["expires_at"]):
        return {"status": "refused", "error": "sell preview expired; preview it again"}
    portfolio = live_portfolio_view()
    position = next(
        (
            item
            for item in (portfolio.get("open") or {}).get("positions", [])
            if item.get("market_slug") == ticket["market_slug"] and item.get("side") == ticket["side"]
        ),
        None,
    )
    held = _number((position or {}).get("available_quantity"), 0.0)
    if portfolio.get("status") != "live" or position is None or ticket["size_shares"] > held + 1e-9:
        return {"status": "refused", "error": "available live shares changed; preview the sell again"}
    # Re-check the bid moved-through condition against a fresh quote.
    snapshot = _live_bbo(ticket["market_slug"])
    if snapshot:
        bid = (snapshot.get(ticket["side"]) or {}).get("bid")
        if bid is not None and ticket["price"] <= float(bid):
            return {"status": "refused", "error": "bid moved above your limit; preview the sell again"}
    command = _resolve_runner() + [
        "sell-position",
        "--market-slug",
        ticket["market_slug"],
        "--side",
        ticket["side"],
        "--price",
        str(ticket["price"]),
        "--size-shares",
        str(ticket["size_shares"]),
        "--execute",
    ]
    process = subprocess.run(
        command,
        cwd=ROOT,
        input="Y\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=_runner_env(),
        check=False,
    )
    raw = process.stdout if process.returncode == 0 else process.stderr
    result = _decode_command_output(raw)
    if not result:
        result = {"status": "refused", "error": raw[-1000:] or "sell command failed"}
    record = {
        **ticket,
        "nonce": None,
        "status": result.get("status", "refused"),
        "order_id": result.get("order_id"),
        "order_state": result.get("order_state"),
        "exchange_price": result.get("exchange_price"),
        "price_basis": "selected_outcome_probability",
        "exchange_price_basis": "long_side_probability",
        "submitted_at_utc": datetime.now(UTC).isoformat(),
        "error": result.get("error"),
    }
    with _ORDER_LOCK:
        orders = _load_orders()
        orders["orders"].append(record)
        _save_orders(orders)
    with _CACHE_LOCK:
        _CACHE.clear()
    return result


def live_gateway_slate(sport: str, day: str) -> dict:
    """Read-only live discovery quotes from the public gateway (indicative)."""
    league = {"mlb": "mlb", "nba": "nba", "wnba": "wnba", "nfl": "nfl"}.get(sport)
    if league is None:
        return {
            "events": [],
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "error": "live indicative gateway is unavailable for this sport",
        }
    url = f"{GATEWAY}/v2/leagues/{league}/events?limit=50&section=general&type=sport"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode())
    except Exception as error:  # noqa: BLE001 - gateway failure degrades to an empty events list
        return {
            "events": [],
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "error": type(error).__name__,
        }
    events = []
    for event in payload.get("events", []):
        start = str(event.get("startTime") or "")
        if not start:
            continue
        try:
            start_et = datetime.fromisoformat(start).astimezone(EASTERN)
        except ValueError:
            continue
        if start_et.date().isoformat() != day:
            continue
        markets = []
        for market in event.get("markets", []):
            sides = []
            for side in market.get("marketSides", []):
                quote = side.get("quote")
                if isinstance(quote, dict):
                    quote = quote.get("value")
                try:
                    quote = float(quote)
                except (TypeError, ValueError):
                    quote = None
                sides.append({"description": side.get("description"), "quote": quote})
            markets.append(
                {
                    "slug": market.get("slug"),
                    "type": market.get("sportsMarketTypeV2") or market.get("sportsMarketType"),
                    "line": market.get("line"),
                    "sides": sides,
                }
            )
        events.append(
            {"title": event.get("title"), "start_utc": start, "slug": event.get("slug"), "markets": markets}
        )
    return {
        "events": events,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "note": "indicative discovery quotes; decision prices come from stored BBO asks",
    }


def _all_ledger_rows_for_price_scan() -> list[dict]:
    """Every row from all four ledgers (Main, Flat, Research, Gated
    Research), for the "Scan Open Ledger Prices" action.

    read_picks() only ever parsed picks.xlsx (Main) -- confirmed a real gap
    (2026-07-31): every open Flat/Research/Gated Research pick's price was
    permanently stale, since nothing else ever refreshed them. Pulled into
    its own function (rather than inlined in _action_command) so tests can
    monkeypatch this one seam instead of four separate parse calls. The
    caller's (sport, game_day, slug) dedup already collapses the same real
    contract appearing in more than one ledger (e.g. an MLB game open in
    both Main and Flat) into a single --contract entry.
    """
    return (
        read_picks()
        + read_flat_picks()
        + _parse_research_picks(gated=False)
        + _parse_research_picks(gated=True)
    )


def _action_command(name: str, payload: dict) -> list[str]:
    runner = _resolve_runner()
    if name == "run_tests":
        # pytest lives next to whatever python the runner resolved to.
        python = runner[0] if runner[0].endswith(("python", "python.exe", "python3")) else sys.executable
        return [python, "-m", "pytest", "tests/", "-q", "--no-header"]
    cli = runner  # module or console-script form
    if name == "daily":
        # One scheduling authority: the run supervisor. Executing
        # run_daily.sh directly here would make the dashboard a second
        # scheduler — two paths capable of acting like the control plane
        # fighting over the daily lock, with a legitimate scheduled run
        # showing up as a failed dashboard job. Route through the
        # supervisor so a busy lease comes back as exit 75 (the daily_lock
        # convention) and is recorded as skipped, not failed.
        return [sys.executable, "-m", "model_prediction.run_supervisor", "run", "daily"]
    if name == "flat_forecast":
        return cli + ["flat-forecast", "--all", "--date", str(payload.get("date") or _today()), "--log"]
    if name == "main_forecast":
        # Same command as Step 3 of run_daily.sh: MLB/WNBA -> picks.xlsx,
        # esports/soccer/KBO/NPB -> separate per-sport research and gated
        # workbooks. One shared forecast pass, not three independent ones —
        # the Ledger, Research, and Gated Research tab buttons all trigger it.
        return cli + [
            "forecast",
            "--all",
            "--date",
            str(payload.get("date") or _today()),
            "--log",
            "--replace-today",
            "--model",
            "learned",
        ]
    if name == "refresh_prices":
        day = str(payload.get("date") or _today())
        command = cli + ["polymarket-ledger-prices", "--date", day]
        seen: set[tuple[str, str, str]] = set()
        archived = set(_load_archive()["pick_ids"])
        for row in _dedupe_picks(_all_ledger_rows_for_price_scan()):
            if row.get("status") != "open" or str(row.get("pick_id")) in archived:
                continue
            quote = _pick_quote(row) or {}
            sport = str(row.get("league") or "").strip().lower()
            slug = str(quote.get("market_slug") or "").strip()
            try:
                game_day = (
                    datetime.fromisoformat(str(row.get("event_start_utc") or ""))
                    .astimezone(EASTERN)
                    .date()
                    .isoformat()
                )
            except ValueError:
                continue
            target = (sport, game_day, slug)
            if sport not in SPORTS or not slug or target in seen:
                continue
            seen.add(target)
            command += ["--contract", f"{sport}@{game_day}={slug}"]
        return command
    if name == "settle":
        return cli + ["settle", "--all-unsettled"]
    if name == "bootstrap":
        command = cli + [
            "bootstrap",
            "--sport",
            _safe_sport(payload.get("sport")),
            "--from",
            str(payload.get("from_date") or _today()),
        ]
        if payload.get("to_date"):
            command += ["--to", str(payload["to_date"])]
        return command
    raise ValueError(f"unknown action: {name}")


class InvalidSportError(ValueError):
    """Raised when a client requests an unsupported sport slug."""


def _safe_sport(value) -> str:
    if value not in SPORTS:
        raise InvalidSportError(f"unsupported sport: {value}")
    return str(value)


def _auto_adjust_unit_value(pct: float = 10.0) -> dict:
    """Set 1U to pct% of the LIVE exchange USD balance, moving up or down.

    The exchange balance is the only honest bankroll number: revaluing
    historical unit P&L at the current unit value (the previous approach)
    was circular and compounded the unit on every win. When the live
    account is unreachable the unit value is left unchanged.
    """
    fraction = max(0.01, min(0.50, pct / 100.0))  # clamp 1-50%
    portfolio = live_portfolio_view()
    if portfolio.get("status") != "live":
        return {
            "status": "unavailable",
            "error": (
                "live exchange balance unreachable: "
                + str(portfolio.get("error") or "authentication required")
            ),
            "note": "Unit value unchanged; auto-sizing requires the real account balance.",
        }
    balance = _number((portfolio.get("balance") or {}).get("current_usd"), None)
    if balance is None or balance <= 0:
        return {
            "status": "unavailable",
            "error": "exchange returned no positive USD balance",
            "note": "Unit value unchanged.",
        }
    suggested = round(balance * fraction, 2)
    current = _unit_value_usd()
    if abs(suggested - current) < 0.01:
        return {
            "status": "no_change",
            "balance_usd": round(balance, 2),
            "current_unit": current,
            "suggested_unit": suggested,
            "note": f"{pct:.1f}% of ${balance:,.2f} = ${suggested:.2f}, already current.",
        }
    try:
        result = _set_unit_value_usd(suggested)
        result["balance_usd"] = round(balance, 2)
        result["action"] = "raised" if suggested > current else "lowered"
        return result
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as error:
        return {"status": "error", "error": str(error)}


def today_picks(day: str) -> dict:
    """Latest unique, locally visible picks played on a US-Eastern date."""
    rows = []
    archived = set(_load_archive()["pick_ids"])
    orders, portfolio_history = _load_orders(), _load_portfolio_history()
    for row in _dedupe_picks(read_picks()):
        if str(row.get("pick_id")) in archived:
            continue
        start = row.get("event_start_utc")
        if not start:
            continue
        try:
            start_dt = datetime.fromisoformat(str(start))
        except ValueError:
            continue
        start_et = start_dt.astimezone(EASTERN)
        if start_et.date().isoformat() != day:
            continue
        rows.append(
            {
                **_decorate_pick(row, orders, portfolio_history),
                "start_et": start_et.strftime("%I:%M %p ET"),
                "start_sort": start_dt.isoformat(),
                "suggested_paper_units": _suggested_units(row),
            }
        )
    rows.sort(key=lambda r: (r["start_sort"], str(r.get("league")), str(r.get("market_type"))))
    return {
        "date": day,
        "picks": rows,
        "count": len(rows),
        "open": sum(1 for r in rows if r.get("status") == "open"),
        "settled": sum(1 for r in rows if r.get("status") == "settled"),
    }


def open_picks() -> dict:
    """All open ledger picks with model probability, market odds, and edge."""
    rows = []
    for row in read_picks():
        if row.get("status") != "open":
            continue
        model_p = _number(row.get("model_probability"))
        market_p = _number(row.get("market_implied_probability"))
        edge = model_p - market_p if model_p and market_p else None
        rows.append(
            {
                "pick_id": str(row.get("pick_id", "")),
                "league": str(row.get("league", "")),
                "away_team": str(row.get("away_team", "")),
                "home_team": str(row.get("home_team", "")),
                "selection": str(row.get("selection", "")),
                "market_type": str(row.get("market_type", "")),
                "event_start_utc": str(row.get("event_start_utc", "")),
                "model_probability": round(model_p, 4) if model_p else None,
                "market_implied_probability": round(market_p, 4) if market_p else None,
                "edge": round(edge, 4) if edge is not None else None,
                "american_odds": row.get("american_odds"),
                "units": _number(row.get("units")),
                "record_type": str(row.get("record_type", "")),
                "model_version": str(row.get("model_version", "")),
                "reason_code": str(row.get("reason_code", "")),
            }
        )
    # Sort: earliest game first
    rows.sort(key=lambda r: r["event_start_utc"])
    qualified = [r for r in rows if r["record_type"] == "QUALIFIED_SHADOW_CALL"]
    research = [r for r in rows if r["record_type"] == "RESEARCH_OBSERVATION"]
    return {
        "open": rows,
        "count": len(rows),
        "qualified_count": len(qualified),
        "research_count": len(research),
        "total_units": round(sum(r["units"] for r in qualified), 2),
    }


def history_picks(days: int = 30, sport: str | None = None) -> dict:
    """Settled picks within the last N days, optionally filtered by sport."""
    cutoff = datetime.now(UTC)
    rows = []
    for row in read_picks():
        if row.get("status") != "settled":
            continue
        if sport and str(row.get("league", "")).lower() != sport.lower():
            continue
        settled_at = row.get("settled_at_utc")
        if settled_at:
            try:
                settled_dt = datetime.fromisoformat(str(settled_at))
            except ValueError:
                continue
            if (cutoff - settled_dt).days > days:
                continue
        model_p = _number(row.get("model_probability"))
        market_p = _number(row.get("market_implied_probability"))
        rows.append(
            {
                "pick_id": str(row.get("pick_id", "")),
                "league": str(row.get("league", "")),
                "away_team": str(row.get("away_team", "")),
                "home_team": str(row.get("home_team", "")),
                "selection": str(row.get("selection", "")),
                "market_type": str(row.get("market_type", "")),
                "result": str(row.get("result", "")),
                "away_score": row.get("away_score"),
                "home_score": row.get("home_score"),
                "model_probability": round(model_p, 4) if model_p else None,
                "market_implied_probability": round(market_p, 4) if market_p else None,
                "pnl_units": _number(row.get("pnl_units")),
                "units": _number(row.get("units")) or _number(row.get("research_score_units")),
                "settled_at_utc": str(row.get("settled_at_utc", "")),
                "event_start_utc": str(row.get("event_start_utc", "")),
                "record_type": str(row.get("record_type", "")),
                "american_odds": row.get("american_odds"),
            }
        )
    rows.sort(key=lambda r: r["settled_at_utc"], reverse=True)
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = sum(1 for r in rows if r["result"] == "loss")
    pushes = sum(1 for r in rows if r["result"] == "push")
    total_pnl = sum(r["pnl_units"] for r in rows)
    return {
        "history": rows,
        "count": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / (wins + losses), 4) if (wins + losses) else None,
        "total_pnl": round(total_pnl, 4),
        "days": days,
        "sport": sport,
    }


def _amount_value(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_model_links() -> dict[tuple[str, str], dict]:
    """Connect exchange contracts to model rows without treating picks as positions."""
    links: dict[tuple[str, str], dict] = {}
    all_rows = read_picks()
    rows_by_id = {str(row.get("pick_id") or ""): row for row in all_rows}

    def _link(row: dict, side: str) -> dict:
        quote = _pick_quote(row) or {}
        bid = _number(quote.get("bid"), None)
        ask = _number(quote.get("ask"), None)
        return {
            "pick_id": str(row.get("pick_id") or ""),
            "league": str(row.get("league") or ""),
            "away_team": str(row.get("away_team") or ""),
            "home_team": str(row.get("home_team") or ""),
            "selection": str(row.get("selection") or ""),
            "market_type": str(row.get("market_type") or ""),
            "model_probability": _number(row.get("model_probability"), None),
            "model_version": str(row.get("model_version") or ""),
            "side": side,
            "decision_price": ask,
            "decision_bid": bid,
            "decision_spread": (round(ask - bid, 6) if ask is not None and bid is not None else None),
            "quote_observed_at_utc": quote.get("observed_at_utc"),
        }

    for row in _dedupe_picks(all_rows):
        quote = _pick_quote(row)
        if quote is None:
            continue
        side = str(quote["side"])
        links[(str(quote["market_slug"]), side)] = _link(row, side)
    # An exchange-acknowledged dashboard order links later fills back to the
    # model pick, including a partial fill followed by cancellation.
    for order in _load_orders()["orders"]:
        if order.get("status") not in {"submitted", "filled", "canceled", "replaced"}:
            continue
        row = rows_by_id.get(str(order.get("pick_id") or ""))
        slug = str(order.get("market_slug") or "")
        side = str(order.get("side") or "")
        if row is not None and slug and side:
            links[(slug, side)] = _link(row, side)
    return links


def _activity_outcome_side(payload: dict) -> str | None:
    """Return long/short only when an exchange activity states its outcome side."""
    for raw in (
        payload.get("outcomeSide"),
        payload.get("positionSide"),
        payload.get("intent"),
        payload.get("side"),
    ):
        value = str(raw or "").upper()
        if value.endswith("_SHORT") or value in {"SHORT", "NO"}:
            return "short"
        if value.endswith("_LONG") or value in {"LONG", "YES"}:
            return "long"
    return None


def _activity_link(
    slug: str,
    explicit_side: str | None,
    links: dict[tuple[str, str], dict],
) -> tuple[str | None, dict | None]:
    """Resolve a side, inferring it only when exactly one linked side exists."""
    if explicit_side:
        return explicit_side, links.get((slug, explicit_side))
    candidates = [(side, link) for (market_slug, side), link in links.items() if market_slug == slug]
    if len(candidates) == 1:
        return candidates[0]
    return None, None


def _selected_short_pnl(exchange_price: float | None, exchange_pnl: float | None) -> float | None:
    """Correct terminal synthetic-NO P&L without rewriting ordinary trade P&L."""
    if exchange_pnl is None:
        return None
    if exchange_price is not None and exchange_price <= 0.01:
        return abs(exchange_pnl)  # YES lost, so the held NO side won.
    if exchange_price is not None and exchange_price >= 0.99:
        return -abs(exchange_pnl)  # YES won, so the held NO side lost.
    return exchange_pnl


def _normalize_live_activity(item: dict, links: dict[tuple[str, str], dict]) -> dict | None:
    trade = item.get("trade") if isinstance(item.get("trade"), dict) else None
    resolution = item.get("positionResolution") if isinstance(item.get("positionResolution"), dict) else None
    if trade:
        slug = str(trade.get("marketSlug") or "")
        occurred = str(trade.get("updateTime") or trade.get("createTime") or "")
        outcome_side, linked = _activity_link(slug, _activity_outcome_side(trade), links)
        exchange_price = _amount_value(trade.get("price"))
        selected_price = exchange_price
        if exchange_price is not None and outcome_side == "short":
            selected_price = round(1 - exchange_price, 6)
        exchange_pnl = _amount_value(trade.get("realizedPnl"))
        selected_pnl = exchange_pnl
        if exchange_pnl is not None and outcome_side == "short":
            selected_pnl = _selected_short_pnl(exchange_price, exchange_pnl)
        return {
            "activity_id": f"trade:{trade.get('id') or slug + ':' + occurred}",
            "type": "trade",
            "market_slug": slug,
            "title": str((trade.get("marketMetadata") or {}).get("title") or slug),
            "occurred_at_utc": occurred,
            "price": selected_price,
            "exchange_price": exchange_price,
            "price_basis": ("selected_short_probability" if outcome_side == "short" else "long_probability"),
            "quantity": _number(trade.get("qtyDecimal") or trade.get("qty"), None),
            "cost_basis_usd": _amount_value(trade.get("costBasis")),
            "fee_usd": _amount_value(
                trade.get("fee") or trade.get("fees") or trade.get("feeAmount") or trade.get("feePaid")
            ),
            "realized_pnl_usd": selected_pnl,
            "exchange_realized_pnl_usd": exchange_pnl,
            "pnl_basis": (
                "terminal_short_outcome_adjustment" if selected_pnl != exchange_pnl else "exchange_reported"
            ),
            "state": str(trade.get("state") or ""),
            "is_aggressor": trade.get("isAggressor"),
            "outcome_side": outcome_side,
            "model_pick": linked,
        }
    if resolution:
        slug = str(resolution.get("marketSlug") or "")
        occurred = str(resolution.get("updateTime") or "")
        outcome_side = _activity_outcome_side(resolution)
        before = resolution.get("beforePosition") or {}
        after = resolution.get("afterPosition") or {}
        metadata = after.get("marketMetadata") or before.get("marketMetadata") or {}
        outcome_side, linked = _activity_link(slug, outcome_side, links)
        before_realized = _amount_value(before.get("realized"))
        after_realized = _amount_value(after.get("realized"))
        realized_delta = after_realized
        if before_realized is not None and after_realized is not None:
            realized_delta = round(after_realized - before_realized, 6)
        return {
            "activity_id": f"settlement:{resolution.get('tradeId') or slug + ':' + occurred}",
            "type": "settlement",
            "market_slug": slug,
            "title": str(metadata.get("title") or slug),
            "outcome": str(metadata.get("outcome") or ""),
            "occurred_at_utc": occurred,
            "resolution_side": str(resolution.get("side") or "").removeprefix("POSITION_RESOLUTION_SIDE_"),
            "outcome_side": outcome_side,
            "before_quantity": _number(before.get("netPositionDecimal") or before.get("netPosition"), None),
            "after_quantity": _number(after.get("netPositionDecimal") or after.get("netPosition"), None),
            "realized_pnl_usd": realized_delta,
            "cumulative_realized_pnl_usd": after_realized,
            "pnl_basis": "position_realized_delta",
            "model_pick": linked,
        }
    return None


def _load_portfolio_history() -> dict:
    payload = _read_json(PORTFOLIO_HISTORY_FILE) or {}
    activities = payload.get("activities") if isinstance(payload, dict) else None
    history_start = (
        str(payload.get("history_start_date") or _today()) if isinstance(payload, dict) else _today()
    )
    rows = [
        item
        for item in (list(activities) if isinstance(activities, list) else [])
        if _activity_on_or_after(item, history_start)
    ]
    return {
        "activities": rows,
        "last_synced_at_utc": payload.get("last_synced_at_utc") if isinstance(payload, dict) else None,
        "history_start_date": history_start,
    }


def _activity_on_or_after(item: dict, history_start: str) -> bool:
    try:
        occurred = datetime.fromisoformat(str(item.get("occurred_at_utc") or ""))
        return occurred.astimezone(EASTERN).date().isoformat() >= history_start
    except ValueError:
        return False


def _save_portfolio_history(activities: list[dict], observed_at: str) -> list[dict]:
    existing = _load_portfolio_history()
    prior = existing["activities"]
    history_start = existing["history_start_date"]
    merged = {
        str(item.get("activity_id")): item
        for item in [*prior, *activities]
        if item.get("activity_id") and _activity_on_or_after(item, history_start)
    }
    rows = sorted(merged.values(), key=lambda item: str(item.get("occurred_at_utc") or ""), reverse=True)[
        :2000
    ]
    DASH_DIR.mkdir(exist_ok=True)
    temporary = PORTFOLIO_HISTORY_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "history_start_date": history_start,
                "last_synced_at_utc": observed_at,
                "activities": rows,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, PORTFOLIO_HISTORY_FILE)
    return rows


def _team_name_index() -> dict[tuple[str, str], str]:
    def _build() -> dict[tuple[str, str], str]:
        registry = _read_json(DATA / "entities" / "teams.json") or {}
        index: dict[tuple[str, str], str] = {}
        for team in registry.get("teams") or []:
            league = str(team.get("league") or "").casefold()
            name = str(team.get("canonical_name") or "").strip()
            candidates = {
                str(team.get("abbreviation") or ""),
                str(team.get("canonical_team_id") or "").rsplit("-", 1)[-1],
            }
            for alias in team.get("aliases") or []:
                alias_name = str(alias.get("source_name") or "").strip()
                candidates.add(alias_name)
                words = re.findall(r"[A-Za-z0-9]+", alias_name)
                if len(words) > 1:
                    candidates.add("".join(word[0] for word in words))
            for candidate in candidates:
                token = re.sub(r"[^a-z0-9]", "", candidate.casefold())
                if token and name:
                    index.setdefault((league, token), name)
        return index

    return _cached("team-name-index", 300, _build)


def _public_market_question(slug: str) -> str | None:
    with _MARKET_QUESTION_LOCK:
        if slug in _MARKET_QUESTION_CACHE:
            return _MARKET_QUESTION_CACHE[slug]
    question: str | None = None
    try:
        request = urllib.request.Request(
            f"{GATEWAY}/v1/market/slug/{quote(slug, safe='-')}",
            headers={"User-Agent": "model-prediction-dashboard/2.0"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        question = str((payload.get("market") or {}).get("question") or "").strip() or None
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        question = None
    with _MARKET_QUESTION_LOCK:
        _MARKET_QUESTION_CACHE[slug] = question
    return question


def _human_market_name(slug: str, title: str = "", *, allow_lookup: bool = True) -> str:
    """Turn an exchange identifier into a compact, readable market name."""
    match = re.match(
        r"^(?P<prefix>[a-z]+)-(?P<league>[a-z0-9]+)-(?P<away>[a-z0-9]+)-"
        r"(?P<home>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<detail>.*))?$",
        slug.casefold(),
    )
    if not match:
        return title if title and title != slug and title != "None" else slug

    league = match.group("league")
    names = _team_name_index()
    away = names.get((league, match.group("away")), match.group("away").upper())
    home = names.get((league, match.group("home")), match.group("home").upper())
    prefix = match.group("prefix")
    detail = match.group("detail") or ""
    league_label = league.upper()
    matchup = f"{away} @ {home}"

    # These prefixes hide materially different contracts behind similar
    # slugs (team total vs match total, first half vs full game, 1X2, props).
    # The exchange question is the canonical, specific market name.
    if allow_lookup and prefix in {"tsc", "atc"}:
        question = _public_market_question(slug)
        if question:
            return question.removesuffix("?")

    if prefix == "aec":
        market = "First 5 moneyline" if detail.startswith("f5") else "Moneyline"
        return f"{league_label} · {matchup} · {market}"
    if prefix in {"tsc", "asc", "atc"}:
        line_match = re.search(r"(\d+)pt(\d+)", detail)
        line = f"{line_match.group(1)}.{line_match.group(2)}" if line_match else ""
        period = "First 5 " if "f5" in detail else ""
        market = {"tsc": "Total", "atc": "Team total", "asc": "Spread"}[prefix]
        suffix = f" {line}" if line else ""
        return f"{league_label} · {matchup} · {period}{market}{suffix}"
    if prefix == "astatc":
        question = _public_market_question(slug)
        if question:
            clean = question.removesuffix("?")
            clean = re.sub(r"\s+in\s+[A-Z0-9 .'-]+\s+vs\.?\s+[A-Z0-9 .'-]+$", "", clean)
            hrr = re.fullmatch(r"Will (.+?) record at least (\d+) hits \+ runs \+ RBIs", clean)
            if hrr:
                clean = f"{hrr.group(1)} · {hrr.group(2)}+ hits + runs + RBIs"
            return f"{clean} · {matchup}"
        return f"{league_label} · {matchup} · Player prop"
    return title if title and title != slug and title != "None" else f"{league_label} · {matchup}"


def _portfolio_history_summary(
    activities: list[dict],
    source: str,
    links: dict[tuple[str, str], dict] | None = None,
) -> dict:
    links = _live_model_links() if links is None else links

    def side_adjust(item: dict) -> dict:
        if item.get("type") != "trade":
            return item
        slug = str(item.get("market_slug") or "")
        outcome_side, linked = _activity_link(slug, str(item.get("outcome_side") or "") or None, links)
        if outcome_side != "short":
            return {**item, "outcome_side": outcome_side, "model_pick": linked}
        exchange_price = _number(item.get("exchange_price"), None)
        if exchange_price is None:
            stored_price = _number(item.get("price"), None)
            if stored_price is not None:
                exchange_price = (
                    1 - stored_price
                    if item.get("price_basis") == "selected_short_probability"
                    else stored_price
                )
        exchange_pnl = _number(item.get("exchange_realized_pnl_usd"), None)
        if exchange_pnl is None:
            stored_pnl = _number(item.get("realized_pnl_usd"), None)
            if stored_pnl is not None:
                exchange_pnl = (
                    -stored_pnl
                    if item.get("pnl_basis")
                    in {
                        "selected_short_inverse_of_exchange_long",
                        "terminal_short_outcome_adjustment",
                    }
                    else stored_pnl
                )
        return {
            **item,
            "price": round(1 - exchange_price, 6) if exchange_price is not None else None,
            "exchange_price": exchange_price,
            "price_basis": "selected_short_probability",
            "realized_pnl_usd": _selected_short_pnl(exchange_price, exchange_pnl),
            "exchange_realized_pnl_usd": exchange_pnl,
            "pnl_basis": (
                "terminal_short_outcome_adjustment"
                if _selected_short_pnl(exchange_price, exchange_pnl) != exchange_pnl
                else item.get("pnl_basis")
            ),
            "outcome_side": outcome_side,
            "model_pick": linked,
        }

    decorated = [
        {
            **side_adjust(item),
            "market_name": _human_market_name(
                str(item.get("market_slug") or ""), str(item.get("title") or "")
            ),
        }
        for item in activities
    ]
    trades = [item for item in decorated if item.get("type") == "trade"]
    settlements = [item for item in decorated if item.get("type") == "settlement"]
    realized = sum(
        value for item in decorated if (value := _number(item.get("realized_pnl_usd"), None)) is not None
    )
    return {
        "activities": decorated,
        "count": len(decorated),
        "trade_count": len(trades),
        "settlement_count": len(settlements),
        "realized_pnl_usd": round(realized, 2),
        "source": source,
    }


def live_portfolio_view() -> dict:
    """Exchange-confirmed positions and activity; model picks never count as exposure."""
    cached = _load_portfolio_history()
    empty_open = {
        "positions": [],
        "count": 0,
        "cost_basis_usd": 0.0,
        "cash_value_usd": 0.0,
        "realized_pnl_usd": 0.0,
    }
    missing = [name for name in ("POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY") if not os.environ.get(name)]
    if missing:
        return {
            "status": "unavailable",
            "error": f"missing {' and '.join(missing)}",
            "open": empty_open,
            "recent_history": _portfolio_history_summary(cached["activities"], "cached"),
            "last_synced_at_utc": cached["last_synced_at_utc"],
            "history_start_date": cached["history_start_date"],
        }
    try:
        process = subprocess.run(
            _resolve_runner() + ["live-portfolio"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=_runner_env(),
            check=False,
        )
        raw_text = process.stdout if process.returncode == 0 else process.stderr
        raw = json.loads(raw_text)
        if process.returncode != 0 or raw.get("status") != "live":
            raise RuntimeError(str(raw.get("error") or "authenticated portfolio request failed"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, RuntimeError) as error:
        return {
            "status": "unavailable",
            "error": str(error)[:300],
            "open": empty_open,
            "recent_history": _portfolio_history_summary(cached["activities"], "cached"),
            "last_synced_at_utc": cached["last_synced_at_utc"],
            "history_start_date": cached["history_start_date"],
        }

    links = _live_model_links()
    positions = []
    for slug, item in (raw.get("positions") or {}).items():
        net = _number(item.get("netPositionDecimal") or item.get("netPosition"), 0.0)
        if abs(net) < 1e-9:
            continue
        side = "long" if net > 0 else "short"
        metadata = item.get("marketMetadata") or {}
        cost = _amount_value(item.get("cost"))
        cash_value = _amount_value(item.get("cashValue"))
        quote = _live_bbo(str(slug)) or {}
        side_quote = quote.get(side) or {}
        bid = _number(side_quote.get("bid"), None)
        ask = _number(side_quote.get("ask"), None)
        mark = cash_value / abs(net) if cash_value is not None and abs(net) > 0 else None
        exit_default = (
            min(0.99, round(float(bid) + 0.01, 2))
            if bid is not None
            else min(0.99, max(0.01, round(float(mark or 0.5), 2)))
        )
        positions.append(
            {
                "market_slug": str(slug),
                "title": str(metadata.get("title") or slug),
                "market_name": _human_market_name(str(slug), str(metadata.get("title") or "")),
                "outcome": str(metadata.get("outcome") or ""),
                "side": side,
                "quantity": abs(net),
                "available_quantity": abs(
                    _number(item.get("qtyAvailableDecimal") or item.get("qtyAvailable"), 0.0)
                ),
                "cost_basis_usd": cost,
                "cash_value_usd": cash_value,
                "bid": bid,
                "ask": ask,
                "exit_limit_default": exit_default,
                "realized_pnl_usd": _amount_value(item.get("realized")),
                "unrealized_pnl_usd": (
                    round(cash_value - cost, 2) if cash_value is not None and cost is not None else None
                ),
                "expired": bool(item.get("expired")),
                "updated_at_utc": str(item.get("updateTime") or ""),
                "model_pick": links.get((str(slug), side)),
            }
        )
    positions.sort(key=lambda item: item["updated_at_utc"], reverse=True)
    normalized = [
        activity
        for item in (raw.get("activities") or [])
        if (activity := _normalize_live_activity(item, links)) is not None
    ]
    history = _save_portfolio_history(normalized, str(raw.get("observed_at_utc") or ""))
    balances = raw.get("balances") or []
    usd = next((item for item in balances if item.get("currency") == "USD"), None)
    return {
        "status": "live",
        "source": raw.get("source"),
        "observed_at_utc": raw.get("observed_at_utc"),
        "history_start_date": _load_portfolio_history()["history_start_date"],
        "open": {
            "positions": positions,
            "count": len(positions),
            "cost_basis_usd": round(sum(_number(item.get("cost_basis_usd")) for item in positions), 2),
            "cash_value_usd": round(sum(_number(item.get("cash_value_usd")) for item in positions), 2),
            "realized_pnl_usd": round(sum(_number(item.get("realized_pnl_usd")) for item in positions), 2),
        },
        "recent_history": _portfolio_history_summary(history, "exchange_and_persisted", links),
        # Every other USD amount this same authenticated API returns (trade
        # price/costBasis/realizedPnl/fee, position cost/cashValue/realized)
        # arrives as a {"value": ..., "currency": ...} envelope and is parsed
        # with _amount_value(), never bare _number() -- these four balance
        # fields were the one place still using _number(), which returns the
        # `default` (None here) on a dict instead of raising, so a real
        # envelope-shaped balance response would have silently rendered every
        # balance figure (and _auto_adjust_unit_value's bankroll-percent
        # sizing, which reads current_usd) as unavailable with no error
        # surfaced.
        # _amount_value() unwraps the envelope when present and still handles
        # a bare number, so this is a strict superset, not a behavior change,
        # for whichever shape the endpoint actually returns.
        "balance": {
            "current_usd": _amount_value((usd or {}).get("currentBalance")),
            "buying_power_usd": _amount_value((usd or {}).get("buyingPower")),
            "open_orders_usd": _amount_value((usd or {}).get("openOrders")),
            "unsettled_funds_usd": _amount_value((usd or {}).get("unsettledFunds")),
        },
    }


def bets_view() -> dict:
    """Backward-compatible route name for the authenticated live portfolio."""
    return live_portfolio_view()


def _model_version_rank(row: dict) -> tuple:
    """Sort key for choosing which duplicate to KEEP. Higher = keep.

    Prefers the numerically-newest model version (v3 > v2), then the most
    recently created row. Production models always outrank older ones.
    """
    version = str(row.get("model_version") or "")
    digits = "".join(ch for ch in version.split("-")[-1] if ch.isdigit())
    version_number = int(digits) if digits else 0
    return (version_number, str(row.get("created_at_utc") or ""))


def dedupe_ledger() -> dict:
    """Remove duplicate OPEN ledger rows through the audited ledger path.

    A duplicate = same contract identity (league/event/market/selection/line)
    logged under more than one model version or run. Keeps one row per
    identity — the newest model version — and removes the rest via
    ``PickLedger.remove_open_rows`` (ledger lock + ``pick_removed`` audit
    events). Settled rows are results and are never touched; staked open rows
    are never deleted. Every per-sport Main file (data/main/<sport>.xlsx)
    that exists is backed up first.
    """
    from model_prediction.main_ledgers import MultiSportPickLedger  # local: heavy import

    existing_main_paths = [path for path in _main_ledger_paths() if path.exists()]
    if not existing_main_paths:
        return {"status": "refused", "error": "no Main ledger files found under data/main/"}
    ledger = MultiSportPickLedger(DATA)
    try:
        rows = ledger.rows()
    except ValueError as error:
        return {"status": "refused", "error": str(error)}
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("status") != "open":
            continue
        groups.setdefault(_pick_identity(row), []).append(row)
    to_remove: list[str] = []
    for members in groups.values():
        if len(members) == 1:
            continue
        unstaked = [m for m in members if _number(m.get("units")) <= 0]
        if not unstaked:
            continue
        survivor = max(unstaked, key=_model_version_rank)
        to_remove.extend(str(m.get("pick_id") or "") for m in unstaked if m is not survivor)
    if not to_remove:
        return {"status": "ok", "removed": 0, "kept": len(rows), "note": "No open duplicate contracts found."}
    import shutil

    stamp = int(time.time())
    backups = []
    for path in existing_main_paths:
        backup = path.with_suffix(f".xlsx.dedupe-bak-{stamp}")
        shutil.copy2(path, backup)
        backups.append(backup.name)
    removed_ids = ledger.remove_open_rows(to_remove, reason="dashboard duplicate-contract dedupe")
    # Prune archived ids that no longer exist so the counter stays honest.
    surviving = {str(r.get("pick_id")) for r in ledger.rows()}
    archive = _load_archive()
    archive["pick_ids"] = sorted(pid for pid in archive["pick_ids"] if pid in surviving)
    archive["history"].append(
        {"at": datetime.now(UTC).isoformat()[:19], "action": "dedupe", "rows": len(removed_ids)}
    )
    _save_archive(archive)
    with _CACHE_LOCK:
        _CACHE.clear()
        _PICKS_CACHE["mtime"] = None
    _log(f"dedupe: removed {len(removed_ids)} open duplicate rows, backups {', '.join(backups)}")
    return {
        "status": "ok",
        "removed": len(removed_ids),
        "kept": len(surviving),
        "backups": backups,
        "removed_pick_ids": removed_ids[:50],
        "note": f"Removed {len(removed_ids)} open duplicates via the audited ledger path. Backups: {', '.join(backups)}.",
    }


def _load_archive() -> dict:
    try:
        payload = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        return {"pick_ids": list(payload.get("pick_ids", [])), "history": list(payload.get("history", []))}
    except (OSError, json.JSONDecodeError):
        return {"pick_ids": [], "history": []}


def _save_archive(archive: dict) -> None:
    DASH_DIR.mkdir(exist_ok=True)
    ARCHIVE_FILE.write_text(json.dumps(archive, indent=1), encoding="utf-8")


def archive_action(action: str, scope: str) -> dict:
    """Persistently hide safe ledger rows from the dashboard table.

    picks.xlsx is never touched: archived rows keep feeding performance,
    calibration, backtests, and research. This is a display ledger-clear,
    not a data delete. Open rows with positive units are never archived.
    """
    archive = _load_archive()
    if action == "restore":
        restored = len(archive["pick_ids"])
        archive["pick_ids"] = []
        archive["history"].append(
            {"at": datetime.now(UTC).isoformat()[:19], "action": "restore", "rows": restored}
        )
        _save_archive(archive)
        with _CACHE_LOCK:
            _CACHE.clear()
        return {"status": "ok", "action": "restore", "restored": restored}
    if action == "clear_ids":
        requested = {str(pick_id) for pick_id in scope if str(pick_id)}
        if not requested:
            return {"status": "refused", "error": "no pick ids supplied"}
        # A visible ledger/Today row is DEDUPED across model versions, so its one
        # pick_id stands in for every sibling row sharing the same contract
        # identity. Expand each requested id to its whole identity group,
        # otherwise dedup resurrects the row from an un-archived sibling.
        all_rows = read_picks()
        identity_to_ids: dict[tuple, set[str]] = {}
        id_to_identity: dict[str, tuple] = {}
        for row in all_rows:
            pid = str(row.get("pick_id") or "")
            if not pid:
                continue
            identity = _pick_identity(row)
            identity_to_ids.setdefault(identity, set()).add(pid)
            id_to_identity[pid] = identity
        expanded: set[str] = set()
        for pid in requested:
            identity = id_to_identity.get(pid)
            expanded |= identity_to_ids.get(identity, {pid}) if identity else {pid}
        exposed = {
            str(row.get("pick_id"))
            for row in all_rows
            if row.get("status") == "open"
            and row.get("record_type") == "QUALIFIED_SHADOW_CALL"
            and float(row.get("units") or 0) > 0
        }
        blocked = sorted(expanded & exposed)
        allowed = expanded - exposed
        existing = set(archive["pick_ids"]) | allowed
        archive["pick_ids"] = sorted(existing)
        archive["history"].append(
            {"at": datetime.now(UTC).isoformat()[:19], "action": "clear_ids", "rows": len(allowed)}
        )
        _save_archive(archive)
        with _CACHE_LOCK:
            _CACHE.clear()
        return {
            "status": "ok",
            "action": "clear_ids",
            "archived_now": len(allowed),
            "rows_selected": len(requested),
            "blocked_open_staked": blocked,
            "archived_total": len(existing),
            "note": "View-only: rows remain in picks.xlsx and keep feeding research metrics.",
        }
    if action != "clear" or scope not in ("day", "week", "month", "all"):
        return {
            "status": "refused",
            "error": "action must be clear(day|week|month|all), clear_ids, or restore",
        }
    today = datetime.now(UTC).astimezone(EASTERN).date()
    days = {"day": 0, "week": 6, "month": 29}.get(scope)
    existing = set(archive["pick_ids"])
    added = protected = 0
    for row in read_picks():
        if (
            row.get("status") == "open"
            and row.get("record_type") == "QUALIFIED_SHADOW_CALL"
            and float(row.get("units") or 0) > 0
        ):
            protected += 1
            continue
        pick_id = str(row.get("pick_id"))
        if pick_id in existing:
            continue
        if days is not None:
            start = str(row.get("event_start_utc") or "")
            try:
                game_day = datetime.fromisoformat(start).astimezone(EASTERN).date()
            except ValueError:
                continue
            if (today - game_day).days > days or game_day > today:
                continue
        existing.add(pick_id)
        added += 1
    archive["pick_ids"] = sorted(existing)
    archive["history"].append(
        {"at": datetime.now(UTC).isoformat()[:19], "action": f"clear:{scope}", "rows": added}
    )
    _save_archive(archive)
    with _CACHE_LOCK:
        _CACHE.clear()
    return {
        "status": "ok",
        "action": f"clear:{scope}",
        "archived_now": added,
        "protected_open_staked": protected,
        "archived_total": len(existing),
        "note": "View-only: all rows remain in picks.xlsx and keep feeding research metrics.",
    }


def _suggested_units(row: dict) -> float | None:
    """Decision-time model size, reconstructed for both open and settled rows.

    (0.5U base + |p-0.5| * 10, capped at 2.0U, nearest 0.25U — the sizing that
    beat flat staking +34.1U vs +13.3U on the MLB walk-forward.) The immutable
    decision probability lets the dashboard retain the same displayed size
    after settlement. Every actual ledger stake stays 0 until a model is
    promoted past research.
    """
    try:
        p = float(row.get("model_probability") or 0)
        market = float(row.get("market_implied_probability") or 0)
    except (TypeError, ValueError):
        return None
    if not (0 < p < 1) or not (0 < market < 1):
        return None
    # Edge-scaled from model confidence, including negative-edge research rows,
    # so the sizing shown before and after settlement remains identical. The
    # +EV badge carries the tail/no-tail context.
    raw = 0.5 + abs(p - 0.5) * (2.0 - 0.5) / 0.15
    units = max(0.5, min(2.0, raw))
    return round(units / 0.25) * 0.25


def _audit_tail() -> dict:
    path = DATA / "events.jsonl"
    events = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        for line in lines[-25:]:
            try:
                item = json.loads(line)
                events.append(
                    {
                        "at": str(item.get("occurred_at_utc", ""))[:19],
                        "type": item.get("event_type"),
                        "subject": str(item.get("subject_id", ""))[:24],
                    }
                )
            except json.JSONDecodeError:
                continue
        return {"total_events": len(lines), "tail": list(reversed(events))}
    return {"total_events": 0, "tail": []}
