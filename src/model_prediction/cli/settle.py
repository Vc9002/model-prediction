"""Settlement command group.

Mechanical extraction from the former cli.py monolith (DD-6 split, stage
3). Grades open picks against ESPN/Polymarket results per sport. The
logger is defined HERE, not imported, for the same reason as the shim:
ruff's BLE001 exemption for the blind-except handlers in this module
requires a locally-defined logger.
"""

from __future__ import annotations

import logging
import math
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..config import (
    PROJECT_ROOT,
    ledger_path,
    market_odds_snapshot_path,
    polymarket_edge_enabled,
)
from ..data_sources.espn import ESPNClient
from ..data_sources.mlb_market_odds import MarketOddsSnapshotStore
from ..data_sources.polymarket_us import (
    PolymarketSnapshotStore,
    probability_to_american,
)
from ..domain import EASTERN, parse_utc, utc_now
from ..ledger import defer_sqlite_xlsx_exports
from ..main_ledgers import MultiSportPickLedger
from ..research_ledgers import existing_research_ledgers
from ..tennis_forward import TENNIS_TOURS, _is_tennis_subperiod_slug
from .state import _LEDGER_LEAGUE_TO_ESPN, _TERMINAL_MARKET_STATES

logger = logging.getLogger("model_prediction.cli")


class _ScoreboardFetchError(RuntimeError):
    """Normalized provider error already logged by the scoreboard cache."""


def _cached_scoreboard(
    espn: ESPNClient,
    league: str,
    game_day: str,
    cache: dict[tuple[str, str], dict | Exception] | None,
) -> dict:
    key = (league, game_day)
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        if isinstance(cached, Exception):
            raise cached
        return cached
    try:
        cached = espn.scoreboard(league, game_day)
    except Exception as error:
        logger.warning(
            "ESPN scoreboard fetch failed for %s on %s; settlement skipping this league",
            league,
            game_day,
            exc_info=True,
        )
        failure = _ScoreboardFetchError(str(error))
        if cache is not None:
            cache[key] = failure
        raise failure from error
    if cache is not None:
        cache[key] = cached
    return cached


