"""Outcome identity and proper probability scoring for every rebuilt market.

Scoring is conditional on an independently replayed pregame prediction. No
price is inferred from a line, and probability quality is not called profit.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .research_forward import aware_time
from .research_generation import ESPORTS, THREE_WAY, normalize_game


def outcome_label(prediction: dict[str, Any], raw: dict[str, Any], captured_at_utc: str) -> str:
    sport, market = prediction["sport"], prediction["market"]
    fixture = prediction["raw_fixture"]
    start, captured = aware_time(fixture["event_start_utc"]), aware_time(captured_at_utc)
    if not aware_time(prediction["observed_at_utc"]) < start < captured:
        raise ValueError("invalid_prediction_or_result_time")
    if market == "nrfi":
        if str(raw.get("status", "")).casefold() not in {"final", "completed", "game over"}:
            raise ValueError("nrfi_result_not_final")
        if (
            aware_time(raw["game_start_utc"]) != start
            or raw["home"]["team_name"] != fixture["home_team"]
            or raw["away"]["team_name"] != fixture["away_team"]
            or not start < aware_time(raw["observed_at_utc"]) <= captured
            or not (
                raw.get("event_id")
                if raw.get("source_schema") == "espn-scoreboard-result-v1"
                else raw.get("game_pk")
            )
        ):
            raise ValueError("nrfi_result_identity_mismatch")
        if raw.get("source_schema") == "espn-scoreboard-result-v1" and (
            raw["event_id"] != fixture["event_id"]
            or str(raw["home"]["team_id"]) != str(fixture["home_team_id"])
            or str(raw["away"]["team_id"]) != str(fixture["away_team_id"])
        ):
            raise ValueError("nrfi_espn_result_identity_mismatch")
        scores = [raw["first_inning_runs_home"], raw["first_inning_runs_away"]]
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v < 0
            or int(v) != v
            for v in scores
        ):
            raise ValueError("invalid_first_inning_scores")
        return "nrfi" if sum(scores) == 0 else "yrfi"
    source_start = raw.get("event_start_utc") or raw.get("start_utc")
    if not source_start and sport in {"KBO", "NPB"} and raw.get("scheduled_local_time"):
        from datetime import datetime

        local = datetime.fromisoformat(f"{raw['game_date']}T{raw['scheduled_local_time']}")
        source_start = local.replace(
            tzinfo=ZoneInfo("Asia/Seoul" if sport == "KBO" else "Asia/Tokyo")
        ).isoformat()
    if not source_start or aware_time(source_start) != start:
        raise ValueError("outcome_start_mismatch_or_unavailable")
    if sport in ESPORTS:
        if not raw.get("end_utc") or not start < aware_time(raw["end_utc"]) <= captured:
            raise ValueError("esports_result_not_completed")
    elif sport not in {"KBO", "NPB"} and str(raw.get("status", "")).casefold() not in {
        "completed",
        "final",
        "closed",
    }:
        raise ValueError("result_not_final")
    if sport != "TENNIS":
        score_fields = ("team1_score", "team2_score") if sport in ESPORTS else ("home_score", "away_score")
        for field in score_fields:
            value = raw[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or int(value) != value
            ):
                raise ValueError("invalid_completed_scores")
    normalized_raw = raw
    if sport == "NCAAF" and any(f"provider_{side}_team_id" in fixture for side in ("home", "away")):
        normalized_raw = dict(raw)
        for side in ("home", "away"):
            # The legacy NCAAF history uses exact names as participant keys.
            # A provider-ID result must independently match both that archived
            # name and its explicitly retained ESPN ID before translation.
            name, provider = fixture[f"{side}_team"], fixture.get(f"provider_{side}_team_id")
            if (
                not provider
                or fixture[f"{side}_team_id"] != name
                or raw.get(f"{side}_team") != name
                or (raw.get(f"{side}_team_id") is not None and str(raw[f"{side}_team_id"]) != str(provider))
            ):
                raise ValueError("outcome_participant_namespace_mismatch")
            normalized_raw[f"{side}_team_id"] = name
    game = normalize_game(normalized_raw, sport, (captured + timedelta(days=1)).date().isoformat())
    expected = prediction["fixture"]
    if (game.event_id, game.a, game.b, game.date) != (
        expected["event_id"],
        expected["a"],
        expected["b"],
        expected["date"],
    ):
        raise ValueError("outcome_participant_or_event_mismatch")
    if sport == "TENNIS" and game.surface != expected["surface"]:
        raise ValueError("outcome_surface_mismatch")
    if market == "moneyline":
        if game.score_a == game.score_b:
            if sport not in THREE_WAY:
                raise ValueError("unexpected_draw")
            return "draw"
        return "a_win" if game.score_a > game.score_b else "b_win"
    line = float(prediction["line"])
    if not math.isfinite(line) or line * 2 != round(line * 2):
        raise ValueError("invalid_contract_line")
    if market == "spread":
        margin = game.score_a - game.score_b + line
        return "home_cover" if margin > 0 else "away_cover" if margin < 0 else "push"
    if market == "total":
        margin = game.score_a + game.score_b - line
        return "over" if margin > 0 else "under" if margin < 0 else "push"
    raise ValueError("unsupported_market")


def proper_score(probabilities: dict[str, float], outcome: str) -> dict[str, Any]:
    if outcome not in probabilities or len(probabilities) < 2:
        raise ValueError("outcome_not_in_probability_space")
    p = {key: float(value) for key, value in probabilities.items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()) or abs(sum(p.values()) - 1) > 1e-8:
        raise ValueError("invalid_probability_distribution")
    # The vector sum is the multiclass Brier convention (twice scalar binary
    # Brier for binary markets). State this explicitly so reports cannot mix them.
    return {
        "outcome": outcome,
        "brier_vector_sum": sum((value - float(key == outcome)) ** 2 for key, value in p.items()),
        "log_loss": -math.log(max(p[outcome], 1e-12)),
        "accuracy": float(max(p, key=lambda key: (p[key], key)) == outcome),
        "log_loss_clip": 1e-12,
    }
