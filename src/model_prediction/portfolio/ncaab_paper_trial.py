"""Isolated NCAAB spread paper trial; never routes to live execution."""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from model_prediction.dashboard.backtests import _lines_match, _pick_quote, _row_line, _team_matches
from model_prediction.dashboard.picks import read_flat_picks, read_picks
from model_prediction.domain import parse_utc
from model_prediction.runtime_paths import RuntimePaths

from .auto_buyer_ledger import read_auto_buyer_ledger, record_auto_buy_execution, settle_auto_buyer_ledger
from .auto_executor import (
    PAPER_AUTO_BUYER_DATA_ROOT,
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    load_auto_buyer_state,
    write_versioned_state_file,
)

# Phantom-edge guard only. Cohort selection happens in eligible_trial_pick() on the
# saved decision-time edge; this ceiling exists so a corrupt quote cannot mint an
# implausible paper fill, not to re-define the cohort.
_PAPER_EXECUTION_SANITY_MAX_EDGE = 0.25

_logger = logging.getLogger(__name__)


def paper_trial_status() -> dict[str, Any]:
    path = RuntimePaths.resolve().runtime_root / "auto_buyer_ncaab_spread_paper_trial.json"
    if not path.is_file():
        return {"enabled": False, "model_id": "ncaab-spread-v6", "market_type": "spread"}
    settings = json.loads(path.read_text(encoding="utf-8"))
    if settings.get("model_id") != "ncaab-spread-v6" or settings.get("market_type") != "spread":
        raise ValueError("NCAAB paper trial config must name only ncaab-spread-v6 spread")
    return settings


def set_paper_trial_enabled(enabled: bool) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    path = RuntimePaths.resolve().runtime_root / "auto_buyer_ncaab_spread_paper_trial.json"
    settings = paper_trial_status()
    if "min_saved_edge" not in settings or "max_saved_edge_exclusive" not in settings:
        raise ValueError("NCAAB paper trial config is missing its saved-edge band")
    settings["enabled"] = enabled
    # Raises StateVersionConflictError (a RuntimeError) if another writer saved
    # this file since paper_trial_status() above read it -- same conflict
    # semantics as the live auto-buyer state file, via the shared helper.
    write_versioned_state_file(path, settings, temp_path=Path(str(path) + f".{os.getpid()}.tmp"))
    return settings


