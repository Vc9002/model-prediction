"""Auto-Buyer Ledger: dedicated audit-backed ledger and log for auto-purchased shares.

Maintains an isolated, clean record of all automated Polymarket purchases:
1. data/auto_buyer_ledger.jsonl: Immutable streaming record of every auto-executed trade.
2. data/auto_buyer_picks.xlsx: Standardized Excel ledger for dashboard and spreadsheet viewing.
3. data/logs/auto_buyer.log: Verbose operational log for tracking executions and settlements.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from ..data_sources.espn import ESPNClient
from ..data_sources.polymarket_execute import ExecutionGateError
from ..domain import PickTag, iso_utc, parse_utc, pick_tag_for, utc_now
from ..esports import TITLE_SPECS as _ESPORTS_TITLE_SPECS
from ..esports import Bo3EsportsClient, _fuzzy_match_team, _load_manual_aliases, _team_alias_index
from ..ledger import FIELDNAMES
from ..pricing import american_to_decimal
from ..research_io import identity_key as _identity_key
from ..runtime_paths import RuntimePaths
from ..xlsx_ledger import _replace_with_retry, read_xlsx_rows, write_xlsx_rows_atomic

# Reverse of esports.TITLE_SPECS[title]["polymarket_league"] -- the Auto-Buyer
# ledger's own `sport` field is written as that league string (e.g. "CS2",
# "LOL"), not the BO3 title key `finished_matches`/`teams` expect ("cs2", "lol").
ESPORTS_TITLE_BY_SPORT: dict[str, str] = {
    str(spec["polymarket_league"]): title for title, spec in _ESPORTS_TITLE_SPECS.items()
}

_paths = RuntimePaths.resolve()
DATA = _paths.runtime_root
LOGS_DIR = DATA / "logs"

AUTO_BUYER_JSONL_PATH = DATA / "auto_buyer_ledger.jsonl"
AUTO_BUYER_XLSX_PATH = DATA / "auto_buyer_picks.xlsx"
AUTO_BUYER_LOG_PATH = LOGS_DIR / "auto_buyer.log"


def resolve_auto_buyer_paths(
    data_root: Path | str | None = None,
    jsonl_path: Path | str | None = None,
    xlsx_path: Path | str | None = None,
) -> tuple[Path, Path]:
    """Resolve JSONL and XLSX paths for the Auto-Buyer ledger.

    An explicit file path wins. The production default is the runtime live
    root; an explicitly named paper_auto_buyer root stays separate.
    """
    if jsonl_path and xlsx_path:
        return Path(jsonl_path), Path(xlsx_path)
    if jsonl_path and not xlsx_path:
        j = Path(jsonl_path)
        x = j.with_name(j.name.replace(".jsonl", ".xlsx")).with_suffix(".xlsx")
        return j, x

    root = Path(data_root) if data_root else None
    paths = RuntimePaths.resolve()

    if root is None or root.resolve() == (paths.repo_root / "data").resolve():
        root = paths.runtime_root
    elif root.resolve() == (paths.repo_root / "data" / "paper_auto_buyer").resolve():
        root = paths.runtime_root / "paper_auto_buyer"
    elif (
        root.resolve() != paths.runtime_root.resolve()
        and root.name != "paper_auto_buyer"
        and not (root / "auto_buyer_ledger.jsonl").exists()
    ):
        paper_root = root / "paper_auto_buyer"
        if (paper_root / "auto_buyer_ledger.jsonl").exists():
            root = paper_root
    return root / "auto_buyer_ledger.jsonl", root / "auto_buyer_picks.xlsx"


logger = logging.getLogger("model_prediction.auto_buyer")

AUTO_BUYER_UNIT_VALUE_USD = 0.50  # 1U = 50 cents
AUTO_BUYER_TERMINAL_RESULTS = frozenset({"win", "loss", "push"})


def _write_jsonl_atomic(j_path: Path, records: list[dict[str, Any]], *, allow_shrink: bool = False) -> None:
    """Safely rewrite JSONL file via temporary file + atomic rename to prevent 0-byte truncation.

    Refuses to shrink the record count unless allow_shrink is set. Every caller in this module
    only appends or mutates existing records, so a write with fewer rows than are currently on
    disk means the read that produced `records` silently dropped data (e.g. a corrupted line
    swallowed by read_auto_buyer_ledger's bare `except json.JSONDecodeError: continue`) -- not a
    legitimate removal. This guards against repeating the 2026-11-10 incident where exactly this
    path silently truncated the live ledger to 0 bytes.
    """
    if not allow_shrink and j_path.exists():
        with j_path.open(encoding="utf-8") as f:
            existing_count = sum(1 for line in f if line.strip())
        if len(records) < existing_count:
            raise ValueError(
                f"refusing to overwrite {j_path} ({existing_count} records on disk) with only "
                f"{len(records)} records -- likely a parse failure dropped data; pass allow_shrink=True "
                "if this is a deliberate removal"
            )
    tmp_path = j_path.with_name(f"{j_path.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    _replace_with_retry(str(tmp_path), j_path)


def _usd_to_auto_buyer_units(value_usd: float, unit_value_usd: float = AUTO_BUYER_UNIT_VALUE_USD) -> float:
    return round(float(value_usd) / float(unit_value_usd), 4)


def _resolve_esports_team(
    name: str,
    teams: dict[str, dict[str, Any]],
    team_index: dict[str, set[str]],
    manual_aliases: dict[str, str],
) -> str | None:
    key = _identity_key(name)
    if key in manual_aliases:
        alias_candidates = team_index.get(_identity_key(manual_aliases[key]))
        if alias_candidates and len(alias_candidates) == 1:
            return next(iter(alias_candidates))
    exact_candidates = team_index.get(key)
    if exact_candidates and len(exact_candidates) == 1:
        return next(iter(exact_candidates))
    return _fuzzy_match_team(name, teams, team_index)


def _normalized_team_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _scoreboard_team_key(name: str, league: str) -> str:
    """Resolve only whole, unambiguous names or registered aliases."""
    normalized = _normalized_team_name(name)
    registry = _paths.repo_root / "data" / "entities" / "teams.json"
    try:
        teams = json.loads(registry.read_text(encoding="utf-8"))["teams"]
    except (OSError, ValueError, KeyError, TypeError):
        return normalized
    candidates = set()
    for team in teams:
        if str(team.get("league", "")).upper() != league:
            continue
        names = [team.get("canonical_name", ""), team.get("abbreviation", "")]
        names.extend(alias.get("source_name", "") for alias in team.get("aliases", []))
        if normalized in {_normalized_team_name(str(value)) for value in names if value}:
            candidates.add(str(team["canonical_team_id"]))
    return next(iter(candidates)) if len(candidates) == 1 else normalized


def _is_settled_auto_buyer_record(record: dict[str, Any]) -> bool:
    return (
        str(record.get("status") or "").lower() == "settled"
        or str(record.get("result") or "").lower() in AUTO_BUYER_TERMINAL_RESULTS
    )


def _uses_live_auto_buyer_ledger(path: Path) -> bool:
    """Return whether a writer targets the production Auto-Buyer ledger.

    Resolve against the same runtime root used by the executor.
    """
    live_j_path, _ = resolve_auto_buyer_paths()
    return path.resolve() == live_j_path.resolve()


def _auto_buyer_pick_tag(path: Path) -> PickTag:
    """Live ledger rows are ``qualified``; every other buyer ledger (paper, trials) is ``shadow``."""
    return pick_tag_for(buyer_mode="live" if _uses_live_auto_buyer_ledger(path) else "paper")


def resolve_auto_buyer_log_path() -> Path:
    """Resolve the Auto-Buyer operational log path, mirroring the live ledger's root."""
    live_j_path, _ = resolve_auto_buyer_paths()
    return live_j_path.parent / "logs" / "auto_buyer.log"


def _get_logger() -> logging.Logger:
    log_path = resolve_auto_buyer_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("auto_buyer_logger")
    # Re-point the handler if the resolved log path has moved (e.g. the live ledger
    # started existing under runtime_root after this process already logged once).
    current_handler = log.handlers[0] if log.handlers else None
    if current_handler is None or getattr(current_handler, "baseFilename", None) != str(log_path):
        for handler in list(log.handlers):
            log.removeHandler(handler)
            handler.close()
        handler = logging.FileHandler(log_path, encoding="utf-8")
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
            if parts[-2] == "pos":
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
    ev_id = str(order_payload.get("event_id") or pick.get("event_id") or "")
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

    # Top-up remainder handling: update existing position row instead of appending duplicate pick row
    if order_payload.get("is_topup") and j_path.exists():
        records = read_auto_buyer_ledger(jsonl_path=j_path)
        matching_idx = None
        for idx, rec in enumerate(records):
            if pid and str(rec.get("pick_id")) == pid:
                matching_idx = idx
                break
            if (
                slug
                and str(rec.get("market_slug")) == slug
                and str(rec.get("token_side") or "").lower() == side
            ):
                matching_idx = idx
                break

        if matching_idx is not None:
            rec = records[matching_idx]
            old_shares = float(rec.get("shares") or 0.0)
            old_cost = float(rec.get("cost_usd") or 0.0)
            total_shares = round(old_shares + shares, 4)
            total_cost = round(old_cost + cost, 4)
            unit_val = float(rec.get("unit_value_usd") or unit_value_usd)
            total_units = _usd_to_auto_buyer_units(total_cost, unit_val)
            avg_price = round(total_cost / total_shares, 4) if total_shares > 0 else price

            existing_oids = list(rec.get("order_ids") or [rec.get("order_id")])
            for new_o in order_payload.get("order_ids") or [oid]:
                if new_o and str(new_o) not in existing_oids:
                    existing_oids.append(str(new_o))

            rec["order_ids"] = existing_oids
            rec["shares"] = total_shares
            rec["cost_usd"] = total_cost
            rec["units"] = round(total_units, 2)
            rec["entry_price"] = avg_price
            rec["primary_filled_shares"] = total_shares
            rec["primary_filled_cost_usd"] = total_cost
            rec["fallback_order_id"] = fallback_order_id
            rec["fallback_resting_shares"] = fallback_resting_shares
            rec["fallback_reconciled"] = not bool(fallback_order_id)
            rec["fill_known"] = bool(order_payload.get("fill_known", True))

            target_spend = float(
                order_payload.get("target_spend") or (float(rec.get("model_units") or 1.0) * unit_val)
            )
            if (
                total_cost >= target_spend - 0.10 or order_state == "ORDER_STATE_FILLED"
            ) and not fallback_order_id:
                rec["order_state"] = "ORDER_STATE_FILLED"
            elif total_shares > 0:
                rec["order_state"] = "ORDER_STATE_PARTIALLY_FILLED"
            else:
                rec["order_state"] = order_state

            try:
                _write_jsonl_atomic(j_path, records)
            except (OSError, ValueError) as err:
                logger.error(f"Failed to update auto_buyer_ledger.jsonl on topup: {err}")

            try:
                if x_path.exists():
                    _, existing_rows = read_xlsx_rows(x_path)
                    by_pick = {str(r.get("pick_id")): r for r in existing_rows}
                    p_key = pid or str(rec.get("order_id"))
                    if p_key in by_pick:
                        american = _probability_to_american(avg_price)
                        decimal_odds = american_to_decimal(american)
                        by_pick[p_key]["units"] = f"{total_units:.2f}"
                        by_pick[p_key]["decimal_odds"] = f"{decimal_odds:.4f}"
                        by_pick[p_key]["american_odds"] = str(american)
                        by_pick[p_key]["rationale"] = (
                            f"Auto-Buyer order {rec.get('order_id')} (+topup {oid}) "
                            f"({total_shares} sh @ ${avg_price:.2f}) on {slug} ({side})"
                        )
                        write_xlsx_rows_atomic(x_path, FIELDNAMES, list(by_pick.values()))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
                logger.warning(f"Failed to update auto_buyer_picks.xlsx on topup: {err}")

            if _uses_live_auto_buyer_ledger(j_path):
                log_auto_buyer_event(
                    f"TOP-UP EXECUTED [{sport}] {away} @ {home} ({sel}) | Market: {slug} ({side}) | "
                    f"+{shares} sh @ ${price:.2f} (New Total: {total_shares} sh, Cost: ${total_cost:.2f}, {total_units:.2f}U) | "
                    f"Model: {model_id} | OrderID: {oid}"
                )

            return rec

    # JSONL specific record
    jsonl_record = {
        "order_id": oid,
        "order_ids": order_payload.get("order_ids") or [oid],
        "pick_id": pid,
        "event_id": ev_id or None,
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
        "model_probability_raw": pick.get("model_probability_raw"),
        "calibration_method": pick.get("calibration_method"),
        "market_probability": round(market_p, 4),
        "edge": round(edge, 4),
        "pick_tag": _auto_buyer_pick_tag(j_path).value,
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
        existing_rows = []
        if x_path.exists():
            _, existing_rows = read_xlsx_rows(x_path)

        american = _probability_to_american(price)
        decimal_odds = american_to_decimal(american)

        xlsx_row: dict[str, Any] = dict.fromkeys(FIELDNAMES, "")
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
    quote_fn: Callable[[str], dict[str, Any]] | None = None,
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
      more so as it progresses;
    - checks resting order freshness and market movement: if the market has
      closed, or if the market moved away such that the edge has degraded
      below the minimum threshold or turned negative, the resting order is
      cancelled to prevent adverse fills.
    """
    j_path, x_path = resolve_auto_buyer_paths(data_root=data_root)
    root = j_path.parent
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

    def _cancel_order(
        order_id: str,
        market_slug: str | None,
        row: dict[str, Any],
        reason: str = "",
    ) -> bool:
        if not hasattr(live_executor, "cancel"):
            return False
        try:
            try:
                live_executor.cancel(order_id, user_command=True, market_slug=market_slug)
            except TypeError:
                live_executor.cancel(order_id, user_command=True)
            if reason:
                row["cancellation_reason"] = reason
            return True
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ExecutionGateError) as exc:
            logger.warning("Failed to cancel resting order %s: %s", order_id, exc)
            return False

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
            update_primary = True
        else:
            new_shares = round(primary_shares + fallback_filled, 4)
            new_cost = round(primary_cost + fallback_filled * entry_price, 4)
            update_primary = False

        if new_shares != r.get("shares") or new_cost != r.get("cost_usd"):
            r["shares"] = new_shares
            r["cost_usd"] = new_cost
            if update_primary:
                r["primary_filled_shares"] = new_shares
                r["primary_filled_cost_usd"] = new_cost
            else:
                r["fallback_filled_shares"] = round(fallback_filled, 4)
                r["fallback_filled_cost_usd"] = round(fallback_filled * entry_price, 4)
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

        market_slug = r.get("market_slug")

        if not is_terminal and started and r.get("fallback_order_id") != r.get("order_id"):
            if _cancel_order(fid, market_slug, r, reason="game_started"):
                is_terminal = True
                state = state or "ORDER_STATE_CANCELED"
            else:
                errors += 1
        elif (
            not is_terminal
            and not started
            and r.get("fallback_order_id") != r.get("order_id")
            and market_slug
        ):
            # Check resting order freshness and adverse market movement
            snap = None
            if quote_fn is not None:
                try:
                    snap = quote_fn(str(market_slug))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    snap = None
            elif data_root is None:
                try:
                    from ..data_sources.polymarket_us import PolymarketUSClient

                    snap = PolymarketUSClient().snapshot(str(market_slug))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError, httpx.HTTPError):
                    snap = None

            if snap is not None:
                m_state = str(snap.get("market_state") or "MARKET_STATE_OPEN").upper()
                side = str(r.get("token_side") or "long").lower()
                side_data = snap.get(side) or {}
                current_ask = side_data.get("ask")
                current_bid = side_data.get("bid")
                model_prob = float(r.get("model_probability") or 0.0)

                min_edge = 0.035
                max_edge = 0.20
                try:
                    from .auto_executor import load_auto_buyer_state

                    st = load_auto_buyer_state()
                    min_edge = float(st.get("min_edge", 0.035))
                    max_edge = float(st.get("max_edge", 0.20))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    min_edge = 0.035
                    max_edge = 0.20

                should_cancel = False
                stale_reason = ""
                if m_state != "MARKET_STATE_OPEN":
                    should_cancel = True
                    stale_reason = f"market_state_{m_state}"
                elif current_ask is not None:
                    try:
                        ask_flt = float(current_ask)
                        current_edge = model_prob - ask_flt
                        if current_edge < min_edge:
                            # Edge lost or turned negative (adverse selection risk)
                            should_cancel = True
                            stale_reason = f"edge_degraded_to_{current_edge:.4f}_below_min_{min_edge:.4f}"
                        elif max_edge is not None and current_edge > max_edge:
                            # Edge became anomalous/uncalibrated
                            should_cancel = True
                            stale_reason = f"edge_anomalous_to_{current_edge:.4f}_above_max_{max_edge:.4f}"
                        elif (
                            current_bid is not None
                            and float(current_bid) > entry_price
                            and (ask_flt - entry_price) > 0.015
                        ):
                            # Market moved up away from resting limit price
                            should_cancel = True
                            stale_reason = f"market_moved_up_ask_{ask_flt:.2f}_vs_limit_{entry_price:.2f}"
                    except (ValueError, TypeError):
                        pass

                if should_cancel:
                    if _cancel_order(fid, market_slug, r, reason=stale_reason):
                        is_terminal = True
                        state = state or "ORDER_STATE_CANCELED"
                    else:
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
            _write_jsonl_atomic(j_path, records)
        except (OSError, ValueError) as err:
            logger.error(f"Failed to rewrite auto_buyer_ledger.jsonl during fallback reconciliation: {err}")
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
    j_path = Path(jsonl_path) if jsonl_path else resolve_auto_buyer_paths()[0]
    if not j_path.exists():
        return []
    records = []
    tag = _auto_buyer_pick_tag(j_path).value
    with j_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Rows written before tags existed get theirs from the ledger they live in.
            if isinstance(record, dict):
                record.setdefault("pick_tag", tag)
            records.append(record)
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


def _exchange_sell_trades(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index exchange position-closing trades by slug, totalled across fills.

    A position closed by selling out of it (manually, before the market resolves)
    never produces an ACTIVITY_TYPE_POSITION_RESOLUTION -- it just stops existing
    as an open position. Without this, such a row stays "pending" forever, no
    matter how many settlement passes run, because nothing else in the activity
    feed ever tells us what happened to it. Polymarket's own trade record already
    carries an authoritative ``realizedPnl`` for the sell, so that's used directly
    rather than re-deriving P&L from buy/sell prices ourselves.
    """

    def amount(value: Any) -> float | None:
        if isinstance(value, dict):
            value = value.get("value")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    sells: dict[str, dict[str, Any]] = {}
    notional: dict[str, float] = {}
    for activity in snapshot.get("activities", []):
        if activity.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        trade = activity.get("trade") or {}
        # A trade record carries both counterparties' orders; ``isAggressor`` says which
        # one is ours. A resting limit sell is filled as the PASSIVE side, with someone
        # else's BUY as the aggressor -- reading only the aggressor order missed every
        # such exit and left the row pending (order DMKMWVEG14ET, 2026-11-10).
        own_key = "passiveExecution" if trade.get("isAggressor") is False else "aggressorExecution"
        own_order = (trade.get(own_key) or {}).get("order") or {}
        # Order SIDE is quoted in the long token, so it cannot tell an exit from an
        # entry: opening a short is ORDER_SIDE_SELL and closing one is ORDER_SIDE_BUY.
        # Only the intent says which. Side-only matching left both 2026-11-11 short
        # exits (orders DNAJQQWG13YB, DNAHTZXG13ZK) pending after they were sold.
        intent = str(own_order.get("intent") or "")
        if intent:
            if intent not in {"ORDER_INTENT_SELL_LONG", "ORDER_INTENT_SELL_SHORT"}:
                continue
        elif str(own_order.get("side") or "") != "ORDER_SIDE_SELL":
            continue
        slug = trade.get("marketSlug") or (own_order.get("marketMetadata") or {}).get("slug")
        if not slug:
            continue
        realized = amount(trade.get("realizedPnl"))
        if realized is None:
            continue
        price = amount(trade.get("price"))
        if price is None:
            price = amount(own_order.get("price"))
        if price is not None and intent == "ORDER_INTENT_SELL_SHORT":
            price = 1.0 - price
        shares = amount(trade.get("qtyDecimal")) or amount(trade.get("qty")) or 0.0
        update_time = str(trade.get("updateTime") or trade.get("createTime") or "")
        # One exit order routinely fills in several trades; keeping only the latest
        # reported a fraction of the realized P&L as if it were the whole exit.
        key = str(slug)
        entry = sells.setdefault(
            key, {"realized_pnl_usd": 0.0, "sell_price": None, "shares_sold": 0.0, "update_time": ""}
        )
        entry["realized_pnl_usd"] = round(entry["realized_pnl_usd"] + realized, 6)
        entry["shares_sold"] = round(entry["shares_sold"] + shares, 6)
        if price is not None:
            notional[key] = notional.get(key, 0.0) + price * shares
            entry["sell_price"] = (
                round(notional[key] / entry["shares_sold"], 6) if entry["shares_sold"] else price
            )
        entry["update_time"] = max(entry["update_time"], update_time)
    return sells


_OFFSET_INTENT = {"short": "ORDER_INTENT_BUY_LONG", "long": "ORDER_INTENT_BUY_SHORT"}
_SPREAD_SIDE_SUFFIX = re.compile(r"-(?:pos|neg)-\d+pt\d+$")


def event_key_from_slug(slug: str) -> str:
    """Slug shared by both sides of one spread market, so the two lines of a game match.

    Positions report ``nba-den-mem-2026-11-11`` while market slugs carry an ``asc-``
    prefix and a ``-pos-4pt5`` suffix; both normalise to the former.
    """
    key = _SPREAD_SIDE_SUFFIX.sub("", str(slug or ""))
    return key.removeprefix("asc-")


def _exchange_offset_fills(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Own entry-direction fills per slug, oldest first.

    Buying the opposite token on a contract you already hold closes it by
    netting out, with no sell order and no ``realizedPnl`` on the fill. Only a
    row's own side and entry time say whether such a fill was an exit.
    """

    def amount(value: Any) -> float | None:
        if isinstance(value, dict):
            value = value.get("value")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    fills: dict[str, list[dict[str, Any]]] = {}
    for activity in snapshot.get("activities", []):
        if activity.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        trade = activity.get("trade") or {}
        own_key = "passiveExecution" if trade.get("isAggressor") is False else "aggressorExecution"
        own_order = (trade.get(own_key) or {}).get("order") or {}
        intent = str(own_order.get("intent") or "")
        slug = trade.get("marketSlug") or (own_order.get("marketMetadata") or {}).get("slug")
        shares = amount(trade.get("qtyDecimal")) or amount(trade.get("qty"))
        price = amount(trade.get("price"))
        if not slug or intent not in _OFFSET_INTENT.values() or not shares or price is None:
            continue
        fills.setdefault(str(slug), []).append(
            {
                "intent": intent,
                "shares": shares,
                "price": price,
                "time": str(trade.get("updateTime") or trade.get("createTime") or ""),
            }
        )
    for items in fills.values():
        items.sort(key=lambda f: f["time"])
    return fills


def _with_offset_exits(
    sell_trade: dict[str, Any] | None,
    fills: list[dict[str, Any]] | None,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    """Fold netting fills after the row's entry into its exit total.

    The exchange reports no P&L for a netting fill, so it is derived from the row's
    entry price and flagged ``derived_pnl_shares`` rather than passed off as exchange-reported.
    """
    side = str(row.get("token_side") or "").lower()
    intent = _OFFSET_INTENT.get(side)
    if not fills or intent is None:
        return sell_trade
    try:
        held = float(row.get("shares"))
        entry = float(row.get("entry_price"))
        entered = parse_utc(str(row.get("executed_at_utc")))
    except (TypeError, ValueError):
        return sell_trade
    merged = (
        dict(sell_trade)
        if sell_trade
        else {
            "realized_pnl_usd": 0.0,
            "sell_price": None,
            "shares_sold": 0.0,
            "update_time": "",
        }
    )
    derived = 0.0
    for fill in fills:
        if fill["intent"] != intent:
            continue
        try:
            if parse_utc(fill["time"]) <= entered:
                continue
        except (TypeError, ValueError):
            continue
        take = min(fill["shares"], held - merged["shares_sold"])
        if take <= 0:
            break
        # Price is quoted in the long token: a short is worth 1 - price when netted out.
        exit_value = 1.0 - fill["price"] if side == "short" else fill["price"]
        merged["realized_pnl_usd"] = round(merged["realized_pnl_usd"] + (exit_value - entry) * take, 6)
        merged["shares_sold"] = round(merged["shares_sold"] + take, 6)
        merged["update_time"] = max(merged["update_time"], fill["time"])
        derived += take
    if not derived:
        return sell_trade
    merged["derived_pnl_shares"] = round(derived, 6)
    return merged


def _manual_position_rows(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    odds_root: Path,
    now_iso: str,
) -> list[dict[str, Any]]:
    """Ledger rows for exchange positions the buyer never opened.

    Without them a hand-placed position is invisible to settlement, to the dashboard,
    and to the buyer's own dedup, which is how it re-entered a game the operator
    had already traded by hand. Tagged ``manual`` so model audits can exclude them.
    """
    known = {(str(r.get("market_slug")), str(r.get("token_side")).lower()) for r in records}
    new_rows: list[dict[str, Any]] = []
    for slug, pos in (snapshot.get("positions") or {}).items():
        if not isinstance(pos, dict):
            continue
        try:
            net = float(pos.get("netPositionDecimal") or pos.get("netPosition") or 0.0)
            cost = abs(float((pos.get("cost") or {}).get("value")))
        except (TypeError, ValueError):
            continue
        if abs(net) < 0.01 or pos.get("expired"):
            continue
        side = "long" if net > 0 else "short"
        if (str(slug), side) in known:
            continue
        shares = abs(net)
        meta = pos.get("marketMetadata") or {}
        title = str(meta.get("title") or "")
        away, _, home = title.partition(" vs. ")
        start = ""
        line = None
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(slug))
        for path in (
            sorted(odds_root.glob(f"odds/*/{match.group(1)}/polymarket_snapshots.jsonl")) if match else []
        ):
            with suppress(OSError, ValueError):
                for text in path.read_text(encoding="utf-8").splitlines():
                    if slug in text:
                        rec = json.loads(text)
                        if rec.get("market_slug") == slug:
                            start = str(rec.get("event_start_utc") or start)
                            line = rec.get("line", line)
        # The other line of the same game is usually already in the ledger and carries
        # the real start time and team names; without a start time the row never settles.
        sibling = next(
            (
                r
                for r in records
                if event_key_from_slug(str(r.get("market_slug"))) == event_key_from_slug(slug)
            ),
            {},
        )
        start = start or str(sibling.get("event_start_utc") or "")
        away = str(sibling.get("away_team") or away).strip()
        home = str(sibling.get("home_team") or home).strip()
        sport = str(sibling.get("sport") or str(meta.get("eventSlug") or slug).split("-")[0]).upper()
        new_rows.append(
            {
                "order_id": f"MANUAL-{slug}",
                "pick_id": f"MANUAL-{slug}",
                "manual": True,
                "model_id": "manual",
                "sport": sport,
                "event_id": str(sibling.get("event_id") or ""),
                "market_slug": str(slug),
                "market_type": "spread" if _SPREAD_SIDE_SUFFIX.search(str(slug)) else "",
                "selection": "manual",
                "token_side": side,
                "away_team": away,
                "home_team": home,
                "line": line,
                "event_start_utc": start,
                "shares": round(shares, 4),
                "cost_usd": round(cost, 4),
                "entry_price": round(cost / shares, 4),
                "executed_at_utc": str(pos.get("updateTime") or now_iso),
                "status": "open",
                "result": "open",
                "order_state": "ORDER_STATE_FILLED",
                "fill_known": True,
                "fallback_reconciled": True,
                "rationale": "Manually placed position imported from the exchange",
            }
        )
    return new_rows


def _position_fully_exited(sell_trade: dict[str, Any] | None, held_shares: Any) -> bool:
    """Whether closing fills cover the whole recorded position.

    Selling part of a position must not settle the row: the remainder is still
    live and will be graded by the market's outcome.
    """
    if sell_trade is None:
        return False
    try:
        held = float(held_shares)
    except (TypeError, ValueError):
        return True
    # Both quantities are share-decimal; the slack only absorbs rounding, not a
    # real residual holding.
    return float(sell_trade.get("shares_sold") or 0.0) >= held - 0.05


def _exchange_resolution_result(
    resolution: dict[str, Any] | None,
    token_side: str,
    *,
    home_team: str | None = None,
    away_team: str | None = None,
    selection: str | None = None,
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

    # Some exchange resolutions (observed on single-team esports "tile"
    # markets) report side POSITION_RESOLUTION_SIDE_NEUTRAL with a zero
    # realized delta rather than LONG/SHORT, so neither check above can grade
    # them -- these positions would otherwise stay pending forever, no matter
    # how many settlement passes run. The resolution still names the winning
    # team directly (``winning_outcome``); fall back to comparing it against
    # our own row's team names when available.
    winning_outcome = str(resolution.get("winning_outcome") or "").strip().casefold()
    if winning_outcome and selection in {"home", "away"}:
        picked_team = str((home_team if selection == "home" else away_team) or "").strip().casefold()
        if picked_team and winning_outcome == picked_team:
            return "win"
        other_team = str((away_team if selection == "home" else home_team) or "").strip().casefold()
        if other_team and winning_outcome == other_team:
            return "loss"
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


def manually_settle_auto_buyer_order(
    result: str,
    data_root: Path | str | None = None,
    order_id: str | None = None,
    pick_id: str | None = None,
    home_score: float | None = None,
    away_score: float | None = None,
    reason: str = "",
    settlement_type: str | None = None,
    settlement_price: float | None = None,
) -> dict[str, Any]:
    """Operator override for a position no automated source can grade.

    Reserved for markets confirmed outside every automated settlement path's
    coverage -- e.g. ESPN's tennis scoreboard only covers the ATP/WTA main
    tours, never ITF/challenger-level events (confirmed live 2026-09-14
    against a stuck Auto-Buyer position: neither player name appeared in
    ESPN's WTA/ATP scoreboards for any nearby date). There is no free,
    already-integrated data source for that tier, so such a position would
    otherwise stay `open` forever with `settle_auto_buyer_ledger` never able
    to resolve it. This function never infers or guesses a result -- the
    caller must have independently verified the real-world outcome (e.g. from
    Polymarket's own resolved market page) before calling it, and the reason
    is recorded on the row for audit.
    """
    if result not in ("win", "loss", "push"):
        raise ValueError(f"result must be one of win/loss/push, got {result!r}")
    if not order_id and not pick_id:
        raise ValueError("must supply order_id or pick_id to identify the order")
    if not reason.strip():
        raise ValueError("reason is required -- record how the real-world result was verified")
    if settlement_type not in (None, "binary", "fair_value"):
        raise ValueError("settlement_type must be binary or fair_value")
    if settlement_type == "fair_value" and settlement_price is None:
        raise ValueError("settlement_price is required for fair_value settlement")
    if settlement_price is not None and not 0.0 <= float(settlement_price) <= 1.0:
        raise ValueError("settlement_price must be between 0 and 1")

    j_path, _ = resolve_auto_buyer_paths(data_root=data_root)
    root = j_path.parent
    records = read_auto_buyer_ledger(jsonl_path=j_path)
    target = None
    for r in records:
        if (order_id and r.get("order_id") == order_id) or (pick_id and r.get("pick_id") == pick_id):
            target = r
            break
    if target is None:
        raise KeyError(f"no matching Auto-Buyer order (order_id={order_id!r}, pick_id={pick_id!r})")
    if str(target.get("status") or "").lower() == "settled":
        raise ValueError(
            f"order {target.get('order_id')} is already settled -- "
            "manual override is only for permanently ungradeable open positions"
        )

    shares = float(target.get("shares") or 0.0)
    cost = float(target.get("cost_usd") or 0.0)
    unit_value_usd = float(target.get("unit_value_usd") or AUTO_BUYER_UNIT_VALUE_USD)
    if settlement_type == "fair_value":
        if result != "push":
            raise ValueError("fair_value settlement must use result='push'")
        pnl_usd = round(
            (float(settlement_price or 0.0) - float(target.get("entry_price") or 0.0)) * shares, 4
        )
    elif result == "win":
        pnl_usd = round(shares - cost, 4)
    elif result == "loss":
        pnl_usd = round(-cost, 4)
    else:
        pnl_usd = 0.0

    target["status"] = "settled"
    target["result"] = result
    target["pnl_usd"] = pnl_usd
    target["pnl_units"] = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
    target["home_score"] = home_score
    target["away_score"] = away_score
    target["settled_at_utc"] = iso_utc(utc_now())
    target["settlement_source"] = "manual_operator"
    target["manual_settlement_reason"] = reason
    if settlement_type is not None:
        target["settlement_type"] = settlement_type
    if settlement_price is not None:
        target["settlement_price"] = float(settlement_price)

    try:
        _write_jsonl_atomic(j_path, records)
    except (OSError, ValueError) as err:
        logger.error(f"Failed to rewrite auto_buyer_ledger.jsonl: {err}")
        raise

    # Reuse the settlement pass to rebuild the derived xlsx export from the
    # now-updated jsonl -- it re-verifies this row against ESPN (a harmless,
    # already-tolerant no-op for a record with no matching ESPN event) rather
    # than duplicating the xlsx-writing logic here.
    settle_auto_buyer_ledger(data_root=root)

    return {
        "order_id": target.get("order_id"),
        "pick_id": target.get("pick_id"),
        "result": result,
        "pnl_usd": pnl_usd,
        "settlement_source": "manual_operator",
    }


def _resolve_closing_probability_for_auto_buyer(
    data_root: Path | str,
    sport: str,
    slug: str,
    event_start_utc: str,
    token_side: str,
    selection: str = "",
    home_team: str = "",
    away_team: str = "",
) -> float | None:
    """Resolve pregame closing ask probability for an auto-buyer order."""
    if not slug or not event_start_utc:
        return None
    try:
        from datetime import timedelta

        from ..data_sources.polymarket_us import PolymarketSnapshotStore
        from ..domain import EASTERN, parse_utc
        from ..learned_forward import _team_matches

        start = parse_utc(event_start_utc)
        sport_dir = sport.lower()
        dates_to_try = [
            start.astimezone(EASTERN).date().isoformat(),
            start.date().isoformat(),
            (start - timedelta(days=1)).date().isoformat(),
            (start + timedelta(days=1)).date().isoformat(),
        ]
        snap = None
        seen: set[str] = set()
        for d in dates_to_try:
            if d in seen:
                continue
            seen.add(d)
            store = PolymarketSnapshotStore.for_sport_date(data_root, sport_dir, d)
            snap = store.closing_snapshot(slug, event_start_utc)
            if snap is not None:
                break
        if snap is None:
            return None

        side_obj = None
        ts = str(token_side).strip().lower()
        if ts in ("long", "short"):
            side_obj = snap.get(ts)
        else:
            sel = str(selection).strip().lower()
            long_desc = str((snap.get("long") or {}).get("description", ""))
            short_desc = str((snap.get("short") or {}).get("description", ""))
            if snap.get("market_type") == "spread" or (
                long_desc.startswith(("+", "-")) and short_desc.startswith(("+", "-"))
            ):
                ref_team = snap.get("team")
                if ref_team:
                    if _team_matches(home_team, ref_team):
                        side_obj = snap.get("long") if sel in ("home", "1") else snap.get("short")
                    elif _team_matches(away_team, ref_team):
                        side_obj = snap.get("long") if sel in ("away", "2") else snap.get("short")
            elif (
                long_desc.strip().lower() in ("yes", "over")
                or short_desc.strip().lower() in ("no", "under")
                or "over" in long_desc.lower()
                or "under" in short_desc.lower()
            ):
                if sel in ("under", "no", "away", "short") and (
                    short_desc.strip().lower() in ("no", "under") or "under" in short_desc.lower()
                ):
                    side_obj = snap.get("short")
                else:
                    side_obj = snap.get("long")
            else:
                sel_name = home_team if sel == "home" else away_team
                if _team_matches(sel_name, long_desc):
                    side_obj = snap.get("long")
                elif _team_matches(sel_name, short_desc):
                    side_obj = snap.get("short")

        if side_obj and side_obj.get("ask") is not None:
            ask = float(side_obj["ask"])
            if 0 < ask < 1:
                return round(ask, 6)
    except (KeyError, ValueError, OSError, TypeError):
        logger.debug("Failed closing lookup for %s", slug, exc_info=True)
    return None


def settle_auto_buyer_ledger(
    data_root: Path | str | None = None,
    espn: ESPNClient | None = None,
    polymarket_executor: Any | None = None,
    polymarket_client: Any | None = None,
    esports_client: Any | None = None,
) -> dict[str, Any]:
    """Settle completed matches in the Auto-Buyer Ledger and compute realized PnL."""
    j_path, x_path = resolve_auto_buyer_paths(data_root=data_root)
    root = j_path.parent
    odds_root = root.parent if root.name == "paper_auto_buyer" else root
    if not (odds_root / "odds").exists() and (DATA / "odds").exists():
        odds_root = DATA
    _esports_cache: dict[str, dict[str, Any]] = {}
    _polymarket_market_cache: dict[str, dict[str, Any]] = {}
    _polymarket_market_client: Any | None = polymarket_client
    _espn_scoreboard_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _get_scoreboard(lg: str, dt_str: str) -> dict[str, Any]:
        k = (lg, dt_str)
        if k not in _espn_scoreboard_cache:
            try:
                _espn_scoreboard_cache[k] = espn_client.scoreboard(lg, dt_str)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                _espn_scoreboard_cache[k] = {}
        return _espn_scoreboard_cache[k]

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
    sell_trades: dict[str, dict[str, Any]] = {}
    offset_fills: dict[str, list[dict[str, Any]]] = {}
    exchange_snapshot: dict[str, Any] | None = None
    if polymarket_executor is not None:
        try:
            snapshot = polymarket_executor.portfolio_snapshot()
            exchange_snapshot = snapshot
            pm_resolutions.update(_exchange_position_resolutions(snapshot))
            sell_trades.update(_exchange_sell_trades(snapshot))
            offset_fills.update(_exchange_offset_fills(snapshot))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
    elif data_root is None:
        try:
            from ..audit import AuditLog
            from ..data_sources.polymarket_execute import PolymarketExecutor

            executor = PolymarketExecutor(audit=AuditLog(root / "audit.jsonl"))
            snapshot = executor.portfolio_snapshot()
            exchange_snapshot = snapshot
            pm_resolutions.update(_exchange_position_resolutions(snapshot))
            sell_trades.update(_exchange_sell_trades(snapshot))
            offset_fills.update(_exchange_offset_fills(snapshot))
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
            if not r.get("probability_clv") or r.get("probability_clv") == "0.0":
                c_prob = _resolve_closing_probability_for_auto_buyer(
                    odds_root,
                    sport,
                    slug,
                    str(r.get("event_start_utc") or ""),
                    r.get("token_side") or "",
                    selection=sel,
                    home_team=home,
                    away_team=away,
                )
                if c_prob is not None:
                    r["closing_price"] = f"{c_prob:.6f}"
                    r["probability_clv"] = f"{c_prob - price:+.6f}"
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
            corrected_res = _exchange_resolution_result(
                exchange_resolution,
                token_side,
                home_team=r.get("home_team"),
                away_team=r.get("away_team"),
                selection=str(r.get("selection") or "").lower(),
            )
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
                start_str = str(r.get("event_start_utc") or "")
                s_dt = parse_utc(start_str) if start_str else now
                if abs((now - s_dt).total_seconds()) > 3 * 86400:
                    settled_count += 1
                    updated_records.append(r)
                    continue
                try:
                    from ..tennis_forward import TENNIS_TOURS

                    game_day = s_dt.strftime("%Y%m%d")
                    t_match_found = False
                    for tour in TENNIS_TOURS:
                        sb = _get_scoreboard(tour, game_day)
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
            if not r.get("probability_clv") or r.get("probability_clv") == "0.0":
                c_prob = _resolve_closing_probability_for_auto_buyer(
                    odds_root,
                    sport,
                    slug,
                    str(r.get("event_start_utc") or ""),
                    r.get("token_side") or "",
                    selection=sel,
                    home_team=home,
                    away_team=away,
                )
                if c_prob is not None:
                    r["closing_price"] = f"{c_prob:.6f}"
                    r["probability_clv"] = f"{c_prob - price:+.6f}"
            settled_count += 1
            updated_records.append(r)
            continue

        start_str = str(r.get("event_start_utc") or "")
        try:
            start_dt = parse_utc(start_str)
        except (ValueError, TypeError):
            updated_records.append(r)
            continue

        exchange_resolution = pm_resolutions.get(slug)
        sell_trade = sell_trades.get(slug) if slug else None
        if slug:
            sell_trade = _with_offset_exits(sell_trade, offset_fills.get(slug), r)
        if sell_trade is not None:
            # Recorded even for a partial exit, which leaves the row open: the buyer
            # reads this to stop adding to a position the operator is selling out of.
            r["manual_exit_shares"] = sell_trade.get("shares_sold")
        if not _position_fully_exited(sell_trade, r.get("shares")):
            sell_trade = None
        if start_dt > now and exchange_resolution is None and sell_trade is None:
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
        sell_pnl_usd: float | None = None

        # Resolve terminal gateway markets before calling slower sport-specific
        # providers. This is essential for paper rows: a resolved walkover or
        # cancellation has a final fair price, and waiting on BO3/ESPN first
        # used to leave the row open indefinitely when that provider was down.
        if slug and sell_pnl_usd is None:
            try:
                from ..data_sources.polymarket_us import PolymarketUSClient

                if _polymarket_market_client is None:
                    _polymarket_market_client = PolymarketUSClient()
                m_info = _polymarket_market_cache.get(slug)
                if m_info is None:
                    m_info = _polymarket_market_client.market(slug)
                    _polymarket_market_cache[slug] = m_info
                if m_info.get("status") == "MARKET_STATUS_RESOLVED":
                    raw_pxs = m_info.get("outcomePrices")
                    prices = json.loads(raw_pxs) if isinstance(raw_pxs, str) else (raw_pxs or [])
                    token_side = str(r.get("token_side") or "").lower()
                    terminal_winners = [
                        index for index, raw_price in enumerate(prices) if abs(float(raw_price) - 1.0) <= 1e-9
                    ]
                    if len(prices) == 2 and len(terminal_winners) == 1 and token_side in {"long", "short"}:
                        winning_side = "long" if terminal_winners[0] == 0 else "short"
                        result = "win" if token_side == winning_side else "loss"
                    elif (
                        len(prices) == 2
                        and token_side in {"long", "short"}
                        and (
                            len(terminal_winners) == 0
                            or any(
                                term in str(m_info.get("description") or "").lower()
                                for term in (
                                    "fair market price",
                                    "fair price",
                                    "last fair market price",
                                    "settle to fair",
                                )
                            )
                        )
                    ):
                        settlement_index = 0 if token_side == "long" else 1
                        r["settlement_type"] = "fair_value"
                        r["settlement_price"] = float(prices[settlement_index])
                        r["settlement_source"] = "polymarket_resolved_fair_price"
                        result = "push"
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # A sell trade means the position no longer exists to be graded by the
        # market's eventual outcome -- it must take priority over any later
        # resolution/score check, and its own realizedPnl is authoritative.
        if sell_trade is not None:
            sell_pnl_usd = sell_trade.get("realized_pnl_usd")
            if sell_pnl_usd is not None:
                result = "win" if sell_pnl_usd > 0 else ("loss" if sell_pnl_usd < 0 else "push")
        # Check direct Polymarket exchange position resolution first (for esports & direct resolutions)
        elif result is not None:
            pass
        elif slug and slug in pm_resolutions:
            pm_res = pm_resolutions[slug]
            result = _exchange_resolution_result(
                pm_res,
                str(r.get("token_side") or "").lower(),
                home_team=r.get("home_team"),
                away_team=r.get("away_team"),
                selection=str(r.get("selection") or "").lower(),
            )
        elif result is None and sport in ("TENNIS", "WTA", "ATP"):
            try:
                from ..tennis_forward import TENNIS_TOURS

                game_day = start_dt.strftime("%Y%m%d")
                for tour in TENNIS_TOURS:
                    sb = _get_scoreboard(tour, game_day)
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
        elif result is None and sport in ESPORTS_TITLE_BY_SPORT:
            # Polymarket US's exchange resolution field (`outcomePrices`) does
            # not reliably snap to 0/1 for esports moneylines the way it does
            # for other sports -- observed live 2026-09-14, six CS2 paper
            # positions stuck `open` for 2-5 days past event start with the
            # market genuinely `MARKET_STATUS_RESOLVED` but outcomePrices
            # still showing pre-close trading prices. BO3 (bo3.gg) is the
            # actual results source behind these markets (see the
            # PROVIDER_PANDASCORE team id on the market's own metadata, and
            # this codebase's existing esports baseline, which is trained
            # from BO3 match history) and exposes a real, explicit
            # `winner_team_id` per finished match with no API key required.
            try:
                title = ESPORTS_TITLE_BY_SPORT[sport]
                cache = _esports_cache.setdefault(title, {"client": esports_client or Bo3EsportsClient()})
                if "teams" not in cache:
                    cache["teams"], _ = cache["client"].teams(title)
                window_start = (start_dt - timedelta(days=2)).date()
                window_end = now.date()
                matches_key = (window_start, window_end)
                if cache.get("matches_key") != matches_key:
                    cache["matches"], _ = cache["client"].finished_matches(title, window_start, window_end)
                    cache["matches_key"] = matches_key
                teams = cache["teams"]
                matches = cache["matches"]
                team_index = _team_alias_index(teams)
                manual_aliases = _load_manual_aliases(root).get(title, {})

                home_id = _resolve_esports_team(home, teams, team_index, manual_aliases)
                away_id = _resolve_esports_team(away, teams, team_index, manual_aliases)
                if home_id and away_id:
                    for m in matches:
                        match_teams = {m["team1_id"], m["team2_id"]}
                        if match_teams != {home_id, away_id}:
                            continue
                        home_win = m["winner_id"] == home_id
                        away_win = m["winner_id"] == away_id
                        if (sel in ("away", "short") and away_win) or (sel in ("home", "long") and home_win):
                            result = "win"
                        elif away_win or home_win:
                            result = "loss"
                        else:
                            continue
                        home_score = m["team1_score"] if m["team1_id"] == home_id else m["team2_score"]
                        away_score = m["team2_score"] if m["team2_id"] == away_id else m["team1_score"]
                        break
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass
        if (result is None or home_score is None or away_score is None) and sport in (
            "MLB",
            "WNBA",
            "NBA",
            "NFL",
            "SOCCER",
            "NCAAF",
            "CFB",
            "NCAAB",
            "CBB",
            "NHL",
            "HOCKEY",
        ):
            try:
                game_day = start_dt.strftime("%Y%m%d")
                prev_day = (start_dt - timedelta(days=1)).strftime("%Y%m%d")
                next_day = (start_dt + timedelta(days=1)).strftime("%Y%m%d")
                if sport == "SOCCER":
                    espn_lg = "soccer_all"
                elif sport in ("NCAAF", "CFB"):
                    espn_lg = "ncaaf"
                elif sport in ("NCAAB", "CBB"):
                    espn_lg = "ncaab"
                elif sport in ("NHL", "HOCKEY"):
                    espn_lg = "nhl"
                else:
                    espn_lg = sport.lower()

                away_key = _scoreboard_team_key(away, espn_lg.upper())
                home_key = _scoreboard_team_key(home, espn_lg.upper())
                candidates: dict[str, tuple[float, float]] = {}
                for d_check in (game_day, prev_day, next_day):
                    sb = _get_scoreboard(espn_lg, d_check)
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
                        a_team = str((a_c.get("team") or {}).get("displayName", ""))
                        h_team = str((h_c.get("team") or {}).get("displayName", ""))
                        if not a_team or not h_team or not home_key or not away_key:
                            continue
                        if (
                            _scoreboard_team_key(a_team, espn_lg.upper()) == away_key
                            and _scoreboard_team_key(h_team, espn_lg.upper()) == home_key
                        ):
                            event_key = str(ev.get("id") or f"{a_team}:{h_team}")
                            candidates[event_key] = (float(a_c["score"]), float(h_c["score"]))
                if len(candidates) == 1:
                    a_sc, h_sc = next(iter(candidates.values()))
                    away_score = int(a_sc)
                    home_score = int(h_sc)

                    if result is None:
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
                            elif a_sc == h_sc:
                                if sel in ("draw", "tie"):
                                    result = "win"
                                elif sport == "SOCCER":
                                    result = "loss"
                                else:
                                    result = "push"
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

                if _polymarket_market_client is None:
                    _polymarket_market_client = PolymarketUSClient()
                m_info = _polymarket_market_cache.get(slug)
                if m_info is None:
                    m_info = _polymarket_market_client.market(slug)
                    _polymarket_market_cache[slug] = m_info
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
                    elif (
                        m_info.get("status") == "MARKET_STATUS_RESOLVED"
                        and len(prices) == 2
                        and token_side in {"long", "short"}
                        and (
                            len(terminal_winners) == 0
                            or any(
                                term in str(m_info.get("description") or "").lower()
                                for term in (
                                    "fair market price",
                                    "fair price",
                                    "last fair market price",
                                    "settle to fair",
                                )
                            )
                        )
                    ):
                        # Sports markets explicitly use the final fair price
                        # for walkovers, cancellations, forfeits, and other
                        # non-binary outcomes. Treat this as a settled push for
                        # win-rate accounting, but preserve the actual mark-to-
                        # fair P&L instead of leaving the position open or
                        # pretending it was a binary win/loss.
                        settlement_index = 0 if token_side == "long" else 1
                        r["settlement_type"] = "fair_value"
                        r["settlement_price"] = float(prices[settlement_index])
                        r["settlement_source"] = "polymarket_resolved_fair_price"
                        result = "push"
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        if result is not None:
            settled_count += 1
            if sell_pnl_usd is not None:
                # Authoritative from the exchange's own trade record -- never
                # re-derive this one from shares/entry price, which would silently
                # diverge from what Polymarket actually realized on the sale.
                pnl_usd = round(sell_pnl_usd, 4)
                pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                r["settlement_source"] = "manual_sell"
                if sell_trade is not None and sell_trade.get("derived_pnl_shares"):
                    r["settlement_pnl_derived_shares"] = sell_trade["derived_pnl_shares"]
            else:
                exchange_resolution = pm_resolutions.get(slug)
                settlement_cost = _exchange_settlement_cost(exchange_resolution, shares, cost)
                if result == "win":
                    pnl_usd = round(shares - settlement_cost, 4)
                    pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                elif result == "loss":
                    pnl_usd = round(-settlement_cost, 4)
                    pnl_units = _usd_to_auto_buyer_units(pnl_usd, unit_value_usd)
                elif result == "push" and r.get("settlement_type") == "fair_value":
                    settlement_price = float(r.get("settlement_price") or 0.0)
                    pnl_usd = round((settlement_price - price) * shares, 4)
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

            c_prob = _resolve_closing_probability_for_auto_buyer(
                odds_root,
                sport,
                slug,
                start_str,
                r.get("token_side") or "",
                selection=sel,
                home_team=home,
                away_team=away,
            )
            if c_prob is not None:
                r["closing_price"] = f"{c_prob:.6f}"
                r["probability_clv"] = f"{c_prob - price:+.6f}"
            elif r.get("probability_clv") in (None, "", "0.0"):
                r["probability_clv"] = ""

            if _uses_live_auto_buyer_ledger(j_path):
                log_auto_buyer_event(
                    f"SETTLED [{sport}] {away} @ {home} | Result: {result.upper()} | PnL: ${pnl_usd:+.2f} ({pnl_units:+.1f}U) | Order: {r.get('order_id')}"
                )
        else:
            pending_count += 1

        updated_records.append(r)

    if exchange_snapshot is not None and root.name != "paper_auto_buyer":
        updated_records.extend(
            _manual_position_rows(exchange_snapshot, updated_records, odds_root, iso_utc(now))
        )

    # Rewrite JSONL atomically
    try:
        _write_jsonl_atomic(j_path, updated_records)
    except (OSError, ValueError) as err:
        logger.error(f"Failed to rewrite auto_buyer_ledger.jsonl: {err}")

    # Rewrite Excel atomically
    try:
        xlsx_rows = []
        for r in updated_records:
            american = _probability_to_american(float(r.get("entry_price") or 0.50))
            decimal_odds = american_to_decimal(american)
            line_val = r.get("line")
            line_str = f"{float(str(line_val)):.2f}" if line_val not in (None, "") else ""
            x_row = dict.fromkeys(FIELDNAMES, "")
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
                    "closing_implied_probability": str(r.get("closing_price") or ""),
                    "probability_clv": str(r.get("probability_clv") or ""),
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
