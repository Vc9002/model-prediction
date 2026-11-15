"""NFL moneyline challenger: the spread engine's own win probability, logged beside the champion.

``nfl-spread-structural-v7`` already converts its projected margin into a home-win
probability, but only the spread market ever served it; moneyline serves the separate
``nfl-elo-trend-lr-v4`` (Elo plus a form term, no quarterback, injury or line input). On
594 out-of-sample games (2024-09-22..2026-10-08) the margin-derived probability scores
Brier 0.2071 against 0.2198, 67.3% against 66.0% winners.

This module logs that probability as its own Flat row per game, under its own model id,
so the two can be compared on results. It never writes to Main and is not a champion:
promotion stays a separate ``model_promotion promote`` decision. The gain comes from the
sportsbook line inside the v7 margin (market weight 0.8), so the model mostly agrees with
the price; it is more accurate, not a source of large edges.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config import unit_policy
from ..data_sources.polymarket_us import probability_to_american
from ..domain import EASTERN, League, MarketType, ModelOrigin, PickRequest
from ..eligibility import evaluate_eligibility
from ..entities import EntityResolutionError
from ..learned_forward import match_executable_quote
from ..ledger import DuplicatePickError

_logger = logging.getLogger(__name__)

NFL_MARGIN_MONEYLINE_MODEL = (
    "nfl-moneyline-margin-v1"  # challenger id; rows carrying it never gate other picks
)
NFL_MARGIN_MONEYLINE_PARENT = "nfl-spread-structural-v7"  # the only engine whose win probability was replayed


def without_moneyline_shadow_rows(rows: list[Any]) -> list[Any]:
    """Drop challenger rows before a cross-market coherence check.

    A shadow moneyline on Flat must never retire or block a champion spread or
    moneyline call on the same game.
    """
    kept = []
    for row in rows:
        version = row.get("model_version") if isinstance(row, dict) else getattr(row, "model_version", None)
        if str(version or "") != NFL_MARGIN_MONEYLINE_MODEL:
            kept.append(row)
    return kept


def moneyline_shadow_input(
    *,
    event_id: str,
    event_start_utc: str,
    home_team: str,
    away_team: str,
    forecast: Any,
    spread_home_line: float,
    input_qualified: bool,
    parent_model_version: str,
) -> dict[str, Any] | None:
    """Capture one game's margin-derived win probability from the spread slate, or None."""
    prob_home_win = getattr(forecast, "prob_home_win", None)
    if parent_model_version != NFL_MARGIN_MONEYLINE_PARENT or prob_home_win is None:
        return None
    return {
        "event_id": event_id,
        "event_start_utc": event_start_utc,
        "home_team": home_team,
        "away_team": away_team,
        "prob_home_win": float(prob_home_win),
        "projected_margin_home": float(forecast.projected_margin_home),
        "spread_home_line": float(spread_home_line),
        "input_qualified": bool(input_qualified),
    }


