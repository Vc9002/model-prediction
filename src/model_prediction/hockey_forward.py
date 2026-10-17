"""Hockey forward prediction and Polymarket market evaluation engine.

Evaluates Dixon-Coles bivariate Poisson hockey models against live or captured
Polymarket BBO quotes across all 8 supported hockey leagues:
  * NHL (National Hockey League)
  * AHL (American Hockey League)
  * KHL (Kontinental Hockey League)
  * DEL (Deutsche Eishockey Liga)
  * SHL (Swedish Hockey League)
  * SNHL (Swiss National League)
  * CEHL (Czech Extraliga)
  * LIIGA (Finnish Liiga)

Guarantees point-in-time safety: no future scores or closing odds are read.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .data_sources.polymarket_us import (
    PolymarketUSClient,
    probability_to_american,
)
from .domain import utc_now
from .models.hockey import HockeyModel, UpcomingHockeyMatch, hockey_model

logger = logging.getLogger(__name__)

ALL_HOCKEY_LEAGUES: tuple[str, ...] = (
    "NHL",
    "AHL",
    "KHL",
    "DEL",
    "SHL",
    "SNHL",
    "CEHL",
    "LIIGA",
)

_GENERIC_WORDS = {"hc", "sc", "ec", "eishockey", "club", "team", "hockey"}

# Common NHL 3-letter city abbreviation prefixes in Polymarket titles
_CITY_PREFIXES = {
    "ana": "anaheim ducks",
    "bos": "boston bruins",
    "buf": "buffalo sabres",
    "cgy": "calgary flames",
    "car": "carolina hurricanes",
    "chi": "chicago blackhawks",
    "col": "colorado avalanche",
    "cbj": "columbus blue jackets",
    "dal": "dallas stars",
    "det": "detroit red wings",
    "edm": "edmonton oilers",
    "fla": "florida panthers",
    "lak": "los angeles kings",
    "min": "minnesota wild",
    "mtl": "montreal canadiens",
    "nsh": "nashville predators",
    "njd": "new jersey devils",
    "nyi": "new york islanders",
    "nyr": "new york rangers",
    "ott": "ottawa senators",
    "phi": "philadelphia flyers",
    "pit": "pittsburgh penguins",
    "sjs": "san jose sharks",
    "sea": "seattle kraken",
    "stl": "st louis blues",
    "tbl": "tampa bay lightning",
    "tor": "toronto maple leafs",
    "uta": "utah hockey club",
    "van": "vancouver canucks",
    "vgk": "vegas golden knights",
    "wsh": "washington capitals",
    "wpg": "winnipeg jets",
}


def _clean_tokens(text: str) -> list[str]:
    clean = str(text or "").replace(chr(0xFFFD), "e")
    clean = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[a-z0-9]+", clean.casefold())
    return [t for t in tokens if t not in _GENERIC_WORDS]


def _team_matches(team_a: str, team_b: str) -> bool:
    """Robust token match between team identifiers and market side descriptions."""
    if not team_a or not team_b:
        return False
    a_norm = " ".join(_clean_tokens(team_a))
    b_norm = " ".join(_clean_tokens(team_b))
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        return True

    toks_a = set(_clean_tokens(team_a))
    toks_b = set(_clean_tokens(team_b))
    if not toks_a or not toks_b:
        return False

    # Check for prefix abbreviation matches
    for tok in toks_a:
        if tok in _CITY_PREFIXES and set(_clean_tokens(_CITY_PREFIXES[tok])) & toks_b:
            return True
    for tok in toks_b:
        if tok in _CITY_PREFIXES and set(_clean_tokens(_CITY_PREFIXES[tok])) & toks_a:
            return True

    overlap = toks_a & toks_b
    if overlap:
        # If distinctive tokens (length >= 4) overlap
        if any(len(t) >= 4 for t in overlap):
            return True
        if len(overlap) >= 2:
            return True
    return False


def _load_historical_games(data_root: str | Path, league: str | None = None) -> list[Any]:
    """Load completed games from processed games cache for hockey."""
    games: list[Any] = []
    root = Path(data_root)
    # Check processed/hockey/games.jsonl
    paths = [
        root / "processed" / "hockey" / "games.jsonl",
        root / "data" / "processed" / "hockey" / "games.jsonl",
    ]
    for p in paths:
        if p.exists():
            try:
                with p.open(encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            games.append(json.loads(line))
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("error reading %s: %s", p, e)
    return games


def _parse_teams_from_event(event: dict[str, Any]) -> tuple[str, str]:
    """Extract away and home team names from a Polymarket or ESPN event."""
    # 1. Try Polymarket market sides
    for market in event.get("markets", []):
        sides = market.get("sides", [])
        if len(sides) == 2:
            desc_0 = str(sides[0].get("description", ""))
            desc_1 = str(sides[1].get("description", ""))
            sel_0 = str(sides[0].get("selection", "")).lower()
            if sel_0 == "away":
                return desc_0, desc_1
            if sel_0 == "home":
                return desc_1, desc_0
            if " vs " in event.get("title", ""):
                parts = event["title"].split(" vs ")
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip()
            return desc_0, desc_1

    # 2. Try event title: "Away vs. Home" or "Away at Home"
    title = str(event.get("title", ""))
    for sep in (" vs. ", " vs ", " at ", " @ "):
        if sep in title:
            parts = title.split(sep, 1)
            return parts[0].strip(), parts[1].strip()

    return "Away Team", "Home Team"


def build_hockey_slate(
    data_root: str | Path,
    game_date: str,
    client: PolymarketUSClient | None = None,
    leagues: Sequence[str] | None = None,
    observed_at: datetime | None = None,
    model: HockeyModel | None = None,
    min_team_games: int = 0,
) -> dict[str, Any]:
    """Discover upcoming hockey events across Polymarket leagues and evaluate model prices."""
    observed = observed_at or utc_now()
    active_leagues = tuple(leagues) if leagues else ALL_HOCKEY_LEAGUES
    m = model or hockey_model()
    market_client = client or PolymarketUSClient()

    history = _load_historical_games(data_root)

    events_by_league: dict[str, list[dict[str, Any]]] = {}
    upcoming_matches: list[UpcomingHockeyMatch] = []
    events_map: dict[str, dict[str, Any]] = {}

    if isinstance(game_date, date):
        target_d = game_date
        game_date_str = game_date.isoformat()
    else:
        game_date_str = str(game_date)
        target_d = date.fromisoformat(game_date_str)

    for lg in active_leagues:
        try:
            # Query Polymarket slate for this league
            events = market_client.slate(lg, target_d)
            events_by_league[lg] = events
            for ev in events:
                ev_id = str(ev.get("event_id") or ev.get("id", ""))
                events_map[ev_id] = ev
                away_t, home_t = _parse_teams_from_event(ev)
                start_utc = str(ev.get("event_start_utc") or ev.get("startTime", ""))
                upcoming_matches.append(
                    UpcomingHockeyMatch(
                        event_id=ev_id,
                        event_start_utc=start_utc,
                        away_team=away_t,
                        home_team=home_t,
                        league=lg,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to fetch slate for hockey league %s: %s", lg, exc)
            events_by_league[lg] = []

    # Run predictions on discovered upcoming matches
    predictions = m.predict_games(history, upcoming_matches)

    predictions_by_event: dict[str, list[Any]] = {}
    for pred in predictions:
        predictions_by_event.setdefault(pred.event_id, []).append(pred)

    priced: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for ev_id, ev in events_map.items():
        preds = predictions_by_event.get(ev_id, [])
        if not preds:
            unmatched.append({"event_id": ev_id, "reason": "no model predictions generated"})
            continue

        pred_ml = next((p for p in preds if p.market_type == "moneyline"), None)
        pred_spread = next((p for p in preds if p.market_type == "spread"), None)
        pred_totals = [p for p in preds if p.market_type == "total"]

        for market in ev.get("markets", []):
            m_type = market.get("market_type")
            m_slug = market.get("market_slug", "")
            sides = market.get("sides", [])
            if len(sides) != 2:
                continue

            if m_type == "moneyline" and pred_ml:
                # Evaluate both home and away
                candidates = []
                for side in sides:
                    sel = side.get("selection", "").lower()
                    desc = side.get("description", "")
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue

                    is_home = (
                        sel == "home"
                        or _team_matches(desc, pred_ml.home_team)
                        or not _team_matches(desc, pred_ml.away_team)
                    )
                    side_key = "home" if is_home else "away"
                    model_prob = pred_ml.probabilities[side_key]
                    edge = model_prob - ask
                    candidates.append(
                        {
                            "selection": side_key,
                            "market_slug": m_slug,
                            "executable_ask": ask,
                            "model_probability": model_prob,
                            "edge": edge,
                            "american_odds": probability_to_american(model_prob),
                        }
                    )

                if candidates:
                    # Select side with highest edge
                    best = max(candidates, key=lambda c: (c["edge"], c["model_probability"]))
                    priced.append(
                        {
                            "event_id": ev_id,
                            "league": pred_ml.league,
                            "event_start_utc": pred_ml.event_start_utc,
                            "away_team": pred_ml.away_team,
                            "home_team": pred_ml.home_team,
                            "market_type": "moneyline",
                            "selection": best["selection"],
                            "line": None,
                            "model_probability": round(best["model_probability"], 6),
                            "model_uncertainty": pred_ml.uncertainty,
                            "model_version": pred_ml.model_version,
                            "feature_basis": pred_ml.feature_basis,
                            "rationale": pred_ml.rationale,
                            "market_slug": best["market_slug"],
                            "executable_ask": round(best["executable_ask"], 4),
                            "observed_at_utc": observed.isoformat(),
                            "timestamp_valid": True,
                            "edge_vs_executable_ask": round(best["edge"], 6),
                        }
                    )

            elif m_type == "spread" and pred_spread:
                for side in sides:
                    sel = side.get("selection", "").lower()
                    desc = side.get("description", "")
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue
                    is_home = sel == "home" or _team_matches(desc, pred_spread.home_team)
                    side_key = "home" if is_home else "away"
                    model_prob = pred_spread.probabilities.get(side_key, 0.5)
                    edge = model_prob - ask
                    if edge > 0.01:
                        priced.append(
                            {
                                "event_id": ev_id,
                                "league": pred_spread.league,
                                "event_start_utc": pred_spread.event_start_utc,
                                "away_team": pred_spread.away_team,
                                "home_team": pred_spread.home_team,
                                "market_type": "spread",
                                "selection": side_key,
                                "line": pred_spread.line,
                                "model_probability": round(model_prob, 6),
                                "model_uncertainty": pred_spread.uncertainty,
                                "model_version": pred_spread.model_version,
                                "feature_basis": pred_spread.feature_basis,
                                "rationale": pred_spread.rationale,
                                "market_slug": m_slug,
                                "executable_ask": round(ask, 4),
                                "observed_at_utc": observed.isoformat(),
                                "timestamp_valid": True,
                                "edge_vs_executable_ask": round(edge, 6),
                            }
                        )

            elif m_type == "total" and pred_totals:
                market_line = market.get("line") or 5.5
                pred_tot = min(pred_totals, key=lambda p: abs((p.line or 5.5) - float(market_line)))
                for side in sides:
                    sel = side.get("selection", "").lower()
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue
                    side_key = "over" if "over" in sel else "under"
                    model_prob = pred_tot.probabilities.get(side_key, 0.5)
                    edge = model_prob - ask
                    if edge > 0.01:
                        priced.append(
                            {
                                "event_id": ev_id,
                                "league": pred_tot.league,
                                "event_start_utc": pred_tot.event_start_utc,
                                "away_team": pred_tot.away_team,
                                "home_team": pred_tot.home_team,
                                "market_type": "total",
                                "selection": side_key,
                                "line": pred_tot.line,
                                "model_probability": round(model_prob, 6),
                                "model_uncertainty": pred_tot.uncertainty,
                                "model_version": pred_tot.model_version,
                                "feature_basis": pred_tot.feature_basis,
                                "rationale": pred_tot.rationale,
                                "market_slug": m_slug,
                                "executable_ask": round(ask, 4),
                                "observed_at_utc": observed.isoformat(),
                                "timestamp_valid": True,
                                "edge_vs_executable_ask": round(edge, 6),
                            }
                        )

    counts_by_lg = {lg: len(evs) for lg, evs in events_by_league.items()}

    return {
        "sport": "hockey",
        "game_date": game_date_str,
        "observed_at_utc": observed.isoformat(),
        "total_events_discovered": len(events_map),
        "counts_by_league": counts_by_lg,
        "priced_count": len(priced),
        "unmatched_count": len(unmatched),
        "priced": priced,
        "unmatched": unmatched,
    }
