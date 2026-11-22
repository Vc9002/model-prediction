"""ESPN core-odds items -> NBA research line records (quick-261120-nbf).

Adapted from ``scripts/ncaab_espn_lines.py`` (same orientation rule: the home spread comes
from the ``details`` text plus the favourite flags, never from the raw ``spread`` sign) with
NBA plausibility bands, and with moneylines kept as implied probabilities. Source prices are
American; every price is converted at parse time and the American value is never stored.

What the lines are: ESPN archives one item per provider with no per-quote observed time.
``close``/``current`` values are a closing-line benchmark only. Opening values come from
(a) per-book ``open`` blocks (2023-24 onward), (b) the ``Opening`` pseudo-provider and the
``initialSpread``/``initialOverUnder`` fields (2016-17 era), and (c) the teamrankings
(provider 1002) movement history, the only source with timestamps (``lineDate``).
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from .nba_backfill_common import american_to_implied, devig_pair

SPREAD_ABS_MAX = 40.0
TOTAL_MIN, TOTAL_MAX = 150.0, 300.0
CLOSE_VS_CURRENT_TOL = 3.0
# Not sportsbooks: the pseudo-provider for openers, ESPN's own consensus, and model/pick sites.
NON_BOOK_NAMES = {"opening", "consensus", "numberfire", "teamrankings", "accuscore"}
TEAMRANKINGS_ID = "1002"

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_DETAILS_MAG = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*$")
_PICK_WORDS = {"EVEN", "PK", "PICK", "PICKEM", "PICK'EM", "PICK EM"}


def parse_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM.search(str(value))
    return float(match.group(0)) if match else None


def spread_ok(value: float | None) -> float | None:
    return value if value is not None and abs(value) <= SPREAD_ABS_MAX else None


def total_ok(value: float | None) -> float | None:
    return value if value is not None and TOTAL_MIN <= value <= TOTAL_MAX else None


def derive_home_spread(
    details: str | None, home_fav: bool | None, away_fav: bool | None, home_ps_current: float | None
) -> tuple[float | None, str]:
    """Home spread, negative = home favoured."""
    text = (details or "").strip()
    if text.upper() in _PICK_WORDS:
        return 0.0, "details_pick"
    match = _DETAILS_MAG.search(text)
    if match and abs(float(match.group(1))) <= SPREAD_ABS_MAX:
        magnitude = abs(float(match.group(1)))
        if magnitude == 0.0:
            return 0.0, "details_pick"
        if home_fav is True and away_fav is not True:
            return -magnitude, "details_flags"
        if away_fav is True and home_fav is not True:
            return magnitude, "details_flags"
    if home_ps_current is not None:
        return home_ps_current, "home_pointspread_block"
    return None, "unresolved"


def _ps(team: dict[str, Any], block: str) -> float | None:
    point_spread = (team.get(block) or {}).get("pointSpread") or {}
    return spread_ok(parse_num(point_spread.get("alternateDisplayValue") or point_spread.get("american")))


def _tot(item: dict[str, Any], block: str) -> float | None:
    total = (item.get(block) or {}).get("total") or {}
    return total_ok(parse_num(total.get("alternateDisplayValue") or total.get("american")))


def _ml_block(team: dict[str, Any], block: str) -> float | None:
    """Implied probability of a moneyLine price block (decimal preferred, else the American string)."""
    ml = (team.get(block) or {}).get("moneyLine") or {}
    dec = ml.get("decimal") if ml.get("decimal") is not None else ml.get("value")
    if isinstance(dec, (int, float)) and not isinstance(dec, bool) and dec > 1.0:
        return round(1.0 / float(dec), 6)
    return american_to_implied(ml.get("american") or ml.get("alternateDisplayValue"))


def trim_item(item: dict[str, Any]) -> dict[str, Any]:
    provider = item.get("provider") or {}
    name = str(provider.get("name") or "")
    home = item.get("homeTeamOdds") or {}
    away = item.get("awayTeamOdds") or {}
    home_fav = home.get("favorite") if isinstance(home.get("favorite"), bool) else None
    away_fav = away.get("favorite") if isinstance(away.get("favorite"), bool) else None
    details = item.get("details")
    detail_match = _DETAILS_MAG.search(str(details or "").strip())
    if detail_match and abs(float(detail_match.group(1))) > SPREAD_ABS_MAX:
        details = None
    home_ps_current = _ps(home, "current")
    spread, orientation = derive_home_spread(details, home_fav, away_fav, home_ps_current)
    ml_home = american_to_implied(home.get("moneyLine"))
    ml_away = american_to_implied(away.get("moneyLine"))
    return {
        "provider_id": str(provider.get("id")) if provider.get("id") is not None else None,
        "provider_name": name,
        "is_live": "live" in name.lower() or bool(item.get("live")) or bool(provider.get("live")),
        "details": details,
        "home_favorite": home_fav,
        "away_favorite": away_fav,
        "home_spread_line": spread,
        "orientation_source": orientation,
        "home_ps_current": home_ps_current,
        "home_ps_open": _ps(home, "open"),
        "home_ps_close": _ps(home, "close"),
        "raw_spread": spread_ok(parse_num(item.get("spread"))),
        "initial_spread_raw": spread_ok(parse_num(item.get("initialSpread"))),
        "total_line": total_ok(parse_num(item.get("overUnder"))),
        "total_current": _tot(item, "current"),
        "total_open": _tot(item, "open"),
        "total_close": _tot(item, "close"),
        "initial_over_under": total_ok(parse_num(item.get("initialOverUnder"))),
        "ml_implied_home": ml_home,
        "ml_implied_away": ml_away,
        "ml_implied_home_open": _ml_block(home, "open"),
        "ml_implied_away_open": _ml_block(away, "open"),
        "ml_implied_home_close": _ml_block(home, "close"),
        "ml_implied_away_close": _ml_block(away, "close"),
    }


def _is_book(rec: dict[str, Any]) -> bool:
    return not rec["is_live"] and rec["provider_name"].strip().lower() not in NON_BOOK_NAMES


def close_values(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    spread = rec["home_spread_line"]
    close = rec["home_ps_close"]
    if close is not None and (spread is None or abs(close - spread) <= CLOSE_VS_CURRENT_TOL):
        spread = close
    total = rec["total_close"] or rec["total_line"] or rec["total_current"]
    return spread, total


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def select_lines(providers: list[dict[str, Any]]) -> dict[str, Any]:
    books = [p for p in providers if _is_book(p)]
    spreads, totals, open_spreads, open_totals, ml_home, ml_home_open = [], [], [], [], [], []
    for book in books:
        spread, total = close_values(book)
        if spread is not None:
            spreads.append(spread)
        if total is not None:
            totals.append(total)
        if book["home_ps_open"] is not None:
            open_spreads.append(book["home_ps_open"])
        if book["total_open"] is not None:
            open_totals.append(book["total_open"])
        home_p, _ = devig_pair(
            book["ml_implied_home_close"] or book["ml_implied_home"],
            book["ml_implied_away_close"] or book["ml_implied_away"],
        )
        if home_p is not None:
            ml_home.append(home_p)
        home_open, _ = devig_pair(book["ml_implied_home_open"], book["ml_implied_away_open"])
        if home_open is not None:
            ml_home_open.append(home_open)
    opening = next((p for p in providers if p["provider_name"].strip().lower() == "opening"), None)
    consensus = next((p for p in providers if p["provider_name"].strip().lower() == "consensus"), None)
    initial_totals = [p["initial_over_under"] for p in providers if p["initial_over_under"] is not None]
    return {
        "n_books": len(books),
        "close_home_spread": _median(spreads),
        "close_spread_range": (max(spreads) - min(spreads)) if spreads else None,
        "close_total": _median(totals),
        "close_total_range": (max(totals) - min(totals)) if totals else None,
        "close_ml_devig_home": _median(ml_home),
        "n_close_spread": len(spreads),
        "n_close_total": len(totals),
        "open_home_spread_books": _median(open_spreads),
        "open_total_books": _median(open_totals),
        "open_ml_devig_home_books": _median(ml_home_open),
        "n_open_spread_books": len(open_spreads),
        "opening_provider_home_spread": opening["home_spread_line"] if opening else None,
        "opening_provider_total": (opening["total_line"] if opening else None),
        "initial_over_under_median": _median(initial_totals),
        "espn_consensus_home_spread": consensus["home_spread_line"] if consensus else None,
        "espn_consensus_total": consensus["total_line"] if consensus else None,
    }


def trim_movement(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """teamrankings movement items -> [{line_date_utc, line, home/away/over/under implied}]."""
    out: list[dict[str, Any]] = []
    for it in (payload or {}).get("items") or []:
        out.append(
            {
                "line_date_utc": it.get("lineDate"),
                "line": parse_num(it.get("line")),
                "home_implied": american_to_implied(it.get("homeOdds")),
                "away_implied": american_to_implied(it.get("awayOdds")),
                "over_implied": american_to_implied(it.get("overOdds")),
                "under_implied": american_to_implied(it.get("underOdds")),
            }
        )
    return sorted(out, key=lambda r: r["line_date_utc"] or "")


def movement_summary(items: list[dict[str, Any]], tip_utc: str | None) -> dict[str, Any]:
    """First/last movement point strictly before tip. ESPN dates are ``YYYY-MM-DDTHH:MMZ``."""
    tip = (tip_utc or "")[:16]
    pre = [r for r in items if r["line_date_utc"] and tip and r["line_date_utc"][:16] < tip]
    return {
        "n_items": len(items),
        "n_pre_tip": len(pre),
        "first_pre_tip_utc": pre[0]["line_date_utc"] if pre else None,
        "first_pre_tip_line": pre[0]["line"] if pre else None,
        "last_pre_tip_utc": pre[-1]["line_date_utc"] if pre else None,
        "last_pre_tip_line": pre[-1]["line"] if pre else None,
    }
