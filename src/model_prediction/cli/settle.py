"""Settlement command group.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
3). Grades open picks against ESPN/Polymarket results per sport. The
logger is defined HERE, not imported, for the same reason as the shim:
ruff's BLE001 exemption for the blind-except handlers in this module
requires a locally-defined logger.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import Any

from ..config import (
    PROJECT_ROOT,
    ledger_path,
    market_odds_snapshot_path,
)
from ..data_sources.espn import ESPNClient
from ..data_sources.mlb_market_odds import MarketOddsSnapshotStore
from ..data_sources.polymarket_us import (
    PolymarketSnapshotStore,
    probability_to_american,
)
from ..domain import EASTERN, parse_utc, utc_now
from ..main_ledgers import MultiSportPickLedger
from ..research_ledgers import existing_research_ledgers
from ..tennis_forward import TENNIS_TOURS, _is_tennis_subperiod_slug
from .state import _LEDGER_LEAGUE_TO_ESPN, _TERMINAL_MARKET_STATES

logger = logging.getLogger("model_prediction.cli")


def _settle_all_unsettled(args, config, ledger) -> dict:
    """Grade every started open pick from ESPN scoreboards or Polymarket resolution."""
    now = utc_now()
    espn = ESPNClient()
    market_store = MarketOddsSnapshotStore(market_odds_snapshot_path(config))
    data_root = Path(ledger_path(config)).parent
    settled, voided, pending, failures = [], [], [], []
    for row in ledger.rows():
        if row["status"] != "open":
            continue
        try:
            start = parse_utc(row["event_start_utc"])
        except ValueError:
            failures.append({"pick_id": row["pick_id"], "reason": "bad event_start_utc"})
            continue
        if start > now:
            pending.append(row["pick_id"])
            continue
        # Esports: settle via Polymarket contract resolution
        if row["league"] in ("LOL", "CS2", "DOTA2", "VALORANT", "RAINBOW_SIX"):
            result = _settle_esports_pick(row, ledger, data_root=data_root)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # KBO/NPB: neither ESPN nor Polymarket resolution covers these --
        # settle from the official league schedule instead.
        if row["league"] in ("KBO", "NPB"):
            result = _settle_international_baseball_pick(row, ledger, config, data_root=data_root)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # Tennis: player-vs-player, not team-vs-team -- ESPN's tennis
        # scoreboard shape doesn't fit `_find_espn_result` at all (see
        # `_find_tennis_result`).
        if row["league"] == "TENNIS":
            result = _settle_tennis_pick(row, ledger, espn, data_root=data_root)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        leagues = _LEDGER_LEAGUE_TO_ESPN.get(row["league"], ())
        if not leagues:
            # Fail loudly instead of pending forever: a league with no ESPN
            # result path can never settle through this branch (2026-07-27
            # audit: a WORLD_CUP row would have stalled silently -- open
            # forever, no error anywhere -- because the empty tuple can
            # never match a result). League.WORLD_CUP stays retired-but-
            # historical; any new league must be wired here deliberately.
            failures.append(
                {
                    "pick_id": row["pick_id"],
                    "reason": f"no ESPN result path for league {row['league']}",
                }
            )
            continue
        game_day = start.astimezone(EASTERN).date().isoformat()
        match = _find_espn_result(espn, leagues, game_day, row)
        if match is None and row["league"] == "SOCCER":
            # Soccer: try collected scores from The Odds API for leagues
            # outside ESPN coverage (e.g. Brazil Serie B, K League 1).
            soccer_scores = _load_soccer_scores()
            match = _find_soccer_result(row, soccer_scores)
        if match is None:
            pending.append(row["pick_id"])
            continue
        status = match.get("status_name", "")
        if status in {"STATUS_POSTPONED", "STATUS_CANCELED"}:
            if args.void_postponed:
                voided.append(ledger.void(row["pick_id"], f"event {status.lower()}")["pick_id"])
            else:
                pending.append(row["pick_id"])
            continue
        if not match.get("completed"):
            pending.append(row["pick_id"])
            continue
        closing_line = closing_odds = closing_probability = None
        quote = market_store.closing_quote(
            row["event_id"], row["event_start_utc"], row["market_type"], row["selection"]
        )
        if quote is not None:
            closing_odds = int(quote["american_odds"])
            closing_probability = float(quote["decision_probability"])
            if quote.get("line") is not None:
                closing_line = float(quote["line"])
        else:
            # Fallback to per-sport Polymarket snapshot history (WNBA, Soccer, etc.)
            slug = _extract_market_slug(str(row.get("rationale", "")))
            if slug is not None and data_root is not None:
                sport_dir = str(row.get("league", "")).lower()
                try:
                    closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                        data_root,
                        sport_dir,
                        slug,
                        row["event_start_utc"],
                        row.get("home_team", ""),
                        row.get("away_team", ""),
                        row.get("selection", ""),
                    )
                except (OSError, ValueError):
                    logger.warning(
                        "%s closing-snapshot lookup failed for slug %s", sport_dir, slug, exc_info=True
                    )
        try:
            if row.get("market_type") in ("nrfi", "yrfi") and "away_1st" in match and "home_1st" in match:
                settle_away = int(match["away_1st"])
                settle_home = int(match["home_1st"])
            else:
                settle_away = int(match["away_score"])
                settle_home = int(match["home_score"])

            result = ledger.settle(
                row["pick_id"],
                settle_away,
                settle_home,
                closing_line,
                closing_odds,
                closing_raw_probability=closing_probability,
            )
            settled.append({"pick_id": row["pick_id"], "result": result["result"]})

        except (KeyError, ValueError) as error:
            failures.append({"pick_id": row["pick_id"], "reason": str(error)})

    try:
        from ..portfolio.polymarket_ledger import settle_polymarket_ledger_rows

        poly_settle_res = settle_polymarket_ledger_rows(data_root=data_root, espn_client=espn)
    except Exception:
        logger.warning("Polymarket edge ledger settlement failed", exc_info=True)
        poly_settle_res = {"status": "error"}

    return {
        "settled": settled,
        "voided": voided,
        "still_open": pending,
        "failures": failures,
        "polymarket_edge_settlement": poly_settle_res,
        "note": "Results pulled from ESPN scoreboards; closing prices from stored pregame BBO asks.",
    }


def _find_espn_result(espn: ESPNClient, leagues, game_day: str, row) -> dict | None:
    """Find a completed-game record matching a ledger row by id or team names."""
    away_names = {row["away_team"].casefold(), row["original_away_team"].casefold()}
    home_names = {row["home_team"].casefold(), row["original_home_team"].casefold()}
    for league in leagues:
        try:
            scoreboard = espn.scoreboard(league, game_day)
        except Exception:
            logger.warning(
                "ESPN scoreboard fetch failed for %s on %s; settlement skipping this league",
                league,
                game_day,
                exc_info=True,
            )
            continue
        for event in scoreboard.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {}).get("type", {})
            by_side = {item.get("homeAway"): item for item in competition.get("competitors", [])}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            id_match = str(event.get("id")) == row["event_id"]
            name_match = (
                away["team"].get("displayName", "").casefold() in away_names
                and home["team"].get("displayName", "").casefold() in home_names
            )
            # Prefer exact event_id match. When the ledger has a numeric ESPN
            # event_id that does NOT match any event in the scoreboard (e.g.
            # re-forecast rows with regenerated IDs), fall back to name matching
            # with a caution: double-header games sharing the same team names
            # on the same day may be matched incorrectly.
            if id_match or name_match:
                pass
            else:
                continue
            record = {
                "status_name": status.get("name", ""),
                "completed": bool(status.get("completed")),
            }
            if record["completed"]:
                try:
                    record["away_score"] = int(float(away.get("score", 0) or 0))
                    record["home_score"] = int(float(home.get("score", 0) or 0))
                    away_lines = away.get("linescores", [])
                    home_lines = home.get("linescores", [])
                    if away_lines and home_lines:
                        record["away_1st"] = int(float(away_lines[0].get("value", 0) or 0))
                        record["home_1st"] = int(float(home_lines[0].get("value", 0) or 0))
                except (TypeError, ValueError):
                    record["completed"] = False
            return record

    return None


def _closing_probability_for_moneyline_pick(
    data_root,
    sport_dir: str,
    slug: str,
    event_start_utc: str,
    home_team: str,
    away_team: str,
    selection: str,
) -> tuple[float | None, int | None]:
    """Best-effort closing (last pregame) probability for a team/player-vs-
    team/player moneyline pick, read from the same per-sport-date Polymarket
    snapshot history the forecast pipeline already captures every day via
    capture_slate_snapshots (data/odds/{sport}/{date}/polymarket_snapshots.jsonl).

    Returns (raw_probability, american_odds) for the row's own selection, or
    (None, None) if no matching pregame snapshot was ever captured for this
    market -- CLV is then simply left blank for that row, same as today.
    """
    from datetime import timedelta

    from ..learned_forward import _team_matches

    start = parse_utc(event_start_utc)
    dates_to_try = [
        start.astimezone(EASTERN).date().isoformat(),
        start.date().isoformat(),
        (start - timedelta(days=1)).date().isoformat(),
        (start + timedelta(days=1)).date().isoformat(),
    ]
    snapshot = None
    seen_dates = set()
    for d in dates_to_try:
        if d in seen_dates:
            continue
        seen_dates.add(d)
        store = PolymarketSnapshotStore.for_sport_date(data_root, sport_dir, d)
        snapshot = store.closing_snapshot(slug, event_start_utc)
        if snapshot is not None:
            break

    if snapshot is None:
        return None, None
    long_desc = str((snapshot.get("long") or {}).get("description", ""))
    short_desc = str((snapshot.get("short") or {}).get("description", ""))

    # Binary Yes/No or Over/Under markets (common in soccer, totals, spreads)
    if long_desc.strip().lower() in ("yes", "over") or short_desc.strip().lower() in ("no", "under"):
        sel = str(selection).strip().lower()
        if sel in ("under", "no", "away") and short_desc.strip().lower() in ("no", "under"):
            side = snapshot.get("short") or {}
        else:
            side = snapshot.get("long") or {}
        ask = side.get("ask")
        if ask is not None and 0 < float(ask) < 1:
            return round(float(ask), 6), probability_to_american(float(ask))

    selected_name = home_team if selection == "home" else away_team
    matches_long = _team_matches(selected_name, long_desc)
    matches_short = _team_matches(selected_name, short_desc)
    if matches_long == matches_short:
        # Ambiguous or no match -- never guess. Defaulting to the long side here
        # attributes a real ask to a row we cannot prove it belongs to, which is
        # the same fabricated-market-evidence class as the 2026-08-29 NCAAF ask.
        # A blank CLV is a missing measurement; a guessed one is a wrong
        # measurement that reads as real.
        return None, None
    side = snapshot.get("long" if matches_long else "short") or {}
    ask = side.get("ask")
    if ask is None or not 0 < float(ask) < 1:
        return None, None
    return round(float(ask), 6), probability_to_american(float(ask))


def _extract_market_slug(rationale: str) -> str | None:
    """Recover the Polymarket market slug embedded in a row's rationale text."""
    import re

    match = re.search(r"market_slug=([a-z0-9\-]+)", rationale)
    if match:
        return match.group(1)
    for m in re.findall(r"\(([a-z0-9\-]+)\)", rationale):
        if "-" in m or m.startswith(("atc", "tsc", "asc", "mkt")):
            return m
    match = re.search(r"\(([a-z0-9\-]+)\)", rationale)
    return match.group(1) if match else None


