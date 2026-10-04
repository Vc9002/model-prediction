"""Versioned, replayable research forecasts for the complete model generation.

Inputs use explicit historical participant IDs, never fuzzy name matching.
This module has no order, promotion, or production-ledger write dependencies.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from .research_generation import (
    ESPORTS,
    FEATURE_NAMES,
    Fixture,
    Game,
    digest,
    file_hash,
    fixture_features,
    load_games,
    predict_bundle,
)

SCHEMA = "research-forward-v1"


def expected_model_id(recipe: dict[str, Any], sport: str, market: str) -> str:
    family = recipe.get("research_family", "generic_history_v1")
    if family == "generic_history_v1":
        return f"{sport.lower()}-{market}-research-20260909-v1"
    if family == "nrfi_clean_history_v2" and sport == "MLB" and market == "nrfi":
        return "mlb-nrfi-clean-research-20260911-v2"
    if family == "recency_history_v2" and market in {"moneyline", "spread", "total"}:
        return f"{sport.lower()}-{market}-recency-research-20260911-v2"
    if family == "ncaaf_joint_score_v2" and sport == "NCAAF" and market in {"moneyline", "spread", "total"}:
        return f"ncaaf-{market}-joint-research-20260911-v2"
    raise ValueError("unsupported_research_model_family")


def aware_time(value: str) -> datetime:
    value_dt = datetime.fromisoformat(value)
    if value_dt.tzinfo is None:
        raise ValueError("timezone_required")
    return value_dt.astimezone(UTC)


def normalize_fixture(raw: dict[str, Any], sport: str, observed_at_utc: str) -> Fixture:
    observed = aware_time(observed_at_utc)
    start = aware_time(raw["event_start_utc"])
    if start <= observed:
        raise ValueError("fixture_not_future")
    # Reject postgame input at this API boundary, even though features never use it.
    forbidden = {"home_score", "away_score", "score_a", "score_b", "winner", "winner_id", "outcome", "result"}
    if forbidden.intersection(raw):
        raise ValueError("fixture_contains_outcome")
    league = raw.get("league", sport) if sport in {"SOCCER", "TENNIS"} else sport
    if sport == "TENNIS":
        a, b = sorted((str(raw["player1_id"]), str(raw["player2_id"])))
    elif sport in ESPORTS:
        a, b = str(raw["team1_id"]), str(raw["team2_id"])
    else:
        a, b = str(raw["home_team_id"]), str(raw["away_team_id"])
    if not a.strip() or not b.strip() or a == b or a == "None" or b == "None":
        raise ValueError("invalid_fixture_participants")
    if not raw.get("event_id"):
        raise ValueError("missing_fixture_event_id")
    return Fixture(
        str(raw["event_id"]),
        start.date().isoformat(),
        f"{league}:{a}",
        f"{league}:{b}",
        str(raw.get("surface") or "unknown").lower(),
    )


def forecast(
    model_path: Path,
    raw: dict[str, Any],
    sport: str,
    market: str,
    observed_at_utc: str,
    *,
    games: list[Game],
    source_evidence: dict[str, Any],
    nrfi_snapshot_path: Path | None = None,
    capture_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one exact contract, retaining named features and source identities.

    Source capture time is supplied by the file-capturing runner. A saved input
    snapshot proves what was used now, not historical point-in-time availability.
    NRFI uses the generation's fitted training priors and the existing live
    feature builder with a conservative two-calendar-day results embargo.
    """
    fixture = normalize_fixture(raw, sport, observed_at_utc)
    recipe = json.loads(model_path.with_name("recipe.json").read_text())
    if recipe["model_id"] != expected_model_id(recipe, sport, market):
        raise ValueError("research_model_identity_mismatch")
    observed = aware_time(observed_at_utc)
    captured = aware_time(source_evidence["captured_at_utc"])
    if captured > observed or not source_evidence.get("source_sha256"):
        raise ValueError("invalid_source_capture_evidence")
    if market == "nrfi":
        from .models.mlb_first_inning_live import live_first_inning_features

        if sport != "MLB" or nrfi_snapshot_path is None:
            raise ValueError("nrfi_source_required")
        if file_hash(nrfi_snapshot_path) != source_evidence["source_sha256"]:
            raise ValueError("nrfi_source_hash_mismatch")
        required = ("home_team", "away_team", "venue_name", "home_starter_name", "away_starter_name")
        if any(not str(raw.get(key) or "").strip() for key in required):
            raise ValueError("nrfi_matchup_inputs_required")
        cutoff = min(
            observed.replace(hour=0, minute=0, second=0, microsecond=0),
            aware_time(raw["event_start_utc"]).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=1),
        )
        if recipe.get("research_family") == "nrfi_clean_history_v2":
            from .research_nrfi import FEATURE_NAMES as CLEAN_NRFI_FEATURES
            from .research_nrfi import live_features

            if recipe["features"] != list(CLEAN_NRFI_FEATURES):
                raise ValueError("unsupported_clean_nrfi_feature_schema")
            features, latest = live_features(nrfi_snapshot_path, raw, observed, recipe["source"]["priors"])
            values = tuple(features[name] for name in recipe["features"])
        else:
            features = live_first_inning_features(
                home_team=raw["home_team"],
                away_team=raw["away_team"],
                venue_name=raw["venue_name"],
                home_starter_name=raw["home_starter_name"],
                away_starter_name=raw["away_starter_name"],
                decision=cutoff,
                snapshot_path=nrfi_snapshot_path,
                priors=recipe["source"]["priors"],
            )
            values = tuple(features[name] for name in recipe["features"])
            latest = f"before:{cutoff.isoformat()}"
    else:
        if recipe["features"] != list(FEATURE_NAMES):
            raise ValueError("unsupported_research_feature_schema")
        if source_evidence.get("normalized_games_sha256") != digest([asdict(g) for g in games]):
            raise ValueError("normalized_history_hash_mismatch")
        result = fixture_features(games, [fixture], available_before=observed.date().isoformat())[
            fixture.event_id
        ]
        if result is None:
            raise ValueError("insufficient_participant_history")
        values, latest = result
    line = raw.get("line")
    if market in {"spread", "total"}:
        if isinstance(line, bool) or not isinstance(line, (float, int)) or not math.isfinite(line):
            raise ValueError("exact_contract_line_required")
        if raw.get("period") != "full_game":
            raise ValueError("exact_full_game_period_required")
        if market == "spread" and raw.get("line_convention") != "signed_home":
            raise ValueError("signed_home_line_required")
    elif line is not None:
        raise ValueError("unexpected_line_for_binary_or_three_way_market")
    labels = recipe["class_labels"] or (
        ["away_cover", "push", "home_cover"] if market == "spread" else ["under", "push", "over"]
    )
    p = predict_bundle(model_path, np.asarray([values]), line=line)[0]
    payload = {
        "schema": SCHEMA,
        "sport": sport,
        "market": market,
        "fixture": asdict(fixture),
        "raw_fixture": raw,
        "observed_at_utc": observed_at_utc,
        "model_id": recipe["model_id"],
        "model_file_sha256": file_hash(model_path),
        "recipe_sha256": file_hash(model_path.with_name("recipe.json")),
        "features": dict(zip(recipe["features"], values, strict=True)),
        "feature_max_event_date": latest,
        "source_evidence": source_evidence,
        "line": line,
        "probabilities": dict(zip(labels, p.tolist(), strict=True)),
        "evidence_origin": "research_forward_capture",
        "historical_source_observation_times_verified": False,
        "economic_evaluation": None,
        "promote": False,
    }
    payload = json.loads(json.dumps(payload, allow_nan=False))
    if capture_context is not None:
        payload["capture_context"] = capture_context
    payload["snapshot_hash"] = digest(payload)
    replay_forecast(payload, model_path)
    return payload