def _settle_all_unsettled(
    args,
    config,
    ledger,
    *,
    espn: ESPNClient | None = None,
    scoreboard_cache: dict[tuple[str, str], dict | Exception] | None = None,
) -> dict:
    """Grade every started open pick from ESPN scoreboards or Polymarket resolution."""
    now = utc_now()
    espn = espn or ESPNClient()
    market_store = MarketOddsSnapshotStore(market_odds_snapshot_path(config))
    data_root = Path(ledger_path(config)).parent
    settled, voided, pending, failures = [], [], [], []
    bo3_client = None
    # Wall time per league: the daily settlement step took 226-576s with no
    # way to tell which provider (ESPN, Polymarket resolution, Bo3) owned it.
    league_seconds: dict[str, float] = {}
    _lap_state: list[Any] = ["(ledger_read)", time.monotonic()]

    def _lap_row(next_label: str) -> None:
        now_t = time.monotonic()
        league_seconds[_lap_state[0]] = round(
            league_seconds.get(_lap_state[0], 0.0) + now_t - _lap_state[1], 2
        )
        _lap_state[0], _lap_state[1] = next_label, now_t

    for row in ledger.rows():
        _lap_row(str(row.get("league") or "?"))
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
            if bo3_client is None:
                from ..esports import Bo3EsportsClient

                bo3_client = Bo3EsportsClient()
            result = _settle_esports_pick(row, ledger, data_root=data_root, bo3_client=bo3_client)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # KBO/NPB: settle from official schedule; fall back to Polymarket
        # resolution when official league sites timeout or fail.
        if row["league"] in ("KBO", "NPB"):
            result = _settle_international_baseball_pick(
                row, ledger, config, data_root=data_root
            ) or _settle_by_resolution(row, ledger)
            if result is None:
                pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # Tennis: player-vs-player, not team-vs-team -- ESPN's tennis
        # scoreboard shape doesn't fit `_find_espn_result` at all (see
        # `_find_tennis_result`).
        if row["league"] == "TENNIS":
            result = _settle_tennis_pick(
                row,
                ledger,
                espn,
                data_root=data_root,
                scoreboard_cache=scoreboard_cache,
            )
            if result is None:
                result = _settle_by_resolution(row, ledger)
            if result is None:
                if (now - start).total_seconds() > 7 * 86400:
                    try:
                        v = ledger.void(
                            row["pick_id"], "tennis match abandoned, unrecorded, or postponed >7 days"
                        )
                        voided.append(v["pick_id"])
                    except (KeyError, ValueError, OSError) as e:
                        failures.append({"pick_id": row["pick_id"], "reason": str(e)})
                else:
                    pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            elif str(result.get("reason", "")).startswith(
                (
                    "UNSUPPORTED_TENNIS_SUBPERIOD_SETTLEMENT",
                    "UNGRADEABLE_TENNIS_DERIVATIVE",
                    "UNSUPPORTED_TENNIS_MARKET_TYPE",
                )
            ):
                try:
                    v = ledger.void(row["pick_id"], result["reason"])
                    voided.append(v["pick_id"])
                except (KeyError, ValueError, OSError) as e:
                    failures.append({"pick_id": row["pick_id"], "reason": str(e)})
            else:
                failures.append(result)
            continue
        # Darts: no ESPN/Polymarket result feed -- grade from the observed
        # match history (Wikipedia brackets, refreshed in the daily forecast).
        if row["league"] in ("DARTS", "DARTS_PDC", "DARTS_MODUS"):
            result = _settle_darts_pick(row, ledger, data_root=data_root) or _settle_by_resolution(
                row, ledger
            )
            if result is None:
                if (now - start).total_seconds() > 7 * 86400:
                    try:
                        voided.append(
                            ledger.void(
                                row["pick_id"], "darts result unrecorded in match history after >7 days"
                            )["pick_id"]
                        )
                    except (KeyError, ValueError, OSError) as e:
                        failures.append({"pick_id": row["pick_id"], "reason": str(e)})
                else:
                    pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
            elif result.get("settled"):
                settled.append(result)
            else:
                failures.append(result)
            continue
        # EuroLeague: ESPN has no feed -- grade from the official live API results
        # cached by the daily forecast.
        if row["league"] == "EUROLEAGUE":
            result = _settle_euroleague_pick(row, ledger, data_root=data_root) or _settle_by_resolution(
                row, ledger
            )
            if result is None:
                if (now - start).total_seconds() > 7 * 86400:
                    try:
                        voided.append(
                            ledger.void(row["pick_id"], "euroleague result unrecorded after >7 days")[
                                "pick_id"
                            ]
                        )
                    except (KeyError, ValueError, OSError) as e:
                        failures.append({"pick_id": row["pick_id"], "reason": str(e)})
                else:
                    pending.append(row["pick_id"])
            elif result.get("voided"):
                voided.append(result["pick_id"])
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
        match = _find_espn_result(
            espn,
            leagues,
            game_day,
            row,
            scoreboard_cache=scoreboard_cache,
        )
        if match is None and row["league"] == "SOCCER":
            # Soccer: try collected scores from The Odds API for leagues
            # outside ESPN coverage (e.g. Brazil Serie B, K League 1).
            soccer_scores = _load_soccer_scores()
            match = _find_soccer_result(row, soccer_scores)
            if match is None:
                match = _find_espn_soccer_result_by_event_id(espn, row)
        if match is None:
            # No ESPN record (leagues ESPN does not carry): the contract's own resolution is
            # enough to grade a win/loss.
            resolved = _settle_by_resolution(row, ledger)
            if resolved is not None and resolved.get("settled"):
                settled.append(resolved)
                continue
            if resolved is not None and resolved.get("voided"):
                voided.append(resolved["pick_id"])
                continue
            if (now - start).total_seconds() > 7 * 86400:
                try:
                    voided.append(
                        ledger.void(row["pick_id"], "event unrecorded on ESPN after >7 days")["pick_id"]
                    )
                except (KeyError, ValueError, OSError) as error:
                    failures.append({"pick_id": row["pick_id"], "reason": str(error)})
            else:
                pending.append(row["pick_id"])
            continue
        status = match.get("status_name", "")
        if status in {"STATUS_POSTPONED", "STATUS_CANCELED"}:
            if getattr(args, "void_postponed", False) or (now - start).total_seconds() > 7 * 86400:
                voided.append(ledger.void(row["pick_id"], f"event {status.lower()}")["pick_id"])
            else:
                pending.append(row["pick_id"])
            continue
        if not match.get("completed"):
            if (now - start).total_seconds() > 7 * 86400:
                try:
                    voided.append(
                        ledger.void(row["pick_id"], f"event uncompleted ({status}) after >7 days")["pick_id"]
                    )
                except (KeyError, ValueError, OSError) as error:
                    failures.append({"pick_id": row["pick_id"], "reason": str(error)})
            else:
                pending.append(row["pick_id"])
            continue
        if row.get("market_type") in ("nrfi", "yrfi") and not all(
            key in match for key in ("away_1st", "home_1st")
        ):
            pending.append(row["pick_id"])
            logger.warning(
                "NRFI settlement pending for %s: explicit first-inning scores unavailable", row["pick_id"]
            )
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

    if polymarket_edge_enabled(config, "ledger"):
        try:
            from ..portfolio.polymarket_ledger import settle_polymarket_ledger_rows

            poly_settle_res = settle_polymarket_ledger_rows(data_root=data_root, espn_client=espn)
        except Exception:
            logger.warning("Polymarket edge ledger settlement failed", exc_info=True)
            poly_settle_res = {"status": "error"}
    else:
        poly_settle_res = {"status": "disabled", "reason": "operator_disabled"}

    _lap_row("(after_loop)")
    return {
        "settled": settled,
        "voided": voided,
        "still_open": pending,
        "failures": failures,
        "seconds_by_league": dict(sorted(league_seconds.items(), key=lambda kv: -kv[1])),
        "polymarket_edge_settlement": poly_settle_res,
        "note": "Results pulled from ESPN scoreboards; closing prices from stored pregame BBO asks.",
    }


