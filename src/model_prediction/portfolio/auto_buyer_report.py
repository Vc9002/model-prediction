"""Daily auto-buyer report per cohort: buys, record, P&L, CLV at the pre-tip close, line moves,
skip reasons and the tip-time split, plus the paper twins' pick-for-pick comparison.

Read-only on the runtime root: the ledgers, the snapshot archive, ``ledgers.db``, the state file
and the tracking logs. ``write_auto_buyer_report`` writes only to the directory it is given.

CLV at the real close. The buyer's own ``closing_price`` is the first snapshot after the buy, which
the same cycle's pre-tip capture takes seconds later, so it is the entry price again. Here the close
is the last open-book quote of the bought contract (slug and side) observed strictly after the buy
and strictly before tip (the earlier of the row's start and the snapshot's own start), the rule of
``PolymarketSnapshotStore.closing_snapshot``. Every row says how long after the buy and before tip
its close was observed; ``close_independent_of_buy`` is true only for a close at least
``INDEPENDENT_CLOSE_SECONDS`` after the buy, and the summary reports CLV both ways.

The buy time is the moment the order was sent, not the ledger's ``executed_at_utc``: that is the
pass's start, and the pass then captures fresh snapshots (stamped at fetch time) before it posts, so
pre-order quotes would read as "after the buy". The anchor is the row's persisted
``submitted_at_utc``, else the fill log's ``sent_utc`` joined on the order id, else (old rows only)
the pass start, which ``buy_anchor_source`` names and which is never counted as independent. It is
floored at the observed time of the quote that priced the order. A resting fallback that filled
later has no recorded fill time, so it is anchored on its submit time and flagged.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import iso_utc, parse_utc, utc_now
from .auto_buyer_tracking import (
    AT_OR_AFTER_CUTOFF,
    BEFORE_CUTOFF,
    EASTERN,
    LEGACY_SKIP_LOG_LAST_DATE,
    LEGACY_SKIP_LOG_NAME,
    LIVE_FILL_LOG_NAME,
    edge_bucket,
    in_preregistered_band,
    skip_log_path,
    tip_fields,
    tracking_log_path,
)

# A close taken by the buy's own cycle lands seconds after the buy and measures nothing.
INDEPENDENT_CLOSE_SECONDS = 300.0
CLOSING_MAIN_LINE_REASON = (
    "each contract is one fixed line and the pre-tip capture snapshots only held contracts, so the "
    "closing main line is not observed"
)
_SLUG_PATTERN = re.compile(r'"market_slug":\s*"([^"]+)"')
_BOOKS = (
    ("live", Path("auto_buyer_ledger.jsonl")),
    ("paper", Path("paper_auto_buyer") / "auto_buyer_ledger.jsonl"),
)
_HISTORY = {
    "live": Path("logs") / "auto_buyer_cycle_history.jsonl",
    "paper": Path("paper_auto_buyer") / "logs" / "auto_buyer_cycle_history.jsonl",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time(value: Any) -> datetime | None:
    try:
        return parse_utc(str(value or ""))
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path, *, contains: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping without parsing every line that holds none of ``contains``.

    The tracking logs grow by one record per cohort row per cycle; the prefilter keeps a dashboard
    request from parsing all of them on a host that has run short of memory.
    """
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if contains and not any(token in line for token in contains):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line still being appended
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _cents(price: float | None, base: float | None) -> float | None:
    if price is None or base is None:
        return None
    return round((price - base) * 100.0, 2)


def _fill_records(runtime_root: Path, report_date: str) -> list[dict[str, Any]]:
    """The live fill log's records for games on ``report_date`` (an order's own tip date)."""
    return [
        r
        for r in _read_jsonl(tracking_log_path(runtime_root, LIVE_FILL_LOG_NAME), contains=(report_date,))
        if r.get("tip_date_et") == report_date
    ]


def _sent_by_order(records: Iterable[dict[str, Any]]) -> dict[str, datetime]:
    """Latest send time per exchange order id across the fill-log records."""
    sent: dict[str, datetime] = {}
    for record in records:
        when = _time(record.get("sent_utc"))
        if when is None:
            continue
        ids = [str(i) for i in record.get("order_ids") or [] if i]
        if record.get("order_id"):
            ids.append(str(record["order_id"]))
        for order_id in ids:
            if order_id not in sent or when > sent[order_id]:
                sent[order_id] = when
    return sent


def _buy_anchor(raw: dict[str, Any], sent: dict[str, datetime]) -> tuple[datetime | None, str]:
    """When the order was sent, and where that time came from (see the module doc)."""
    anchor: datetime | None
    submitted = _time(raw.get("submitted_at_utc"))
    if submitted is not None:
        anchor, source = submitted, "submitted"
    else:
        ids = [str(i) for i in raw.get("order_ids") or [] if i]
        if raw.get("order_id"):
            ids.append(str(raw["order_id"]))
        joined = [sent[i] for i in ids if i in sent]
        if joined:
            anchor, source = max(joined), "fill_log"
        else:
            anchor, source = _time(raw.get("executed_at_utc")), "pass_start"
    priced_at = _time(raw.get("quote_observed_at_utc"))
    if anchor is not None and priced_at is not None and priced_at > anchor:
        anchor = priced_at
    return anchor, source if anchor is not None else "missing"


