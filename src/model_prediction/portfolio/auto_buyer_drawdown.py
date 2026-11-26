"""Advisory drawdown report for the live auto-buyer cohorts (quick-261125-hih DD, operator D-06).

The live cohort pass never auto-quarantines (D-06). In its place, this report shows the operator each
live cohort's settled record, ROI and drawdown, and raises advisory warnings. A warning never stops
buying and is never an alarm: it is surfaced in ``last_run``, ``/api/auto-buyer/status`` and the
dashboard's auto-buyer panel.

``live_cohort_drawdown_report`` is the single entry point a later Telegram task calls.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..domain import iso_utc, parse_utc, utc_now

logger = logging.getLogger(__name__)

DEFAULT_DRAWDOWN_WARNING_USD = 100.0
DEFAULT_DRAWDOWN_WARNING_UNITS = 20.0
DRAWDOWN_WARNING_USD_KEY = "drawdown_warning_usd"
DRAWDOWN_WARNING_UNITS_KEY = "drawdown_warning_units"
# The old quarantine rule, now advisory: the last 30 settled rows' ROI below -10% with >= 15 settled.
LAST_N_SETTLED = 30
LAST_N_MIN_SETTLED = 15
LAST_N_ROI_WARNING = -0.10
_SETTLED_RESULTS = ("win", "loss", "push")
_EASTERN = ZoneInfo("America/New_York")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _threshold(state: dict[str, Any], key: str, default: float) -> float:
    raw = state.get(key)
    if raw is None or isinstance(raw, bool):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _approval_start(raw: Any) -> datetime | None:
    """The instant a cohort's rows start counting; None when the approval cannot be read.

    The operator writes a date (``"2026-11-25"``); that means from midnight Eastern, the operator's
    day. ``parse_utc`` rejects a date-only value, so it is handled here rather than silently dropping
    the filter.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if _DATE_ONLY.match(text):
        day = datetime.fromisoformat(text)
        return day.replace(tzinfo=_EASTERN).astimezone(UTC)
    try:
        return parse_utc(text)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _excluded(row: dict[str, Any]) -> bool:
    from .auto_buyer_ledger import is_manual_auto_buyer_record

    if is_manual_auto_buyer_record(row) or row.get("submit_unknown") is True:
        return True
    return str(row.get("result") or "").lower() == "void" and _float(row.get("shares")) == 0.0


def _drawdowns(series: list[float]) -> tuple[float, float]:
    """(max, current) peak-to-trough drawdown of a cumulative P&L series that starts at 0."""
    peak = total = worst = 0.0
    for step in series:
        total += step
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst, peak - total


def _cohort_report(
    cohort: Any,
    approved_raw: Any,
    rows: list[dict[str, Any]],
    *,
    default_unit: float,
    warn_usd: float,
    warn_units: float,
) -> dict[str, Any]:
    approved = _approval_start(approved_raw)
    matched: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("model_id") or "").strip() != cohort.model_id:
            continue
        if str(row.get("sport") or row.get("league") or "").strip().casefold() != cohort.sport:
            continue
        if str(row.get("market_type") or "").strip().lower() != cohort.market or _excluded(row):
            continue
        # Rows carry the label of the cohort they were bought under (the buyer's order payload);
        # one bought under another window of the same model is not this cohort's. Untagged rows
        # (written before the label reached the ledger) fall back to model, sport and market.
        tag = str(row.get("cohort") or "").strip()
        if tag and tag != cohort.label:
            continue
        if approved is not None:
            executed_text = str(row.get("executed_at_utc") or "").strip()
            try:
                if parse_utc(executed_text) < approved:
                    continue
            except (TypeError, ValueError, OverflowError):
                # A present-but-unreadable stamp is kept: this report is the loss alarm, so a row it
                # cannot date counts toward the drawdown rather than hiding a loss. A blank or missing
                # stamp keeps the old skip (the real writer always writes one; fixtures omit it).
                if not executed_text:
                    continue
        matched.append(row)

    def unit_of(row: dict[str, Any]) -> float:
        unit = _float(row.get("unit_value_usd"), default_unit)
        return unit if unit > 0 else default_unit

    settled = [r for r in matched if str(r.get("result") or "").lower() in _SETTLED_RESULTS]
    settled.sort(key=lambda r: (str(r.get("settled_at_utc") or ""), str(r.get("executed_at_utc") or "")))
    settled_ids = {id(r) for r in settled}
    open_rows = [
        r for r in matched if id(r) not in settled_ids and str(r.get("status") or "open").lower() == "open"
    ]
    pnl = [_float(r.get("pnl_usd")) for r in settled]
    pnl_units = [_float(r.get("pnl_usd")) / unit_of(r) for r in settled]
    cost = sum(_float(r.get("cost_usd")) for r in settled)
    last = settled[-LAST_N_SETTLED:]
    last_cost = sum(_float(r.get("cost_usd")) for r in last)
    last_roi = sum(_float(r.get("pnl_usd")) for r in last) / last_cost if last_cost > 0 else None
    max_dd, current_dd = _drawdowns(pnl)
    max_dd_u, current_dd_u = _drawdowns(pnl_units)
    current_dd, current_dd_u = round(current_dd, 2), round(current_dd_u, 2)

    warnings: list[str] = []
    if len(last) >= LAST_N_MIN_SETTLED and last_roi is not None and last_roi < LAST_N_ROI_WARNING:
        warnings.append(
            f"last-{LAST_N_SETTLED} ROI {last_roi:+.1%} over {len(last)} settled "
            f"(warning below {LAST_N_ROI_WARNING:.0%} with at least {LAST_N_MIN_SETTLED} settled)"
        )
    if current_dd >= warn_usd or current_dd_u >= warn_units:
        warnings.append(
            f"current drawdown ${current_dd:.2f} / {current_dd_u:.1f}U from peak "
            f"(warning at ${warn_usd:.2f} or {warn_units:.1f}U)"
        )
    total_pnl = sum(pnl)
    return {
        "label": cohort.label,
        "model_id": cohort.model_id,
        "sport": cohort.sport,
        "market": cohort.market,
        "approved_at_utc": approved_raw,
        "approval_filter": "none" if approved is None else iso_utc(approved),
        "rows": len(matched),
        "open": len(open_rows),
        "open_cost_usd": round(sum(_float(r.get("cost_usd")) for r in open_rows), 4),
        "wins": sum(1 for r in settled if str(r.get("result")).lower() == "win"),
        "losses": sum(1 for r in settled if str(r.get("result")).lower() == "loss"),
        "pushes": sum(1 for r in settled if str(r.get("result")).lower() == "push"),
        "settled": len(settled),
        "cost_usd": round(cost, 4),
        "pnl_usd": round(total_pnl, 4),
        "pnl_units": round(sum(pnl_units), 4),
        "roi": round(total_pnl / cost, 6) if cost > 0 else None,
        "last30_n": len(last),
        "last30_roi": round(last_roi, 6) if last_roi is not None else None,
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_units": round(max_dd_u, 2),
        "current_drawdown_usd": current_dd,
        "current_drawdown_units": current_dd_u,
        "warnings": warnings,
    }