def _extract_espn_record(status: dict, away: dict, home: dict) -> dict:
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
                firsts = (away_lines[0], home_lines[0])
                values = [inning.get("value") for inning in firsts]
                if all(
                    inning.get("period", 1) == 1
                    and not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(value)
                    and value >= 0
                    and value == int(value)
                    for inning, value in zip(firsts, values, strict=True)
                ):
                    record["away_1st"], record["home_1st"] = (int(value) for value in values)
        except (TypeError, ValueError):
            record["completed"] = False
    return record


def _find_espn_result(
    espn: ESPNClient,
    leagues,
    game_day: str,
    row,
    *,
    scoreboard_cache: dict[tuple[str, str], dict | Exception] | None = None,
) -> dict | None:
    """Find a completed-game record matching a ledger row by id or team names.

    Prioritizes exact event_id match across all scoreboard events. When no exact
    id match exists, falls back to name matching. If multiple games share the same
    team names on the same day (e.g. doubleheaders), disambiguates by start time.
    """
    away_names = {row["away_team"].casefold(), row["original_away_team"].casefold()}
    home_names = {row["home_team"].casefold(), row["original_home_team"].casefold()}
    row_event_id = str(row.get("event_id") or "").strip()

    events_to_check: list[tuple[dict, dict, dict, dict]] = []
    for league in leagues:
        try:
            scoreboard = _cached_scoreboard(espn, league, game_day, scoreboard_cache)
        except _ScoreboardFetchError:
            continue
        for event in scoreboard.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {}).get("type", {})
            by_side = {item.get("homeAway"): item for item in competition.get("competitors", [])}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            if row_event_id and str(event.get("id")) == row_event_id:
                return _extract_espn_record(status, away, home)
            events_to_check.append((event, competition, away, home))

    name_matched: list[tuple[dict, dict, dict, dict]] = []
    for event, competition, away, home in events_to_check:
        away_team_name = away["team"].get("displayName", "").casefold()
        home_team_name = home["team"].get("displayName", "").casefold()
        if away_team_name in away_names and home_team_name in home_names:
            name_matched.append((event, competition, away, home))

    if not name_matched:
        return None

    if len(name_matched) == 1:
        _, chosen_comp, chosen_away, chosen_home = name_matched[0]
        return _extract_espn_record(chosen_comp.get("status", {}).get("type", {}), chosen_away, chosen_home)

    # Disambiguate multiple matches (e.g. doubleheaders) by start time
    row_start_raw = row.get("event_start_utc") or row.get("commence_time")
    row_start_dt = None
    if row_start_raw:
        try:
            row_start_dt = parse_utc(str(row_start_raw))
        except (ValueError, TypeError):
            row_start_dt = None

    if row_start_dt is not None:

        def _time_delta(item: tuple[dict, dict, dict, dict]) -> float:
            ev, comp, _, _ = item
            ev_date_raw = comp.get("date") or ev.get("date")
            if not ev_date_raw:
                return float("inf")
            try:
                ev_dt = parse_utc(str(ev_date_raw))
                return abs((ev_dt - row_start_dt).total_seconds())
            except (ValueError, TypeError):
                return float("inf")

        _, chosen_comp, chosen_away, chosen_home = min(name_matched, key=_time_delta)
    else:
        _, chosen_comp, chosen_away, chosen_home = name_matched[0]

    return _extract_espn_record(chosen_comp.get("status", {}).get("type", {}), chosen_away, chosen_home)


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

    match = re.search(r"market_slug=([a-z0-9_\-]+)", rationale)
    if match:
        return match.group(1)
    for m in re.findall(r"\(([a-z0-9_\-]+)\)", rationale):
        if "-" in m or m.startswith(("atc", "tsc", "asc", "mkt", "aec")):
            return m
    match = re.search(r"\(([a-z0-9_\-]+)\)", rationale)
    return match.group(1) if match else None


