"""Operator control of the live auto-buyer from Telegram (operator-approved 2026-11-26, "Full + cancel orders").

Runnable as ``python -m model_prediction.telegram_control <command>`` and printing ONE JSON object
``{ok, message, ...}`` (exit 0 when ok, else 1). The Telegram bot (outside this repo) runs it as a subprocess and
relays ``message``; every decision that matters lives here, so the bot stays a thin, stateless-but-for-one-code relay.

Commands (each maps onto the repo function the dashboard or the cancel worker already uses):

- ``status``: read-only, no network. Switches, caps, today's live and paper spend, the ledger's resting-order count.
- ``live-off``: ``toggle_live_trading(False)``. Real money off now; paper keeps running.
- ``pause``: ``toggle_auto_buyer(False)``, the master switch (stops paper and live).
- ``resume``: ``toggle_live_trading(False)`` FIRST, then ``toggle_auto_buyer(True)``. Paper back on, live stays off even
  if it was on before the pause.
- ``live-on --confirmed``: ``toggle_live_trading(True)``. Refuses without ``--confirmed`` (the bot passes it only after
  the operator echoes its one-time code) and while the master switch is off.
- ``orders``: read-only GET of the account's open orders, marked ledger-known vs manual.
- ``cancel ID``: cancels exactly ONE open order, by full id or a unique prefix, through the same per-id path the
  cancel worker uses (``auto_buyer_ledger._cancel_by_id``) under the same lock. No cancel-all, no loop.

Prices are percentages or cents, never American odds. Every mutating command appends audit lines to
``auto_buyer_telegram_audit.jsonl`` next to the auto-buyer state file, before and after it acts.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

from .daily_lock import acquire_lock, release_lock
from .domain import iso_utc, utc_now
from .runtime_paths import RuntimePaths

AUDIT_FILE_NAME = "auto_buyer_telegram_audit.jsonl"
# The cancel worker holds its lock for most of each 2-minute run when the exchange is slow; waiting this long
# for it keeps /cancel usable without ever cancelling alongside the worker.
CANCEL_LOCK_WAIT_SECONDS = 12.0
# A prefix shorter than this could match the only open order by accident ("/cancel D").
MIN_PREFIX = 4
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{4,64}")
# Kill-switch writes retry a transient failure (Windows sharing violation on the state file replace, a
# version conflict with the buyer's own save) instead of reporting a switch as unchanged.
TOGGLE_ATTEMPTS = 3
TOGGLE_RETRY_DELAY_SECONDS = 0.4
# ``fallback_verified_via`` values that show the exchange confirmed a resting GTD fallback; "none" means
# nothing did.
_FALLBACK_VERIFIED_VIA = frozenset({"order_readback", "open_orders"})
_MUTATING = frozenset({"live-off", "pause", "resume", "live-on", "cancel"})
USAGE = "usage: status | live-off | pause | resume | live-on --confirmed | orders | cancel ID"

_sleep = time.sleep


@dataclass(frozen=True)
class _Context:
    paths: RuntimePaths
    executor_module: ModuleType
    ledger_module: ModuleType


@dataclass(frozen=True)
class _Listing:
    """The open orders as read right now. ``scope`` is ``account`` when the account-wide read worked and
    ``partial`` when only the ledger's markets could be read, in which case an order missing from it proves
    nothing and a prefix match is not trusted."""

    orders: list[dict[str, Any]]
    scope: str
    note: str


def _fail(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message[:900], **extra}


def _ok(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "message": message, **extra}


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _context() -> _Context:
    # Resolve strictly BEFORE the buyer modules are imported: they bind DATA and the state file at import.
    paths = RuntimePaths.resolve(require_external_runtime=True)
    from .portfolio import auto_buyer_ledger, auto_executor

    bound = auto_executor.DATA
    if bound.resolve() != paths.runtime_root:
        raise RuntimeError(
            f"auto_executor is bound to {bound}, not the runtime root {paths.runtime_root}; refusing"
        )
    return _Context(paths, auto_executor, auto_buyer_ledger)


def _build_executor(paths: RuntimePaths) -> Any:
    """The exchange client, built like the ledger's own default executor. Credentials come from the
    environment or the repo ``.env`` (loaded by ``polymarket_execute``) and are never printed."""
    from .audit import AuditLog
    from .data_sources.polymarket_execute import PolymarketExecutor

    return PolymarketExecutor(audit=AuditLog(paths.runtime_root / "audit.jsonl"))


def _usd(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "not set"
    return f"${number:,.0f}" if number == int(number) else f"${number:,.2f}"


def _append_audit(paths: RuntimePaths, entry: dict[str, Any]) -> bool:
    try:
        path = paths.runtime_root / AUDIT_FILE_NAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return False
    return True


def _ledger_rows(ctx: _Context) -> list[dict[str, Any]]:
    return ctx.ledger_module.read_auto_buyer_ledger(
        jsonl_path=ctx.paths.runtime_root / "auto_buyer_ledger.jsonl"
    )


def _resting_ledger_rows(ctx: _Context, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unreconciled resting GTD fallbacks the ledger still believes are on the book."""
    abl = ctx.ledger_module
    return [
        r
        for r in rows
        if r.get("fallback_order_id")
        and not r.get("fallback_reconciled")
        and abl._is_resting_fallback_leg(r)
        and not r.get("cancel_abandoned_at_utc")
        and not abl._is_settled_auto_buyer_record(r)
    ]


