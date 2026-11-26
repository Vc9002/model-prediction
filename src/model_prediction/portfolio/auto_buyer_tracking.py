"""Tracking records for the auto-buyer's cohort books: live order attempts and cohort skips.

Accounting only. Nothing here may change a buy decision or stop a cycle: every write is best
effort and swallows its own failure, and the fill-log wrapper re-raises the buy path's exceptions
unchanged. Each record carries the game's tip time in US Eastern time, its side of the 5 PM ET cut
and its saved-edge bucket, so the pre-registered afternoon test
(docs/research/ncaab_spread_v7_afternoon_test.md) can be recomputed from the records alone.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..data_sources.polymarket_execute import ExecutionGateError, OrderSubmitUnknownError
from ..domain import iso_utc, parse_utc, utc_now

_logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
# The pre-registered afternoon/evening cut. A 17:00:00 tip is evening.
AFTERNOON_CUTOFF_ET = clock_time(17, 0)
BEFORE_CUTOFF = "before_5pm_et"
AT_OR_AFTER_CUTOFF = "5pm_or_later_et"
# Saved-edge buckets; the live cohorts' 5-12% band is the 5-8% and 8-12% buckets together.
EDGE_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 0.02, "0-2%"),
    (0.02, 0.05, "2-5%"),
    (0.05, 0.08, "5-8%"),
    (0.08, 0.12, "8-12%"),
    (0.12, 0.25, "12-25%"),
)
PREREGISTERED_BAND = (0.05, 0.12)

LIVE_FILL_LOG_NAME = "auto_buyer_live_fills.jsonl"
SKIP_LOG_NAME = "auto_buyer_skip_reasons.jsonl"
# The started gate counts a stale open row twice; the second name says why it was skipped.
_SECONDARY_REASONS = frozenset({"rejected_stale_open"})
# Exchange order fields that may carry an average fill price, a fee or a fill time. The US API
# documents none of them for the order response, so each is read only when present.
_AVG_PRICE_KEYS = ("avgPx", "avgPrice", "averagePrice", "avg_price")
_FEE_KEYS = ("fee", "fees", "totalFee", "commission")
_TRANSACT_KEYS = ("transactTime", "updateTime", "lastUpdateTime")


def tracking_log_path(data_root: Path | str, name: str) -> Path:
    """``<data_root>/logs/<name>``; the caller passes the runtime root it already resolved."""
    return Path(data_root) / "logs" / name


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def tip_fields(event_start_utc: Any) -> dict[str, Any]:
    """Tip time in UTC and US Eastern time, its ET date, and its side of the 5 PM ET cut.

    An unparseable start gives nulls, never a guessed window.
    """
    try:
        start = parse_utc(str(event_start_utc or ""))
    except (TypeError, ValueError):
        return {"tip_utc": None, "tip_et": None, "tip_date_et": None, "tip_window": None}
    eastern = start.astimezone(EASTERN)
    return {
        "tip_utc": iso_utc(start),
        "tip_et": eastern.isoformat(),
        "tip_date_et": eastern.date().isoformat(),
        "tip_window": BEFORE_CUTOFF if eastern.time() < AFTERNOON_CUTOFF_ET else AT_OR_AFTER_CUTOFF,
    }


def edge_bucket(edge: Any) -> str | None:
    value = _finite(edge)
    if value is None:
        return None
    if value < 0:
        return "<0%"
    for low, high, label in EDGE_BUCKETS:
        if low <= value < high:
            return label
    return ">=25%"


def in_preregistered_band(edge: Any) -> bool:
    value = _finite(edge)
    return value is not None and PREREGISTERED_BAND[0] <= value < PREREGISTERED_BAND[1]


def append_jsonl_records(path: Path | str, records: Iterable[dict[str, Any]]) -> bool:
    """Append records as JSON lines in one write. Never raises; False means nothing was written."""
    try:
        lines = "".join(json.dumps(record, default=str, sort_keys=True) + "\n" for record in records)
        if not lines:
            return True
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(lines)
        return True
    except Exception:  # tracking must never reach the buy path
        _logger.warning("auto-buyer tracking write to %s failed", path, exc_info=True)
        return False


# ---------------------------------------------------------------- live fill log


def _exchange_order(sub: dict[str, Any]) -> dict[str, Any]:
    raw = sub.get("raw_response")
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("order")
    return nested if isinstance(nested, dict) else raw


def _first(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if source.get(key) not in (None, ""):
            return source[key]
    return None


def _fill_path(sub: dict[str, Any], requested: float | None) -> str:
    if sub.get("fill_known") is False:
        return "unknown"
    filled = _finite(sub.get("filled_size_shares")) or 0.0
    resting = bool(sub.get("fallback_order_id")) and str(sub.get("fallback_status") or "") == "resting"
    if requested and filled + 1e-9 >= requested:
        return "ioc_full"
    if filled > 0:
        return "ioc_partial_plus_fallback" if resting else "ioc_partial"
    return "fallback_only" if resting else "ioc_none"


def _fill_record(
    *,
    outcome: str,
    order_payload: dict[str, Any],
    pick_row: dict[str, Any],
    ticket: Any,
    sub: dict[str, Any] | None,
    error: BaseException | None,
    sent_utc: datetime,
    returned_utc: datetime,
    latency_ms: float,
) -> dict[str, Any]:
    sub = sub or {}
    order = _exchange_order(sub)
    requested = _finite(getattr(ticket, "size_shares", None))
    filled = _finite(sub.get("filled_size_shares")) if sub else None
    avg_price = _finite(sub.get("avg_fill_price"))
    if avg_price is None:
        avg_price = _finite(_first(order, _AVG_PRICE_KEYS))
    fee = _finite(sub.get("fee_usd"))
    if fee is None:
        fee = _finite(_first(order, _FEE_KEYS))
    saved_edge = _finite(pick_row.get("edge"))
    transact = _first(order, _TRANSACT_KEYS)
    return {
        "record_type": "live_order_attempt",
        "outcome": outcome,
        "error": None if error is None else f"{type(error).__name__}: {error}"[:500],
        "pick_id": order_payload.get("pick_id"),
        "ticket_pick_id": order_payload.get("ticket_pick_id"),
        "is_topup": order_payload.get("is_topup"),
        "cohort": order_payload.get("cohort"),
        "model_id": order_payload.get("model_id"),
        "sport": order_payload.get("sport"),
        "market_type": order_payload.get("market_type"),
        "selection": order_payload.get("selection"),
        "line": order_payload.get("line"),
        "market_slug": getattr(ticket, "market_slug", order_payload.get("market_slug")),
        "token_side": getattr(ticket, "token_side", order_payload.get("token_side")),
        "order_type": getattr(ticket, "order_type", None),
        "ioc_fallback_resting": getattr(ticket, "ioc_fallback_resting", None),
        "resting_expires_at_utc": getattr(ticket, "resting_expires_at_utc", None),
        "limit_price": _finite(getattr(ticket, "price", order_payload.get("limit_price"))),
        "requested_shares": requested,
        "requested_cost_usd": _finite(order_payload.get("cost_usd")),
        "saved_edge": saved_edge,
        "live_edge": _finite(order_payload.get("edge")),
        "model_probability": _finite(order_payload.get("model_probability")),
        "edge_bucket": edge_bucket(saved_edge),
        "in_preregistered_band": in_preregistered_band(saved_edge),
        "order_id": sub.get("order_id"),
        "order_ids": sub.get("order_ids") or ([sub["order_id"]] if sub.get("order_id") else []),
        "order_state": sub.get("order_state"),
        "fill_known": sub.get("fill_known") if sub else None,
        "filled_shares": filled,
        "fill_rate": round(filled / requested, 4) if filled is not None and requested else None,
        # limit x filled shares, which is what the executor reports; an exchange average, when the
        # response carries one, is avg_fill_price.
        "filled_cost_at_limit_usd": _finite(sub.get("estimated_filled_cost_usd")),
        "avg_fill_price": avg_price,
        "fee_usd": fee,
        "fallback_order_id": sub.get("fallback_order_id"),
        "fallback_status": sub.get("fallback_status"),
        "fallback_resting_shares": _finite(sub.get("fallback_resting_shares")),
        "fallback_error": sub.get("fallback_error"),
        "good_till_time": sub.get("good_till_time"),
        "fill_path": _fill_path(sub, requested) if sub else None,
        "intent_id": sub.get("intent_id") or getattr(error, "intent_id", None),
        "idempotency_key": sub.get("idempotency_key") or getattr(error, "idempotency_key", None),
        "pick_decision_utc": pick_row.get("created_at_utc") or pick_row.get("observed_at_utc"),
        "pick_quote_observed_utc": pick_row.get("market_quote_observed_at_utc"),
        "buyer_decision_utc": order_payload.get("timestamp_utc"),
        "sent_utc": iso_utc(sent_utc),
        "returned_utc": iso_utc(returned_utc),
        "submit_latency_ms": round(latency_ms, 1),
        "exchange_transact_utc": transact,
        "event_start_utc": order_payload.get("event_start_utc"),
        **tip_fields(order_payload.get("event_start_utc")),
    }


def _outcome_of(error: BaseException) -> str:
    if isinstance(error, OrderSubmitUnknownError):
        return "submit_unknown"
    if isinstance(error, ExecutionGateError):
        return "refused"
    return "error"


def _log_fill(log_path: Path | str, **fields: Any) -> None:
    try:
        append_jsonl_records(log_path, [_fill_record(**fields)])
    except Exception:  # a broken record must never reach the buy path
        _logger.warning("auto-buyer live fill record failed", exc_info=True)


def execute_with_fill_log(
    execute: Callable[..., dict[str, Any]],
    *,
    log_path: Path | str,
    order_payload: dict[str, Any],
    source_row: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Call ``execute(**kwargs)`` and append one fill-log record for the attempt.

    The result is returned and any exception re-raised unchanged; the record is written either
    way and its own failure is swallowed.
    """
    ticket = kwargs.get("ticket")
    sent = utc_now()
    started = time.monotonic()
    try:
        sub = execute(**kwargs)
    except Exception as error:
        _log_fill(
            log_path,
            outcome=_outcome_of(error),
            order_payload=order_payload,
            pick_row=source_row,
            ticket=ticket,
            sub=None,
            error=error,
            sent_utc=sent,
            returned_utc=utc_now(),
            latency_ms=(time.monotonic() - started) * 1000,
        )
        raise
    _log_fill(
        log_path,
        outcome=str((sub or {}).get("status") or "submitted"),
        order_payload=order_payload,
        pick_row=source_row,
        ticket=ticket,
        sub=sub if isinstance(sub, dict) else {},
        error=None,
        sent_utc=sent,
        returned_utc=utc_now(),
        latency_ms=(time.monotonic() - started) * 1000,
    )
    return sub


