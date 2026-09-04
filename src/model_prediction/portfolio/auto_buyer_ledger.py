"""Auto-Buyer Ledger: dedicated audit-backed ledger and log for auto-purchased shares.

Maintains an isolated, clean record of all automated Polymarket purchases:
1. data/auto_buyer_ledger.jsonl: Immutable streaming record of every auto-executed trade.
2. data/auto_buyer_picks.xlsx: Standardized Excel ledger for dashboard and spreadsheet viewing.
3. data/logs/auto_buyer.log: Verbose operational log for tracking executions and settlements.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from ..data_sources.espn import ESPNClient
from ..data_sources.polymarket_execute import ExecutionGateError
from ..domain import iso_utc, parse_utc, utc_now
from ..ledger import FIELDNAMES
from ..pricing import american_to_decimal
from ..runtime_paths import RuntimePaths
from ..xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

_paths = RuntimePaths.resolve()
DATA = _paths.repo_root / "data"
LOGS_DIR = DATA / "logs"

AUTO_BUYER_JSONL_PATH = DATA / "auto_buyer_ledger.jsonl"
AUTO_BUYER_XLSX_PATH = DATA / "auto_buyer_picks.xlsx"
AUTO_BUYER_LOG_PATH = LOGS_DIR / "auto_buyer.log"

logger = logging.getLogger("model_prediction.auto_buyer")

AUTO_BUYER_UNIT_VALUE_USD = 0.50  # 1U = 50 cents
AUTO_BUYER_TERMINAL_RESULTS = frozenset({"win", "loss", "push"})


def _usd_to_auto_buyer_units(value_usd: float, unit_value_usd: float = AUTO_BUYER_UNIT_VALUE_USD) -> float:
    return round(float(value_usd) / float(unit_value_usd), 4)


def _is_settled_auto_buyer_record(record: dict[str, Any]) -> bool:
    return (
        str(record.get("status") or "").lower() == "settled"
        or str(record.get("result") or "").lower() in AUTO_BUYER_TERMINAL_RESULTS
    )


def _uses_live_auto_buyer_ledger(path: Path) -> bool:
    """Return whether a writer targets the production Auto-Buyer ledger."""
    return path.resolve() == AUTO_BUYER_JSONL_PATH.resolve()


def _get_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("auto_buyer_logger")
    if not log.handlers:
        handler = logging.FileHandler(AUTO_BUYER_LOG_PATH, encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


def _probability_to_american(prob: float) -> int:
    p = max(0.01, min(0.99, float(prob)))
    if p >= 0.50:
        return round(-100.0 * p / (1.0 - p))
    return round(100.0 * (1.0 - p) / p)


def _extract_line_from_record(r: dict[str, Any]) -> float | None:
    """Extract over/under or spread line from record or market slug."""
    raw_line = r.get("line")
    if raw_line not in (None, ""):
        with suppress(ValueError, TypeError):
            return float(str(raw_line))
    slug = str(r.get("market_slug") or "")
    if not slug:
        return None
    m_pt = re.search(r"(neg-|pos-)?(\d+)pt(\d+)", slug)
    if m_pt:
        val = float(f"{m_pt.group(2)}.{m_pt.group(3)}")
        if m_pt.group(1) == "neg-":
            val = -val
        return val
    m_dot = re.search(r"(neg-|pos-)?(\d+)\.(\d+)", slug)
    if m_dot:
        val = float(f"{m_dot.group(2)}.{m_dot.group(3)}")
        if m_dot.group(1) == "neg-":
            val = -val
        return val
    parts = slug.split("-")
    if len(parts) >= 8:
        last = parts[-1]
        if last.isdigit():
            if parts[-2] == "neg":
                return -float(last)
            elif parts[-2] == "pos":
                return float(last)
            return float(last)
    return None


def log_auto_buyer_event(message: str, level: str = "INFO") -> None:
    """Append a human-readable operational message to data/logs/auto_buyer.log."""
    log = _get_logger()
    if level == "WARNING":
        log.warning(message)
    elif level == "ERROR":
        log.error(message)
    else:
        log.info(message)


def record_auto_buy_execution(
    order_payload: dict[str, Any],
    order_id: str | None = None,
    order_state: str = "FILLED",
    pick_row: dict[str, Any] | None = None,
    jsonl_path: Path | str | None = None,
    xlsx_path: Path | str | None = None,
) -> dict[str, Any]:
    """Record an auto-bought trade to both the JSONL and Excel auto-buyer ledgers."""
    j_path = Path(jsonl_path) if jsonl_path else AUTO_BUYER_JSONL_PATH
    x_path = Path(xlsx_path) if xlsx_path else AUTO_BUYER_XLSX_PATH
    j_path.parent.mkdir(parents=True, exist_ok=True)
    x_path.parent.mkdir(parents=True, exist_ok=True)

    pick = pick_row or {}
    oid = str(order_id or order_payload.get("order_id") or "")
    pid = str(order_payload.get("pick_id") or pick.get("pick_id") or "")
    sport = str(order_payload.get("sport") or pick.get("sport") or pick.get("league") or "UNKNOWN").upper()
    away = str(pick.get("away_team") or "")
    home = str(pick.get("home_team") or "")
    mtype = str(pick.get("market_type") or "moneyline")
    sel = str(order_payload.get("selection") or pick.get("selection") or "")
    slug = str(order_payload.get("market_slug") or pick.get("market_slug") or "")
    side = str(order_payload.get("token_side") or pick.get("token_side") or "long").lower()
    # `or`-chained defaulting treats a genuine 0.0 fill as falsy and falls
    # through to the pick's requested shares -- that would fabricate
    # phantom filled shares for a zero-fill primary order, so shares/cost
    # must check for presence explicitly instead.
    _shares_raw = order_payload.get("shares")
    if _shares_raw is None:
        _shares_raw = pick.get("shares")
    shares = float(_shares_raw) if _shares_raw is not None else 1.0
    price = float(order_payload.get("limit_price") or pick.get("market_implied_probability") or 0.50)
    _cost_raw = order_payload.get("cost_usd")
    cost = float(_cost_raw) if _cost_raw is not None else shares * price
    model_units = float(pick.get("units") or pick.get("display_units") or 1.0)
    unit_value_usd = float(order_payload.get("unit_value_usd") or AUTO_BUYER_UNIT_VALUE_USD)
    units = _usd_to_auto_buyer_units(cost, unit_value_usd)
    model_id = str(order_payload.get("model_id") or pick.get("model_id") or pick.get("model_version") or "")
    model_p = float(pick.get("model_probability") or 0.0)
    market_p = float(pick.get("market_probability") or pick.get("market_implied_probability") or price)
    edge = float(order_payload.get("edge") or (model_p - market_p))
    start_utc = str(order_payload.get("event_start_utc") or pick.get("event_start_utc") or "")
    exec_utc = str(order_payload.get("timestamp_utc") or iso_utc(utc_now()))
    line_val = _extract_line_from_record(pick) or _extract_line_from_record(order_payload)
    if line_val is None:
        line_val = _extract_line_from_record({"market_slug": slug})

    # A resting fallback order (see polymarket_execute.py's
    # ioc_fallback_resting) hasn't filled yet at record time -- shares/cost
    # here are only the confirmed primary IOC fill. fallback_order_id lets
    # reconcile_pending_auto_buyer_fallbacks() find this row later and add
    # in whatever the resting order actually fills; primary_filled_shares/
    # _cost_usd are the immutable baseline that reconciliation adds on top
    # of, so repeated reconciliation runs stay idempotent.
    fallback_order_id = order_payload.get("fallback_order_id") or None
    fallback_resting_shares = round(float(order_payload.get("fallback_resting_shares") or 0.0), 4)

    # JSONL specific record
    jsonl_record = {
        "order_id": oid,
        "order_ids": order_payload.get("order_ids") or [oid],
        "pick_id": pid,
        "executed_at_utc": exec_utc,
        "event_start_utc": start_utc,
        "sport": sport,
        "away_team": away,
        "home_team": home,
        "market_type": mtype,
        "selection": sel,
        "line": line_val,
        "market_slug": slug,
        "token_side": side,
        "shares": shares,
        "entry_price": round(price, 4),
        "cost_usd": round(cost, 4),
        "units": round(units, 2),
        "model_units": round(model_units, 2),
        "unit_value_usd": unit_value_usd,
        "model_id": model_id,
        "model_probability": round(model_p, 4),
        "market_probability": round(market_p, 4),
        "edge": round(edge, 4),
        "order_state": order_state,
        "status": "open",
        "result": "open",
        "pnl_usd": 0.0,
        "pnl_units": 0.0,
        "away_score": None,
        "home_score": None,
        "settled_at_utc": "",
        "rationale": f"Auto-Buyer order {oid} on {slug} ({side})",
        "fallback_order_id": fallback_order_id,
        "fallback_resting_shares": fallback_resting_shares,
        "fallback_reconciled": not bool(fallback_order_id),
        "fill_known": bool(order_payload.get("fill_known", True)),
        "primary_filled_shares": round(shares, 4),
        "primary_filled_cost_usd": round(cost, 4),
    }

    # 1. Append to JSONL log
    try:
        with j_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_record, sort_keys=True) + "\n")
    except OSError as err:
        logger.warning(f"Failed to append to auto_buyer_ledger.jsonl: {err}")

    # 2. Update Excel ledger
    try:
        existing_rows: list[dict[str, Any]] = []
        if x_path.exists():
            _, existing_rows = read_xlsx_rows(x_path)

        american = _probability_to_american(price)
        decimal_odds = american_to_decimal(american)

        xlsx_row: dict[str, Any] = {f: "" for f in FIELDNAMES}
        xlsx_row.update(
            {
                "pick_id": pid or oid,
                "created_at_utc": exec_utc,
                "event_start_utc": start_utc,
                "event_id": str(pick.get("event_id") or slug),
                "league": sport,
                "away_team": away,
                "home_team": home,
                "market_type": mtype,
                "selection": sel,
                "line": str(pick.get("line") or ""),
                "sportsbook": "polymarket_us",
                "american_odds": str(american),
                "decimal_odds": f"{decimal_odds:.4f}",
                "market_implied_probability": f"{market_p:.4f}",
                "model_probability": f"{model_p:.4f}",
                "model_uncertainty": "0.0",
                "edge": f"{edge:.4f}",
                "confidence_score": "0.0",
                "units": f"{units:.2f}",
                "model_version": model_id,
                "status": "open",
                "result": "",
                "away_score": "",
                "home_score": "",
                "probability_clv": "0.0",
                "pnl_units": "0.0",
                "settled_at_utc": "",
                "record_type": "decision",
                "decision": "take",
                "reason_code": "AUTO_BUYER_EXECUTION",
                "rationale": f"Auto-Buyer order {oid} ({shares} sh @ ${price:.2f}) on {slug} ({side})",
                "risks": "Auto-executed Polymarket US order",
                "ledger_schema_version": "4",
            }
        )

        existing_by_pick = {str(r.get("pick_id")): r for r in existing_rows}
        p_key = str(xlsx_row["pick_id"])
        if p_key in existing_by_pick:
            existing_by_pick[p_key].update(xlsx_row)
            all_rows = list(existing_by_pick.values())
        else:
            all_rows = [*existing_rows, xlsx_row]

        write_xlsx_rows_atomic(x_path, FIELDNAMES, all_rows)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
        logger.warning(f"Failed to update auto_buyer_picks.xlsx: {err}")

    # 3. Write to operational log
    if _uses_live_auto_buyer_ledger(j_path):
        log_auto_buyer_event(
            f"EXECUTED [{sport}] {away} @ {home} ({sel}) | Market: {slug} ({side}) | "
            f"{shares} shares @ ${price:.2f} (Cost: ${cost:.2f}) | Model: {model_id} | Edge: +{edge * 100:.1f}% | OrderID: {oid}"
        )

    return jsonl_record


def reconcile_pending_auto_buyer_fallbacks(
    data_root: Path | str | None = None,
    executor: Any | None = None,
) -> dict[str, Any]:
    """Resolve resting IOC-fallback orders left open by record_auto_buy_execution.

    A resting fallback (see polymarket_execute.py's ``ioc_fallback_resting``)
    isn't reconciled synchronously -- it may sit on the book for a while
    before it fills, gets cancelled, or expires. This walks every ledger row
    with an unreconciled ``fallback_order_id``, reads the exchange's
    authoritative order state via ``order_snapshots``, and:

    - adds whatever the resting order has filled (at its own recorded price,
      since a GTC fallback never chases -- it only ever rests at the
      original ticket price) on top of the immutable
      ``primary_filled_shares``/``primary_filled_cost_usd`` baseline, so
      repeated runs stay idempotent rather than double-counting;
    - marks the row reconciled once the exchange reports a terminal state
      (filled/canceled/expired/rejected);
    - cancels the resting order once its event has started and it is still
      open -- a stale resting limit has no business filling after pregame
      information is void, and a game that already started only becomes
      more so as it progresses.
    """
    root = Path(data_root) if data_root else DATA
    j_path = root / "auto_buyer_ledger.jsonl"
    x_path = root / "auto_buyer_picks.xlsx"
    empty = {"reconciled_filled": 0, "cancelled_expired": 0, "still_pending": 0, "errors": 0}
    if not j_path.exists():
        return empty

    records = read_auto_buyer_ledger(jsonl_path=j_path)
    pending = [
        r
        for r in records
        if (r.get("fallback_order_id") and not r.get("fallback_reconciled"))
        or (str(r.get("status", "")).lower() == "open" and r.get("fill_known") is False)
    ]
    if not pending:
        return empty

    live_executor = executor
    if live_executor is None:
        try:
            from ..audit import AuditLog
            from ..data_sources.polymarket_execute import PolymarketExecutor

            live_executor = PolymarketExecutor(audit=AuditLog(root / "audit.jsonl"))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return empty

    order_ids = sorted(
        {
            str(r["fallback_order_id"])
            for r in pending
            if r.get("fallback_order_id") and not r.get("fallback_reconciled")
        }
        | {
            str(r["order_id"])
            for r in pending
            if str(r.get("status", "")).lower() == "open"
            and r.get("fill_known") is False
            and r.get("order_id")
        }
    )
    try:
        snapshot = live_executor.order_snapshots(order_ids)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError):
        return {**empty, "still_pending": len(pending), "errors": len(pending)}
    if snapshot.get("status") != "live":
        return {**empty, "still_pending": len(pending)}
    by_id = {str(item.get("order_id")): item for item in snapshot.get("orders", [])}

    now = utc_now()
    terminal_states = {
        "ORDER_STATE_FILLED",
        "ORDER_STATE_CANCELED",
        "ORDER_STATE_EXPIRED",
        "ORDER_STATE_REJECTED",
    }
    reconciled_filled = cancelled_expired = still_pending = errors = 0
    changed = False

    for r in records:
        fid = r.get("fallback_order_id") or (r.get("order_id") if r.get("fill_known") is False else None)
        if not fid:
            continue
        if r.get("fallback_reconciled") and r.get("fill_known") is not False:
            continue
        fid = str(fid)
        order = by_id.get(fid)
        if order is None:
            still_pending += 1
            continue

        state = str(order.get("order_state") or "").upper()
        try:
            fallback_filled = max(0.0, float(order.get("cum_quantity") or 0.0))
        except (TypeError, ValueError):
            fallback_filled = 0.0

        # If resting fallback was placed with capped resting shares
        if r.get("fallback_resting_shares") is not None and r.get("fallback_order_id") != r.get("order_id"):
            fallback_filled = min(fallback_filled, float(r.get("fallback_resting_shares") or 0.0))

        primary_shares = float(r.get("primary_filled_shares") or 0.0)
        primary_cost = float(r.get("primary_filled_cost_usd") or 0.0)
        entry_price = float(r.get("entry_price") or 0.0)

        if r.get("fill_known") is False and r.get("fallback_order_id") == r.get("order_id"):
            new_shares = round(fallback_filled, 4)
            new_cost = round(fallback_filled * entry_price, 4)
        else:
            new_shares = round(primary_shares + fallback_filled, 4)
            new_cost = round(primary_cost + fallback_filled * entry_price, 4)

        if new_shares != r.get("shares") or new_cost != r.get("cost_usd"):
            r["shares"] = new_shares
            r["cost_usd"] = new_cost
            r["primary_filled_shares"] = new_shares
            r["primary_filled_cost_usd"] = new_cost
            try:
                unit_value = float(r.get("unit_value_usd") or AUTO_BUYER_UNIT_VALUE_USD)
            except (TypeError, ValueError):
                unit_value = AUTO_BUYER_UNIT_VALUE_USD
            if unit_value > 0:
                r["units"] = _usd_to_auto_buyer_units(new_cost, unit_value)
            changed = True

        is_terminal = state in terminal_states
        try:
            started = parse_utc(str(r.get("event_start_utc") or "")) <= now
        except ValueError:
            started = False

        if not is_terminal and started and r.get("fallback_order_id") != r.get("order_id"):
            try:
                live_executor.cancel(fid, user_command=True)
                is_terminal = True
                state = state or "ORDER_STATE_CANCELED"
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError):
                errors += 1

        if is_terminal:
            r["fallback_reconciled"] = True
            r["fill_known"] = True
            is_unknown_primary = r.get("fallback_order_id") == r.get("order_id")
            if is_unknown_primary:
                requested_total = float(r.get("fallback_resting_shares") or 0.0)
            else:
                requested_total = round(
                    primary_shares + float(r.get("fallback_resting_shares") or 0.0),
                    4,
                )
            if state == "ORDER_STATE_FILLED" or (new_shares + 1e-9 >= requested_total and new_shares > 0):
                r["order_state"] = "ORDER_STATE_FILLED"
            elif new_shares > 0:
                r["order_state"] = "ORDER_STATE_PARTIALLY_FILLED"
            else:
                r["order_state"] = state
                r["status"] = "settled"
                r["result"] = "void"
                r["pnl_usd"] = 0.0
                r["pnl_units"] = 0.0
                r["settled_at_utc"] = iso_utc(now)
            changed = True
            if fallback_filled > 0:
                reconciled_filled += 1
            else:
                cancelled_expired += 1
        else:
            still_pending += 1

    if changed:
        try:
            with j_path.open("w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, sort_keys=True) + "\n")
        except OSError as err:
            logger.warning(f"Failed to rewrite auto_buyer_ledger.jsonl during fallback reconciliation: {err}")
        try:
            if x_path.exists():
                _, existing_rows = read_xlsx_rows(x_path)
                by_pick = {str(row.get("pick_id")): row for row in existing_rows}
                for r in records:
                    row = by_pick.get(str(r.get("pick_id")))
                    if row is not None:
                        row["units"] = f"{float(r.get('units') or 1.0):.2f}"
                write_xlsx_rows_atomic(x_path, FIELDNAMES, list(by_pick.values()))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
            logger.warning(f"Failed to update auto_buyer_picks.xlsx during fallback reconciliation: {err}")

    return {
        "reconciled_filled": reconciled_filled,
        "cancelled_expired": cancelled_expired,
        "still_pending": still_pending,
        "errors": errors,
    }


def read_auto_buyer_ledger(
    jsonl_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Read all entries from the Auto-Buyer JSONL ledger."""
    j_path = Path(jsonl_path) if jsonl_path else AUTO_BUYER_JSONL_PATH
    if not j_path.exists():
        return []
    records = []
    with j_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _exchange_position_resolutions(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index exchange position resolutions by slug with authoritative winner.

    Polymarket supplies the winning LONG/SHORT side in ``positionResolution.side``.
    Its before/after ``realized`` values can both be zero even for a full loss,
    so zero realized delta is not evidence of a push.
    """
    resolutions: dict[str, dict[str, Any]] = {}
    for activity in snapshot.get("activities", []):
        if activity.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        details = activity.get("positionResolution") or {}
        before = details.get("beforePosition") or {}
        after = details.get("afterPosition") or {}
        metadata = before.get("marketMetadata") or after.get("marketMetadata") or {}
        slug = metadata.get("slug") or details.get("marketSlug")
        if not slug:
            continue

        def amount(value: Any) -> float | None:
            if isinstance(value, dict):
                value = value.get("value")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        before_realized = amount(before.get("realized"))
        after_realized = amount(after.get("realized"))
        realized_delta = after_realized
        if before_realized is not None and after_realized is not None:
            realized_delta = round(after_realized - before_realized, 6)
        resolution_side = str(details.get("side") or "").removeprefix("POSITION_RESOLUTION_SIDE_").lower()
        resolutions[str(slug)] = {
            "realized_usd": realized_delta,
            "winning_side": resolution_side if resolution_side in {"long", "short"} else None,
            "position_cost_usd": amount(before.get("cost")),
            "position_quantity": amount(before.get("netPositionDecimal") or before.get("netPosition")),
            "update_time": details.get("updateTime"),
            "winning_outcome": metadata.get("outcome"),
        }
    return resolutions


def _exchange_resolution_result(
    resolution: dict[str, Any] | None,
    token_side: str,
) -> str | None:
    """Grade a purchased side from an authenticated exchange resolution."""
    if resolution is None:
        return None
    winning_side = str(resolution.get("winning_side") or "").lower()
    if winning_side in {"long", "short"} and token_side in {"long", "short"}:
        return "win" if token_side == winning_side else "loss"
    realized_delta = resolution.get("realized_usd")
    if realized_delta is not None and abs(float(realized_delta)) > 1e-9:
        return "win" if float(realized_delta) > 0 else "loss"
    return None


def _exchange_settlement_cost(
    resolution: dict[str, Any] | None,
    shares: float,
    recorded_cost: float,
) -> float:
    """Use fee-inclusive exchange cost when it exactly matches this order."""
    if resolution is None:
        return recorded_cost
    position_cost = resolution.get("position_cost_usd")
    position_quantity = resolution.get("position_quantity")
    if position_cost is None or position_quantity is None:
        return recorded_cost
    if abs(abs(float(position_quantity)) - shares) > 1e-9:
        # The resolution aggregates more than this one order; do not assign
        # the full position cost to every ledger row.
        return recorded_cost
    return float(position_cost)


def backfill_auto_buyer_ledger_from_audit(
    audit_path: Path | str | None = None,
    picks_cache: list[dict[str, Any]] | None = None,
) -> int:
    """Backfill the dedicated Auto-Buyer ledger from existing audit log execution events."""
    a_path = Path(audit_path) if audit_path else DATA / "audit.jsonl"
    if not a_path.exists():
        return 0

    if picks_cache is None:
        from ..dashboard.picks import read_picks

        try:
            picks_cache = read_picks()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            picks_cache = []

    picks_by_id = {str(p.get("pick_id")): p for p in (picks_cache or [])}

    existing = {str(r.get("order_id")) for r in read_auto_buyer_ledger()}
    count = 0

    with a_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("event_type") != "order_executed":
                continue

            payload = event.get("payload") or {}
            oid = str(payload.get("order_id") or "")
            if not oid or oid in existing:
                continue

            subj_id = str(event.get("subject_id") or "")
            pick_row = picks_by_id.get(subj_id) or {}

            record_auto_buy_execution(
                order_payload={
                    "order_id": oid,
                    "pick_id": subj_id,
                    "market_slug": payload.get("market_slug"),
                    "token_side": payload.get("token_side") or payload.get("side"),
                    "limit_price": payload.get("price") or payload.get("exchange_price"),
                    "shares": payload.get("size_shares", 1.0),
                    "cost_usd": payload.get("estimated_cost_usd"),
                    "timestamp_utc": event.get("occurred_at_utc"),
                },
                order_id=oid,
                order_state="FILLED",
                pick_row=pick_row,
            )
            existing.add(oid)
            count += 1

    log_auto_buyer_event(f"Backfilled {count} historical auto-buy orders into auto_buyer_ledger.")
    return count


def settle_auto_buyer_ledger(
    data_root: Path | str | None = None,
    espn: ESPNClient | None = None,
    polymarket_executor: Any | None = None,
    polymarket_client: Any | None = None,
) -> dict[str, Any]:
    """Settle completed matches in the Auto-Buyer Ledger and compute realized PnL."""
    root = Path(data_root) if data_root else DATA
    j_path = root / "auto_buyer_ledger.jsonl"
    x_path = root / "auto_buyer_picks.xlsx"

    if not j_path.exists():
        return {
            "settled": 0,
            "pending": 0,
            "remaining_pending": 0,
            "newly_settled": 0,
            "corrected": 0,
            "reopened": 0,
            "changed": 0,
            "changes": [],
        }

    espn_client = espn or ESPNClient()
    now = utc_now()
    records = read_auto_buyer_ledger(jsonl_path=j_path)
    before_states = [dict(record) for record in records]
    settled_count = 0
    pending_count = 0

    updated_records: list[dict[str, Any]] = []

    pm_resolutions: dict[str, dict[str, Any]] = {}
    if polymarket_executor is not None:
        try:
            pm_resolutions.update(_exchange_position_resolutions(polymarket_executor.portfolio_snapshot()))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
    elif data_root is None:
        try:
            from ..audit import AuditLog
            from ..data_sources.polymarket_execute import PolymarketExecutor

            executor = PolymarketExecutor(audit=AuditLog(root / "audit.jsonl"))
            pm_resolutions.update(_exchange_position_resolutions(executor.portfolio_snapshot()))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass

    for r in records:
        sport = str(r.get("sport") or "").upper()
        away = str(r.get("away_team") or "")
        home = str(r.get("home_team") or "")
        sel = str(r.get("selection") or "").lower()
        price = float(r.get("entry_price") or 0.50)
        # `or`-chained defaulting treats a genuine 0.0 (voided/zero-fill) row
        # as falsy and substitutes 1.0 -- that resurrects a phantom position
        # (and phantom P&L) on the very next settle cycle for a row that was
        # deliberately voided, so presence must be checked explicitly.
        _raw_shares = r.get("shares")
        shares = float(_raw_shares) if _raw_shares is not None else 1.0
        _raw_cost = r.get("cost_usd")
        cost = float(_raw_cost) if _raw_cost is not None else shares * price
        units = float(r.get("units") or 1.0)
        try:
            unit_value_usd = float(r.get("unit_value_usd") or AUTO_BUYER_UNIT_VALUE_USD)
        except (TypeError, ValueError):
            unit_value_usd = AUTO_BUYER_UNIT_VALUE_USD
        if unit_value_usd <= 0:
            unit_value_usd = AUTO_BUYER_UNIT_VALUE_USD
        slug = str(r.get("market_slug") or "")

        # Auto-Buyer units are cash units, not the originating model's stake
        # label. Preserve that label separately while normalizing every old
        # and new row to its immutable execution-time unit value. The current
        # Auto-Buyer setting only sizes future orders.
        if r.get("model_units") is None:
            r["model_units"] = units
        r["unit_value_usd"] = unit_value_usd
        r["units"] = _usd_to_auto_buyer_units(cost, unit_value_usd)
        units = float(r["units"])
        if str(r.get("status") or "").lower() == "settled":
            r["pnl_units"] = _usd_to_auto_buyer_units(float(r.get("pnl_usd") or 0.0), unit_value_usd)

        # 0. Void / Zero-share guard: A voided row or a settled row with 0 shares has 0 PnL
        if r.get("result") == "void" or (
            _raw_shares is not None and shares <= 0.0 and str(r.get("status", "")).lower() == "settled"
        ):
            r["shares"] = 0.0
            r["cost_usd"] = 0.0
            r["units"] = 0.0
            r["status"] = "settled"
            r["result"] = "void"
            r["pnl_usd"] = 0.0
            r["pnl_units"] = 0.0
            if not r.get("settled_at_utc"):
                r["settled_at_utc"] = iso_utc(now)
            settled_count += 1
            updated_records.append(r)
            continue

        # 1. Re-verify already settled records against reality
        if r.get("status") == "settled":
            # Exchange resolution is authoritative for positions that have
            # already left the account. Recheck these first so a prior
            # zero-realized parsing error can repair a false push.
            exchange_resolution = pm_resolutions.get(slug)
            token_side = str(r.get("token_side") or "").lower()
            corrected_res = _exchange_resolution_result(exchange_resolution, token_side)
            if corrected_res is not None:
                assert exchange_resolution is not None
                settlement_cost = _exchange_settlement_cost(exchange_resolution, shares, cost)
                expected_pnl_usd = round(
                    shares - settlement_cost if corrected_res == "win" else -settlement_cost,
                    4,
                )
                if (
                    corrected_res != r.get("result")
                    or abs(float(r.get("pnl_usd") or 0.0) - expected_pnl_usd) > 1e-9
                ):
                    r["pnl_usd"] = expected_pnl_usd
                    r["pnl_units"] = _usd_to_auto_buyer_units(expected_pnl_usd, unit_value_usd)
                    r["result"] = corrected_res
                    r["settled_at_utc"] = str(exchange_resolution.get("update_time") or iso_utc(now))
                    logger.warning(
                        "Corrected exchange settlement for %s (%s): now %s from resolution %s",
                        r.get("order_id"),
                        slug,
                        corrected_res,
                        exchange_resolution,
                    )
                settled_count += 1
                updated_records.append(r)
                continue

            # Tennis re-verification
            if sport in ("TENNIS", "WTA", "ATP"):
                try:
                    from ..tennis_forward import TENNIS_TOURS

                    start_str = str(r.get("event_start_utc") or "")
                    s_dt = parse_utc(start_str) if start_str else now
                    game_day = s_dt.strftime("%Y%m%d")
                    t_match_found = False
                    for tour in TENNIS_TOURS:
                        sb = espn_client.scoreboard(tour, game_day)
                        for ev in sb.get("events", []):
                            for grp in ev.get("groupings", []):
                                for comp in grp.get("competitions", []):
                                    comps = comp.get("competitors", [])
                                    if len(comps) != 2:
                                        continue
                                    c1, c2 = comps[0], comps[1]
                                    n1 = str((c1.get("athlete") or {}).get("displayName", "")).casefold()
                                    n2 = str((c2.get("athlete") or {}).get("displayName", "")).casefold()
                                    if not n1 or not n2 or not home.casefold() or not away.casefold():
                                        continue
                                    if (home.casefold() in n1 or n1 in home.casefold()) and (
                                        away.casefold() in n2 or n2 in away.casefold()
                                    ):
                                        home_comp, away_comp = c1, c2
                                    elif (home.casefold() in n2 or n2 in home.casefold()) and (
                                        away.casefold() in n1 or n1 in away.casefold()
                                    ):
                                        home_comp, away_comp = c2, c1
                                    else:
                                        continue

                                    t_match_found = True
                                    is_comp = comp.get("status", {}).get("type", {}).get("completed", False)
                                    if not is_comp:
                                        # Match was erroneously marked settled before playing
                                        r["status"] = str(r.get("order_state") or "SUBMITTED").lower()
                                        r["result"] = "open"
                                        r["pnl_usd"] = 0.0
                                        r["pnl_units"] = 0.0
                                        r["settled_at_utc"] = ""
                                        r["away_score"] = None
                                        r["home_score"] = None
                                        pending_count += 1
                                        logger.warning(
                                            "Reverted premature tennis settlement for %s (%s @ %s): match is not completed",
                                            r.get("order_id"),
                                            away,
                                            home,
                                        )
                                    else:
                                        h_win = bool(home_comp.get("winner"))
                                        a_win = bool(away_comp.get("winner"))
                                        if (sel in ("away", "short") and a_win) or (
                                            sel in ("home", "long") and h_win
                                        ):
                                            corrected_res = "win"
                                        elif a_win or h_win:
                                            corrected_res = "loss"
                                        else:
                                            corrected_res = "push"

                                        if corrected_res != r.get("result"):
                                            if corrected_res == "win":
                                                pnl_usd = round(shares * (1.0 - price), 4)
                                                pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                                            elif corrected_res == "loss":
                                                pnl_usd = round(-cost, 4)
                                                pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                                            else:
                                                pnl_usd = 0.0
                                                pnl_units = 0.0
                                            r["result"] = corrected_res
                                            r["pnl_usd"] = pnl_usd
                                            r["pnl_units"] = pnl_units
                                            r["away_score"] = 1 if a_win else 0
                                            r["home_score"] = 1 if h_win else 0
                                            logger.warning(
                                                "Corrected tennis settlement for %s (%s @ %s): was %s, now %s",
                                                r.get("order_id"),
                                                away,
                                                home,
                                                r.get("result"),
                                                corrected_res,
                                            )
                                        settled_count += 1
                                    break
                                if t_match_found:
                                    break
                            if t_match_found:
                                break
                        if t_match_found:
                            break
                    if not t_match_found:
                        settled_count += 1
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                    settled_count += 1
                updated_records.append(r)
                continue

            # Totals and spreads re-verification
            mtype = str(r.get("market_type") or "moneyline").lower()
            a_sc = r.get("away_score")
            h_sc = r.get("home_score")
            if a_sc is not None and h_sc is not None and mtype in ("total", "spread"):
                line = _extract_line_from_record(r)
                if line is not None:
                    r["line"] = line
                    corrected_res = None
                    if mtype == "total":
                        tot = float(a_sc) + float(h_sc)
                        if tot > line:
                            corrected_res = "win" if sel == "over" else "loss"
                        elif tot < line:
                            corrected_res = "win" if sel == "under" else "loss"
                        else:
                            corrected_res = "push"
                    elif mtype == "spread":
                        raw_margin = (
                            (float(a_sc) - float(h_sc))
                            if sel in ("away", "short")
                            else (float(h_sc) - float(a_sc))
                        )
                        margin = raw_margin + line
                        corrected_res = "win" if margin > 0 else ("loss" if margin < 0 else "push")

                    if corrected_res and corrected_res != r.get("result"):
                        if corrected_res == "win":
                            pnl_usd = round(shares * (1.0 - price), 4)
                            pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                        elif corrected_res == "loss":
                            pnl_usd = round(-cost, 4)
                            pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                        else:
                            pnl_usd = 0.0
                            pnl_units = 0.0
                        r["result"] = corrected_res
                        r["pnl_usd"] = pnl_usd
                        r["pnl_units"] = pnl_units
                        logger.warning(
                            "Corrected auto-buyer settled record %s: was %s, now %s (score %s-%s, line %s)",
                            r.get("order_id"),
                            r.get("result"),
                            corrected_res,
                            a_sc,
                            h_sc,
                            line,
                        )
            settled_count += 1
            updated_records.append(r)
            continue

        start_str = str(r.get("event_start_utc") or "")
        try:
            start_dt = parse_utc(start_str)
        except (ValueError, TypeError):
            updated_records.append(r)
            continue

        if start_dt > now:
            pending_count += 1
            updated_records.append(r)
            continue

        # An unreconciled fallback (resting order, or an IOC fill we could
        # not observe) means `shares`/`cost_usd` are still provisional --
        # settling now would compute real win/loss P&L against a phantom
        # position. reconcile_pending_auto_buyer_fallbacks() runs before
        # this function in the daily settle path, but this guard is the
        # last line of defense if that ordering is ever violated or the
        # exchange order hasn't reached a terminal state yet.
        if r.get("fallback_order_id") and not r.get("fallback_reconciled"):
            pending_count += 1
            updated_records.append(r)
            continue
        if r.get("fill_known") is False:
            pending_count += 1
            updated_records.append(r)
            continue

        # Check completed outcome
        result = None
        away_score = None
        home_score = None

        # Check direct Polymarket exchange position resolution first (for esports & direct resolutions)
        if slug and slug in pm_resolutions:
            pm_res = pm_resolutions[slug]
            result = _exchange_resolution_result(
                pm_res,
                str(r.get("token_side") or "").lower(),
            )
        elif sport in ("TENNIS", "WTA", "ATP"):
            try:
                from ..tennis_forward import TENNIS_TOURS

                game_day = start_dt.strftime("%Y%m%d")
                for tour in TENNIS_TOURS:
                    sb = espn_client.scoreboard(tour, game_day)
                    for ev in sb.get("events", []):
                        for grp in ev.get("groupings", []):
                            for comp in grp.get("competitions", []):
                                if not comp.get("status", {}).get("type", {}).get("completed"):
                                    continue
                                comps = comp.get("competitors", [])
                                if len(comps) != 2:
                                    continue
                                c1, c2 = comps[0], comps[1]
                                n1 = str((c1.get("athlete") or {}).get("displayName", "")).casefold()
                                n2 = str((c2.get("athlete") or {}).get("displayName", "")).casefold()
                                if not n1 or not n2 or not home.casefold() or not away.casefold():
                                    continue
                                if (home.casefold() in n1 or n1 in home.casefold()) and (
                                    away.casefold() in n2 or n2 in away.casefold()
                                ):
                                    home_comp, away_comp = c1, c2
                                elif (home.casefold() in n2 or n2 in home.casefold()) and (
                                    away.casefold() in n1 or n1 in away.casefold()
                                ):
                                    home_comp, away_comp = c2, c1
                                else:
                                    continue

                                home_win = bool(home_comp.get("winner"))
                                away_win = bool(away_comp.get("winner"))
                                if (sel in ("away", "short") and away_win) or (
                                    sel in ("home", "long") and home_win
                                ):
                                    result = "win"
                                elif away_win or home_win:
                                    result = "loss"
                                else:
                                    result = "push"
                                away_score = 1 if away_win else 0
                                home_score = 1 if home_win else 0
                                break
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass
        elif sport in ("MLB", "WNBA", "NBA", "NFL", "SOCCER", "NCAAF"):
            try:
                game_day = start_dt.strftime("%Y%m%d")
                sb = espn_client.scoreboard(sport.lower(), game_day)
                for ev in sb.get("events", []):
                    comp = (ev.get("competitions") or [{}])[0]
                    if not comp.get("status", {}).get("type", {}).get("completed"):
                        continue
                    comps = comp.get("competitors", [])
                    if len(comps) != 2:
                        continue
                    c_map = {c.get("homeAway"): c for c in comps}
                    a_c, h_c = c_map.get("away"), c_map.get("home")
                    if not a_c or not h_c:
                        continue
                    a_team = str((a_c.get("team") or {}).get("displayName", "")).casefold()
                    h_team = str((h_c.get("team") or {}).get("displayName", "")).casefold()
                    if not a_team or not h_team or not home.casefold() or not away.casefold():
                        continue
                    if (away.casefold() in a_team or a_team in away.casefold()) and (
                        home.casefold() in h_team or h_team in home.casefold()
                    ):
                        a_sc = float(a_c.get("score") or 0)
                        h_sc = float(h_c.get("score") or 0)
                        away_score = int(a_sc)
                        home_score = int(h_sc)
                        mtype = str(r.get("market_type") or "moneyline").lower()
                        if mtype == "total":
                            line = _extract_line_from_record(r) or 0.0
                            r["line"] = line
                            tot = a_sc + h_sc
                            if tot > line:
                                result = "win" if sel == "over" else "loss"
                            elif tot < line:
                                result = "win" if sel == "under" else "loss"
                            else:
                                result = "push"
                        elif mtype == "spread":
                            line = _extract_line_from_record(r) or 0.0
                            r["line"] = line
                            raw_margin = (a_sc - h_sc) if sel in ("away", "short") else (h_sc - a_sc)
                            margin = raw_margin + line
                            if margin > 0:
                                result = "win"
                            elif margin < 0:
                                result = "loss"
                            else:
                                result = "push"
                        elif mtype == "moneyline":
                            if a_sc > h_sc:
                                result = "win" if sel in ("away", "short") else "loss"
                            elif h_sc > a_sc:
                                result = "win" if sel in ("home", "long") else "loss"
                            else:
                                result = "push"
                        break
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # Check Polymarket US gateway market resolution status fallback. This must
        # also run for scheduled settlement, which always supplies ``data_root``.
        # The recorded exchange side is authoritative: binary outcome index 0 is
        # long and index 1 is short. Team-name matching is unsafe because a model
        # selection can differ from the side that was actually purchased.
        if result is None and slug:
            try:
                from ..data_sources.polymarket_us import PolymarketUSClient

                pm_cli = polymarket_client or PolymarketUSClient()
                m_info = pm_cli.market(slug)
                if m_info.get("status") == "MARKET_STATUS_RESOLVED":
                    raw_pxs = m_info.get("outcomePrices")
                    prices = json.loads(raw_pxs) if isinstance(raw_pxs, str) else (raw_pxs or [])
                    terminal_winners = [
                        index for index, raw_price in enumerate(prices) if abs(float(raw_price) - 1.0) <= 1e-9
                    ]
                    token_side = str(r.get("token_side") or "").lower()
                    if len(prices) == 2 and len(terminal_winners) == 1 and token_side in {"long", "short"}:
                        winning_side = "long" if terminal_winners[0] == 0 else "short"
                        result = "win" if token_side == winning_side else "loss"
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        if result is not None:
            settled_count += 1
            exchange_resolution = pm_resolutions.get(slug)
            settlement_cost = _exchange_settlement_cost(exchange_resolution, shares, cost)
            if result == "win":
                pnl_usd = round(shares - settlement_cost, 4)
                pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
            elif result == "loss":
                pnl_usd = round(-settlement_cost, 4)
                pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
            else:
                pnl_usd = 0.0
                pnl_units = 0.0

            r["status"] = "settled"
            r["result"] = result
            r["pnl_usd"] = pnl_usd
            r["pnl_units"] = pnl_units
            r["settled_at_utc"] = iso_utc(now)
            r["away_score"] = away_score
            r["home_score"] = home_score

            if _uses_live_auto_buyer_ledger(j_path):
                log_auto_buyer_event(
                    f"SETTLED [{sport}] {away} @ {home} | Result: {result.upper()} | PnL: ${pnl_usd:+.2f} ({pnl_units:+.1f}U) | Order: {r.get('order_id')}"
                )
        else:
            pending_count += 1

        updated_records.append(r)

    # Rewrite JSONL atomically
    try:
        with j_path.open("w", encoding="utf-8") as f:
            for r in updated_records:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    except OSError as err:
        logger.warning(f"Failed to rewrite auto_buyer_ledger.jsonl: {err}")

    # Rewrite Excel atomically
    try:
        xlsx_rows = []
        for r in updated_records:
            american = _probability_to_american(float(r.get("entry_price") or 0.50))
            decimal_odds = american_to_decimal(american)
            line_val = r.get("line")
            line_str = f"{float(str(line_val)):.2f}" if line_val not in (None, "") else ""
            x_row = {f: "" for f in FIELDNAMES}
            x_row.update(
                {
                    "pick_id": str(r.get("pick_id") or r.get("order_id")),
                    "created_at_utc": str(r.get("executed_at_utc") or ""),
                    "event_start_utc": str(r.get("event_start_utc") or ""),
                    "event_id": str(r.get("market_slug") or ""),
                    "league": str(r.get("sport") or ""),
                    "away_team": str(r.get("away_team") or ""),
                    "home_team": str(r.get("home_team") or ""),
                    "market_type": str(r.get("market_type") or ""),
                    "selection": str(r.get("selection") or ""),
                    "line": line_str,
                    "sportsbook": "polymarket_us",
                    "american_odds": str(american),
                    "decimal_odds": f"{decimal_odds:.4f}",
                    "market_implied_probability": f"{float(r.get('market_probability') or 0.5):.4f}",
                    "model_probability": f"{float(r.get('model_probability') or 0.5):.4f}",
                    "edge": f"{float(r.get('edge') or 0.0):.4f}",
                    "units": f"{float(r.get('units') or 1.0):.2f}",
                    "model_version": str(r.get("model_id") or ""),
                    "status": str(r.get("status") or "open"),
                    "result": str(r.get("result") or ""),
                    "away_score": str(r.get("away_score") or ""),
                    "home_score": str(r.get("home_score") or ""),
                    "pnl_units": f"{float(r.get('pnl_units') or 0.0):.2f}",
                    "settled_at_utc": str(r.get("settled_at_utc") or ""),
                    "record_type": "decision",
                    "decision": "take",
                    "reason_code": "AUTO_BUYER_EXECUTION",
                    "rationale": str(r.get("rationale") or ""),
                    "ledger_schema_version": "4",
                }
            )
            xlsx_rows.append(x_row)

        write_xlsx_rows_atomic(x_path, FIELDNAMES, xlsx_rows)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
        logger.warning(f"Failed to update auto_buyer_picks.xlsx: {err}")

    changes: list[dict[str, Any]] = []
    newly_settled = 0
    corrected = 0
    reopened = 0
    for before, after in zip(before_states, updated_records):
        before_result = str(before.get("result") or "").lower()
        after_result = str(after.get("result") or "").lower()
        before_terminal = _is_settled_auto_buyer_record(before)
        after_terminal = _is_settled_auto_buyer_record(after)
        state_changed = any(
            before.get(field) != after.get(field)
            for field in ("status", "result", "pnl_usd", "settled_at_utc")
        )
        if not state_changed:
            continue
        if not before_terminal and after_terminal:
            newly_settled += 1
        elif before_terminal and not after_terminal:
            reopened += 1
        elif before_terminal and after_terminal and before_result != after_result:
            corrected += 1
        changes.append(
            {
                "order_id": str(after.get("order_id") or ""),
                "market_slug": str(after.get("market_slug") or ""),
                "selection": str(after.get("selection") or ""),
                "from_status": str(before.get("status") or ""),
                "to_status": str(after.get("status") or ""),
                "from_result": before_result,
                "to_result": after_result,
                "pnl_usd": float(after.get("pnl_usd") or 0.0),
            }
        )

    remaining_pending = sum(not _is_settled_auto_buyer_record(record) for record in updated_records)
    if pending_count != remaining_pending:
        logger.warning(
            "Settlement traversal counted %d pending rows; authoritative ledger count is %d",
            pending_count,
            remaining_pending,
        )

    return {
        "settled": settled_count,
        "pending": remaining_pending,
        "remaining_pending": remaining_pending,
        "newly_settled": newly_settled,
        "corrected": corrected,
        "reopened": reopened,
        "changed": len(changes),
        "changes": changes,
    }


def summarize_auto_buyer_performance(
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute consolidated performance metrics, ROI, and win-rate accounting for Auto-Buyer."""
    rows = records if records is not None else read_auto_buyer_ledger()
    settled_rows = [r for r in rows if _is_settled_auto_buyer_record(r)]
    open_rows = [r for r in rows if r not in settled_rows]

    wins = sum(1 for r in settled_rows if r.get("result") == "win")
    losses = sum(1 for r in settled_rows if r.get("result") == "loss")
    pushes = sum(1 for r in settled_rows if r.get("result") == "push")

    total_cost_usd = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    settled_cost_usd = sum(float(r.get("cost_usd") or 0.0) for r in settled_rows)
    realized_pnl_usd = sum(float(r.get("pnl_usd") or 0.0) for r in settled_rows)
    realized_pnl_units = sum(
        _usd_to_auto_buyer_units(
            float(r.get("pnl_usd") or 0.0),
            float(r.get("unit_value_usd") or AUTO_BUYER_UNIT_VALUE_USD),
        )
        for r in settled_rows
    )

    roi_pct = (realized_pnl_usd / settled_cost_usd * 100.0) if settled_cost_usd > 0 else 0.0
    win_rate_pct = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    avg_edge_pct = (
        sum(float(r.get("edge") or 0.0) for r in settled_rows) / len(settled_rows) * 100.0
        if settled_rows
        else 0.0
    )

    return {
        "total_orders": len(rows),
        "open_orders": len(open_rows),
        "settled_orders": len(settled_rows),
        "record": f"{wins}W - {losses}L" + (f" - {pushes}P" if pushes else ""),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate_pct": round(win_rate_pct, 1),
        "realized_pnl_usd": round(realized_pnl_usd, 2),
        "realized_pnl_units": round(realized_pnl_units, 2),
        "realized_roi_pct": round(roi_pct, 1),
        "settled_cost_usd": round(settled_cost_usd, 2),
        "total_cost_usd": round(total_cost_usd, 2),
        "avg_edge_pct": round(avg_edge_pct, 1),
    }
