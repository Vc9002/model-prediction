"""Polymarket Edge Ledger: records and manages trades from the Polymarket CLOB Edge Scanner.

Uses the standard project Pick Ledger format, supporting the shared ledger table
renderers, audit logging, and settlement.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import utc_now
from ..ledger import FIELDNAMES
from ..pricing import american_to_decimal
from ..xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

logger = logging.getLogger(__name__)

DEFAULT_POLY_LEDGER_PATH = PROJECT_ROOT / "data" / "polymarket_picks.xlsx"


def _probability_to_american(prob: float) -> int:
    """Convert decimal probability (0.01 to 0.99) to American moneyline odds."""
    p = max(0.01, min(0.99, float(prob)))
    if p >= 0.50:
        return round(-100.0 * p / (1.0 - p))
    return round(100.0 * (1.0 - p) / p)


def _get_ledger_path(data_root: Path | str | None = None) -> Path:
    if data_root is not None:
        p = Path(data_root)
        if p.is_dir():
            return p / "polymarket_picks.xlsx"
        return p
    return DEFAULT_POLY_LEDGER_PATH


def read_polymarket_ledger_rows(data_root: Path | str | None = None) -> list[dict[str, Any]]:
    """Read all rows from the Polymarket Edge Ledger."""
    path = _get_ledger_path(data_root)
    if not path.exists():
        return []
    _, rows = read_xlsx_rows(path)
    return rows


def record_polymarket_orders(
    orders: list[dict[str, Any]],
    data_root: Path | str | None = None,
    replace_today: bool = False,
) -> dict[str, Any]:
    """Record a list of order ticket dicts/decisions to the Polymarket Edge Ledger."""
    path = _get_ledger_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        _, existing_rows = read_xlsx_rows(path)
    else:
        existing_rows = []

    existing_by_id = {str(r.get("pick_id")): r for r in existing_rows}

    now_iso = utc_now().isoformat()
    recorded_count = 0
    skipped_duplicates = 0
    new_rows: list[dict[str, Any]] = []

    for order in orders:
        market_id = str(order.get("market_id") or "")
        side = str(order.get("side") or "BUY_YES")
        event_start = str(order.get("event_start_utc") or "")
        target_sel = str(order.get("target_selection") or "")
        home = str(order.get("home_team") or "")
        away = str(order.get("away_team") or "")
        question = str(order.get("question") or f"{home} vs {away}")
        league = str(order.get("league") or "POLYMARKET").upper()

        # Infer league if default
        if league == "POLYMARKET" or not league:
            for s in (
                "MLB",
                "WNBA",
                "NBA",
                "NFL",
                "CS2",
                "LOL",
                "DOTA2",
                "VALORANT",
                "TENNIS",
                "SOCCER",
                "KBO",
                "NPB",
            ):
                if s in question.upper():
                    league = s
                    break

        order_price = float(order.get("order_price") or 0.50)
        market_price = float(order.get("market_price") or order_price)
        model_prob = float(order.get("model_probability") or 0.50)
        edge = float(order.get("edge") or (model_prob - order_price))
        ev_pct = float(order.get("ev_pct") or 0.0)
        stake = float(order.get("stake_units") or 1.0)
        reason = str(order.get("reason") or f"{side} on {target_sel} (Edge +{edge:.1%})")

        # Deterministic 16-character pick_id based on market_id, side, and start
        raw_key = f"{market_id}:{side}:{event_start}:{target_sel}"
        pick_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

        if pick_id in existing_by_id:
            skipped_duplicates += 1
            continue

        american = _probability_to_american(order_price)
        decimal_odds = american_to_decimal(american)

        row: dict[str, Any] = {
            "pick_id": pick_id,
            "created_at_utc": now_iso,
            "event_start_utc": event_start,
            "event_id": market_id,
            "league": league,
            "away_team": away,
            "home_team": home,
            "market_type": "moneyline",
            "selection": target_sel or ("home" if side == "BUY_YES" else "away"),
            "line": "",
            "sportsbook": "polymarket_us",
            "american_odds": american,
            "decimal_odds": round(decimal_odds, 4),
            "market_implied_probability": round(market_price, 4),
            "model_probability": round(model_prob, 4),
            "model_uncertainty": 0.0,
            "edge": round(edge, 4),
            "trade_candidate": 1,
            "confidence_score": round(ev_pct, 2),
            "units": round(stake, 2),
            "model_version": "polymarket-kelly-v1",
            "status": "open",
            "result": "",
            "away_score": "",
            "home_score": "",
            "probability_clv": 0.0,
            "pnl_units": "",
            "settled_at_utc": "",
            "record_type": "decision",
            "decision": "take",
            "reason_code": "POLYMARKET_CLOB_EDGE",
            "decision_no_vig_probability": round(market_price, 4),
            "sportsbook_key": "polymarket_us",
            "rationale": reason,
            "risks": "CLOB liquidity / spread slippage",
            "ledger_schema_version": 4,
        }
        new_rows.append(row)
        existing_by_id[pick_id] = row
        recorded_count += 1

    if new_rows:
        all_rows = existing_rows + new_rows
        # Sort by event_start_utc descending
        all_rows.sort(
            key=lambda r: str(r.get("event_start_utc") or r.get("created_at_utc") or ""), reverse=True
        )
        write_xlsx_rows_atomic(path, FIELDNAMES, all_rows)

    return {
        "status": "ok",
        "recorded_count": recorded_count,
        "skipped_duplicates": skipped_duplicates,
        "total_rows": len(existing_by_id),
        "ledger_path": str(path),
    }


def settle_polymarket_ledger_rows(
    data_root: Path | str | None = None,
    espn_client: Any | None = None,
) -> dict[str, Any]:
    """Settle open rows in the Polymarket Edge Ledger against ESPN and match scores."""
    from ..data_sources.espn import ESPNClient
    from ..domain import MarketType, PickResult
    from ..pricing import grade_pick, profit_units

    path = _get_ledger_path(data_root)
    if not path.exists():
        return {"settled_count": 0, "open_count": 0, "total_rows": 0, "settled_picks": []}

    _, rows = read_xlsx_rows(path)
    now = utc_now()
    now_iso = now.isoformat()
    espn = espn_client or ESPNClient()

    settled_picks: list[dict[str, Any]] = []
    open_count = 0
    modified = False

    # League to ESPN league mapping
    league_map = {
        "MLB": ("mlb",),
        "NBA": ("nba",),
        "WNBA": ("wnba",),
        "NFL": ("nfl",),
        "SOCCER": (
            "eng.1",
            "esp.1",
            "ger.1",
            "ita.1",
            "fra.1",
            "usa.1",
            "uefa.champions",
            "uefa.europa",
            "mex.1",
        ),
    }

    for row in rows:
        status = str(row.get("status") or "").lower()
        if status != "open":
            continue

        open_count += 1
        event_start_str = str(row.get("event_start_utc") or "")
        if not event_start_str:
            continue

        try:
            start_dt = datetime.fromisoformat(event_start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now.tzinfo)
        except (ValueError, TypeError):
            continue

        # Check games that have already started
        if start_dt > now:
            continue

        lg = str(row.get("league") or "").upper()
        espn_leagues = league_map.get(lg, (lg.lower(),))
        game_day = start_dt.date().isoformat()

        away_name = str(row.get("away_team") or "").casefold()
        home_name = str(row.get("home_team") or "").casefold()

        match_found = None
        for espn_lg in espn_leagues:
            try:
                sb = espn.scoreboard(espn_lg, game_day)
                for event in sb.get("events", []):
                    status_type = event.get("status", {}).get("type", {})
                    if not status_type.get("completed", False):
                        continue
                    comps = event.get("competitions", [{}])[0].get("competitors", [])
                    if len(comps) != 2:
                        continue
                    c_away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
                    c_home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])

                    h_names = {
                        str(c_home.get("team", {}).get("displayName") or "").casefold(),
                        str(c_home.get("team", {}).get("name") or "").casefold(),
                        str(c_home.get("team", {}).get("abbreviation") or "").casefold(),
                    }
                    a_names = {
                        str(c_away.get("team", {}).get("displayName") or "").casefold(),
                        str(c_away.get("team", {}).get("name") or "").casefold(),
                        str(c_away.get("team", {}).get("abbreviation") or "").casefold(),
                    }

                    if (home_name in h_names or any(hn in home_name for hn in h_names if len(hn) > 3)) and (
                        away_name in a_names or any(an in away_name for an in a_names if len(an) > 3)
                    ):
                        try:
                            h_score = int(c_home.get("score", 0))
                            a_score = int(c_away.get("score", 0))
                            match_found = (a_score, h_score)
                        except (ValueError, TypeError):
                            continue
                if match_found is not None:
                    break
            except (KeyError, TypeError, ValueError, OSError):
                logger.debug("Scoreboard lookup failed for %s on %s", espn_lg, game_day, exc_info=True)
                continue

        if match_found is not None:
            a_score, h_score = match_found
            mtype_str = str(row.get("market_type") or "moneyline").lower()
            try:
                mtype = MarketType(mtype_str)
            except ValueError:
                mtype = MarketType.MONEYLINE

            sel = str(row.get("selection") or "home").lower()
            line_val = None
            if row.get("line") not in (None, ""):
                try:
                    line_val = float(row["line"])
                except (ValueError, TypeError):
                    line_val = None

            graded_result = grade_pick(
                market_type=mtype,
                selection=sel,
                line=line_val,
                away_score=a_score,
                home_score=h_score,
                league=lg,
            )

            units = float(row.get("units") or 1.0)
            dec_odds = float(row.get("decimal_odds") or 1.909)
            pnl = profit_units(graded_result, units, dec_odds)

            row["status"] = "settled"
            row["result"] = (
                "win"
                if graded_result is PickResult.WIN
                else ("loss" if graded_result is PickResult.LOSS else "push")
            )
            row["away_score"] = a_score
            row["home_score"] = h_score
            row["pnl_units"] = round(pnl, 4)
            row["settled_at_utc"] = now_iso
            modified = True
            open_count -= 1
            settled_picks.append({"pick_id": row.get("pick_id"), "result": row["result"], "pnl": pnl})

    if modified:
        write_xlsx_rows_atomic(path, FIELDNAMES, rows)

    return {
        "status": "ok",
        "settled_count": len(settled_picks),
        "open_count": open_count,
        "total_rows": len(rows),
        "settled_picks": settled_picks,
    }