def _settle_darts_pick(row: dict, ledger, data_root=None) -> dict | None:
    """Settle a darts pick from the cached match history; None while the result is unrecorded."""
    from ..darts_forward import _player_matches
    from ..darts_history import load_darts_history

    if data_root is None:
        return None
    start = parse_utc(row["event_start_utc"])
    home, away = str(row.get("home_team", "")), str(row.get("away_team", ""))
    for record in load_darts_history(data_root):
        gap_days = abs((date.fromisoformat(record.match_date) - start.date()).days)
        if gap_days > 2:
            continue
        if _player_matches(home, record.player_a) and _player_matches(away, record.player_b):
            home_legs, away_legs = record.legs_a, record.legs_b
        elif _player_matches(home, record.player_b) and _player_matches(away, record.player_a):
            home_legs, away_legs = record.legs_b, record.legs_a
        else:
            continue
        slug = _extract_market_slug(str(row.get("rationale", "")))
        closing_probability = closing_odds = None
        if slug is not None:
            try:
                closing_probability, closing_odds = _closing_probability_for_moneyline_pick(
                    data_root, "darts", slug, row["event_start_utc"], home, away, row.get("selection", "")
                )
            except (OSError, ValueError):
                logger.warning("darts closing-snapshot lookup failed for slug %s", slug, exc_info=True)
        try:
            settled = ledger.settle(
                row["pick_id"],
                away_legs,
                home_legs,
                None,
                closing_odds,
                closing_raw_probability=closing_probability,
            )
        except (KeyError, ValueError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
        return {"pick_id": row["pick_id"], "settled": True, "result": settled["result"]}
    return None


def _settle_euroleague_pick(row: dict, ledger, data_root=None) -> dict | None:
    """Settle a EuroLeague pick from the cached official results; None while unrecorded."""
    import json

    from ..data_sources.euroleague import games_path

    if data_root is None or not games_path(data_root).exists():
        return None
    start = parse_utc(row["event_start_utc"])
    home, away = str(row.get("home_team", "")), str(row.get("away_team", ""))
    for line in games_path(data_root).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["home_team"] != home or record["away_team"] != away:
            continue
        if abs((parse_utc(record["event_start_utc"]) - start).total_seconds()) > 2 * 86400:
            continue
        try:
            settled = ledger.settle(row["pick_id"], record["away_score"], record["home_score"], None, None)
        except (KeyError, ValueError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
        return {"pick_id": row["pick_id"], "settled": True, "result": settled["result"]}
    return None


def _scores_for_outcome(market_type: str, selection: str, line, won: bool) -> tuple[int, int]:
    """Minimal (away, home) scores that grade the pick ``won``/lost under ``grade_pick``.

    Resolution tells us only whether the contract paid, which is all grading needs;
    the scores are synthetic and never used for modelling.
    """
    import math

    selection = selection.lower()
    if market_type == "total":
        over_score = (math.floor(float(line)) + 1, 0)  # total just above the line
        under_score = (0, 0)
        return over_score if (selection == "over") == won else under_score
    if market_type == "spread":
        # margin for the selected side must land on the covering side of the line
        margin = math.floor(-float(line)) + 1 if won else math.floor(-float(line))
        sel_score, other = max(margin, 0), max(-margin, 0)
    else:
        sel_score, other = (1, 0) if won else (0, 1)
    return (other, sel_score) if selection == "home" else (sel_score, other)


def _settle_by_resolution(row: dict, ledger, client=None) -> dict | None:
    """Settle any pick from its Polymarket contract's terminal resolution.

    Used when no results API covers the league. None while the market is unresolved or
    the terminal prices are not a clean 1/0 (never guessed); the caller keeps the pick
    pending and eventually voids it.
    """
    from ..data_sources.polymarket_us import PolymarketUSClient, _amount

    slug = _extract_market_slug(str(row.get("rationale", "")))
    market_type = str(row.get("market_type") or "").lower().split(".")[-1]
    if slug is None or market_type not in {"moneyline", "spread", "total"}:
        return None
    client = client or PolymarketUSClient()
    try:
        market = client.market(slug)
        book = client.book(slug)
    except Exception:
        logger.warning("resolution lookup failed for %s", slug, exc_info=True)
        return None
    if not _market_is_terminal(market, book):
        return None
    sides = market.get("marketSides", [])
    prices = [(side, _amount(side.get("price"))) for side in sides]
    valid_prices = [p for _, p in prices if p is not None]
    if len(prices) != 2 or len(valid_prices) != 2:
        return None
    if sorted(valid_prices) != [0.0, 1.0]:
        # Market reached terminal state with non-binary prices (e.g. 0.3/0.7 or void/split
        # settlement due to player withdrawal, cancellation, or walkover). Void cleanly.
        try:
            voided = ledger.void(
                row["pick_id"],
                f"market resolved terminal with non-binary prices {valid_prices}",
            )
            return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError, OSError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
    selection = str(row.get("selection") or "").lower()
    own = None
    if market_type == "total":
        own = next(
            (
                side
                for side, _ in prices
                if str(side.get("description", "")).lower().startswith(selection[:1])
            ),
            None,
        )
    else:
        team = str(row["home_team"] if selection == "home" else row["away_team"])
        tkey = _identity_key(team)
        for side, _ in prices:
            name = _identity_key(str((side.get("team") or {}).get("name") or side.get("description") or ""))
            if name and (name in tkey or tkey in name):
                if market_type == "spread":
                    try:
                        if (
                            abs(float(str(side.get("description")).replace("+", "")) - float(row["line"]))
                            > 1e-6
                        ):
                            continue
                    except (TypeError, ValueError):
                        continue
                own = side
                break
    if own is None:
        return {"pick_id": row["pick_id"], "reason": f"resolution side unresolved for {slug}"}
    won = next(price for side, price in prices if side is own) == 1.0
    away, home = _scores_for_outcome(market_type, selection, row.get("line"), won)
    try:
        settled = ledger.settle(row["pick_id"], away, home, None, None)
    except (KeyError, ValueError) as error:
        return {"pick_id": row["pick_id"], "reason": str(error)}
    return {
        "pick_id": row["pick_id"],
        "settled": True,
        "result": settled["result"],
        "source": "polymarket_resolution",
    }


_RESOLVED_MARKET_STATUSES = frozenset({"MARKET_STATUS_RESOLVED"})


def _market_is_terminal(market: dict, book: dict) -> bool:
    """True when a Polymarket contract has finished.

    The book state turns terminal once it expires, but a resolved market can keep reporting an
    open, empty book (MARKET_STATE_OPEN with no bid or ask) while the market itself is already
    MARKET_STATUS_RESOLVED with side prices 1 and 0 (CS2 FORZE Reload vs BakS, 2026-10-18, sat
    open for days). Either signal counts; callers still require clean 1/0 side prices before
    they settle anything.
    """
    if str(book.get("state") or "") in _TERMINAL_MARKET_STATES:
        return True
    return str(market.get("status") or "") in _RESOLVED_MARKET_STATUSES


def _settle_esports_pick(row: dict, ledger, data_root=None, bo3_client=None) -> dict | None:
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
    book_state = str(book.get("state") or "")
    if not _market_is_terminal(market, book):
        try:
            start_dt = parse_utc(row["event_start_utc"])
            if (utc_now() - start_dt).total_seconds() > 7 * 86400:
                from ..esports import resolve_esports_match_result

                bo3_res = resolve_esports_match_result(
                    row["league"],
                    str(row["home_team"]),
                    str(row["away_team"]),
                    str(row["event_start_utc"]),
                    data_root or PROJECT_ROOT / "data",
                    client=bo3_client,
                )
                if bo3_res is not None:
                    away_score = bo3_res.get("away_score", 1 if bo3_res["away_win"] else 0)
                    home_score = bo3_res.get("home_score", 1 if bo3_res["home_win"] else 0)
                    result = ledger.settle(row["pick_id"], away_score, home_score, None, None)
                    return {"pick_id": row["pick_id"], "result": result["result"], "settled": True}
                voided = ledger.void(
                    row["pick_id"],
                    f"esports match >7d old unresolved with book state {book_state}; BO3 has no record",
                )
                return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError, OSError, httpx.HTTPError) as err:
            logger.debug("Failed checking stale esports pick %s: %s", row["pick_id"], err)
        return None
    prices: dict[str, float] = {}
    for side in market.get("marketSides", []):
        price = _amount(side.get("price"))
        if price is not None:
            prices[str(side.get("description") or "")] = price
    if len(prices) != 2 or sorted(prices.values()) != [0.0, 1.0]:
        # A non-binary terminal price on a resolved esports market is NOT
        # reliably a forfeit/postponement signal -- confirmed live
        # 2026-09-14: real, cleanly-decided CS2 matches (verified against
        # BO3's own `winner_team_id`) still come back from Polymarket US
        # showing pre-close trading prices instead of a clean [0.0, 1.0],
        # for reasons on Polymarket's side, not the match's. Voiding on this
        # signal alone silently mis-records real wins/losses as pushes (see
        # the ~260 esports picks voided this way since 2026-07-26). Check
        # BO3's actual match result before assuming forfeit/postponement.
        from ..esports import resolve_esports_match_result

        bo3_result = None
        try:
            bo3_result = resolve_esports_match_result(
                row["league"],
                str(row["home_team"]),
                str(row["away_team"]),
                str(row["event_start_utc"]),
                data_root or PROJECT_ROOT / "data",
                client=bo3_client,
            )
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError):
            logger.warning("BO3 esports result lookup failed for slug %s", slug, exc_info=True)
        if bo3_result is not None:
            away_score = bo3_result.get("away_score", 1 if bo3_result["away_win"] else 0)
            home_score = bo3_result.get("home_score", 1 if bo3_result["home_win"] else 0)
            try:
                result = ledger.settle(row["pick_id"], away_score, home_score, None, None)
                return {"pick_id": row["pick_id"], "result": result["result"], "settled": True}
            except (KeyError, ValueError) as error:
                return {"pick_id": row["pick_id"], "reason": str(error)}
        # BO3 has no unambiguous result either -- this genuinely is an
        # unresolvable fixture (forfeit, disqualification, or a
        # postponement never rescheduled within two weeks all "settle to
        # the last fair market price" per these contracts' own rules).
        try:
            voided = ledger.void(
                row["pick_id"],
                f"esports market terminal ({book_state}) with prices {prices}; "
                "BO3 has no confirming finished-match result either",
            )
            return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
    winning_description = next(name for name, price in prices.items() if price == 1.0)
    m_type_raw = str(row.get("market_type") or "moneyline").lower()
    if m_type_raw.startswith("markettype."):
        m_type_raw = m_type_raw.split(".")[-1]

    if m_type_raw == "total":
        win_desc_lower = winning_description.lower()
        if "over" in win_desc_lower or win_desc_lower.startswith("o"):
            home_score, away_score = 2, 1
        elif "under" in win_desc_lower or win_desc_lower.startswith("u"):
            home_score, away_score = 2, 0
        else:
            return {
                "pick_id": row["pick_id"],
                "reason": f"unrecognized total winning description: {winning_description!r}",
            }
    elif m_type_raw == "spread":
        win_desc_lower = winning_description.lower()
        home_key = _identity_key(str(row["home_team"]))
        away_key = _identity_key(str(row["away_team"]))
        winner_key = _identity_key(winning_description)

        home_in_desc = home_key in winner_key or str(row["home_team"]).lower() in win_desc_lower
        away_in_desc = away_key in winner_key or str(row["away_team"]).lower() in win_desc_lower

        if not home_in_desc and not away_in_desc:
            anchor_team = next(
                (
                    (side.get("team") or {}).get("name")
                    for side in market.get("marketSides", [])
                    if side.get("team")
                ),
                None,
            )
            long_desc = next(
                (s.get("description") for s in market.get("marketSides", []) if s.get("long")), None
            )
            winning_is_long = winning_description == long_desc
            anchor_is_home = _identity_key(str(anchor_team or "")) == home_key
            if (winning_is_long and anchor_is_home) or (not winning_is_long and not anchor_is_home):
                covering_is_home = True
            else:
                covering_is_home = False
        else:
            covering_is_home = home_in_desc

        is_minus = "-1.5" in win_desc_lower or win_desc_lower.startswith("-")
        if covering_is_home:
            if is_minus:
                home_score, away_score = 2, 0
            else:
                home_score, away_score = 1, 2
        else:
            if is_minus:
                home_score, away_score = 0, 2
            else:
                home_score, away_score = 2, 1
    else:
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
    if data_root is not None and m_type_raw == "moneyline":
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
        try:
            if (utc_now() - start).total_seconds() > 7 * 86400:
                voided = ledger.void(
                    row["pick_id"],
                    f"{row['league']} game scheduled on {game_date} cancelled/postponed (no final score posted after >7 days)",
                )
                return {"pick_id": row["pick_id"], "voided": True, "result": voided["result"]}
        except (KeyError, ValueError, OSError) as error:
            return {"pick_id": row["pick_id"], "reason": str(error)}
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