def live_cohort_drawdown_report(
    state: dict[str, Any] | None = None,
    *,
    data_root: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per active live cohort: settled record, ROI, last-30 ROI and drawdown, with advisory warnings.

    Rows are the live ledger's rows of the cohort's model, sport and market executed on or after its
    ``approved_at_utc`` (a date means midnight Eastern). Void zero-fill, manual and submit_unknown rows
    are excluded. A warning fires when the last-30 settled ROI is below -10% with at least 15 settled,
    or when the CURRENT drawdown from peak reaches ``drawdown_warning_usd`` (state key, default 100)
    or ``drawdown_warning_units`` (state key, default 20). Current, not maximum, so the warning clears
    on recovery; the maximum is reported alongside. Never raises: an error returns ``status: error``.

    This is the single entry point for the Telegram alert task.
    """
    computed_at = iso_utc(now or utc_now())
    try:
        from . import auto_buyer_ledger, auto_executor

        if state is None:
            state = auto_executor.load_auto_buyer_state()
        warn_usd = _threshold(state, DRAWDOWN_WARNING_USD_KEY, DEFAULT_DRAWDOWN_WARNING_USD)
        warn_units = _threshold(state, DRAWDOWN_WARNING_UNITS_KEY, DEFAULT_DRAWDOWN_WARNING_UNITS)
        thresholds = {
            DRAWDOWN_WARNING_USD_KEY: warn_usd,
            DRAWDOWN_WARNING_UNITS_KEY: warn_units,
            "last30_roi": LAST_N_ROI_WARNING,
            "last30_min_settled": LAST_N_MIN_SETTLED,
        }
        active = set(auto_executor._parse_cohorts(state).active)
        entries: list[tuple[Any, Any]] = []
        raw_entries = state.get(auto_executor.COHORTS_STATE_KEY)
        for entry in raw_entries if isinstance(raw_entries, list) else []:
            cohort, _error = auto_executor._parse_one_cohort(entry)
            if (
                cohort is not None
                and cohort.mode == "live"
                and cohort in active
                and all(cohort != seen for seen, _ in entries)
            ):
                entries.append((cohort, entry.get("approved_at_utc")))
        cohorts: list[dict[str, Any]] = []
        if entries:
            root = Path(data_root) if data_root is not None else auto_executor.DATA
            rows = auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=root / "auto_buyer_ledger.jsonl")
            default_unit = _threshold(state, "unit_value_usd", auto_buyer_ledger.AUTO_BUYER_UNIT_VALUE_USD)
            cohorts = [
                _cohort_report(
                    cohort,
                    approved,
                    rows,
                    default_unit=default_unit,
                    warn_usd=warn_usd,
                    warn_units=warn_units,
                )
                for cohort, approved in entries
            ]
        return {
            "status": "ok",
            "computed_at_utc": computed_at,
            "thresholds": thresholds,
            "cohorts": cohorts,
            "warnings": [f"{c['label']}: {w}" for c in cohorts for w in c["warnings"]],
        }
    except Exception as exc:  # an advisory report must never break a caller
        logger.warning("Live cohort drawdown report failed", exc_info=True)
        return {
            "status": "error",
            "computed_at_utc": computed_at,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "cohorts": [],
            "warnings": [],
        }
