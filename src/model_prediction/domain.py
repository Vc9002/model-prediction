from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

# This is a US sports-market project: "today" for forecasting, searching, and
# backfill defaults must always mean the US-Eastern calendar day, never the
# host machine's local timezone or a UTC calendar day (which drifts a day
# off during US evening games). Hardcoded here as the single source of truth
# -- other modules should import EASTERN/eastern_today from here rather than
# redefining ZoneInfo("America/New_York") themselves.
EASTERN = ZoneInfo("America/New_York")


class League(StrEnum):
    MLB = "MLB"
    NBA = "NBA"
    WNBA = "WNBA"
    NFL = "NFL"
    TENNIS = "TENNIS"
    SOCCER = "SOCCER"
    LOL = "LOL"
    CS2 = "CS2"
    DOTA2 = "DOTA2"
    VALORANT = "VALORANT"
    RAINBOW_SIX = "RAINBOW_SIX"
    KBO = "KBO"
    NPB = "NPB"

# Canonical sport tuples — single source of truth for iteration and CLI choices.
# When adding a sport, update League, these tuples, and config/model.yaml together.
# WORLD_CUP retired 2026-07-27: the tournament is over, no games left to
# forecast or settle, and it was a half-removed inconsistency (dropped from
# live trading/settlement but still a valid enum value) that could have let a
# stray pick settle-stall forever. ESPN's historical scoreboard path for it
# (data_sources/espn.py) is untouched -- that's a raw ingestion string key,
# not this enum, and still works for backfilling already-played tournaments.
PRODUCTION_SPORTS: tuple[str, ...] = ("mlb", "wnba")
LEARNED_PRODUCTION_SPORTS: tuple[str, ...] = (
    "mlb", "nba", "wnba", "nfl", "soccer", "lol", "cs2", "dota2", "valorant", "rainbow_six",
)
ALL_SPORTS: tuple[str, ...] = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis")


class MarketType(StrEnum):
    MONEYLINE = "moneyline"
    SPREAD = "spread"
    TOTAL = "total"


class PickStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"