def _find_tennis_result(
    espn: ESPNClient,
    game_day: str,
    row: dict,
    *,
    scoreboard_cache: dict[tuple[str, str], dict | Exception] | None = None,
) -> dict | None:
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
            scoreboard = _cached_scoreboard(espn, tour, game_day, scoreboard_cache)
        except _ScoreboardFetchError:
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
            c1, c2 = competitors[0], competitors[1]
            c1_name = str((c1.get("athlete") or {}).get("displayName", ""))
            c2_name = str((c2.get("athlete") or {}).get("displayName", ""))

            source_result_id = f"{event.get('id')}:{competition.get('id')}"
            identity_match = bool(ledger_event_id) and source_result_id == ledger_event_id
            if identity_only and not identity_match:
                continue

            if (
                c1_name.casefold() in away_names
                or any(an in c1_name.casefold() for an in away_names if len(an) > 3)
            ) and (
                c2_name.casefold() in home_names
                or any(hn in c2_name.casefold() for hn in home_names if len(hn) > 3)
            ):
                away, home = c1, c2
                name_match = True
            elif (
                c2_name.casefold() in away_names
                or any(an in c2_name.casefold() for an in away_names if len(an) > 3)
            ) and (
                c1_name.casefold() in home_names
                or any(hn in c1_name.casefold() for hn in home_names if len(hn) > 3)
            ):
                away, home = c2, c1
                name_match = True
            else:
                by_side = {item.get("homeAway"): item for item in competitors}
                away, home = by_side.get("away", c1), by_side.get("home", c2)
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