# ---------------------------------------------------------------- cohort skip reasons


def _rejection_counters(result: Any) -> dict[str, int]:
    return {name: value for name, value in vars(result).items() if name.startswith("rejected_")}


class CohortSkipRecorder:
    """Per-pick reason a cohort row was not bought in one pass, from the result's counters.

    ``observe`` runs at the top of every loop iteration (and ``finish`` once after the loop): a
    rejected_* counter that moved since the previous cohort row was observed names that row's
    reason, so the buyer's ~35 rejection sites need no edit. A cohort row that moved no counter was
    bought (or the pass stopped on it) and is not a skip. Records are kept in memory; the cycle
    writes them, so a preview or a directly built buyer has no filesystem side effect.
    """

    def __init__(self, *, book: str, enabled: bool, now: datetime) -> None:
        self.book = book
        self.enabled = enabled
        self.now = now
        self.records: list[dict[str, Any]] = []
        self._pending: tuple[dict[str, Any], Any] | None = None
        self._before: dict[str, int] = {}
        self._notes: dict[str, Any] = {}

    def observe(self, row: dict[str, Any] | None, cohort: Any, result: Any) -> None:
        try:
            if self._pending is not None:
                after = _rejection_counters(result)
                reasons = sorted(name for name, value in after.items() if value > self._before.get(name, 0))
                if reasons:
                    self.records.append(self._record(*self._pending, reasons))
            self._pending = None
            self._notes = {}
            if self.enabled and row is not None and cohort is not None:
                self._pending = (row, cohort)
                self._before = _rejection_counters(result)
        except Exception:  # tracking must never reach the buy path
            _logger.warning("auto-buyer skip record failed", exc_info=True)
            self._pending = None

    def note(self, **values: Any) -> None:
        if self._pending is not None:
            self._notes.update(values)

    def reject_all(
        self, rows: Iterable[dict[str, Any]], match: Callable[[dict[str, Any]], Any], reason: str
    ) -> None:
        """A whole pass rejected before its loop (the daily loss breaker)."""
        if not self.enabled:
            return
        try:
            for row in rows:
                if str(row.get("status") or "open").lower() != "open":
                    continue
                cohort = match(row)
                if cohort is not None:
                    self.records.append(self._record(row, cohort, [reason]))
        except Exception:  # tracking must never reach the buy path
            _logger.warning("auto-buyer skip record failed", exc_info=True)

    def finish(self, result: Any) -> None:
        self.observe(None, None, result)
        result.cohort_skips = self.records

    def _record(self, row: dict[str, Any], cohort: Any, reasons: list[str]) -> dict[str, Any]:
        primary = next((name for name in reasons if name not in _SECONDARY_REASONS), reasons[0])
        saved_edge = _finite(row.get("edge"))
        model_age = None
        try:
            created = parse_utc(str(row.get("created_at_utc") or row.get("observed_at_utc") or ""))
            model_age = round((self.now - created).total_seconds() / 60.0, 1)
        except (TypeError, ValueError):
            pass
        return {
            "record_type": "cohort_skip",
            "cycle_at_utc": iso_utc(self.now),
            "book": self.book,
            "cohort": getattr(cohort, "label", None),
            "pick_id": row.get("pick_id"),
            "model_id": row.get("model_id") or row.get("model_version"),
            "market_type": row.get("market_type"),
            "selection": row.get("selection"),
            "line": row.get("line"),
            "reason": primary,
            "reasons": reasons,
            "saved_edge": saved_edge,
            "live_edge": _finite(self._notes.get("live_edge")),
            "ask": _finite(self._notes.get("ask")),
            "market_slug": self._notes.get("market_slug") or row.get("market_slug"),
            "model_age_minutes": model_age,
            "event_start_utc": row.get("event_start_utc"),
            "edge_bucket": edge_bucket(saved_edge),
            "in_preregistered_band": in_preregistered_band(saved_edge),
            **tip_fields(row.get("event_start_utc")),
        }
