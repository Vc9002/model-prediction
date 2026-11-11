"""Isolated NCAAB spread paper trial; never routes to live execution."""

from __future__ import annotations

import json
import math
from typing import Any

from model_prediction.dashboard.picks import read_flat_picks, read_picks
from model_prediction.runtime_paths import RuntimePaths

from .auto_buyer_ledger import record_auto_buy_execution, settle_auto_buyer_ledger
from .auto_executor import AutoExecutionConfig, AutoPolymarketBuyer


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

    trial_root = runtime / "paper_trials" / "ncaab_spread_v6"
    paper_root = trial_root / "paper_auto_buyer"
    paper_root.mkdir(parents=True, exist_ok=True)
    settle_auto_buyer_ledger(data_root=paper_root)
    seen: set[str] = set()
    picks: list[dict[str, Any]] = []
    for row in read_picks() + read_flat_picks():
        if not eligible_trial_pick(row, min_edge=min_edge, max_edge=max_edge):
            continue
        pick_id = str(row.get("pick_id") or "")
        if not pick_id or pick_id in seen:
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
        min_edge=min_edge,
        max_edge=max_edge,
        capture_missing_slates=False,
        refresh_stale_quotes=False,
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