def _settle_tennis_pick(
    row: dict,
    ledger,
    espn: ESPNClient,
    data_root=None,
    *,
    scoreboard_cache: dict[tuple[str, str], dict | Exception] | None = None,
) -> dict | None:
    try:
        start = parse_utc(row["event_start_utc"])
    except ValueError:
        return {"pick_id": row["pick_id"], "reason": "bad event_start_utc"}
    game_day = start.astimezone(EASTERN).date().isoformat()
    match = _find_tennis_result(espn, game_day, row, scoreboard_cache=scoreboard_cache)
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


def _find_espn_soccer_result_by_event_id(espn, row: dict) -> dict | None:
    """Resolve a soccer row from ESPN by the event id already stored on it.

    Soccer picks carry ESPN event ids (nine-digit, e.g. 401906390), so the
    result can be looked up by identity instead of by competition + date +
    team name. That matters twice over. It needs no credential -- the
    API-Football path above returns no_api_key and silently skips whenever
    API_FOOTBALL_KEY is unset, which is how soccer results went 7 days stale
    to 2026-08-24 with every daily run still exiting 0. And it cannot
    mis-match: team-name matching across ~45
    soccer competitions is the collision class this codebase has been bitten
    by before, and an id either resolves or it does not.

    ESPN's "all" path resolves an event without knowing its league, so this
    covers competitions outside the configured league list too.
    """
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        return None
    try:
        summary = espn.summary("SOCCER_ALL", event_id)
    except Exception:
        logger.warning("ESPN soccer summary lookup failed for event %s", event_id, exc_info=True)
        return None
    competitions = (summary.get("header") or {}).get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    status_name = str((((competition.get("status") or {}).get("type")) or {}).get("name") or "")
    scores: dict[str, int] = {}
    for competitor in competition.get("competitors") or []:
        side = str(competitor.get("homeAway") or "")
        try:
            scores[side] = int(competitor.get("score"))
        except (TypeError, ValueError):
            return None
    if "home" not in scores or "away" not in scores:
        return None
    # STATUS_FULL_TIME is soccer's final state; STATUS_FINAL is what the rest
    # of the settle path expects, so normalise rather than teach every caller
    # a second spelling of "done".
    completed = status_name in {"STATUS_FULL_TIME", "STATUS_FINAL"}
    return {
        "status_name": "STATUS_FINAL" if completed else status_name,
        "completed": completed,
        "away_score": scores["away"],
        "home_score": scores["home"],
    }


