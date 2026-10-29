"""Research-only joint NFL forecast from verified v6 decision packets."""

from __future__ import annotations

import math
from typing import Any
from zoneinfo import ZoneInfo

from ..domain import parse_utc
from ..production_registry import compute_artifact_hash
from .nfl_joint_market_v2 import forecast_with_market_anchor
from .nfl_market_anchor_v1 import SettledQuote, asof_market_weight

MODEL_VERSION = "nfl-joint-market-v3"
EASTERN = ZoneInfo("America/New_York")


def _probability(value: Any, name: str) -> float:
    probability = float(value)
    if not math.isfinite(probability) or not 0 < probability < 1:
        raise ValueError(f"invalid_{name}")
    return probability


def _selected_spread_ask(row: dict[str, Any]) -> float:
    quote = row["quote"]
    line = float(row["line"])
    quoted_line = float(quote["line"])
    if line == quoted_line and line != -quoted_line:
        side = "long"
    elif line == -quoted_line and line != quoted_line:
        side = "short"
    else:
        raise ValueError("ambiguous_spread_quote_side")
    return _probability(quote[f"{side}_ask"], "spread_ask")


def forecast_packet(
    packet: dict[str, Any], artifact: dict[str, Any], history: list[SettledQuote]
) -> dict[str, Any]:
    """Price the packet's original exact lines; issue no bet or unit decision."""
    if artifact.get("model_version") != MODEL_VERSION or artifact.get("artifact_hash") != compute_artifact_hash(
        artifact
    ):
        raise ValueError("v3_artifact_hash_mismatch")
    if packet.get("schema") != "nfl-joint-decision-packet-v1" or packet.get("replay_ready") is not True:
        raise ValueError("incomplete_joint_packet")
    captured = parse_utc(packet["captured_at_utc"])
    start = parse_utc(packet["event_start_utc"])
    if not captured < start:
        raise ValueError("packet_not_pregame")
    markets = packet["markets"]
    for market in ("moneyline", "spread", "total"):
        row = markets[market]
        if row["status"] != "verified" or not isinstance(row.get("quote"), dict):
            raise ValueError("incomplete_joint_packet")
        snapshot = row["model_snapshot"]
        if snapshot["artifact_hash"] != artifact["inputs"][f"{market}_artifact_hash"]:
            raise ValueError("parent_artifact_hash_mismatch")
        if market in ("spread", "total") and snapshot["features"].get("input_qualified") is not True:
            raise ValueError("unqualified_v6_input")
        if "observed_at_utc" in snapshot and parse_utc(snapshot["observed_at_utc"]) > captured:
            raise ValueError("future_model_snapshot")
    ml = markets["moneyline"]
    ml_quote = ml["quote"]
    selected_model = _probability(ml["model_probability"], "moneyline_model_probability")
    selected_market = _probability(ml_quote["no_vig_selected"], "moneyline_no_vig")
    long_ask = _probability(ml_quote["long_ask"], "moneyline_long_ask")
    short_ask = _probability(ml_quote["short_ask"], "moneyline_short_ask")
    selected_ask = _probability(ml_quote["selected_ask"], "moneyline_selected_ask")
    if min(abs(selected_ask - long_ask), abs(selected_ask - short_ask)) > 1e-6:
        raise ValueError("moneyline_quote_side_mismatch")
    if abs(selected_market - selected_ask / (long_ask + short_ask)) > 1e-5:
        raise ValueError("moneyline_no_vig_mismatch")
    if ml["selection"] not in ("home", "away"):
        raise ValueError("invalid_moneyline_selection")
    home_model = selected_model if ml["selection"] == "home" else 1 - selected_model
    home_market = selected_market if ml["selection"] == "home" else 1 - selected_market
    game_date = start.astimezone(EASTERN).date()
    weight = asof_market_weight(history, game_date, captured)
    spread = markets["spread"]
    total = markets["total"]
    margin = spread["model_snapshot"]["inference_inputs"]
    points = total["model_snapshot"]["inference_inputs"]
    forecast = forecast_with_market_anchor(
        home_model,
        home_market,
        weight,
        float(margin["mean"]),
        float(margin["sd"]),
        float(points["mean"]),
        float(points["sd"]),
        float(total["model_snapshot"]["artifact"]["league_avg_total"]),
    )
    if spread["selection"] not in ("home", "away") or total["selection"] not in ("over", "under"):
        raise ValueError("invalid_derivative_selection")
    spread_line = float(spread["line"])
    total_line = float(total["line"])
    if abs(total_line - float(total["quote"]["line"])) > 1e-6:
        raise ValueError("total_quote_line_mismatch")
    probabilities = {
        "moneyline": forecast.home_win_probability()
        if ml["selection"] == "home"
        else 1 - forecast.home_win_probability(),
        "spread": forecast.home_cover_probability(spread_line)
        if spread["selection"] == "home"
        else forecast.away_cover_probability(spread_line),
        "total": forecast.over_probability(total_line)
        if total["selection"] == "over"
        else forecast.under_probability(total_line),
    }
    asks = {
        "moneyline": selected_ask,
        "spread": _selected_spread_ask(spread),
        "total": _probability(
            total["quote"]["long_ask" if total["selection"] == "over" else "short_ask"],
            "total_ask",
        ),
    }
    contracts = {
        market: {
            "selection": markets[market]["selection"],
            "line": markets[market].get("line"),
            "ask": asks[market],
            "quote_record_id": markets[market]["quote"]["lineage"]["market_snapshot_record_id"],
        }
        for market in ("moneyline", "spread", "total")
    }
    return {
        "model_version": MODEL_VERSION,
        "artifact_hash": artifact["artifact_hash"],
        "event_id": packet["event_id"],
        "event_start_utc": packet["event_start_utc"],
        "decision_at_utc": packet["captured_at_utc"],
        "market_weight": weight,
        "home_win_probability": forecast.home_win_probability(),
        "selected_probabilities": probabilities,
        "selected_asks": asks,
        "contracts": contracts,
        "raw_edges": {market: probabilities[market] - asks[market] for market in probabilities},
        "decision": "RESEARCH_FORECAST_ONLY",
    }
