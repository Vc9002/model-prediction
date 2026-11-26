"""Polymarket Edge Ledger: records and manages trades from the Polymarket CLOB Edge Scanner.

Uses the standard project Pick Ledger format, supporting the shared ledger table
renderers, audit logging, and settlement.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import EASTERN, utc_now
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


# Fields for the exact-membership test only. location / shortDisplayName are school-only names
# ("Kansas" for Kansas Jayhawks): they make a school-only row an exact match, but must never feed the
# substring test, where "kansas" would sit inside "kansas state" and pick the wrong orientation.
_ESPN_TEAM_EXACT_FIELDS = ("displayName", "name", "abbreviation", "location", "shortDisplayName")
_ESPN_TEAM_SUBSTRING_FIELDS = ("displayName", "name", "abbreviation")

# Polymarket US contract slugs; manual-sync rows carry one in event_id.
_CONTRACT_SLUG_RE = re.compile(r"^(aec|asc|tsc|atc|mkt)-")


# Sentinel: the nearest exact game by start time is the one still being played.
_PENDING_EXACT = "pending"


def _espn_event_start(event: dict[str, Any]) -> datetime | None:
    """Scheduled start of a scoreboard event, or None when ESPN omits or garbles it."""
    raw = event.get("date") or (event.get("competitions") or [{}])[0].get("date")
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_start_is_genuine(row: dict[str, Any], start_dt: datetime) -> bool:
    """False for the synced noon-UTC placeholder and the position-update-time fallback: neither
    says which game of a doubleheader the bet was on."""
    utc_start = start_dt.astimezone(UTC)
    if (utc_start.hour, utc_start.minute, utc_start.second) == (12, 0, 0):
        return False
    return "event_start_utc defaulted" not in str(row.get("note") or "")


def _nearest_exact_game(
    start_dt: datetime,
    finished: list[tuple[str, int, int, bool, bool, datetime | None]],
    pending_starts: list[datetime | None],
) -> Any:
    """Pick between exact games when some are final and some are not, by the start closest to the
    row's real start. Returns the finished candidate, ``_PENDING_EXACT`` when the unfinished game
    is the nearer one, or None when a start is missing or two games tie (grading would be a guess).
    """
    contenders: list[tuple[datetime | None, Any]] = [(c[5], c) for c in finished]
    contenders += [(s, _PENDING_EXACT) for s in pending_starts]
    if any(event_start is None for event_start, _ in contenders):
        return None
    ranked = sorted(contenders, key=lambda item: abs((item[0] - start_dt).total_seconds()))
    if abs((ranked[0][0] - start_dt).total_seconds()) == abs((ranked[1][0] - start_dt).total_seconds()):
        return None
    return ranked[0][1]


def _espn_team_names(team: dict[str, Any], fields: tuple[str, ...]) -> set[str]:
    names = {str(team.get(field) or "").casefold().strip() for field in fields}
    return names - {"", "none", "tbd", "unknown"}


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
    resettle_all: bool = False,
) -> dict[str, Any]:
    """Settle open rows in the Polymarket Edge Ledger against ESPN and match scores."""
    return settle_ledger_rows_against_espn(
        _get_ledger_path(data_root), espn_client=espn_client, resettle_all=resettle_all
    )


def settle_ledger_rows_against_espn(
    path: Path,
    espn_client: Any | None = None,
    resettle_all: bool = False,
) -> dict[str, Any]:
    """Grade every open row at ``path`` (shared FIELDNAMES xlsx schema) against ESPN/tennis/
    Polymarket-resolution scores. Shared by the Polymarket Edge Ledger and the manual bet
    ledger -- the score-matching logic (team-name matching, tennis draws, line parsing) is
    schema-generic, not specific to how a row was recorded.
    """
    from ..data_sources.espn import ESPNClient
    from ..domain import MarketType, PickResult
    from ..pricing import grade_pick, profit_units

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
    try:
        from ..cli.state import _LEDGER_LEAGUE_TO_ESPN

        league_map = _LEDGER_LEAGUE_TO_ESPN
    except ImportError:
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
        if not resettle_all and status != "open":
            continue

        event_start_str = str(row.get("event_start_utc") or "")
        if not event_start_str:
            if status == "open":
                open_count += 1
            continue

        try:
            start_dt = datetime.fromisoformat(event_start_str)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now.tzinfo)
        except (ValueError, TypeError):
            if status == "open":
                open_count += 1
            continue

        # Check games that have not yet started: keep them open
        if start_dt > now:
            if status != "open" or row.get("result"):
                row["status"] = "open"
                row["result"] = ""
                row["pnl_units"] = ""
                row["settled_at_utc"] = ""
                row["away_score"] = ""
                row["home_score"] = ""
                modified = True
            open_count += 1
            continue

        open_count += 1
        lg = str(row.get("league") or "").upper()
        espn_leagues: tuple[str, ...]
        if lg == "POLYMARKET" or not lg:
            espn_leagues = ("mlb", "wnba", "nba", "nfl", "eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "usa.1")
            check_tennis = True
        else:
            espn_leagues = league_map.get(lg, (lg.lower(),))
            check_tennis = lg in ("TENNIS", "WTA", "ATP")

        # Eastern like settle.py: a night game's 00:30Z start belongs to the previous evening's slate.
        # The synced noon-UTC placeholder is 7-8am Eastern, the same calendar day as before.
        game_day = start_dt.astimezone(EASTERN).date().isoformat()
        away_name = str(row.get("away_team") or "").casefold().strip()
        home_name = str(row.get("home_team") or "").casefold().strip()
        slug = str(row.get("market_slug") or "")
        if not slug:
            # Manual-sync rows keep the contract slug in event_id (market_slug stays empty).
            event_ref = str(row.get("event_id") or "")
            if _CONTRACT_SLUG_RE.match(event_ref):
                slug = event_ref

        match_found = None
        match_ambiguous = False

        if check_tennis:
            try:
                from ..tennis_forward import TENNIS_TOURS

                for tour in TENNIS_TOURS:
                    sb = espn.scoreboard(tour, start_dt.strftime("%Y%m%d"))
                    for event in sb.get("events", []):
                        for grp in event.get("groupings", []):
                            for comp in grp.get("competitions", []):
                                if not comp.get("status", {}).get("type", {}).get("completed"):
                                    continue
                                comps = comp.get("competitors", [])
                                if len(comps) != 2:
                                    continue
                                c1, c2 = comps[0], comps[1]
                                n1 = str((c1.get("athlete") or {}).get("displayName", "")).casefold().strip()
                                n2 = str((c2.get("athlete") or {}).get("displayName", "")).casefold().strip()
                                if (
                                    not n1
                                    or not n2
                                    or n1 in ("none", "tbd", "unknown")
                                    or n2 in ("none", "tbd", "unknown")
                                    or not home_name
                                    or not away_name
                                ):
                                    continue
                                if (home_name in n1 or n1 in home_name) and (
                                    away_name in n2 or n2 in away_name
                                ):
                                    home_c, away_c = c1, c2
                                elif (home_name in n2 or n2 in home_name) and (
                                    away_name in n1 or n1 in away_name
                                ):
                                    home_c, away_c = c2, c1
                                else:
                                    continue

                                a_win = 1 if away_c.get("winner") else 0
                                h_win = 1 if home_c.get("winner") else 0
                                match_found = (a_win, h_win)
                                break
                            if match_found:
                                break
                        if match_found:
                            break
                    if match_found:
                        break
            except Exception:
                logger.warning("polymarket tennis scoreboard lookup failed", exc_info=True)

        if match_found is None:
            # (event id, away score, home score, exact, secondary, scheduled start): every scoreboard
            # event the row's two names match, across all leagues. Taking the first substring hit graded "Michigan"
            # against "Michigan State @ Ohio State" whenever that game was listed first.
            # ``secondary`` marks an event from a league other than the row's own (an NBA row also
            # searches NCAAB/WNCAAB).
            candidates: list[tuple[str, int, int, bool, bool, datetime | None]] = []
            ambiguous: list[tuple[str, bool]] = []  # (event id, secondary)
            primary_lg = espn_leagues[0] if lg and lg != "POLYMARKET" else None
            primary_unfinished = False
            pending_exact_starts: list[datetime | None] = []
            for espn_lg in espn_leagues:
                secondary = primary_lg is not None and espn_lg != primary_lg
                try:
                    sb = espn.scoreboard(espn_lg, game_day)
                    for event in sb.get("events", []):
                        completed = event.get("status", {}).get("type", {}).get("completed", False)
                        comps = event.get("competitions", [{}])[0].get("competitors", [])
                        if len(comps) != 2:
                            continue
                        c_away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
                        c_home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
                        event_id = str(event.get("id") or "")

                        h_team = c_home.get("team") or {}
                        a_team = c_away.get("team") or {}
                        h_exact = _espn_team_names(h_team, _ESPN_TEAM_EXACT_FIELDS)
                        a_exact = _espn_team_names(a_team, _ESPN_TEAM_EXACT_FIELDS)
                        if not h_exact or not a_exact or not home_name or not away_name:
                            continue

                        # Both orientations are judged on every event. The normal one used to be tried
                        # first and win on any substring hit even when the inverted one was the exact
                        # match (Kansas away at Kansas State against a Kansas State @ Kansas row).
                        exact_normal = home_name in h_exact and away_name in a_exact
                        exact_inverted = home_name in a_exact and away_name in h_exact
                        if secondary and not (exact_normal or exact_inverted):
                            # Another league's event needs an exact name match: college abbreviations
                            # ("MINN", "WASH") are substrings of full NBA/MLB names.
                            continue
                        is_ambiguous = False
                        if exact_normal and exact_inverted:
                            is_ambiguous = True
                        elif exact_normal or exact_inverted:
                            inverted = exact_inverted
                            exact = True
                        else:
                            h_names = _espn_team_names(h_team, _ESPN_TEAM_SUBSTRING_FIELDS)
                            a_names = _espn_team_names(a_team, _ESPN_TEAM_SUBSTRING_FIELDS)
                            h_match_normal = any(
                                (hn in home_name or home_name in hn) for hn in h_names if len(hn) > 3
                            )
                            a_match_normal = any(
                                (an in away_name or away_name in an) for an in a_names if len(an) > 3
                            )
                            h_match_inverted = any(
                                (an in home_name or home_name in an) for an in a_names if len(an) > 3
                            )
                            a_match_inverted = any(
                                (hn in away_name or away_name in hn) for hn in h_names if len(hn) > 3
                            )
                            normal = h_match_normal and a_match_normal
                            flipped = h_match_inverted and a_match_inverted
                            if not normal and not flipped:
                                continue
                            if normal and flipped:
                                is_ambiguous = True
                            else:
                                inverted = flipped
                                exact = False
                        if not completed:
                            # The row's own league has a matching game that is not final yet: no other
                            # league's result may stand in for it.
                            if not secondary:
                                primary_unfinished = True
                                if exact_normal or exact_inverted:
                                    pending_exact_starts.append(_espn_event_start(event))
                            continue
                        if is_ambiguous:
                            ambiguous.append((event_id or "?", secondary))
                            continue
                        try:
                            if inverted:
                                # c_away is the row's home team, c_home is the row's away team
                                h_score = int(c_away.get("score", 0))
                                a_score = int(c_home.get("score", 0))
                            else:
                                h_score = int(c_home.get("score", 0))
                                a_score = int(c_away.get("score", 0))
                        except (ValueError, TypeError):
                            continue
                        candidate = (event_id, a_score, h_score, exact, secondary, _espn_event_start(event))
                        if candidate not in candidates:
                            candidates.append(candidate)
                except (KeyError, TypeError, ValueError, OSError):
                    logger.debug("Scoreboard lookup failed for %s on %s", espn_lg, game_day, exc_info=True)
                    continue

            primary_hits = [c for c in candidates if not c[4]]
            primary_ambiguous = [event_id for event_id, secondary in ambiguous if not secondary]
            if primary_hits or primary_ambiguous or primary_unfinished:
                # Never grade off another league while the row's own league has a matching event,
                # final or not.
                candidates = primary_hits
                ambiguous_ids = primary_ambiguous
            else:
                ambiguous_ids = [event_id for event_id, _ in ambiguous]

            exact_hits = [c for c in candidates if c[3]]
            guess = False
            if pending_exact_starts:
                # The row's own exact game is still being played. A final game of the same names that
                # is not exact (Michigan State for a Michigan row) never stands in for it; the row
                # waits on the normal open / 48h path. With a final exact game too (doubleheader), only
                # a real row start can say which of the two the bet was on.
                if exact_hits:
                    picked = (
                        _nearest_exact_game(start_dt, exact_hits, pending_exact_starts)
                        if _row_start_is_genuine(row, start_dt)
                        else None
                    )
                    if picked is None:
                        guess = True
                    elif picked is not _PENDING_EXACT:
                        match_found = (picked[1], picked[2])
            elif len(exact_hits) == 1:
                match_found = (exact_hits[0][1], exact_hits[0][2])
            elif not exact_hits and len(candidates) == 1 and not ambiguous_ids:
                match_found = (candidates[0][1], candidates[0][2])
            else:
                guess = bool(candidates or ambiguous_ids)
            if guess:
                # Several different games match the row's names, or one event matches in both
                # orientations, and none is the only exact one: grading any of them is a guess,
                # so the row stays open.
                match_ambiguous = True
                logger.warning(
                    "ambiguous scoreboard match for %s @ %s on %s: events %s; leaving row %s open",
                    away_name,
                    home_name,
                    game_day,
                    [c[0] or "?" for c in candidates]
                    + ambiguous_ids
                    + ["(unfinished exact game)"] * len(pending_exact_starts),
                    row.get("pick_id"),
                )

        if match_found is None and slug:
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
                            out_key = str(out_name).casefold().strip()
                            if out_key and out_key == home_name and out_key != away_name:
                                h_win, a_win = 1, 0
                            elif out_key and out_key == away_name and out_key != home_name:
                                h_win, a_win = 0, 1
                            else:
                                h_win = int(
                                    bool(home_name and (home_name in out_key or out_key in home_name))
                                )
                                a_win = int(
                                    bool(away_name and (away_name in out_key or out_key in away_name))
                                )
                            if h_win and a_win:
                                # Substring hit on both row names (Kansas / Kansas State): the winning
                                # outcome cannot be attributed to one side.
                                logger.warning(
                                    "resolution outcome %r matches both row teams for %s; leaving row open",
                                    out_name,
                                    slug,
                                )
                                match_ambiguous = True
                            elif h_win or a_win:
                                match_found = (a_win, h_win)
                            break
            except (KeyError, TypeError, ValueError, OSError):
                logger.debug("Polymarket market lookup failed for %s", slug, exc_info=True)

        if match_found is not None:
            a_score, h_score = match_found
            mtype_str = str(row.get("market_type") or "moneyline").lower()
            try:
                mtype = MarketType(mtype_str)
            except ValueError:
                mtype = MarketType.MONEYLINE

            sel_raw = str(row.get("selection") or "").casefold().strip()
            if sel_raw in ("home", "long", "over", "yes", "nrfi", "away", "short", "under", "no", "yrfi"):
                sel = sel_raw
            elif sel_raw == home_name:
                sel = "home"
            elif sel_raw == away_name:
                sel = "away"
            elif home_name and (sel_raw in home_name or home_name in sel_raw):
                sel = "home"
            elif away_name and (sel_raw in away_name or away_name in sel_raw):
                sel = "away"
            else:
                sel = sel_raw

            line_val = None
            if row.get("line") not in (None, ""):
                try:
                    line_val = float(row["line"])
                except (ValueError, TypeError):
                    line_val = None
            if line_val is None:
                slug_str = str(row.get("market_slug") or "")
                m_pt = re.search(r"-(\d+)pt(\d+)", slug_str)
                if m_pt:
                    line_val = float(f"{m_pt.group(1)}.{m_pt.group(2)}")
                else:
                    m_dot = re.search(r"-(\d+)\.(\d+)", slug_str)
                    if m_dot:
                        line_val = float(f"{m_dot.group(1)}.{m_dot.group(2)}")
                    else:
                        m_i = re.search(r"-(\d+)(?:$|[a-z])", slug_str)
                        if m_i:
                            line_val = float(m_i.group(1))

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
            row["away_score"] = str(a_score)
            row["home_score"] = str(h_score)
            row["pnl_units"] = str(round(pnl, 4))
            row["settled_at_utc"] = now_iso
            modified = True
            open_count -= 1
            settled_picks.append({"pick_id": row.get("pick_id"), "result": row["result"], "pnl": pnl})

        elif match_ambiguous:
            # A decided game must not be voided because our own name matching cannot pick its
            # event; the warning above names the candidates and the row is retried every run.
            if status != "open":
                # resettle_all: a settled row stays settled, so it is not an open row.
                open_count -= 1
            continue
        elif (now - start_dt).total_seconds() > 48 * 3600:
            row["status"] = "voided"
            row["result"] = "push"
            row["pnl_units"] = "0.0"
            row["settled_at_utc"] = now_iso
            row["void_reason"] = "match_unresolved_after_48h"
            modified = True
            open_count -= 1
        else:
            if row.get("status") != "open" or row.get("result"):
                row["status"] = "open"
                row["result"] = ""
                row["pnl_units"] = ""
                row["settled_at_utc"] = ""
                row["away_score"] = ""
                row["home_score"] = ""
                modified = True

    if modified:
        write_xlsx_rows_atomic(path, FIELDNAMES, rows)

    return {
        "status": "ok",
        "settled_count": len(settled_picks),
        "open_count": open_count,
        "total_rows": len(rows),
        "settled_picks": settled_picks,
    }
