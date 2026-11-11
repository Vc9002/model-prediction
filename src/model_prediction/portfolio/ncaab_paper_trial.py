"""Isolated NCAAB spread paper trial; never routes to live execution."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from model_prediction.dashboard.picks import read_flat_picks, read_picks
from model_prediction.runtime_paths import RuntimePaths

from .auto_buyer_ledger import record_auto_buy_execution, settle_auto_buyer_ledger
from .auto_executor import PAPER_AUTO_BUYER_DATA_ROOT, AutoExecutionConfig, AutoPolymarketBuyer

# Phantom-edge guard only. Cohort selection happens in eligible_trial_pick() on the
# saved decision-time edge; this ceiling exists so a corrupt quote cannot mint an
# implausible paper fill, not to re-define the cohort.
_PAPER_EXECUTION_SANITY_MAX_EDGE = 0.25


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
    temporary = Path(str(path) + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
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


def _recorded_paper_pick_ids(paper_root: Any) -> set[str]:
    """Pick IDs already present in the dashboard's shared paper ledger."""
    from .auto_buyer_ledger import read_auto_buyer_ledger

    path = paper_root / "auto_buyer_ledger.jsonl"
    if not path.exists():
        return set()
    try:
        rows = read_auto_buyer_ledger(path)
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    return {str(r.get("pick_id")) for r in rows if r.get("pick_id")}


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
    # The buyer's own dedup index only sees its scratch root, so an already
    # recorded paper fill would otherwise be re-appended on every 30-minute
    # cycle. Seed the exclusion set from the shared paper ledger itself.
    already_recorded = _recorded_paper_pick_ids(paper_root)
    seen: set[str] = set()
    picks: list[dict[str, Any]] = []
    for row in read_picks() + read_flat_picks():
        if row.get("status") != "open":
            continue
        if not eligible_trial_pick(row, min_edge=min_edge, max_edge=max_edge):
            continue
        pick_id = str(row.get("pick_id") or "")
        if not pick_id or pick_id in seen or pick_id in already_recorded:
            continue
        seen.add(pick_id)
        picks.append(row)

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
    return {"status": "paper", "candidates": len(picks), "paper_orders": len(result.dry_run_orders)}