def _raw_state(ctx: _Context) -> dict[str, Any] | None:
    return ctx.executor_module._read_raw_auto_buyer_state()


def _retry_toggle(ctx: _Context, call: Any, value: bool) -> dict[str, Any]:
    conflict = ctx.executor_module.StateVersionConflictError
    for attempt in range(TOGGLE_ATTEMPTS):
        try:
            return call(value)
        except (OSError, conflict):
            if attempt + 1 >= TOGGLE_ATTEMPTS:
                raise
            _sleep(TOGGLE_RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


def _toggle_live(ctx: _Context, value: bool) -> dict[str, Any]:
    return _retry_toggle(ctx, ctx.executor_module.toggle_live_trading, value)


def _toggle_master(ctx: _Context, value: bool) -> dict[str, Any]:
    return _retry_toggle(ctx, ctx.executor_module.toggle_auto_buyer, value)


def _kill_switch_note(state: dict[str, Any]) -> tuple[str, bool]:
    """(sentence about the resting-order cancels the switch ran, whether any cancel failed)."""
    summary = state.get("kill_switch_cancel")
    if not isinstance(summary, dict):
        return "", False
    if summary.get("status") == "error":
        return f" Cancelling resting live orders FAILED ({summary.get('error')}); check /orders.", True
    cancelled = len(summary.get("cancelled") or [])
    failed = len(summary.get("failed") or [])
    if failed:
        return f" Resting live orders cancelled: {cancelled}; {failed} NOT cancelled, check /orders.", True
    if cancelled:
        return f" Resting live orders cancelled: {cancelled}.", False
    return " No resting live orders were on the ledger.", False


# ------------------------------------------------------------------ status


def _cmd_status(ctx: _Context, _args: list[str]) -> dict[str, Any]:
    ae = ctx.executor_module
    ae._read_raw_auto_buyer_state()  # raises on an unreadable file: the loader would silently read defaults
    status = ae.auto_buyer_status()
    root = ctx.paths.runtime_root
    live_spend, live_pnl = ae._today_auto_buyer_totals(root, live_book=True)
    paper_spend, _paper_pnl = ae._today_auto_buyer_totals(root / "paper_auto_buyer", live_book=False)
    live_loss = abs(min(live_pnl, 0.0))
    caps = {
        "daily_spend_usd": status.get("max_daily_spend_usd"),
        "per_game_usd": status.get("max_game_stake_usd"),
        "daily_loss_usd": status.get("max_daily_loss_usd"),
    }
    resting = _resting_ledger_rows(ctx, _ledger_rows(ctx))
    enabled = status.get("enabled") is True
    live_enabled = status.get("live_enabled") is True
    active = bool(status.get("live_trading_active"))
    cohorts = [str(label) for label in status.get("live_cohorts") or []]
    lines = [
        f"Auto-buyer: {'ENABLED' if enabled else 'PAUSED (master switch off)'}",
        f"Real money switch: {'ON' if live_enabled else 'OFF'}",
        f"Live trading active: {'YES' if active else 'no'}"
        + (f" ({', '.join(cohorts)})" if cohorts else " (no live cohort)"),
        (
            f"Live today: {_usd(round(live_spend, 2))} spent of {_usd(caps['daily_spend_usd'])}; "
            f"loss {_usd(round(live_loss, 2))} of {_usd(caps['daily_loss_usd'])}; "
            f"per-game cap {_usd(caps['per_game_usd'])}"
        ),
        f"Paper today: {_usd(round(paper_spend, 2))} spent (same {_usd(caps['daily_spend_usd'])} cap)",
        f"Resting live orders (ledger-known): {len(resting)}. /orders reads the exchange.",
    ]
    return _ok(
        "\n".join(lines),
        enabled=enabled,
        live_enabled=live_enabled,
        live_trading_active=active,
        live_cohorts=cohorts,
        caps=caps,
        live_spend_today_usd=round(live_spend, 2),
        live_loss_today_usd=round(live_loss, 2),
        paper_spend_today_usd=round(paper_spend, 2),
        resting_live_orders_ledger=len(resting),
    )


# ------------------------------------------------------------------ switches


def _cmd_live_off(ctx: _Context, _args: list[str]) -> dict[str, Any]:
    try:
        state = _toggle_live(ctx, False)
    except Exception as exc:  # noqa: BLE001 -- reported: the switch is NOT confirmed off
        return _fail(f"Real money was NOT confirmed off ({_error_text(exc)}). Check /status now.")
    raw = _raw_state(ctx)
    if raw is None or ctx.ledger_module.live_switch_on(raw):
        return _fail("ALARM: the state file still reads real money ON after /liveoff. Check /status now.")
    note, cancel_failed = _kill_switch_note(state)
    return _ok(
        "Real money OFF. Paper keeps running." + note,
        live_enabled=False,
        live_trading_active=False,
        cancel_failed=cancel_failed,
    )


def _cmd_pause(ctx: _Context, _args: list[str]) -> dict[str, Any]:
    try:
        state = _toggle_master(ctx, False)
    except Exception as exc:  # noqa: BLE001 -- reported: the switch is NOT confirmed off
        return _fail(f"The buyer was NOT confirmed paused ({_error_text(exc)}). Check /status now.")
    raw = _raw_state(ctx)
    if raw is None or raw.get("enabled") is True:
        return _fail("ALARM: the state file still reads the buyer ENABLED after /pause. Check /status now.")
    note, cancel_failed = _kill_switch_note(state)
    return _ok(
        "Auto-buyer paused: paper and live are both off."
        + note
        + " /resume brings paper back (live stays off).",
        enabled=False,
        live_trading_active=False,
        cancel_failed=cancel_failed,
    )


def _cmd_resume(ctx: _Context, _args: list[str]) -> dict[str, Any]:
    if _raw_state(ctx) is None:
        return _fail("Refused: there is no auto-buyer state file to resume.")
    # Live goes off BEFORE the master comes on, so no moment exists with both on that the operator did not ask for.
    try:
        _toggle_live(ctx, False)
    except Exception as exc:  # noqa: BLE001 -- the master stays as it was
        return _fail(f"Not resumed: real money could not be switched off first ({_error_text(exc)}).")
    raw = _raw_state(ctx)
    if raw is None or raw.get("live_enabled") is not False:
        return _fail("Not resumed: the state file does not read real money OFF after the first step.")
    try:
        _toggle_master(ctx, True)
    except Exception as exc:  # noqa: BLE001 -- reported; live is already off
        return _fail(f"Resume failed ({_error_text(exc)}). Real money is OFF; check /status.")
    raw = _raw_state(ctx)
    if raw is None or ctx.ledger_module.live_switch_on(raw):
        with contextlib.suppress(Exception):
            _toggle_live(ctx, False)
        return _fail("ALARM: real money read ON after /resume; it was switched off again. Check /status now.")
    return _ok(
        "Paper auto-buyer back ON. Real money is still OFF: use /liveon for real money.",
        enabled=True,
        live_enabled=False,
        live_trading_active=False,
    )


def _cmd_live_on(ctx: _Context, args: list[str]) -> dict[str, Any]:
    if "--confirmed" not in args:
        return _fail("Refused: live-on needs --confirmed. Real money stays off.")
    ae = ctx.executor_module
    raw = _raw_state(ctx)
    # Strict `is True`, like the buyer: a string or a typo in the file reads as off.
    if raw is None or raw.get("enabled") is not True:
        return _fail("Refused: the master switch is off, so real money cannot go on. Send /resume first.")
    problem = ae._live_cap_problem(ae.load_auto_buyer_state())
    if problem:
        return _fail(f"Refused: {problem}.")
    already = ctx.ledger_module.live_switch_on(raw)
    if not already:
        try:
            _toggle_live(ctx, True)
        except Exception as exc:  # noqa: BLE001 -- reported: live stays as it was
            return _fail(f"Real money was NOT turned on ({_error_text(exc)}).")
        raw = _raw_state(ctx)
        if raw is None or not ctx.ledger_module.live_switch_on(raw):
            return _fail("Real money did not read ON after the write. Check /status.")
    status = ae.auto_buyer_status()
    caps = {
        "daily_spend_usd": status.get("max_daily_spend_usd"),
        "per_game_usd": status.get("max_game_stake_usd"),
        "daily_loss_usd": status.get("max_daily_loss_usd"),
    }
    cohorts = [str(label) for label in status.get("live_cohorts") or []]
    verified = any(
        str(r.get("fallback_verified_via") or "") in _FALLBACK_VERIFIED_VIA for r in _ledger_rows(ctx)
    )
    parts = [
        ("Real money was already ON." if already else "Real money is now ON.")
        + f" Caps: {_usd(caps['daily_spend_usd'])}/day spend, {_usd(caps['per_game_usd'])}/game, "
        f"{_usd(caps['daily_loss_usd'])}/day loss.",
        "Live cohorts: "
        + (", ".join(cohorts) if cohorts else "none active, so no real order will be placed.")
        + ".",
    ]
    if not verified:
        parts.append(
            "WARNING: the GTD fallback read-back has not yet been verified on a real order "
            "(no ledger row has fallback_verified_via set)."
        )
    return _ok(
        " ".join(parts),
        live_enabled=True,
        live_trading_active=bool(status.get("live_trading_active")),
        caps=caps,
        live_cohorts=cohorts,
        fallback_verified=verified,
    )


# ------------------------------------------------------------------ orders


def _price_cents(order: dict[str, Any]) -> float | None:
    """The price the order pays for ITS outcome, in cents. The exchange quotes ``price`` in the long
    token's coordinate, so a short intent's own price is the complement."""
    raw: Any = order.get("price")
    raw = raw.get("value") if isinstance(raw, dict) else raw
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if "SHORT" in str(order.get("intent") or "").upper():
        price = 1.0 - price
    return round(price * 100.0, 2)


def _order_quantity(order: dict[str, Any]) -> float | None:
    for key in ("leavesQuantity", "quantity"):
        try:
            value = float(order.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        return value
    return None


def _order_words(order: dict[str, Any]) -> str:
    intent = str(order.get("intent") or "").replace("ORDER_INTENT_", "").replace("_", " ")
    return intent or "?"


def _order_age_minutes(order: dict[str, Any], now: datetime) -> float | None:
    from .data_sources.polymarket_execute import _exchange_time

    created = _exchange_time(order.get("createTime"))
    return None if created is None else max(0.0, (now - created).total_seconds() / 60.0)


def _list_open_orders(ctx: _Context, executor: Any) -> _Listing:
    """Open orders from the account-wide read, unioned with a read of the ledger's resting markets.

    The repo only ever reads ``/v1/orders/open`` filtered by market slug, so the account-wide read is
    not proven to return everything: the union keeps every order the ledger knows about visible even if
    that read comes back empty, and a refused account read degrades to ``partial`` instead of lying.
    """
    ledger_slugs = sorted(
        {str(r["market_slug"]) for r in _resting_ledger_rows(ctx, _ledger_rows(ctx)) if r.get("market_slug")}
    )
    by_id: dict[str, dict[str, Any]] = {}
    scope = "account"
    note = ""
    account_error: Exception | None = None
    try:
        for order in executor.open_orders([]):
            if order.get("id"):
                by_id[str(order["id"])] = order
    except Exception as exc:  # noqa: BLE001 -- degrade to the ledger's markets below, or report
        account_error = exc
        scope = "partial"
        note = f"the account-wide read failed ({_error_text(exc)}); showing only the ledger's markets"
    if ledger_slugs:
        try:
            for order in executor.open_orders(ledger_slugs):
                if order.get("id"):
                    by_id.setdefault(str(order["id"]), order)
        except Exception as exc:  # the account read alone may still be complete
            if account_error is not None:
                raise
            note = f"the ledger-market read failed ({_error_text(exc)})"
    elif account_error is not None:
        raise account_error
    return _Listing(list(by_id.values()), scope, note)


def _short_ids(ids: list[str]) -> dict[str, str]:
    """The shortest prefix (at least MIN_PREFIX) of each id that no other listed id shares."""
    out: dict[str, str] = {}
    folded = [i.casefold() for i in ids]
    for full in ids:
        length = min(MIN_PREFIX, len(full))
        while length < len(full) and sum(1 for f in folded if f.startswith(full[:length].casefold())) > 1:
            length += 1
        out[full] = full[:length]
    return out


def _ledger_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        ids = [row.get("fallback_order_id"), row.get("order_id"), *(row.get("order_ids") or [])]
        for order_id in ids:
            if order_id:
                index.setdefault(str(order_id), row)
    return index


def _describe(order: dict[str, Any], row: dict[str, Any] | None) -> str:
    slug = str(order.get("marketSlug") or "?")
    if row is None:
        return slug
    team = row.get("away_team") if row.get("selection") == "away" else row.get("home_team")
    market = str(row.get("market_type") or "")
    line = row.get("line")
    return (
        " ".join(
            str(p)
            for p in (team or row.get("selection"), market, line if line is not None else "")
            if p != ""
        )
        + f" ({slug})"
    )


def _format_order(order: dict[str, Any], row: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    cents = _price_cents(order)
    quantity = _order_quantity(order)
    age = _order_age_minutes(order, now)
    return {
        "id": str(order.get("id")),
        "market_slug": order.get("marketSlug"),
        "intent": _order_words(order),
        "price_cents": cents,
        "quantity": quantity,
        "age_minutes": None if age is None else round(age, 1),
        "known": row is not None,
        "label": _describe(order, row),
    }


def _cmd_orders(ctx: _Context, _args: list[str]) -> dict[str, Any]:
    listing = _list_open_orders(ctx, _build_executor(ctx.paths))
    index = _ledger_index(_ledger_rows(ctx))
    now = utc_now()
    ordered = sorted(listing.orders, key=lambda o: str(o.get("createTime") or ""))
    shorts = _short_ids([str(o["id"]) for o in ordered])
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    for n, order in enumerate(ordered, start=1):
        item = {"n": n, **_format_order(order, index.get(str(order["id"])), now)}
        item["short_id"] = shorts[item["id"]]
        items.append(item)
        cents = "?" if item["price_cents"] is None else f"{item['price_cents']:g}"
        qty = "?" if item["quantity"] is None else f"{item['quantity']:g}"
        age = "?" if item["age_minutes"] is None else f"{item['age_minutes']:.0f}"
        lines.append(
            f"{n}. {item['short_id']} {item['intent']} {qty} sh @ {cents}¢, {age} min, "
            f"{'ledger' if item['known'] else 'manual'}\n   {item['label']}"
        )
    header = f"Open live orders: {len(items)}" + ("" if listing.scope == "account" else " (partial read)")
    if listing.note:
        header += f"\nNote: {listing.note}."
    body = "\n".join(lines) if lines else "None resting."
    footer = "\n/cancel <id> cancels exactly one order." if items else ""
    return _ok(f"{header}\n{body}{footer}", orders=items, scope=listing.scope, note=listing.note)


# ------------------------------------------------------------------ cancel


def _resolve_order(ident: str, listing: _Listing) -> tuple[dict[str, Any] | None, str]:
    """The ONE open order ``ident`` names, or (None, why not). Matches only against the fresh listing."""
    if not _ID_PATTERN.fullmatch(ident):
        return None, f"Refused: give one order id or a prefix of at least {MIN_PREFIX} letters or digits."
    wanted = ident.casefold()
    exact = [o for o in listing.orders if str(o.get("id")).casefold() == wanted]
    if len(exact) == 1:
        return exact[0], ""
    if listing.scope != "account":
        return None, (
            "Refused: the account-wide order read failed, so only a full order id can be cancelled "
            f"and {ident} is not among the orders I could read."
        )
    matches = [o for o in listing.orders if str(o.get("id")).casefold().startswith(wanted)]
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return (
            None,
            f"Refused: {ident} is ambiguous ({len(matches)} open orders start with it). Use more characters.",
        )
    return None, f"Refused: no open order matches {ident}. Nothing was cancelled."


def _read_state_once(executor: Any, order_id: str) -> str | None:
    """The order's state per the exchange, None when unreadable or absent."""
    try:
        snapshot = executor.order_snapshots([order_id])
    except Exception:  # noqa: BLE001 -- the caller reports an unverified cancel
        return None
    if snapshot.get("status") != "live":
        return None
    for order in snapshot.get("orders", []):
        if str(order.get("order_id")) == order_id:
            return str(order.get("order_state") or "").upper() or None
    return None


def _memoise_cancelled(ctx: _Context, order_id: str) -> str:
    """Tell the cancel worker this id is done, as it does for the ids it cancels itself. A failure only costs
    one redundant (harmless) cancel attempt on the worker's next run."""
    import model_prediction.auto_buyer_cancel_worker as worker

    path = ctx.paths.runtime_root / worker.MEMO_FILE_NAME
    now = utc_now()
    try:
        memo = worker._load_memo(path, now)
        not_found = worker._load_memo(path, now, worker.NOT_FOUND_KEY)
        memo[order_id] = iso_utc(now)
        not_found.pop(order_id, None)
        worker._save_memo(path, memo, not_found)
    except OSError as exc:
        return f" (cancel-worker memo not updated: {_error_text(exc)})"
    return ""


def _cmd_cancel(ctx: _Context, args: list[str]) -> dict[str, Any]:
    import model_prediction.auto_buyer_cancel_worker as worker

    ident = args[0].strip()
    lock = acquire_lock(ctx.paths.lock_root / worker.LOCK_FILE_NAME, wait_seconds=CANCEL_LOCK_WAIT_SECONDS)
    if lock is None:
        return _fail("Not cancelled: the cancel worker is running. Retry in a minute.")
    try:
        executor = _build_executor(ctx.paths)
        listing = _list_open_orders(ctx, executor)  # fresh read, now, not the earlier /orders
        order, why = _resolve_order(ident, listing)
        if order is None:
            return _fail(why)
        order_id = str(order["id"])
        slug = str(order.get("marketSlug") or "") or None
        described = _format_order(order, _ledger_index(_ledger_rows(ctx)).get(order_id), utc_now())
        cents = "?" if described["price_cents"] is None else f"{described['price_cents']:g}"
        qty = "?" if described["quantity"] is None else f"{described['quantity']:g}"
        what = f"{order_id} ({described['intent']} {qty} sh @ {cents}¢, {described['label']})"
        try:
            ctx.ledger_module._cancel_by_id(executor, order_id, slug)  # exactly one id, never a batch
        except Exception as exc:  # noqa: BLE001 -- reported, never retried
            return _fail(
                f"Cancel of {what} FAILED ({_error_text(exc)}). It may still be resting; check /orders."
            )
        state = _read_state_once(executor, order_id)
        if state is not None and state not in ctx.ledger_module._TERMINAL_ORDER_STATES:
            return _fail(
                f"Cancel of {what} was sent and accepted, but the order still reads {state}; "
                "NOT confirmed. Send /orders to check.",
                order_id=order_id,
                state=state,
            )
        note = _memoise_cancelled(ctx, order_id) if state is not None else ""
        suffix = (
            f" Exchange now reads {state}."
            if state is not None
            else " Accepted; the follow-up read was unavailable, send /orders to confirm."
        )
        return _ok(
            f"Cancelled {what}.{suffix} The ledger restates fills on its next reconcile.{note}",
            order_id=order_id,
            state=state,
        )
    finally:
        release_lock(lock)


# ------------------------------------------------------------------ dispatch


_HANDLERS = {
    "status": _cmd_status,
    "live-off": _cmd_live_off,
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "live-on": _cmd_live_on,
    "orders": _cmd_orders,
    "cancel": _cmd_cancel,
}


def _check_usage(command: str, args: list[str]) -> str | None:
    if command not in _HANDLERS:
        return f"Unknown command {command!r}. {USAGE}"
    if command == "cancel":
        return None if len(args) == 1 else f"cancel takes exactly one order id. {USAGE}"
    if command == "live-on":
        return None if all(a == "--confirmed" for a in args) else f"live-on takes only --confirmed. {USAGE}"
    return None if not args else f"{command} takes no arguments. {USAGE}"


def _audit_entry(command: str, args: list[str], result: dict[str, Any] | None, phase: str) -> dict[str, Any]:
    return {
        "utc": iso_utc(datetime.now(UTC)),
        "command": command,
        "args": args,
        "phase": phase,
        "result": result,
        "source": "telegram",
    }


def run_command(command: str, args: list[str]) -> dict[str, Any]:
    usage = _check_usage(command, args)
    if usage is not None:
        return _fail(usage)
    try:
        ctx = _context()
    except Exception as exc:  # noqa: BLE001 -- includes the fail-closed missing runtime root
        return _fail(_error_text(exc))
    mutating = command in _MUTATING
    if mutating:
        audited = _append_audit(ctx.paths, _audit_entry(command, args, None, "started"))
        # Only the command that ADDS risk is held to the audit trail; a kill switch or a cancel must never wait on it.
        if not audited and command == "live-on":
            return _fail("Refused: the audit log is not writable, so real money cannot be turned on.")
    try:
        # A dependency that prints would corrupt the one-JSON-object contract on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            result = _HANDLERS[command](ctx, args)
    except Exception as exc:  # noqa: BLE001 -- a command never raises into the bot
        result = _fail(f"{command} failed: {_error_text(exc)}")
    if mutating:
        _append_audit(
            ctx.paths,
            _audit_entry(
                command, args, {"ok": result.get("ok"), "message": result.get("message")}, "finished"
            ),
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    result = _fail(USAGE) if not args else run_command(args[0], args[1:])
    print(json.dumps(result, default=str), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