def replay_forecast(payload: dict[str, Any], model_path: Path) -> dict[str, float]:
    if payload.get("schema") != SCHEMA or payload.get("snapshot_hash") != digest(
        {k: v for k, v in payload.items() if k != "snapshot_hash"}
    ):
        raise ValueError("research_snapshot_hash_mismatch")
    if (
        file_hash(model_path) != payload["model_file_sha256"]
        or file_hash(model_path.with_name("recipe.json")) != payload["recipe_sha256"]
    ):
        raise ValueError("research_snapshot_artifact_mismatch")
    fixture = normalize_fixture(payload["raw_fixture"], payload["sport"], payload["observed_at_utc"])
    if asdict(fixture) != payload["fixture"]:
        raise ValueError("research_snapshot_fixture_mismatch")
    recipe = json.loads(model_path.with_name("recipe.json").read_text())
    if recipe["model_id"] != payload["model_id"] or payload["model_id"] != expected_model_id(
        recipe, payload["sport"], payload["market"]
    ):
        raise ValueError("research_snapshot_model_mismatch")
    if payload["line"] != payload["raw_fixture"].get("line"):
        raise ValueError("research_snapshot_line_mismatch")
    if aware_time(payload["source_evidence"]["captured_at_utc"]) > aware_time(payload["observed_at_utc"]):
        raise ValueError("research_snapshot_capture_time_mismatch")
    context = payload.get("capture_context")
    if context is not None and (
        context.get("policy") != "first_incumbent_after_preparation_v1"
        or not context.get("pick_id")
        or not context.get("incumbent_snapshot_hash")
        or len(context.get("preparation_sha256", "")) != 64
        or not (
            aware_time(payload["source_evidence"]["captured_at_utc"])
            <= aware_time(context["prepared_at_utc"])
            <= aware_time(payload["observed_at_utc"])
            <= aware_time(context["incumbent_created_at_utc"])
            <= aware_time(context["prediction_created_at_utc"])
            < aware_time(payload["raw_fixture"]["event_start_utc"])
        )
    ):
        raise ValueError("research_pair_capture_context_mismatch")
    if set(payload["features"]) != set(recipe["features"]):
        raise ValueError("research_snapshot_feature_mismatch")
    x = np.asarray([[payload["features"][name] for name in recipe["features"]]])
    p = predict_bundle(model_path, x, line=payload["line"])[0]
    labels = recipe["class_labels"] or (
        ["away_cover", "push", "home_cover"] if recipe["target"] == "spread" else ["under", "push", "over"]
    )
    replayed = dict(zip(labels, p.tolist(), strict=True))
    if set(replayed) != set(payload["probabilities"]) or any(
        abs(replayed[key] - payload["probabilities"][key]) > 1e-12 for key in replayed
    ):
        raise ValueError("research_snapshot_prediction_mismatch")
    return replayed


def rebuild_forecast(payload: dict[str, Any], model_path: Path, archived_source: Path) -> dict[str, float]:
    """Independently rebuild the named features from the archived source bytes."""
    replay_forecast(payload, model_path)
    if file_hash(archived_source) != payload["source_evidence"]["source_sha256"]:
        raise ValueError("research_archived_source_hash_mismatch")
    nrfi = payload["market"] == "nrfi"
    games = (
        []
        if nrfi
        else load_games(
            archived_source, payload["sport"], aware_time(payload["observed_at_utc"]).date().isoformat()
        )[0]
    )
    rebuilt = forecast(
        model_path,
        payload["raw_fixture"],
        payload["sport"],
        payload["market"],
        payload["observed_at_utc"],
        games=games,
        source_evidence=payload["source_evidence"],
        nrfi_snapshot_path=archived_source if nrfi else None,
        capture_context=payload.get("capture_context"),
    )
    if rebuilt != payload:
        raise ValueError("research_source_rebuild_mismatch")
    return rebuilt["probabilities"]