def _settle_esports_pick(row: dict, ledger, data_root=None) -> dict | None:
    """Settle an esports pick from the exchange's terminal market state.

    A resolved Polymarket market reports a terminal book state (verified live:
    ``MARKET_STATE_EXPIRED``) and terminal side prices — exactly 1 for the
    winning team's side and 0 for the loser. Returns None while pending.

    Ledger home/away were assigned from the contract's side ordering at log
    time (teams[0]=home), so the winning description maps directly onto the
    ledger's home/away teams: home won -> scores (0, 1); away won -> (1, 0).
    """
    from ..data_sources.polymarket_us import PolymarketUSClient, _amount

    rationale = str(row.get("rationale", ""))
    slug = _extract_market_slug(rationale)
    if slug is None:
        return {"pick_id": row["pick_id"], "reason": "no market slug recorded on row"}
    client = PolymarketUSClient()
    try:
        market = client.market(slug)
        book = client.book(slug)
    except Exception:
        logger.warning(
            "Polymarket market/book fetch failed for slug %s; pick %s stays unsettled",
            slug,
            row.get("pick_id"),
            exc_info=True,
        )
        return None
    if str(book.get("state") or "") not in _TERMINAL_MARKET_STATES:
        return None
    prices: dict[str, float] = {}
    for side in market.get("marketSides", []):
        price = _amount(side.get("price"))
        if price is None:
            return None
        prices[str(side.get("description") or "")] = price
    if len(prices) != 2 or sorted(prices.values()) != [0.0, 1.0]:
        # A terminal book with a non-binary settlement price is not "still
        # pending" -- Polymarket's stats.settlementPx confirms this already
        # IS the market's final, official settlement value; it's just not a
        # clean win/loss. Per these contracts' own resolution rules (forfeit,
        # disqualification, or a postponement never rescheduled within two
        # weeks all "settle to the last fair market price"), this means the
        # match never definitively completed as scheduled. Void rather than
        # leave the pick open forever with no path to resolution.
        try:
            voided = ledger.void(
                row["pick_id"],
                "esports market settled to a non-binary price (forfeit/postponement per market rules)",
            )
            return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
    winning_description = next(name for name, price in prices.items() if price == 1.0)
    home_key = _identity_key(str(row["home_team"]))
    away_key = _identity_key(str(row["away_team"]))
    winner_key = _identity_key(winning_description)
    if winner_key == home_key:
        away_score, home_score = 0, 1
    elif winner_key == away_key:
        away_score, home_score = 1, 0
    else:
        return {
            "pick_id": row["pick_id"],
            "reason": f"winning side {winning_description!r} matches neither ledger team",
        }
    closing_probability = closing_odds = None
    if data_root is not None:
        try:
            closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                data_root,
                "esports",
                slug,
                row["event_start_utc"],
                row["home_team"],
                row["away_team"],
                row["selection"],
            )
        except (OSError, ValueError):
            logger.warning("esports closing-snapshot lookup failed for slug %s", slug, exc_info=True)
    try:
        result = ledger.settle(
            row["pick_id"],
            away_score,
            home_score,
            None,
            closing_odds,
            closing_raw_probability=closing_probability,
        )
        return {"pick_id": row["pick_id"], "result": result["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


def _settle_international_baseball_pick(row: dict, ledger, config, data_root=None) -> dict | None:
    """Settle a KBO/NPB pick from the official league schedule.

    Ledger home/away for these leagues are Polymarket's own team-name
    strings (see international_baseball.forecast_international_baseball_slate),
    not the official schedule's game_id -- find_international_baseball_result
    matches by game_date + team alias instead. Returns None while the game
    hasn't posted a final result yet.
    """
    from ..international_baseball import find_international_baseball_result

    if data_root is None:
        data_root = Path(ledger_path(config)).parent
    try:
        start = parse_utc(row["event_start_utc"])
    except ValueError:
        return {"pick_id": row["pick_id"], "reason": "bad event_start_utc"}
    game_date = start.date().isoformat()
    result = find_international_baseball_result(
        data_root, row["league"], game_date, row["home_team"], row["away_team"]
    )
    if result is None:
        return None
    away_score, home_score = result
    closing_probability = closing_odds = None
    slug = _extract_market_slug(str(row.get("rationale", "")))
    if slug is not None and data_root is not None:
        sport_dir = str(row.get("league", "")).lower()
        try:
            closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                data_root,
                sport_dir,
                slug,
                row["event_start_utc"],
                row.get("home_team", ""),
                row.get("away_team", ""),
                row.get("selection", ""),
            )
        except (OSError, ValueError):
            logger.warning("%s closing-snapshot lookup failed for slug %s", sport_dir, slug, exc_info=True)
    try:
        settlement_value = 0.5 if away_score == home_score else None
        settled = ledger.settle(
            row["pick_id"],
            away_score,
            home_score,
            None,
            closing_odds,
            closing_raw_probability=closing_probability,
            binary_contract_settlement_value=settlement_value,
        )
        return {"pick_id": row["pick_id"], "result": settled["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


_TENNIS_IRREGULAR_RESULT_MARKERS = (
    "abandon",
    "cancel",
    "default",
    "disqualif",
    "retire",
    "walkover",
    "w/o",
)


def _tennis_irregular_result_reason(competition: dict) -> str | None:
    """Identify results whose derivative settlement is book-specific."""

    def strings(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    result_text = " ".join(
        strings(
            {
                "status": competition.get("status"),
                "notes": competition.get("notes"),
            }
        )
    ).casefold()
    marker = next((item for item in _TENNIS_IRREGULAR_RESULT_MARKERS if item in result_text), None)
    if marker is None:
        return None
    return f"irregular result marker {marker!r} in ESPN status/notes"


def _tennis_completed_game_totals(away: dict, home: dict) -> tuple[int, int] | str:
    """Extract aligned set-game scores, or explain why derivatives cannot grade."""
    away_lines = away.get("linescores")
    home_lines = home.get("linescores")
    if not isinstance(away_lines, list) or not isinstance(home_lines, list):
        return "ESPN result is missing per-set linescores"
    if len(away_lines) != len(home_lines) or not 2 <= len(away_lines) <= 5:
        return "ESPN per-set linescores are missing or misaligned"

    away_games: list[int] = []
    home_games: list[int] = []
    try:
        for away_set, home_set in zip(away_lines, home_lines, strict=True):
            away_value = float(away_set["value"])
            home_value = float(home_set["value"])
            if (
                away_value < 0
                or home_value < 0
                or not away_value.is_integer()
                or not home_value.is_integer()
                or away_value == home_value
            ):
                return "ESPN per-set linescores contain an invalid game score"
            away_games.append(int(away_value))
            home_games.append(int(home_value))
    except (KeyError, TypeError, ValueError):
        return "ESPN per-set linescores contain a non-numeric game score"

    away_set_wins = sum(away_game > home_game for away_game, home_game in zip(away_games, home_games))
    home_set_wins = len(away_games) - away_set_wins
    away_winner = away.get("winner") is True
    home_winner = home.get("winner") is True
    if away_winner == home_winner:
        return "ESPN completed result lacks one unambiguous match winner"
    winner_set_wins = away_set_wins if away_winner else home_set_wins
    loser_set_wins = home_set_wins if away_winner else away_set_wins
    if winner_set_wins < 2 or winner_set_wins <= loser_set_wins:
        return "ESPN per-set linescores do not describe a normally completed match"
    return sum(away_games), sum(home_games)


def _find_tennis_result(espn: ESPNClient, game_day: str, row: dict) -> dict | None:
    """Match a ledger row to a completed WTA or ATP singles match by player name.

    ESPN's tennis scoreboard nests matches under `groupings` with
    `athlete`-shaped competitors (see
    `data_sources.espn.completed_tennis_singles_matches`) rather than the
    flat `competitions`/`team` shape `_find_espn_result` assumes, so tennis
    needs its own matcher. Checks both tours (tennis_forward.TENNIS_TOURS) --
    this used to check WTA only, back when tennis was WTA-only. ATP was added
    to forecasting on 2026-08-03 but this settlement matcher wasn't updated
    alongside it. Combined tournaments (most ATP 500/1000s, all four majors)
    happen to return both tours' matches from either endpoint (see
    espn.completed_tennis_singles_matches's docstring), which is why this
    wasn't caught immediately -- but a genuinely ATP-only (non-combined)
    event never appears under the WTA endpoint at all, so any pick on one
    could never settle. Found 2026-08-04 while investigating a stuck-open
    ATP pick; that specific pick turned out to be a real tournament
    reschedule (unrelated), but the underlying WTA-only gap was real.
    """
    away_names = {row["away_team"].casefold(), row["original_away_team"].casefold()}
    home_names = {row["home_team"].casefold(), row["original_home_team"].casefold()}
    competitions: list[tuple[dict, dict]] = []
    for tour in TENNIS_TOURS:
        try:
            scoreboard = espn.scoreboard(tour, game_day)
        except Exception:
            logger.warning(
                "ESPN %s scoreboard fetch failed for %s; tennis settlement skipping this tour",
                tour,
                game_day,
                exc_info=True,
            )
            continue
        for event in scoreboard.get("events", []):
            for grouping in event.get("groupings", []):
                for competition in grouping.get("competitions", []):
                    competitions.append((event, competition))

    ledger_event_id = str(row.get("event_id") or "")
    for identity_only in (True, False):
        for event, competition in competitions:
            slug = str(competition.get("type", {}).get("slug", ""))
            if "singles" not in slug:
                continue
            competitors = competition.get("competitors", [])
            if len(competitors) != 2:
                continue
            by_side = {item.get("homeAway"): item for item in competitors}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            source_result_id = f"{event.get('id')}:{competition.get('id')}"
            identity_match = bool(ledger_event_id) and source_result_id == ledger_event_id
            if identity_only and not identity_match:
                continue
            away_name = str((away.get("athlete") or {}).get("displayName", ""))
            home_name = str((home.get("athlete") or {}).get("displayName", ""))
            name_match = away_name.casefold() in away_names and home_name.casefold() in home_names
            if not identity_only and not name_match:
                continue

            status = competition.get("status", {}).get("type", {})
            completed = bool(status.get("completed"))
            record = {"completed": completed, "status_name": str(status.get("name", ""))}
            if ledger_event_id:
                record.update(
                    {
                        "source_event_id": str(event.get("id") or ""),
                        "source_competition_id": str(competition.get("id") or ""),
                        "source_result_id": source_result_id,
                        "match_basis": "event_id" if identity_match else "player_names",
                    }
                )
            if completed:
                record["away_score"] = 1 if away.get("winner") else 0
                record["home_score"] = 1 if home.get("winner") else 0
                irregular_reason = _tennis_irregular_result_reason(competition)
                game_totals = _tennis_completed_game_totals(away, home)
                if irregular_reason is not None:
                    record["derivative_ungradeable_reason"] = irregular_reason
                elif isinstance(game_totals, str):
                    if game_totals != "ESPN result is missing per-set linescores":
                        record["derivative_ungradeable_reason"] = game_totals
                else:
                    record["away_games"], record["home_games"] = game_totals
            return record
    return None


def _settle_tennis_pick(row: dict, ledger, espn: ESPNClient, data_root=None) -> dict | None:
    try:
        start = parse_utc(row["event_start_utc"])
    except ValueError:
        return {"pick_id": row["pick_id"], "reason": "bad event_start_utc"}
    game_day = start.astimezone(EASTERN).date().isoformat()
    match = _find_tennis_result(espn, game_day, row)
    if match is None or not match.get("completed"):
        return None
    market_type = str(row.get("market_type") or "").casefold()
    slug = _extract_market_slug(str(row.get("rationale", "")))
    if market_type != "moneyline" and slug is not None and _is_tennis_subperiod_slug(slug):
        return {
            "pick_id": row["pick_id"],
            "reason": (
                "UNSUPPORTED_TENNIS_SUBPERIOD_SETTLEMENT: exact set/period identity "
                "and result dimensions are unavailable"
            ),
        }
    if market_type in {"spread", "total"}:
        ungradeable_reason = match.get("derivative_ungradeable_reason")
        if ungradeable_reason is not None:
            return {
                "pick_id": row["pick_id"],
                "reason": f"UNGRADEABLE_TENNIS_DERIVATIVE: {ungradeable_reason}",
            }
        if "away_games" not in match or "home_games" not in match:
            return {
                "pick_id": row["pick_id"],
                "reason": ("UNGRADEABLE_TENNIS_DERIVATIVE: actual aligned per-set game scores are required"),
            }
        settle_away = int(match["away_games"])
        settle_home = int(match["home_games"])
    elif market_type == "moneyline":
        settle_away = int(match["away_score"])
        settle_home = int(match["home_score"])
    else:
        return {
            "pick_id": row["pick_id"],
            "reason": f"UNSUPPORTED_TENNIS_MARKET_TYPE: {market_type or 'unknown'}",
        }
    closing_probability = closing_odds = None
    if market_type == "moneyline" and data_root is not None and slug is not None:
        try:
            closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                data_root,
                "tennis",
                slug,
                row["event_start_utc"],
                row["home_team"],
                row["away_team"],
                row["selection"],
            )
        except (OSError, ValueError):
            logger.warning("tennis closing-snapshot lookup failed for slug %s", slug, exc_info=True)
    try:
        settled = ledger.settle(
            row["pick_id"],
            settle_away,
            settle_home,
            None,
            closing_odds,
            closing_raw_probability=closing_probability,
        )
        return {"pick_id": row["pick_id"], "result": settled["result"], "settled": True}
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}


def _identity_key(value: str) -> str:
    """Fold to comparable ASCII-alnum-lowercase (NFKD decomposition drops
    accents/diacritics), so real esports team names with accented spellings
    (e.g. "Grêmio Esports", "KRÜ Esports") match a market's side
    description consistently rather than silently falling into the
    "matches neither ledger team" fail-closed path. Same fix as
    ``features/starter_history.py::_normalize_name``.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c.lower() for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def _load_soccer_scores() -> dict[str, dict[str, Any]]:
    """Load collected soccer scores from the historical JSONL, keyed by event_id."""
    import json

    path = PROJECT_ROOT / "data" / "historical" / "soccer_games_all.jsonl"
    scores: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return scores
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = str(game.get("event_id", ""))
            if event_id:
                scores[event_id] = game
    return scores


def _find_soccer_result(
    row: dict,
    scores: dict[str, dict[str, Any]],
) -> dict | None:
    """Match a ledger row to a collected soccer score by event_id or team names."""
    # Exact event_id match first.
    event_id = str(row.get("event_id", ""))
    if event_id and event_id in scores:
        game = scores[event_id]
        try:
            return {
                "status_name": "STATUS_FINAL",
                "completed": True,
                "away_score": int(game["away_score"]),
                "home_score": int(game["home_score"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
    # Fall back to team-name matching.
    away_names = {str(row.get("away_team", "")).casefold(), str(row.get("original_away_team", "")).casefold()}
    home_names = {str(row.get("home_team", "")).casefold(), str(row.get("original_home_team", "")).casefold()}
    for game in scores.values():
        if (
            str(game.get("away_team", "")).casefold() in away_names
            and str(game.get("home_team", "")).casefold() in home_names
        ):
            try:
                return {
                    "status_name": "STATUS_FINAL",
                    "completed": True,
                    "away_score": int(game["away_score"]),
                    "home_score": int(game["home_score"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return None


def run_settle(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if args.all_unsettled:
        output = _settle_all_unsettled(args, config, ledger)
        # Also settle the flat ledger
        flat_ledger = MultiSportPickLedger(data_root, flat=True)
        output["flat_settlement"] = _settle_all_unsettled(args, config, flat_ledger)
        data_directory = Path(ledger_path(config)).parent
        research_settlement = {}
        for sport_ledger in existing_research_ledgers(data_directory):
            research_settlement[sport_ledger.path.stem] = _settle_all_unsettled(
                args,
                config,
                sport_ledger,
            )
        if research_settlement:
            output["research_settlement"] = research_settlement
        gated_settlement = {}
        for sport_ledger in existing_research_ledgers(
            data_directory,
            gated=True,
        ):
            gated_settlement[sport_ledger.path.stem] = _settle_all_unsettled(
                args,
                config,
                sport_ledger,
            )
        if gated_settlement:
            output["gated_research_settlement"] = gated_settlement
        # Also settle the dedicated Auto-Buyer ledger
        try:
            from ..portfolio.auto_buyer_ledger import settle_auto_buyer_ledger

            output["auto_buyer_settlement"] = settle_auto_buyer_ledger(data_directory)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as err:
            logger.warning("Auto-buyer settlement failed: %s", err)
    else:
        if not args.pick_id or args.away_score is None or args.home_score is None:
            raise ValueError("provide --pick-id with --away-score/--home-score, or --all-unsettled")
        closing_line = args.closing_line
        closing_odds = args.closing_american_odds
        closing_probability = None
        if closing_odds is None:
            row = next((r for r in ledger.rows() if r["pick_id"] == args.pick_id), None)
            if row is None:
                raise KeyError(f"unknown pick id: {args.pick_id}")
            quote = MarketOddsSnapshotStore(market_odds_snapshot_path(config)).closing_quote(
                row["event_id"], row["event_start_utc"], row["market_type"], row["selection"]
            )
            if quote is not None:
                closing_odds = int(quote["american_odds"])
                closing_probability = float(quote["decision_probability"])
                if quote.get("line") is not None:
                    closing_line = float(quote["line"])
        output = ledger.settle(
            args.pick_id,
            args.away_score,
            args.home_score,
            closing_line,
            closing_odds,
            closing_no_vig_probability=args.closing_no_vig_probability,
            closing_consensus_probability=args.closing_consensus_probability,
            closing_consensus_line=args.closing_consensus_line,
            closing_raw_probability=closing_probability,
        )
    return output
