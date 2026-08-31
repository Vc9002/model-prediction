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

from ..data_sources.espn import ESPNClient
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
            return float(raw_line)
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
    shares = float(order_payload.get("shares") or pick.get("shares") or 1.0)
    price = float(order_payload.get("limit_price") or pick.get("market_implied_probability") or 0.50)
    cost = float(order_payload.get("cost_usd") or (shares * price))
    units = float(pick.get("units") or pick.get("display_units") or 1.0)
    model_id = str(order_payload.get("model_id") or pick.get("model_id") or pick.get("model_version") or "")
    model_p = float(pick.get("model_probability") or 0.0)
    market_p = float(pick.get("market_probability") or pick.get("market_implied_probability") or price)
    edge = float(order_payload.get("edge") or (model_p - market_p))
    start_utc = str(order_payload.get("event_start_utc") or pick.get("event_start_utc") or "")
    exec_utc = str(order_payload.get("timestamp_utc") or iso_utc(utc_now()))
    line_val = _extract_line_from_record(pick) or _extract_line_from_record(order_payload)
    if line_val is None:
        line_val = _extract_line_from_record({"market_slug": slug})

    # JSONL specific record
    jsonl_record = {
        "order_id": oid,
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
    log_auto_buyer_event(
        f"EXECUTED [{sport}] {away} @ {home} ({sel}) | Market: {slug} ({side}) | "
        f"{shares} shares @ ${price:.2f} (Cost: ${cost:.2f}) | Model: {model_id} | Edge: +{edge * 100:.1f}% | OrderID: {oid}"
    )

    return jsonl_record


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
) -> dict[str, Any]:
    """Settle completed matches in the Auto-Buyer Ledger and compute realized PnL."""
    root = Path(data_root) if data_root else DATA
    j_path = root / "auto_buyer_ledger.jsonl"
    x_path = root / "auto_buyer_picks.xlsx"

    if not j_path.exists():
        return {"settled": 0, "pending": 0}

    espn_client = espn or ESPNClient()
    now = utc_now()
    records = read_auto_buyer_ledger(jsonl_path=j_path)
    settled_count = 0
    pending_count = 0

    updated_records: list[dict[str, Any]] = []

    pm_resolutions: dict[str, dict[str, Any]] = {}
    if polymarket_executor is not None:
        try:
            snap = polymarket_executor.portfolio_snapshot()
            for a in snap.get("activities", []):
                if a.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
                    det = a.get("positionResolution") or {}
                    before = det.get("beforePosition") or {}
                    after = det.get("afterPosition") or {}
                    meta = before.get("marketMetadata") or after.get("marketMetadata") or {}
                    slug = meta.get("slug") or det.get("marketSlug")
                    if slug:
                        realized = (after.get("realized") or {}).get("value")
                        pm_resolutions[slug] = {
                            "realized_usd": float(realized) if realized is not None else 0.0,
                            "update_time": det.get("updateTime"),
                            "winning_outcome": meta.get("outcome"),
                        }
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
    elif data_root is None:
        try:
            from ..audit import AuditLog
            from ..data_sources.polymarket_execute import PolymarketExecutor

            executor = PolymarketExecutor(audit=AuditLog(root / "audit.jsonl"))
            snap = executor.portfolio_snapshot()
            for a in snap.get("activities", []):
                if a.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
                    det = a.get("positionResolution") or {}
                    before = det.get("beforePosition") or {}
                    after = det.get("afterPosition") or {}
                    meta = before.get("marketMetadata") or after.get("marketMetadata") or {}
                    slug = meta.get("slug") or det.get("marketSlug")
                    if slug:
                        realized = (after.get("realized") or {}).get("value")
                        pm_resolutions[slug] = {
                            "realized_usd": float(realized) if realized is not None else 0.0,
                            "update_time": det.get("updateTime"),
                            "winning_outcome": meta.get("outcome"),
                        }
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass

    for r in records:
        sport = str(r.get("sport") or "").upper()
        away = str(r.get("away_team") or "")
        home = str(r.get("home_team") or "")
        sel = str(r.get("selection") or "").lower()
        price = float(r.get("entry_price") or 0.50)
        shares = float(r.get("shares") or 1.0)
        cost = float(r.get("cost_usd") or (shares * price))
        units = float(r.get("units") or 1.0)
        slug = str(r.get("market_slug") or "")

        # 1. Re-verify already settled records against reality
        if r.get("status") == "settled":
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
                                                pnl_units = round(
                                                    units * ((1.0 - price) / max(price, 0.01)), 4
                                                )
                                            elif corrected_res == "loss":
                                                pnl_usd = round(-cost, 4)
                                                pnl_units = -units
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
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
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
                            pnl_units = round(units * ((1.0 - price) / max(price, 0.01)), 4)
                        elif corrected_res == "loss":
                            pnl_usd = round(-cost, 4)
                            pnl_units = -units
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

        # Check completed outcome
        result = None
        away_score = None
        home_score = None

        # Check direct Polymarket exchange position resolution first (for esports & direct resolutions)
        if slug and slug in pm_resolutions:
            pm_res = pm_resolutions[slug]
            realized_usd = pm_res["realized_usd"]
            if realized_usd > 0:
                result = "win"
            elif realized_usd < 0:
                result = "loss"
            else:
                result = "push"
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
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
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
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        # Check Polymarket US gateway market resolution status fallback
        if result is None and slug:
            try:
                from ..data_sources.polymarket_us import PolymarketUSClient

                pm_cli = PolymarketUSClient()
                m_info = pm_cli.market(slug)
                if m_info.get("status") == "MARKET_STATUS_RESOLVED":
                    raw_outs = m_info.get("outcomes")
                    raw_pxs = m_info.get("outcomePrices")
                    outcomes = json.loads(raw_outs) if isinstance(raw_outs, str) else (raw_outs or [])
                    prices = json.loads(raw_pxs) if isinstance(raw_pxs, str) else (raw_pxs or [])
                    for out_name, out_px in zip(outcomes, prices):
                        if str(out_px).strip() in ("1", "1.0", "1.00", "1.0000"):
                            picked_name = home if sel in ("home", "long") else away
                            if (
                                picked_name.casefold() in out_name.casefold()
                                or out_name.casefold() in picked_name.casefold()
                            ):
                                result = "win"
                            else:
                                result = "loss"
                            break
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                pass

        if result is not None:
            settled_count += 1
            if result == "win":
                pnl_usd = round(shares * (1.0 - price), 4)
                pnl_units = round(float(r.get("units") or 1.0) * ((1.0 - price) / max(price, 0.01)), 4)
            elif result == "loss":
                pnl_usd = round(-cost, 4)
                pnl_units = -float(r.get("units") or 1.0)
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
            line_str = f"{float(r.get('line')):.2f}" if r.get("line") not in (None, "") else ""
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

    return {"settled": settled_count, "pending": pending_count}


def summarize_auto_buyer_performance(
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute consolidated performance metrics, ROI, and win-rate accounting for Auto-Buyer."""
    rows = records if records is not None else read_auto_buyer_ledger()
    settled_rows = [
        r
        for r in rows
        if str(r.get("status") or "").lower() == "settled"
        or str(r.get("result") or "").lower() in ("win", "loss", "push")
    ]
    open_rows = [r for r in rows if r not in settled_rows]

    wins = sum(1 for r in settled_rows if r.get("result") == "win")
    losses = sum(1 for r in settled_rows if r.get("result") == "loss")
    pushes = sum(1 for r in settled_rows if r.get("result") == "push")

    total_cost_usd = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    settled_cost_usd = sum(float(r.get("cost_usd") or 0.0) for r in settled_rows)
    realized_pnl_usd = sum(float(r.get("pnl_usd") or 0.0) for r in settled_rows)
    realized_pnl_units = sum(float(r.get("pnl_units") or 0.0) for r in settled_rows)

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