def log_nfl_margin_moneyline_shadow(
    *,
    forecast: dict[str, Any],
    data_root: Any,
    args_date: str,
    config: dict,
    registry: Any,
    bans: Any,
    flat_ledger: Any,
    observed_now: datetime,
) -> dict[str, Any]:
    """Append one Flat moneyline row per priced game for the margin challenger."""
    from . import forecast as _forecast

    artifact, artifact_error = _forecast._load_exact_artifact_contract(NFL_MARGIN_MONEYLINE_MODEL)
    if artifact is None:
        return {"status": "blocked", "reason": artifact_error, "logged": 0, "skipped": []}
    parent, parent_error = _forecast._load_exact_artifact_contract(NFL_MARGIN_MONEYLINE_PARENT)
    if parent is None or parent.get("artifact_hash") != artifact.get("parent_artifact_hash"):
        # The replayed evidence belongs to one exact parent artifact; a changed parent is a different model.
        return {
            "status": "blocked",
            "reason": parent_error or "NO_CALL_PARENT_ARTIFACT_HASH_MISMATCH",
            "logged": 0,
            "skipped": [],
        }
    artifact_hash = str(artifact["artifact_hash"])
    logged: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in forecast.get("moneyline_shadow_inputs") or []:
        event_id = str(entry["event_id"])
        prob_home = float(entry["prob_home_win"])
        selection = "home" if prob_home >= 0.5 else "away"
        probability = prob_home if selection == "home" else 1.0 - prob_home
        quote = match_executable_quote(
            data_root,
            "nfl",
            args_date,
            SimpleNamespace(  # type: ignore[arg-type]
                away_team=entry["away_team"],
                home_team=entry["home_team"],
                selection=selection,
                event_start_utc=entry["event_start_utc"],
                # Bounds the quote to the slate's own decision time: a snapshot
                # captured after the margin was computed must not price it.
                model_input_snapshot={"observed_at_utc": forecast.get("observed_at_utc")},
            ),
        )
        lineage = None
        if quote is not None and bool(quote.get("timestamp_valid", False)):
            lineage = _forecast._canonical_market_snapshot_lineage(
                quote,
                Path(
                    quote.get("_archive_path")
                    or Path(data_root) / "odds" / "nfl" / args_date / "polymarket_snapshots.jsonl"
                ),
            )
        if quote is None or lineage is None:
            skipped.append({"event_id": event_id, "reason": "NO_CALL_MONEYLINE_QUOTE_UNAVAILABLE"})
            continue
        ask = float(quote["executable_ask"])
        try:
            event_et = (
                datetime.fromisoformat(str(entry["event_start_utc"]))
                .astimezone(EASTERN)
                .strftime("%Y-%m-%dT%H:%M:%S%z")
            )
        except (ValueError, TypeError):
            event_et = str(entry["event_start_utc"])
        inputs_note = (
            "" if entry["input_qualified"] else " Efficiency or quarterback inputs were unqualified."
        )
        request = PickRequest(
            event_start_utc=event_et,
            event_id=event_id,
            league=League.NFL,
            away_team=entry["away_team"],
            home_team=entry["home_team"],
            market_type=MarketType.MONEYLINE,
            selection=selection,
            line=None,
            sportsbook="polymarket_us",
            american_odds=probability_to_american(ask),
            model_probability=round(probability, 6),
            model_uncertainty=None,
            model_version=NFL_MARGIN_MONEYLINE_MODEL,
            rationale=(
                f"Challenger moneyline from the {NFL_MARGIN_MONEYLINE_PARENT} margin: projected home margin "
                f"{entry['projected_margin_home']:+.1f} at home line {entry['spread_home_line']:+.1f}, "
                f"home win {prob_home:.4f}; executable ask {ask:.4f} ({quote['market_slug']}); "
                f"model edge vs ask {probability - ask:+.4f}.{inputs_note}"
            ),
            risks=(
                "Shadow challenger, Flat only. The margin is 80% sportsbook line, so agreement with the "
                "price is expected and large edges are not."
            ),
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            model_enabled=True,
            observed_at_utc=str(quote.get("observed_at_utc") or ""),
            model_artifact_hash=artifact_hash,
            calibration_method="margin_logistic",
            calibration_version=NFL_MARGIN_MONEYLINE_MODEL,
            calibration_artifact_hash=artifact_hash,
            feature_schema_version="1",
            entity_map_version=registry.version,
            code_revision=artifact_hash,
            decision_no_vig_probability=quote.get("no_vig_probability"),
            **lineage,
        )
        try:
            request.validate(now=observed_now)
            away = registry.resolve(request.league, request.away_team, request.event_start_utc)
            home = registry.resolve(request.league, request.home_team, request.event_start_utc)
            with _forecast._LEDGER_LOCK:
                eligibility = _forecast._downgrade_unserved(
                    evaluate_eligibility(
                        request,
                        registry,
                        bans,
                        flat_ledger.exposure(
                            request,
                            now=observed_now,
                            canonical_team_ids=(away.canonical_team_id, home.canonical_team_id),
                        ),
                        unit_policy(config),
                        now=observed_now,
                    )
                )
                _forecast._append_secondary_ledger(
                    flat_ledger, request, eligibility, observed_now, "nfl_moneyline_shadow:flat_ledger"
                )
            logged.append(event_id)
        except DuplicatePickError:
            skipped.append({"event_id": event_id, "reason": "duplicate"})
        except (EntityResolutionError, ValueError) as error:
            skipped.append({"event_id": event_id, "reason": str(error)[:200]})
    return {
        "status": "ok",
        "model_version": NFL_MARGIN_MONEYLINE_MODEL,
        "logged": len(logged),
        "logged_event_ids": logged,
        "skipped": skipped,
    }
