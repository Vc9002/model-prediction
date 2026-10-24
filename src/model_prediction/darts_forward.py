"""Darts forward prediction and Polymarket market evaluation engine.

Evaluates exact Markov leg-progression darts models against live or captured
Polymarket BBO quotes across supported darts competitions:
  * PDC (Professional Darts Corporation - European Tour, ProTour, World Championship)
  * MODUS (MODUS Super Series)

Guarantees point-in-time safety: no future results or closing odds are read.
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

import httpx

from .darts_history import load_darts_history
from .data_sources.polymarket_us import (
    PolymarketUSClient,
    probability_to_american,
)
from .domain import utc_now
from .models.darts import DartsModel, UpcomingDartsMatch, darts_model

logger = logging.getLogger(__name__)

ALL_DARTS_LEAGUES: tuple[str, ...] = (
    "DARTS_PDC",
    "DARTS_MODUS",
)


def _clean_tokens(text: str) -> list[str]:
    clean = str(text or "").replace(chr(0xFFFD), "e")
    clean = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[a-z0-9]+", clean.casefold())
    return tokens


def _player_matches(name_a: str, name_b: str) -> bool:
    """Check if two player names refer to the same competitor."""
    if not name_a or not name_b:
        return False
    a_tokens = _clean_tokens(name_a)
    b_tokens = _clean_tokens(name_b)
    if not a_tokens or not b_tokens:
        return False
    if " ".join(a_tokens) == " ".join(b_tokens):
        return True
    # Match surname if multi-word
    return (
        len(a_tokens) >= 2
        and len(b_tokens) >= 2
        and a_tokens[-1] == b_tokens[-1]
        and a_tokens[0][0] == b_tokens[0][0]
    )


def _resolve_player(name: str, known: Sequence[str]) -> str:
    """Map a market name onto the unique rated player it refers to, else keep it."""
    exact = [candidate for candidate in known if _clean_tokens(candidate) == _clean_tokens(name)]
    if exact:
        return exact[0]
    fuzzy = [candidate for candidate in known if _player_matches(name, candidate)]
    return fuzzy[0] if len(fuzzy) == 1 else name


def _parse_players_from_title(title: str) -> tuple[str, str] | None:
    """Parse player A and player B from typical darts match titles: 'Player A vs. Player B'."""
    clean_title = str(title or "").strip()
    match = re.split(r"\s+vs\.?\s+|\s+v\s+", clean_title, flags=re.IGNORECASE)
    if len(match) == 2:
        p1 = match[0].strip()
        p2 = match[1].strip()
        if p1 and p2:
            return p1, p2
    return None


def _load_cached_darts_snapshots(
    data_root: str | Path,
    game_date: str,
) -> list[dict[str, Any]]:
    """Load cached Polymarket snapshots from data/odds/darts/{game_date}."""
    path = Path(data_root) / "odds" / "darts" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                row = json.loads(line_str)
                if row.get("timestamp_valid", True):
                    records.append(row)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return records


def build_darts_slate(
    *,
    data_root: str | Path | None = None,
    game_date: str | date | None = None,
    client: PolymarketUSClient | None = None,
    model: DartsModel | None = None,
    leagues: Sequence[str] | None = None,
    observed_at: datetime | None = None,
    min_team_matches: int = 0,
    longshot_ask_floor: float | None = None,
    longshot_min_sample: int = 5,
    max_claimed_edge: float | None = None,
) -> dict[str, Any]:
    """Build prospective darts slate and evaluate actionable edges against executable asks."""
    active_model = model or darts_model()
    target_leagues = tuple(leagues or ALL_DARTS_LEAGUES)
    observed = observed_at or utc_now()

    # Determine game_date
    if isinstance(game_date, str):
        g_date = date.fromisoformat(game_date)
    elif isinstance(game_date, date):
        g_date = game_date
    else:
        g_date = observed.date()

    events: list[dict[str, Any]] = []

    # 1. Check local odds cache first
    if data_root is not None:
        cached = _load_cached_darts_snapshots(data_root, g_date.isoformat())
        if cached:
            # Group snapshots by event_id or title
            by_event: dict[str, list[dict[str, Any]]] = {}
            for s in cached:
                ev_id = str(s.get("event_id") or s.get("event_title") or "")
                by_event.setdefault(ev_id, []).append(s)
            for ev_id, snaps in by_event.items():
                first = snaps[0]
                events.append(
                    {
                        "id": ev_id,
                        "title": first.get("event_title", ""),
                        "startTime": first.get("event_start_utc", observed.isoformat()),
                        "league": first.get("league", "DARTS_PDC"),
                        "markets": [
                            {
                                "market_type": s.get("market_type", "moneyline"),
                                "market_slug": s.get("market_slug", ""),
                                "line": s.get("line"),
                                "sides": [
                                    {
                                        "description": s.get("long", {}).get("description", ""),
                                        "selection": "home",
                                        "price_probability": s.get("long", {}).get("ask"),
                                    },
                                    {
                                        "description": s.get("short", {}).get("description", ""),
                                        "selection": "away",
                                        "price_probability": s.get("short", {}).get("ask"),
                                    },
                                ],
                            }
                            for s in snaps
                        ],
                    }
                )

    # 2. If no cached events, query Polymarket US live client
    if not events:
        pm_client = client or PolymarketUSClient()
        for lg in target_leagues:
            try:
                lg_slate = pm_client.slate(league=lg, game_date=g_date)
                for ev in lg_slate:
                    ev["league"] = lg
                events.extend(lg_slate)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                logger.warning("Could not fetch Polymarket darts slate for %s: %s", lg, exc)

    if not events:
        return {
            "date": g_date.isoformat(),
            "sport": "darts",
            "leagues": target_leagues,
            "events_count": 0,
            "priced_contracts": 0,
            "priced": [],
        }

    history = load_darts_history(data_root) if data_root is not None else []
    rated_names = sorted({name for record in history for name in (record.player_a, record.player_b)})

    # Extract matchups for prediction
    upcoming: list[UpcomingDartsMatch] = []
    event_map: dict[str, dict[str, Any]] = {}

    for ev in events:
        ev_id = str(ev.get("event_id") or ev.get("id") or "")
        title = str(ev.get("title") or "")
        players = _parse_players_from_title(title)
        if not players:
            continue
        p1, p2 = (_resolve_player(name, rated_names) for name in players)
        league_name = ev.get("league", "DARTS_PDC")

        # Set standard format: PDC European Tour matches are best-of-11 legs; MODUS is best-of-7 legs
        best_of = 7 if "MODUS" in league_name.upper() else 11

        up_match = UpcomingDartsMatch(
            event_id=ev_id,
            event_start_utc=str(ev.get("event_start_utc") or ev.get("startTime") or observed.isoformat()),
            player_a=p1,
            player_b=p2,
            best_of_legs=best_of,
            league=league_name,
        )
        upcoming.append(up_match)
        event_map[ev_id] = ev

    # Generate model predictions
    predictions = active_model.predict_games(history, upcoming)

    # Group predictions by event_id and market_type
    preds_by_event: dict[str, dict[str, list[Any]]] = {}
    for p in predictions:
        preds_by_event.setdefault(p.event_id, {}).setdefault(p.market_type, []).append(p)

    priced: list[dict[str, Any]] = []
    skipped_thin: list[str] = []

    for ev_id, ev in event_map.items():
        ev_preds = preds_by_event.get(ev_id, {})
        pred_ml_list = ev_preds.get("moneyline", [])
        pred_ml = pred_ml_list[0] if pred_ml_list else None
        pred_spreads = ev_preds.get("spread", [])
        pred_totals = ev_preds.get("total", [])

        if not pred_ml:
            continue
        if (
            min_team_matches
            and min(
                active_model.match_counts.get(pred_ml.home_team, 0),
                active_model.match_counts.get(pred_ml.away_team, 0),
            )
            < min_team_matches
        ):
            skipped_thin.append(ev_id)
            continue

        for market in ev.get("markets", []):
            m_type = market.get("market_type")
            m_slug = market.get("market_slug", "")
            sides = market.get("sides", [])
            if len(sides) != 2:
                continue

            if m_type == "moneyline" and pred_ml:
                candidates = []
                for side in sides:
                    desc = side.get("description", "")
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue
                    home_match = _player_matches(desc, pred_ml.home_team)
                    away_match = _player_matches(desc, pred_ml.away_team)
                    if home_match == away_match:
                        continue
                    is_p1 = home_match
                    side_key = "home" if is_p1 else "away"
                    model_prob = pred_ml.probabilities[side_key]
                    edge = model_prob - ask
                    sample_min = min(
                        active_model.match_counts.get(pred_ml.home_team, 0),
                        active_model.match_counts.get(pred_ml.away_team, 0),
                    )
                    # Both prunes are opt-in via model config; unvalidated thresholds must not
                    # silently change live output (max_claimed_edge drops the largest edges).
                    if (
                        longshot_ask_floor is not None
                        and ask < longshot_ask_floor
                        and sample_min < longshot_min_sample
                    ):
                        continue
                    if max_claimed_edge is not None and edge > max_claimed_edge:
                        continue
                    candidates.append(
                        {
                            "selection": side_key,
                            "market_slug": m_slug,
                            "executable_ask": ask,
                            "model_probability": model_prob,
                            "edge": edge,
                            "american_odds": probability_to_american(model_prob),
                            "units": DartsModel.compute_dynamic_units(edge),
                        }
                    )
                if candidates:
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
                            "units": best.get("units", 1.0),
                        }
                    )

            elif m_type == "spread" and pred_spreads:
                market_line = market.get("line")
                if market_line is None:
                    continue
                pred_spread = next((p for p in pred_spreads if p.line == float(market_line)), None)
                if pred_spread is None:
                    continue
                for side in sides:
                    desc = side.get("description", "")
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue
                    home_match = _player_matches(desc, pred_spread.home_team)
                    away_match = _player_matches(desc, pred_spread.away_team)
                    if home_match == away_match:
                        continue
                    is_p1 = home_match
                    side_key = "home" if is_p1 else "away"
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
                                "units": DartsModel.compute_dynamic_units(edge),
                            }
                        )

            elif m_type == "total" and pred_totals:
                market_line = market.get("line")
                if market_line is None:
                    continue
                pred_tot = next((p for p in pred_totals if p.line == float(market_line)), None)
                if pred_tot is None:
                    continue
                for side in sides:
                    sel = side.get("selection", "").lower()
                    desc = side.get("description", "").lower()
                    ask = side.get("price_probability")
                    if ask is None or not (0 < ask < 1):
                        continue
                    is_over = "over" in sel or "over" in desc or ">" in desc
                    is_under = "under" in sel or "under" in desc or "<" in desc
                    if is_over == is_under:
                        continue
                    side_key = "over" if is_over else "under"
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
                                "units": DartsModel.compute_dynamic_units(edge),
                            }
                        )

    return {
        "date": g_date.isoformat(),
        "sport": "darts",
        "leagues": target_leagues,
        "events_count": len(events),
        "priced_contracts": len(priced),
        "priced": priced,
        "history_matches": len(history),
        "skipped_thin_history": skipped_thin,
    }