# ---------------------------------------------------------------- cohorts


def _state_cohorts(runtime_root: Path) -> list[dict[str, Any]]:
    try:
        state = json.loads((runtime_root / "auto_buyer_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cohorts = state.get("cohorts") if isinstance(state, dict) else None
    return [c for c in cohorts if isinstance(c, dict)] if isinstance(cohorts, list) else []


def _cohort_label(entry: dict[str, Any]) -> str:
    low = _number(entry.get("min_saved_edge"))
    high = _number(entry.get("max_saved_edge_exclusive"))
    upper = "inf" if high is None else f"{high:g}"
    return f"{entry.get('model_id')}:{entry.get('market')}:[{(low or 0.0):g},{upper}):{entry.get('mode')}"


def _match_cohort(
    book: str, model_id: str, market: str, saved_edge: float | None, cohorts: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The state cohort of this book/model/market whose window holds the saved edge, else the only one."""
    same = [
        c
        for c in cohorts
        if c.get("mode") == book and c.get("model_id") == model_id and c.get("market") == market
    ]
    if saved_edge is not None:
        for entry in same:
            low = _number(entry.get("min_saved_edge")) or 0.0
            high = _number(entry.get("max_saved_edge_exclusive"))
            if saved_edge >= low and (high is None or saved_edge < high):
                return entry
    return same[0] if len(same) == 1 else None


# ---------------------------------------------------------------- pick context (ledgers.db)


def _pick_context(ledgers_db: Path, pick_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = sorted({p for p in pick_ids if p})
    if not wanted or not ledgers_db.exists():
        return {}
    found: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(f"{ledgers_db.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                "SELECT pick_id, ledger_tier, line, edge, market_probability, created_at_utc, "
                f"decision_payload_json FROM ledger_records WHERE pick_id IN ({marks})",
                chunk,
            ).fetchall()
            for pick_id, tier, line, edge, ask, created, payload_raw in rows:
                if pick_id in found and found[pick_id]["tier"] == "flat":
                    continue  # the cohorts buy flat rows; a Main copy never replaces the flat one
                try:
                    payload = json.loads(payload_raw or "{}")
                except ValueError:
                    payload = {}
                snapshot = payload.get("model_input_snapshot_json")
                if isinstance(snapshot, str):
                    try:
                        snapshot = json.loads(snapshot)
                    except ValueError:
                        snapshot = {}
                features = (snapshot.get("features") or {}) if isinstance(snapshot, dict) else {}
                found[pick_id] = {
                    "tier": tier,
                    "line": _number(line),
                    "saved_edge": _number(edge),
                    "price": _number(ask)
                    if _number(ask) is not None
                    else _number(payload.get("market_implied_probability")),
                    "created_at_utc": created,
                    "quote_observed_at_utc": payload.get("market_quote_observed_at_utc"),
                    "slug": payload.get("market_slug") or features.get("research_quote_join.market_slug"),
                }
    finally:
        conn.close()
    return found


# ---------------------------------------------------------------- closing quotes (snapshot archive)


def _snapshot_paths(runtime_root: Path, sport: str, tip: datetime) -> list[Path]:
    dates = dict.fromkeys([tip.astimezone(EASTERN).date().isoformat(), tip.date().isoformat()])
    return [runtime_root / "odds" / sport / day / "polymarket_snapshots.jsonl" for day in dates]


def _side_quote(snapshot: dict[str, Any], side: str) -> tuple[float | None, float | None]:
    quote = snapshot.get(side) or {}
    if not isinstance(quote, dict):
        return None, None
    ask = _number(quote.get("ask"))
    mid = _number(quote.get("midpoint"))
    if mid is None and ask is not None and _number(quote.get("bid")) is not None:
        mid = round((ask + float(quote["bid"])) / 2.0, 4)
    return ask, mid


def _closing_quotes(
    runtime_root: Path, wanted: list[tuple[int, str, str, datetime, datetime]]
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], set[int]]:
    """One pass per snapshot file for every wanted (key, sport, slug, buy, tip).

    Returns the close per key (observed strictly after the buy and strictly before tip, book open),
    the newest snapshot at or before the buy per key (the contract's line and anchor team), and the
    keys whose snapshot files do not exist.
    """
    by_path: dict[Path, list[tuple[int, str, datetime, datetime]]] = defaultdict(list)
    for key, sport, slug, buy, tip in wanted:
        for path in _snapshot_paths(runtime_root, sport, tip):
            by_path[path].append((key, slug, buy, tip))
    closes: dict[int, tuple[datetime, dict[str, Any]]] = {}
    at_buy: dict[int, tuple[datetime, dict[str, Any]]] = {}
    seen_file: set[int] = set()
    for path, items in by_path.items():
        if not path.exists():
            continue
        by_slug: dict[str, list[tuple[int, datetime, datetime]]] = defaultdict(list)
        for key, slug, buy, tip in items:
            seen_file.add(key)
            by_slug[slug].append((key, buy, tip))
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                match = _SLUG_PATTERN.search(line)
                if match is None or match.group(1) not in by_slug:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("market_slug") not in by_slug:
                    continue
                observed = _time(item.get("observed_at_utc"))
                if observed is None:
                    continue
                own_start_raw = item.get("event_start_utc")
                own_start = _time(own_start_raw) if own_start_raw else None
                if own_start_raw and own_start is None:
                    continue
                state = item.get("market_state")
                is_open = state is None or state == "MARKET_STATE_OPEN"
                for key, buy, tip in by_slug[item["market_slug"]]:
                    if observed <= buy:
                        previous = at_buy.get(key)
                        if previous is None or observed >= previous[0]:
                            at_buy[key] = (observed, item)
                        continue
                    kickoff = min(tip, own_start) if own_start is not None else tip
                    if observed >= kickoff or not is_open:
                        continue
                    previous = closes.get(key)
                    if previous is None or observed >= previous[0]:
                        closes[key] = (observed, item)
    return (
        {key: item for key, (_, item) in closes.items()},
        {key: item for key, (_, item) in at_buy.items()},
        {key for key, *_ in wanted} - seen_file,
    )


# ---------------------------------------------------------------- per-row records


def _base_rows(
    runtime_root: Path,
    cohorts: list[dict[str, Any]],
    context: dict[str, dict[str, Any]],
    sent: dict[str, datetime],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for book, relative in _BOOKS:
        for raw in _read_jsonl(runtime_root / relative):
            pick_id = str(raw.get("pick_id") or "")
            pick = context.get(pick_id, {})
            model_id = str(raw.get("model_id") or "")
            market = str(raw.get("market_type") or "").lower()
            saved_edge = pick.get("saved_edge")
            entry = None if raw.get("manual") else _match_cohort(book, model_id, market, saved_edge, cohorts)
            # The cohort the order was placed under, written on the row, beats re-deriving it from
            # a state file the operator may have edited since.
            stored = raw.get("cohort")
            stored_label = stored.strip() if isinstance(stored, str) and stored.strip() else None
            if stored_label and not raw.get("manual"):
                entry = next((c for c in cohorts if _cohort_label(c) == stored_label), None) or entry
            fill_price = _number(raw.get("avg_fill_price"))
            entry_price = _number(raw.get("entry_price"))
            anchor, anchor_source = _buy_anchor(raw, sent)
            shares = _number(raw.get("shares")) or 0.0
            primary = _number(raw.get("primary_filled_shares"))
            fallback_fill = primary is not None and shares > primary + 1e-6
            if raw.get("manual"):
                label = "manual"
            elif stored_label:
                label = stored_label
            elif entry:
                label = _cohort_label(entry)
            else:
                label = f"{model_id}:{market}:unlisted:{book}"
            rows.append(
                {
                    "book": book,
                    "cohort": label,
                    "paper_twin": bool(entry and entry.get("paper_twin") is True),
                    "pick_id": pick_id,
                    "order_id": raw.get("order_id"),
                    "model_id": model_id,
                    "sport": str(raw.get("sport") or "").lower(),
                    "market_type": market,
                    "selection": raw.get("selection"),
                    "market_slug": raw.get("market_slug"),
                    "token_side": str(raw.get("token_side") or "").lower(),
                    "status": raw.get("status"),
                    "result": str(raw.get("result") or "").lower(),
                    "shares": shares,
                    "requested_shares": _number(raw.get("requested_shares")),
                    "primary_filled_shares": primary,
                    "resting_fallback_fill": fallback_fill,
                    "cost_usd": _number(raw.get("cost_usd")) or 0.0,
                    "pnl_usd": _number(raw.get("pnl_usd")),
                    "fee_usd": _number(raw.get("fee_usd")),
                    "entry_price": entry_price,
                    "avg_fill_price": fill_price,
                    "live_edge": _number(raw.get("edge")),
                    "saved_edge": saved_edge,
                    "edge_bucket": edge_bucket(saved_edge),
                    "in_preregistered_band": in_preregistered_band(saved_edge),
                    "executed_at_utc": raw.get("executed_at_utc"),
                    "submitted_at_utc": raw.get("submitted_at_utc"),
                    "buy_anchor_utc": iso_utc(anchor) if anchor is not None else None,
                    "buy_anchor_source": anchor_source,
                    # A pass-start anchor can sit before the order's own quote capture, a resting
                    # fallback fills at an unrecorded time after submit, and a top-up leg fills after
                    # the first send the row keeps: the close is not provably after the fill, so none
                    # of them counts as an independent close.
                    "close_anchor_uncertain": anchor_source == "pass_start"
                    or fallback_fill
                    or raw.get("anchor_topped_up") is True,
                    "event_start_utc": raw.get("event_start_utc"),
                    **tip_fields(raw.get("event_start_utc")),
                    "pick_line": pick.get("line"),
                    "pick_price": pick.get("price"),
                    "pick_slug": pick.get("slug"),
                    "pick_created_at_utc": pick.get("created_at_utc"),
                    "pick_quote_observed_at_utc": pick.get("quote_observed_at_utc"),
                    "pick_slug_matches_bought": (pick.get("slug") == raw.get("market_slug"))
                    if pick.get("slug")
                    else None,
                    "buy_line": _number(raw.get("line")),
                    "buy_price": fill_price if fill_price is not None else entry_price,
                }
            )
    return rows


def _attach_closes(runtime_root: Path, rows: list[dict[str, Any]], report_date: str) -> None:
    """Close quote, CLV and line-move fields for the report date's rows; other rows stay null, so
    one request scans only that date's snapshot files."""
    wanted: list[tuple[int, str, str, datetime, datetime]] = []
    for key, row in enumerate(rows):
        row.update(
            close_status=None,
            close_observed_at_utc=None,
            close_price_tip=None,
            close_mid_tip=None,
            close_seconds_before_tip=None,
            close_seconds_after_buy=None,
            close_independent_of_buy=None,
            clv_cents=None,
            clv_mid_cents=None,
            buy_contract_line=None,
            buy_contract_team=None,
            buy_to_close_cents=None,
            pick_to_buy_cents=_cents(row["buy_price"], row["pick_price"]),
            pick_to_close_cents=None,
            closing_main_line=None,
            closing_main_line_reason=CLOSING_MAIN_LINE_REASON,
        )
        buy, tip = _time(row["buy_anchor_utc"]), _time(row["event_start_utc"])
        if row["tip_date_et"] != report_date:
            row["close_status"] = "not_in_report_date"
        elif row["cohort"] == "manual":
            row["close_status"] = "manual_row"
        elif row["shares"] <= 0 or row["result"] == "void":
            # A zero-fill IOC whose fallback expired owns no position: nothing to close against.
            row["close_status"] = "unfilled"
        elif buy is None:
            row["close_status"] = "missing_buy_time"
        elif tip is None:
            row["close_status"] = "missing_tip"
        elif not row["market_slug"] or row["token_side"] not in ("long", "short") or not row["sport"]:
            row["close_status"] = "missing_contract"
        else:
            wanted.append((key, row["sport"], str(row["market_slug"]), buy, tip))
    closes, at_buy, no_file = _closing_quotes(runtime_root, wanted)
    for key, _sport, _slug, buy, tip in wanted:
        row = rows[key]
        reference = closes.get(key) or at_buy.get(key)
        if reference is not None:
            row["buy_contract_line"] = _number(reference.get("line"))
            row["buy_contract_team"] = reference.get("team")
        close = closes.get(key)
        if close is None:
            row["close_status"] = "no_snapshot_file" if key in no_file else "no_close_after_buy_before_tip"
            continue
        observed = _time(close["observed_at_utc"])
        assert observed is not None  # _closing_quotes keeps parsed observations only
        ask, mid = _side_quote(close, row["token_side"])
        after_buy = (observed - buy).total_seconds()
        row.update(
            close_status="ok" if ask is not None else "no_side_quote",
            close_observed_at_utc=close["observed_at_utc"],
            close_price_tip=ask,
            close_mid_tip=mid,
            close_seconds_before_tip=(tip - observed).total_seconds(),
            close_seconds_after_buy=after_buy,
            close_independent_of_buy=after_buy >= INDEPENDENT_CLOSE_SECONDS
            and not row["close_anchor_uncertain"],
            clv_cents=_cents(ask, row["buy_price"]),
            clv_mid_cents=_cents(mid, row["buy_price"]),
            buy_to_close_cents=_cents(ask, row["buy_price"]),
            pick_to_close_cents=_cents(ask, row["pick_price"]),
        )


# ---------------------------------------------------------------- summaries


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(statistics.fmean(clean), 4) if clean else None


def _settled(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() == "settled"


def _book_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if _settled(r)]
    pnl = round(sum(r["pnl_usd"] or 0.0 for r in settled), 2)
    fees = round(sum(r["fee_usd"] or 0.0 for r in rows if r["fee_usd"] is not None), 2)
    staked_settled = sum(r["cost_usd"] for r in settled)
    with_close = [r for r in rows if r["clv_cents"] is not None]
    independent = [r for r in with_close if r["close_independent_of_buy"]]
    # The ledger restates shares after reconcile, so these include resting fallbacks that filled later.
    sized = [r for r in rows if r["requested_shares"]]
    requested = sum(r["requested_shares"] for r in sized)
    return {
        "orders": len(rows),
        "filled_orders": sum(1 for r in rows if r["shares"] > 0),
        "staked_usd": round(sum(r["cost_usd"] for r in rows), 2),
        "wins": sum(1 for r in settled if r["result"] == "win"),
        "losses": sum(1 for r in settled if r["result"] == "loss"),
        "pushes": sum(1 for r in settled if r["result"] == "push"),
        "voids": sum(1 for r in rows if r["result"] == "void"),
        "open": sum(1 for r in rows if not _settled(r) and r["result"] != "void"),
        "pnl_usd": pnl,
        "fees_usd": fees,
        "fee_known_orders": sum(1 for r in rows if r["fee_usd"] is not None),
        "pnl_net_usd": round(pnl - fees, 2),
        "roi": round((pnl - fees) / staked_settled, 4) if staked_settled > 0 else None,
        "clv_n": len(with_close),
        "clv_cents_mean": _mean(r["clv_cents"] for r in with_close),
        "clv_mid_cents_mean": _mean(r["clv_mid_cents"] for r in with_close),
        "clv_independent_n": len(independent),
        "clv_independent_cents_mean": _mean(r["clv_cents"] for r in independent),
        "clv_independent_mid_cents_mean": _mean(r["clv_mid_cents"] for r in independent),
        "avg_saved_edge": _mean(r["saved_edge"] for r in rows),
        "avg_live_edge": _mean(r["live_edge"] for r in rows),
        "requested_shares": round(requested, 2),
        "final_filled_shares": round(sum(r["shares"] for r in sized), 2),
        "final_fill_rate": round(sum(r["shares"] for r in sized) / requested, 4) if requested > 0 else None,
        "fallback_filled_shares": round(
            sum(max(0.0, r["shares"] - (r["primary_filled_shares"] or 0.0)) for r in sized), 2
        ),
    }


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    running = peak = drawdown = 0.0
    ordered = sorted(
        (r for r in rows if _settled(r)), key=lambda r: (str(r["tip_utc"]), str(r["executed_at_utc"]))
    )
    for row in ordered:
        running += (row["pnl_usd"] or 0.0) - (row["fee_usd"] or 0.0)
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return round(drawdown, 2)


def _terminal_skips(runtime_root: Path, report_date: str) -> dict[tuple[str, str], dict[str, Any]]:
    """The last skip record before tip of each (book, pick) of the report date, from that date's file.

    An open row keeps matching its cohort after tip, so every later cycle logs it as started; the
    last pre-tip record says why it was never bought. A pick with no pre-tip record keeps its latest.
    Dates up to LEGACY_SKIP_LOG_LAST_DATE also read the one pre-split log.
    """
    terminal: dict[tuple[str, str], tuple[bool, datetime, dict[str, Any]]] = {}
    records = _read_jsonl(skip_log_path(runtime_root, report_date))
    if report_date <= LEGACY_SKIP_LOG_LAST_DATE:
        # Listed first so a per-date record wins a tie on the same cycle.
        legacy = tracking_log_path(runtime_root, LEGACY_SKIP_LOG_NAME)
        records = _read_jsonl(legacy, contains=(report_date,)) + records
    for record in records:
        if record.get("tip_date_et") != report_date:
            continue
        cycle = _time(record.get("cycle_at_utc"))
        if cycle is None:
            continue
        tip = _time(record.get("tip_utc"))
        rank = (tip is None or cycle < tip, cycle)
        key = (str(record.get("book") or ""), str(record.get("pick_id") or ""))
        previous = terminal.get(key)
        if previous is None or rank >= previous[:2]:
            terminal[key] = (*rank, record)
    return {key: record for key, (_pre_tip, _cycle, record) in terminal.items()}


def _fill_log_reasons(fill_records: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Per pick, its newest order attempt as (reason, cohort): ``fill_log:<outcome>``, with the fill
    path appended for a submitted order (an IOC that filled nothing and left no fallback)."""
    newest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for record in fill_records:
        pick_id = str(record.get("pick_id") or "")
        sent = _time(record.get("sent_utc"))
        if not pick_id or sent is None:
            continue
        if pick_id not in newest or sent >= newest[pick_id][0]:
            newest[pick_id] = (sent, record)
    out = {}
    for pick_id, (_sent, record) in newest.items():
        outcome = str(record.get("outcome"))
        reason = f"fill_log:{outcome}" + (
            f":{record.get('fill_path') or 'unknown'}" if outcome == "submitted" else ""
        )
        out[pick_id] = (reason, str(record.get("cohort")))
    return out


def _skip_reasons(
    terminal: dict[tuple[str, str], dict[str, Any]],
    fill_reasons: dict[str, tuple[str, str]],
    bought: set[tuple[str, str]],
) -> dict[str, Any]:
    """Count each unbought cohort pick once. A live pick the buyer tried to order is explained by its
    order attempt (the fill log); any other by its terminal skip record."""
    counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    reasons: dict[tuple[str, str], tuple[str, str]] = {
        key: (str(record.get("reason")), str(record.get("cohort"))) for key, record in terminal.items()
    }
    reasons.update({("live", pick_id): reason for pick_id, reason in fill_reasons.items()})
    for key, (reason, cohort) in reasons.items():
        if key not in bought:
            counts[key[0]][cohort][reason] += 1
    return {book: {cohort: dict(c) for cohort, c in by_cohort.items()} for book, by_cohort in counts.items()}


def _fill_stats(fill_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in fill_records:
        by_cohort[str(record.get("cohort"))].append(record)
    stats = {}
    for cohort, records in by_cohort.items():
        requested = sum(_number(r.get("requested_shares")) or 0.0 for r in records)
        filled = sum(_number(r.get("filled_shares")) or 0.0 for r in records)
        stats[cohort] = {
            "attempts": len(records),
            "outcomes": dict(Counter(str(r.get("outcome")) for r in records)),
            "fill_paths": dict(Counter(str(r.get("fill_path")) for r in records if r.get("fill_path"))),
            "requested_shares": round(requested, 2),
            "ioc_filled_shares": round(filled, 2),
            "ioc_fill_rate": round(filled / requested, 4) if requested > 0 else None,
            "fees_usd": round(sum(_number(r.get("fee_usd")) or 0.0 for r in records), 2),
        }
    return stats


def _loss_stop_cycles(runtime_root: Path, report_date: str) -> dict[str, int]:
    hits = {}
    for book, relative in _HISTORY.items():
        count = 0
        # An ET date's cycles carry that UTC date or the next one.
        next_day = (datetime.fromisoformat(report_date) + timedelta(days=1)).date().isoformat()
        for record in _read_jsonl(runtime_root / relative, contains=(report_date, next_day)):
            if record.get("pass") != f"cohort_{book}":
                continue
            executed = _time(record.get("executed_at_utc"))
            if executed is None or executed.astimezone(EASTERN).date().isoformat() != report_date:
                continue
            if (_number(record.get("rejected_daily_loss_breaker")) or 0) > 0:
                count += 1
        hits[book] = count
    return hits


def _twin_comparison(
    rows: list[dict[str, Any]], report_date: str, terminal: dict[tuple[str, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    twins = {(r["model_id"], r["market_type"]) for r in rows if r["paper_twin"]}
    for model_id, market in sorted(twins):
        day = [
            r
            for r in rows
            if r["tip_date_et"] == report_date and (r["model_id"], r["market_type"]) == (model_id, market)
        ]
        live = {r["pick_id"]: r for r in day if r["book"] == "live" and r["cohort"] != "manual"}
        paper = {r["pick_id"]: r for r in day if r["book"] == "paper" and r["paper_twin"]}
        both = sorted(set(live) & set(paper))
        out.append(
            {
                "model_id": model_id,
                "market_type": market,
                "both": both,
                "live_only": sorted(set(live) - set(paper)),
                # Why the twin did not buy what live bought (its last skip before tip), None if unlogged.
                "live_only_reasons": {
                    pick_id: (terminal.get(("paper", pick_id)) or {}).get("reason")
                    for pick_id in sorted(set(live) - set(paper))
                },
                "paper_only": sorted(set(paper) - set(live)),
                "entry_diff_cents_mean": _mean(
                    _cents(live[p]["buy_price"], paper[p]["buy_price"]) for p in both
                ),
                "same_result": sum(1 for p in both if live[p]["result"] == paper[p]["result"]),
                "live": _book_stats(list(live.values())),
                "paper": _book_stats(list(paper.values())),
            }
        )
    return out


def build_auto_buyer_report(
    runtime_root: Path | str,
    report_date: str,
    *,
    ledgers_db: Path | str | None = None,
    day_rows_only: bool = False,
) -> dict[str, Any]:
    """The report for one US-Eastern game date (by tip time). Reads only; writes nothing.

    The summaries always use every date's rows (the cumulative figures need them); ``day_rows_only``
    trims the returned ``rows`` to the report date, for a payload that has no use for the rest.
    """
    root = Path(runtime_root)
    cohorts = _state_cohorts(root)
    pick_ids = [str(r.get("pick_id") or "") for _, rel in _BOOKS for r in _read_jsonl(root / rel)]
    context = _pick_context(Path(ledgers_db) if ledgers_db else root / "ledgers" / "ledgers.db", pick_ids)
    fill_records = _fill_records(root, report_date)
    rows = _base_rows(root, cohorts, context, _sent_by_order(fill_records))
    _attach_closes(root, rows, report_date)
    # A zero-fill IOC whose fallback expired (void, no shares) owns nothing: its order attempt, not
    # a purchase, is what the skip reasons should say.
    bought = {(r["book"], r["pick_id"]) for r in rows if r["shares"] > 0 or r["result"] != "void"}
    terminal = _terminal_skips(root, report_date)
    fills = _fill_stats(fill_records)
    summaries = []
    for book, cohort in sorted({(r["book"], r["cohort"]) for r in rows}):
        mine = [r for r in rows if (r["book"], r["cohort"]) == (book, cohort)]
        day = [r for r in mine if r["tip_date_et"] == report_date]
        to_date = [r for r in mine if r["tip_date_et"] is not None and r["tip_date_et"] <= report_date]
        if not day and not to_date:
            continue
        summaries.append(
            {
                "book": book,
                "cohort": cohort,
                "paper_twin": any(r["paper_twin"] for r in mine),
                "day": _book_stats(day),
                "tip_split": {
                    window: _book_stats([r for r in day if r["tip_window"] == window])
                    for window in (BEFORE_CUTOFF, AT_OR_AFTER_CUTOFF)
                },
                "edge_buckets": {
                    bucket: _book_stats([r for r in day if r["edge_bucket"] == bucket])
                    for bucket in sorted({str(r["edge_bucket"]) for r in day})
                },
                "cumulative": {
                    **{k: v for k, v in _book_stats(to_date).items() if "clv" not in k},
                    "max_drawdown_usd": _max_drawdown(to_date),
                },
                "fills": fills.get(cohort, {}) if book == "live" else {},
            }
        )
    return {
        "report_date": report_date,
        "generated_at_utc": iso_utc(utc_now()),
        "definitions": {
            "date": "US-Eastern date of the game's tip",
            "tip_window": f"{BEFORE_CUTOFF} = tip before 17:00:00 ET, {AT_OR_AFTER_CUTOFF} = at or after",
            "edge_bucket": "the pick's saved edge (ledger_records.edge) at decision time",
            "clv_cents": "close ask minus the entry (fill) price, in cents; positive = beat the close",
            "clv_mid_cents": "close midpoint minus the entry price, in cents",
            "close": "last open-book quote of the bought contract after the buy and before tip",
            "buy": (
                "when the order was sent: the row's submitted_at_utc, else the fill log's sent_utc by order "
                "id, else the pass start (buy_anchor_source), floored at the quote that priced the order"
            ),
            "close_independent_of_buy": (
                f"close observed at least {INDEPENDENT_CLOSE_SECONDS:g} s after the buy, and the buy time "
                "known (not the pass start, not a resting fallback fill)"
            ),
            "pnl_net_usd": "ledger pnl_usd minus fee_usd where the row records a fee",
        },
        "cohorts": summaries,
        "skip_reasons": _skip_reasons(terminal, _fill_log_reasons(fill_records), bought),
        "loss_stop_cycles": _loss_stop_cycles(root, report_date),
        "twin_comparison": _twin_comparison(rows, report_date, terminal),
        "rows": [r for r in rows if r["tip_date_et"] == report_date] if day_rows_only else rows,
    }


# ---------------------------------------------------------------- output


def _fmt(value: Any, *, kind: str = "") -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "usd":
        return f"${value:,.2f}"
    if kind == "cents":
        return f"{value:.1f}¢"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Auto-buyer daily report, {report['report_date']} (ET tip date)",
        "",
        f"Generated {report['generated_at_utc']}. Prices are cents; edges and ROI are percentages.",
        "",
        (
            "| Book | Cohort | Orders | W-L-P | Open | Staked | P&L net | Fees | ROI | CLV (n) | CLV indep. (n) "
            "| Avg saved edge | Avg live edge |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cohorts"]:
        d = c["day"]
        lines.append(
            f"| {c['book']} | {c['cohort']}{' (twin)' if c['paper_twin'] else ''} | {d['orders']} "
            f"| {d['wins']}-{d['losses']}-{d['pushes']} | {d['open']} | {_fmt(d['staked_usd'], kind='usd')} "
            f"| {_fmt(d['pnl_net_usd'], kind='usd')} | {_fmt(d['fees_usd'], kind='usd')} | {_fmt(d['roi'], kind='pct')} "
            f"| {_fmt(d['clv_cents_mean'], kind='cents')} ({d['clv_n']}) "
            f"| {_fmt(d['clv_independent_cents_mean'], kind='cents')} ({d['clv_independent_n']}) "
            f"| {_fmt(d['avg_saved_edge'], kind='pct')} | {_fmt(d['avg_live_edge'], kind='pct')} |"
        )
    lines += [
        "",
        "## Tip-time split (5 PM ET)",
        "",
        "| Book | Cohort | Window | Orders | W-L-P | P&L net | CLV |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["cohorts"]:
        for window, d in c["tip_split"].items():
            lines.append(
                f"| {c['book']} | {c['cohort']} | {window} | {d['orders']} | {d['wins']}-{d['losses']}-{d['pushes']} "
                f"| {_fmt(d['pnl_net_usd'], kind='usd')} | {_fmt(d['clv_cents_mean'], kind='cents')} |"
            )
    lines += [
        "",
        "## Cumulative to date",
        "",
        "| Book | Cohort | Orders | W-L-P | P&L net | ROI | Max drawdown |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["cohorts"]:
        d = c["cumulative"]
        lines.append(
            f"| {c['book']} | {c['cohort']} | {d['orders']} | {d['wins']}-{d['losses']}-{d['pushes']} "
            f"| {_fmt(d['pnl_net_usd'], kind='usd')} | {_fmt(d['roi'], kind='pct')} | {_fmt(d['max_drawdown_usd'], kind='usd')} |"
        )
    lines += [
        "",
        "## Live fills",
        "",
        "| Cohort | Attempts | Outcomes | Fill paths | IOC fill rate |",
        "|---|---|---|---|---|",
    ]
    for c in report["cohorts"]:
        f = c["fills"]
        if f:
            lines.append(
                f"| {c['cohort']} | {f['attempts']} | {f['outcomes']} | {f['fill_paths']} | {_fmt(f['ioc_fill_rate'], kind='pct')} |"
            )
    lines += [
        "",
        "## Skip reasons (the order attempt, else the terminal skip, of each unbought cohort pick)",
        "",
    ]
    for book, by_cohort in sorted(report["skip_reasons"].items()):
        for cohort, counts in sorted(by_cohort.items()):
            detail = ", ".join(f"{reason} {n}" for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]))
            lines.append(f"- {book} {cohort}: {detail}")
    lines += ["", f"Loss-stop cycles: {report['loss_stop_cycles']}", "", "## Paper twin vs live", ""]
    for t in report["twin_comparison"]:
        lines.append(
            f"- {t['model_id']} {t['market_type']}: both {len(t['both'])}, live only {len(t['live_only'])}, "
            f"paper only {len(t['paper_only'])}, mean entry difference live minus paper "
            f"{_fmt(t['entry_diff_cents_mean'], kind='cents')}, same result {t['same_result']}"
        )
    return "\n".join(lines) + "\n"


def write_auto_buyer_report(report: dict[str, Any], out_dir: Path | str) -> dict[str, str]:
    """Write the JSON, the markdown and the day's CLV and line-move rows under ``out_dir/<date>``."""
    target = Path(out_dir) / report["report_date"]
    target.mkdir(parents=True, exist_ok=True)
    day_rows = [r for r in report["rows"] if r["tip_date_et"] == report["report_date"]]
    clv_keys = (
        "book",
        "cohort",
        "pick_id",
        "order_id",
        "market_slug",
        "token_side",
        "tip_utc",
        "tip_et",
        "tip_window",
        "edge_bucket",
        "in_preregistered_band",
        "executed_at_utc",
        "buy_anchor_utc",
        "buy_anchor_source",
        "resting_fallback_fill",
        "close_anchor_uncertain",
        "buy_price",
        "close_status",
        "close_observed_at_utc",
        "close_price_tip",
        "close_mid_tip",
        "close_seconds_before_tip",
        "close_seconds_after_buy",
        "close_independent_of_buy",
        "clv_cents",
        "clv_mid_cents",
    )
    move_keys = (
        "book",
        "cohort",
        "pick_id",
        "market_type",
        "selection",
        "tip_et",
        "tip_window",
        "edge_bucket",
        "pick_created_at_utc",
        "pick_quote_observed_at_utc",
        "pick_slug",
        "pick_line",
        "pick_price",
        "executed_at_utc",
        "market_slug",
        "pick_slug_matches_bought",
        "buy_line",
        "buy_contract_line",
        "buy_contract_team",
        "buy_price",
        "close_observed_at_utc",
        "close_price_tip",
        "pick_to_buy_cents",
        "buy_to_close_cents",
        "pick_to_close_cents",
        "closing_main_line",
        "closing_main_line_reason",
    )
    paths = {
        "json": target / "auto_buyer_daily.json",
        "markdown": target / "auto_buyer_daily.md",
        "close_clv": target / "close_clv.jsonl",
        "line_moves": target / "line_moves.jsonl",
    }
    paths["json"].write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(report), encoding="utf-8")
    for name, keys in (("close_clv", clv_keys), ("line_moves", move_keys)):
        paths[name].write_text(
            "".join(json.dumps({k: r.get(k) for k in keys}, default=str) + "\n" for r in day_rows),
            encoding="utf-8",
        )
    return {name: str(path) for name, path in paths.items()}


def cmd_auto_buyer_report(args: Any) -> dict[str, Any]:
    """``python -m model_prediction auto-buyer-report [--date D] [--out-dir DIR]``."""
    from ..runtime_paths import RuntimePaths

    try:
        report_date = (
            date.fromisoformat(args.date).isoformat()
            if args.date
            else utc_now().astimezone(EASTERN).date().isoformat()
        )
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, got {args.date!r}") from None
    paths = RuntimePaths.resolve()
    report = build_auto_buyer_report(paths.runtime_root, report_date, ledgers_db=paths.ledgers_db)
    out_dir = Path(args.out_dir) if args.out_dir else paths.runtime_root / "reports" / "auto_buyer"
    written = write_auto_buyer_report(report, out_dir)
    return {
        "report_date": report_date,
        "files": written,
        "cohorts": [{"book": c["book"], "cohort": c["cohort"], **c["day"]} for c in report["cohorts"]],
        "skip_reasons": report["skip_reasons"],
        "loss_stop_cycles": report["loss_stop_cycles"],
    }