def eligible_trial_pick(row: dict[str, Any], *, min_edge: float, max_edge: float) -> bool:
    """Use the saved decision edge, not a later quote, to select the cohort."""
    if str(row.get("model_id") or row.get("model_version") or "") != "ncaab-spread-v6":
        return False
    if str(row.get("sport") or row.get("league") or "").lower() != "ncaab":
        return False
    if str(row.get("market_type") or "").lower() != "spread":
        return False
    try:
        edge = float(row["edge"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(edge) and min_edge <= edge < max_edge


def _cohort_market(row: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict, dict] | None:
    """The one exchange spread contract carrying this pick's teams, side and line."""
    selection = str(row.get("selection") or "").lower()
    line = _row_line(row)
    for event in events:
        for market in event.get("markets", []):
            if market.get("market_type") != "spread":
                continue
            sides = {str(side.get("selection") or "").lower(): side for side in market.get("sides", [])}
            if not all(
                key in sides
                and _team_matches(str(row.get(f"{key}_team") or ""), str(sides[key].get("team") or ""))
                for key in ("away", "home")
            ):
                continue
            if selection in sides and _lines_match(line, sides[selection].get("line")):
                return event, market
    return None


def capture_unmapped_cohort_markets(
    picks: list[dict[str, Any]],
    runtime_root: Path,
    *,
    client: Any | None = None,
    capture_fn: Callable[..., dict[str, Any]] | None = None,
    quote_fn: Callable[[dict[str, Any]], dict[str, Any] | None] = _pick_quote,
) -> int:
    """Snapshot the exact contract for each cohort pick the buyer cannot map yet.

    NCAAB forecasts price off an indicative slate, so a pick only becomes
    mappable once something writes its contract to the executable snapshot file.
    Nothing did for picks that entered the band on a later refresh at a moved
    line: on 2026-11-11 four of eight cohort picks were rejected as unmapped on
    every cycle while their markets were open on the exchange. This fetches
    market data only -- one contract per pick, never an order.
    """
    from model_prediction.data_sources.polymarket_us import PolymarketUSClient, capture_slate_snapshots

    eastern = ZoneInfo("America/New_York")
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in picks:
        if quote_fn(row) is not None:
            continue
        try:
            game_date = (
                parse_utc(str(row.get("event_start_utc") or "")).astimezone(eastern).date().isoformat()
            )
        except (TypeError, ValueError):
            continue
        by_date.setdefault(game_date, []).append(row)
    captured = 0
    market_client = client
    for game_date, rows in sorted(by_date.items()):
        try:
            market_client = market_client or PolymarketUSClient()
            slate = market_client.sport_slate("ncaab", date.fromisoformat(game_date), "America/New_York")
            wanted: dict[str, list[dict[str, Any]]] = {}
            for league, events in slate.events.items():
                for row in rows:
                    match = _cohort_market(row, events)
                    if match is not None:
                        event, market = match
                        wanted.setdefault(league, []).append({**event, "markets": [market]})
            if wanted:
                result = (capture_fn or capture_slate_snapshots)(
                    market_client, wanted, runtime_root, game_date
                )
                captured += int(result.get("captured") or 0)
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError):
            # Quotes are a best-effort input to a paper cohort; an exchange hiccup
            # must not fail the cycle that also runs the live buyer.
            _logger.warning("NCAAB paper trial market capture failed for %s", game_date, exc_info=True)
    return captured


def _paper_position_keys(row: dict[str, Any]) -> set[tuple[str, ...]]:
    """Game-market identity for a ledger row or a candidate pick row.

    This is the ONLY identity function for both sides of the dedup, so the two
    sides cannot silently drift apart. Normalized exactly like the live
    buyer's own bought-index (auto_executor.py _build_bought_index /
    _is_already_bought): `str(x or "")` first so a None event_id (every
    NBA/NFL row in the shared paper ledger carries one) never becomes the
    literal string "None", then strip/lower team and market names and take
    the UTC date (first 10 chars) of the start timestamp.

    Selection and line are deliberately NOT part of the key:
    - the live buyer locks the whole game-market on either line -- holding or
      having sold one side also blocks the other (auto_executor.py
      ~L1544-1547);
    - pick rows store line as a string ("8.5") while ledger rows store a
      float (8.5);
    - for this spread-only cohort the live buyer's selection-level keys are
      subsets of these game-market keys anyway.
    """
    keys: set[tuple[str, ...]] = set()
    pick_id = str(row.get("pick_id") or "")
    if pick_id:
        keys.add(("pick", pick_id))
    event_id = str(row.get("event_id") or "").strip()
    market_type = str(row.get("market_type") or "").strip().lower()
    if event_id and market_type:
        keys.add(("event_market", event_id, market_type))
    away = str(row.get("away_team") or "").strip().lower()
    home = str(row.get("home_team") or "").strip().lower()
    start_date = str(row.get("event_start_utc") or "")[:10]
    if away and home and start_date and market_type:
        keys.add(("matchup_market", away, home, start_date, market_type))
    return keys


def _recorded_paper_dedup_keys(paper_root: Any) -> set[tuple[str, ...]]:
    """Game-market identity of every fill already recorded in the shared paper ledger."""
    path = paper_root / "auto_buyer_ledger.jsonl"
    if not path.exists():
        return set()
    try:
        rows = read_auto_buyer_ledger(path)
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    keys: set[tuple[str, ...]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        keys |= _paper_position_keys(row)
    return keys


def run_ncaab_spread_paper_trial() -> dict[str, Any]:
    """Evaluate one configured paper cohort alongside the normal live cycle."""
    runtime = RuntimePaths.resolve().runtime_root
    settings_file = runtime / "auto_buyer_ncaab_spread_paper_trial.json"
    if not settings_file.is_file():
        return {"status": "disabled", "reason": "config_missing"}
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    if not settings.get("enabled", False):
        return {"status": "disabled"}
    if settings.get("model_id") != "ncaab-spread-v6" or settings.get("market_type") != "spread":
        raise ValueError("NCAAB paper trial config must name only ncaab-spread-v6 spread")
    min_edge = float(settings["min_saved_edge"])
    max_edge = float(settings["max_saved_edge_exclusive"])
    if not (math.isfinite(min_edge) and math.isfinite(max_edge) and 0 <= min_edge < max_edge <= 1):
        raise ValueError("Invalid NCAAB paper trial saved-edge band")

    # The buyer keeps its own scratch root for quote/snapshot caching, but the
    # SETTLED PAPER LEDGER is written to the dashboard's canonical paper root so
    # these fills are visible on Auto-Buyer Paper Ledger. Writing to an isolated
    # paper_trials/<id>/paper_auto_buyer/ root left six real paper fills
    # invisible to the dashboard (2026-11-11).
    trial_root = runtime / "paper_trials" / "ncaab_spread_v6"
    paper_root = PAPER_AUTO_BUYER_DATA_ROOT
    paper_root.mkdir(parents=True, exist_ok=True)
    settle_auto_buyer_ledger(data_root=paper_root)
    # The buyer's own dedup index only sees its scratch root, AND the forecast
    # mints a fresh pick_id on every re-forecast, so pick_id alone cannot be
    # the identity -- it re-bought NC Central @ VMI away +8.5 twice (event_id
    # 203460, 17:48Z then 21:11Z) and home -8.5 on event_id 203509 the same
    # way on 2026-11-11. Seed the exclusion set from the shared paper
    # ledger's own game-market identity instead.
    already_recorded = _recorded_paper_dedup_keys(paper_root)
    seen: set[str] = set()
    picks: list[dict[str, Any]] = []
    for row in read_picks() + read_flat_picks():
        if row.get("status") != "open":
            continue
        if not eligible_trial_pick(row, min_edge=min_edge, max_edge=max_edge):
            continue
        pick_id = str(row.get("pick_id") or "")
        if not pick_id or pick_id in seen:
            continue
        if _paper_position_keys(row) & already_recorded:
            continue
        seen.add(pick_id)
        picks.append(row)

    captured_markets = capture_unmapped_cohort_markets(picks, runtime)
    config = AutoExecutionConfig(
        mode="paper",
        execute_live=False,
        paper_ncaab_spread_v6_trial=True,
        whitelisted_models=("ncaab-spread-v6",),
        blacklisted_models=(),
        disabled_sport_markets=(("ncaab", "moneyline"), ("ncaab", "total")),
        # Cohort membership is decided ABOVE by eligible_trial_pick() on the
        # SAVED decision-time edge. The buyer's own edge gates run against a
        # freshly fetched ask, so re-applying the saved-edge band here would
        # silently drop cohort members whenever the price drifts between the
        # decision and this run -- the 2026-11-11 NC A&T pick moved from 7.9%
        # to 8.9% and was excluded even though the live bucket had already
        # bought it. Keep only a sanity ceiling here (a phantom-edge guard).
        min_edge=0.0,
        max_edge=_PAPER_EXECUTION_SANITY_MAX_EDGE,
        # Same window as the live buyer. Without it this cohort fell back to the
        # 24h default and could buy hours before tip-off, off a pick that a later
        # forecast run would supersede.
        max_event_lookahead_hours=float(load_auto_buyer_state()["max_event_lookahead_hours"]),
        capture_missing_slates=False,
        refresh_stale_quotes=True,
        require_model_timestamp=True,
    )
    result = AutoPolymarketBuyer(config=config, data_root=trial_root).evaluate_and_execute(picks)
    for preview in result.dry_run_orders:
        pick_id = str(preview.get("pick_id") or "")
        if not pick_id:
            raise ValueError("Paper trial preview missing pick_id")
        order_id = f"PAPER-NCAAB-V6-{pick_id}"
        payload = {**preview, "order_id": order_id, "order_ids": [order_id], "fill_known": True}
        record_auto_buy_execution(
            payload,
            order_id=order_id,
            order_state="ORDER_STATE_FILLED",
            pick_row=payload,
            jsonl_path=paper_root / "auto_buyer_ledger.jsonl",
            xlsx_path=paper_root / "auto_buyer_picks.xlsx",
        )
    return {
        "status": "paper",
        "candidates": len(picks),
        "captured_markets": captured_markets,
        "paper_orders": len(result.dry_run_orders),
    }