@defer_sqlite_xlsx_exports()
def run_settle(args, config, registry, bans, ledger, audit, data_root) -> dict:
    if args.all_unsettled:
        espn = ESPNClient()
        scoreboard_cache: dict[tuple[str, str], dict | Exception] = {}
        output = _settle_all_unsettled(
            args,
            config,
            ledger,
            espn=espn,
            scoreboard_cache=scoreboard_cache,
        )
        # Also settle the flat ledger
        flat_ledger = MultiSportPickLedger(data_root, flat=True)
        output["flat_settlement"] = _settle_all_unsettled(
            args,
            config,
            flat_ledger,
            espn=espn,
            scoreboard_cache=scoreboard_cache,
        )
        data_directory = Path(ledger_path(config)).parent
        research_settlement = {}
        for sport_ledger in existing_research_ledgers(data_directory):
            research_settlement[sport_ledger.path.stem] = _settle_all_unsettled(
                args,
                config,
                sport_ledger,
                espn=espn,
                scoreboard_cache=scoreboard_cache,
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
                espn=espn,
                scoreboard_cache=scoreboard_cache,
            )
        if gated_settlement:
            output["gated_research_settlement"] = gated_settlement
        # Resolve any resting IOC-fallback orders before settlement, so a
        # fallback that filled overnight is reflected in today's shares/cost
        # rather than waiting for the next cycle.
        try:
            from ..portfolio.auto_buyer_ledger import (
                reconcile_pending_auto_buyer_fallbacks,
                settle_auto_buyer_ledger,
            )

            output["auto_buyer_fallback_reconciliation"] = reconcile_pending_auto_buyer_fallbacks(
                data_directory
            )
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