class PickResult(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"


class RecordType(StrEnum):
    RESEARCH_OBSERVATION = "RESEARCH_OBSERVATION"
    QUALIFIED_SHADOW_CALL = "QUALIFIED_SHADOW_CALL"


class ModelOrigin(StrEnum):
    STATISTICAL_MODEL = "statistical_model"
    ANALYST_ESTIMATE = "analyst_estimate"
    MARKET_BASELINE = "market_baseline"
    SYNTHETIC_TEST = "synthetic_test"


class ModelState(StrEnum):
    RESEARCH = "research"
    SHADOW_CANDIDATE = "shadow_candidate"
    SHADOW_QUALIFIED = "shadow_qualified"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class NoCallReason(StrEnum):
    LOW_EDGE = "NO_CALL_LOW_EDGE"
    STALE_DATA = "NO_CALL_STALE_DATA"
    MISSING_UNCERTAINTY = "NO_CALL_MISSING_UNCERTAINTY"
    MODEL_UNVALIDATED = "NO_CALL_MODEL_UNVALIDATED"
    EXPOSURE_LIMIT = "NO_CALL_EXPOSURE_LIMIT"
    EVENT_STARTED = "NO_CALL_EVENT_STARTED"
    INVALID_MARKET = "NO_CALL_INVALID_MARKET"
    DUPLICATE = "NO_CALL_DUPLICATE"
    TEAM_BANNED = "NO_CALL_TEAM_BANNED"
    ENTITY_UNRESOLVED = "NO_CALL_ENTITY_UNRESOLVED"
    MODEL_INELIGIBLE = "NO_CALL_MODEL_INELIGIBLE"
    LARGE_DISAGREEMENT = "NO_CALL_LARGE_DISAGREEMENT_REVIEW"
    MARKET_UNAVAILABLE = "NO_CALL_MARKET_UNAVAILABLE"


BASELINE_IDENTIFIERS = {
    "BASELINE_BOOK_RAW",
    "BASELINE_BOOK_NO_VIG",
    "BASELINE_CONSENSUS_NO_VIG",
}


LOSS_CLASSIFICATIONS = {
    "bad_luck",
    "missing_information",
    "bad_data",
    "model_error",
    "market_or_rule_error",
    "process_error",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def eastern_today() -> date:
    """Today's date in US-Eastern local time -- the canonical "today" for this project."""
    return datetime.now(UTC).astimezone(EASTERN).date()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PickRequest:
    event_start_utc: str
    event_id: str
    league: League
    away_team: str
    home_team: str
    market_type: MarketType
    selection: str
    line: float | None
    sportsbook: str
    american_odds: int
    model_probability: float
    model_uncertainty: float | None
    model_version: str
    rationale: str
    risks: str
    model_origin: ModelOrigin = ModelOrigin.ANALYST_ESTIMATE
    model_state: ModelState = ModelState.RESEARCH
    baseline_identifier: str | None = None
    observed_at_utc: str | None = None
    model_artifact_hash: str = ""
    calibration_method: str = "identity"
    calibration_version: str = "identity-v1"
    calibration_artifact_hash: str = ""
    feature_schema_version: str = "1"
    entity_map_version: str = "1"
    code_revision: str = "unknown"
    correlation_tags: tuple[str, ...] = ("same_event", "same_team_same_day", "same_league_same_day")
    decision_no_vig_probability: float | None = None
    decision_consensus_probability: float | None = None
    decision_consensus_line: float | None = None
    # Diagnostic only: each feature's real value at decision time, for audit.
    # The ledger has held columns for all of these since 2026-07-22 ("for
    # dashboard feature attribution") but PickRequest never carried the
    # values to populate them — every one of these was silently blank for
    # every real pick until 2026-07-25, not just pitcher_era_gap. Whichever
    # subset a given sport/artifact actually uses is populated from
    # candidate.feature_basis; the rest stay None. Never read back by any
    # code path — model_probability already has these baked in via the
    # artifact's fitted coefficients.
    elo_probability: float | None = None
    trend_gap: float | None = None
    defensive_trend_gap: float | None = None
    park_factor: float | None = None
    weather_factor: float | None = None
    pitcher_era_gap: float | None = None
    probable_starter_era_gap: float | None = None
    # Comma-joined feature names that fell back to a neutral default for this
    # specific game (e.g. ESPN hasn't posted both starters yet) — surfaced as
    # a visible dashboard badge, not just buried in the rationale text.
    unavailable_features: str | None = None

    def validate(self, now: datetime | None = None) -> None:
        current = now or utc_now()
        if parse_utc(self.event_start_utc) <= current:
            raise ValueError("cannot create a call after the event has started")
        if not self.event_id.strip() or not self.away_team.strip() or not self.home_team.strip():
            raise ValueError("event id and both teams are required")
        if self.away_team.strip().lower() == self.home_team.strip().lower():
            raise ValueError("away and home teams must differ")
        if self.market_type is MarketType.MONEYLINE:
            # "draw" is only meaningful for 3-way sports (soccer); grade_pick
            # grades it as its own binary yes/no rather than a margin bet.
            allowed = {"away", "home", "draw"}
            if self.line is not None:
                raise ValueError("moneyline calls must not have a line")
        elif self.market_type is MarketType.SPREAD:
            allowed = {"away", "home"}
            if self.line is None:
                raise ValueError("spread calls require a selection-relative line")
        else:
            allowed = {"over", "under"}
            if self.line is None or self.line <= 0:
                raise ValueError("total calls require a positive line")
        if self.selection.lower() not in allowed:
            raise ValueError(f"selection must be one of {sorted(allowed)}")
        if not (-100000 < self.american_odds < 100000) or -99 <= self.american_odds <= 99:
            raise ValueError("American odds must be <= -100 or >= +100")
        if not 0 < self.model_probability < 1:
            raise ValueError("model probability must be between 0 and 1")
        if self.model_uncertainty is not None and not 0 <= self.model_uncertainty < 0.5:
            raise ValueError("model uncertainty must be in [0, 0.5)")
        if not self.model_version.strip() or not self.rationale.strip():
            raise ValueError("model version and rationale are required")
        if self.model_origin is ModelOrigin.MARKET_BASELINE:
            if self.baseline_identifier not in BASELINE_IDENTIFIERS:
                raise ValueError(f"market baseline requires one of {sorted(BASELINE_IDENTIFIERS)}")
        elif self.baseline_identifier is not None:
            raise ValueError("baseline identifier is valid only for market_baseline origin")
        if self.observed_at_utc is not None and parse_utc(self.observed_at_utc) > current:
            raise ValueError("observation timestamp cannot be in the future")
        for name, probability in (
            ("decision_no_vig_probability", self.decision_no_vig_probability),
            ("decision_consensus_probability", self.decision_consensus_probability),
        ):
            if probability is not None and not 0 < probability < 1:
                raise ValueError(f"{name} must be between 0 and 1")

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_start_utc": self.event_start_utc,
            "event_id": self.event_id,
            "league": self.league.value,
            "away_team": self.away_team,
            "home_team": self.home_team,
            "market_type": self.market_type.value,
            "selection": self.selection.lower(),
            "line": self.line,
            "sportsbook": self.sportsbook,
            "american_odds": self.american_odds,
            "model_probability": self.model_probability,
            "model_uncertainty": self.model_uncertainty,
            "model_version": self.model_version,
            "rationale": self.rationale,
            "risks": self.risks,
            "model_origin": self.model_origin.value,
            "model_state_at_decision": self.model_state.value,
            "baseline_identifier": self.baseline_identifier,
            "observed_at_utc": self.observed_at_utc,
            "model_artifact_hash": self.model_artifact_hash,
            "calibration_method": self.calibration_method,
            "calibration_version": self.calibration_version,
            "calibration_artifact_hash": self.calibration_artifact_hash,
            "feature_schema_version": self.feature_schema_version,
            "entity_map_version": self.entity_map_version,
            "code_revision": self.code_revision,
            "correlation_tags": list(self.correlation_tags),
            "decision_no_vig_probability": self.decision_no_vig_probability,
            "decision_consensus_probability": self.decision_consensus_probability,
            "decision_consensus_line": self.decision_consensus_line,
            "elo_probability": self.elo_probability,
            "trend_gap": self.trend_gap,
            "park_factor": self.park_factor,
            "weather_factor": self.weather_factor,
            "pitcher_era_gap": self.pitcher_era_gap,
            "probable_starter_era_gap": self.probable_starter_era_gap,
            "unavailable_features": self.unavailable_features,
        }
