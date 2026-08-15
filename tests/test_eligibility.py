from datetime import UTC, datetime

import pytest

from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest, RecordType
from model_prediction.eligibility import evaluate_eligibility
from model_prediction.units import Exposure, UnitPolicy

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def request(
    market: MarketType = MarketType.MONEYLINE,
    away: str = "BOS",
    home: str = "NYY",
    origin: ModelOrigin = ModelOrigin.STATISTICAL_MODEL,
    state: ModelState = ModelState.SHADOW_QUALIFIED,
    probability: float = 0.6235,
    uncertainty: float | None = 0.004,
    observed_at: str = "2026-07-13T11:00:00Z",
) -> PickRequest:
    return PickRequest(
        event_start_utc="2026-07-14T00:00:00Z",
        event_id="game-1",
        league=League.MLB,
        away_team=away,
        home_team=home,
        market_type=market,
        selection="over" if market is MarketType.TOTAL else "home",
        line=8.5 if market is MarketType.TOTAL else (-1.5 if market is MarketType.SPREAD else None),
        sportsbook="Book",
        american_odds=-110,
        model_probability=probability,
        model_uncertainty=uncertainty,
        model_version="v1",
        rationale="test",
        risks="",
        model_origin=origin,
        model_state=state,
        observed_at_utc=observed_at,
        model_artifact_hash="model-hash",
        calibration_artifact_hash="calibration-hash",
        code_revision="abc123",
    )


@pytest.mark.parametrize("market", list(MarketType))
@pytest.mark.parametrize("banned_side", ["home", "away"])
def test_ban_blocks_every_market_and_side_with_zero_exposure(registry, ban_list, market, banned_side) -> None:
    ban_list.add(League.MLB, "NYY")
    req = request(
        market=market,
        away="NYY" if banned_side == "away" else "BOS",
        home="NYY" if banned_side == "home" else "BOS",
    )
    result = evaluate_eligibility(req, registry, ban_list, Exposure(), UnitPolicy(), NOW)
    assert result.record_type is RecordType.RESEARCH_OBSERVATION
    assert result.reason_code == "NO_CALL_TEAM_BANNED"
    assert result.units == 0
    assert result.banned_team.canonical_team_id == "mlb-nyy"


@pytest.mark.parametrize("origin", [ModelOrigin.ANALYST_ESTIMATE, ModelOrigin.MARKET_BASELINE])
def test_ban_cannot_be_bypassed_by_origin(registry, ban_list, origin) -> None:
    ban_list.add(League.MLB, "Yankees")
    req = request(origin=origin)
    if origin is ModelOrigin.MARKET_BASELINE:
        object.__setattr__(req, "baseline_identifier", "BASELINE_BOOK_RAW")
    result = evaluate_eligibility(req, registry, ban_list, Exposure(), UnitPolicy(), NOW)
    assert result.reason_code == "NO_CALL_TEAM_BANNED" and result.units == 0


def test_promotion_tier_no_longer_gates_qualified_calls(registry, ban_list) -> None:
    """Operator directive, 2026-08-02: "remove all promotion qualification,
    its up to me". RESEARCH/SHADOW_CANDIDATE/DEGRADED/SHADOW_QUALIFIED all
    now equally produce a real QUALIFIED_SHADOW_CALL -- a model's
    walk-forward/locked-holdout promotion tier no longer decides eligibility.
    RETIRED and SUSPENDED remain hard stops (explicit "off" states, not
    promotion tiers -- see lifecycle.can_create_qualified_call)."""
    for state in (
        ModelState.SHADOW_QUALIFIED,
        ModelState.RESEARCH,
        ModelState.SHADOW_CANDIDATE,
        ModelState.DEGRADED,
    ):
        result = evaluate_eligibility(
            request(home="BAL", state=state), registry, ban_list, Exposure(), UnitPolicy(), NOW
        )
        assert result.record_type is RecordType.QUALIFIED_SHADOW_CALL and result.units > 0

    for state in (ModelState.SUSPENDED, ModelState.RETIRED):
        result = evaluate_eligibility(
            request(home="BAL", state=state), registry, ban_list, Exposure(), UnitPolicy(), NOW
        )
        # Every research observation still carries a real, model-derived
        # paper size (operator directive, 2026-07-31: "every pick should
        # have units and pnl, hard code this") -- only a banned team stays
        # hard-zero, and none of these states are that.
        assert result.record_type is RecordType.RESEARCH_OBSERVATION and result.units > 0


def test_future_observed_at_becomes_research(registry, ban_list) -> None:
    """P1-4: a timestamp ahead of `now` used to pass the freshness check
    outright (only the "too old" direction was ever guarded), letting a
    clock-skewed or malformed observed_at_utc slip through as trusted,
    current data. Confirms the future-timestamp branch fires before the
    stale-data branch and produces the same NO_CALL_STALE_DATA outcome."""
    future = evaluate_eligibility(
        request(home="BAL", observed_at="2026-07-13T13:00:00Z"),
        registry,
        ban_list,
        Exposure(),
        UnitPolicy(),
        NOW,
    )
    assert future.reason_code == "NO_CALL_STALE_DATA"
    assert future.record_type is RecordType.RESEARCH_OBSERVATION
    assert future.units > 0


def test_stale_missing_uncertainty_low_edge_and_exposure_become_research(registry, ban_list) -> None:
    stale = evaluate_eligibility(
        request(home="BAL", observed_at="2026-07-12T00:00:00Z"),
        registry,
        ban_list,
        Exposure(),
        UnitPolicy(),
        NOW,
    )
    missing = evaluate_eligibility(
        request(home="BAL", uncertainty=None), registry, ban_list, Exposure(), UnitPolicy(), NOW
    )
    low = evaluate_eligibility(
        request(home="BAL", probability=0.54), registry, ban_list, Exposure(), UnitPolicy(), NOW
    )
    capped = evaluate_eligibility(
        request(home="BAL"), registry, ban_list, Exposure(event_units=2), UnitPolicy(), NOW
    )
    assert stale.reason_code == "NO_CALL_STALE_DATA"
    assert missing.reason_code == "QUALIFIED"  # uncertainty defaults to 0.05 — pick qualifies
    # Low edge and exposure caps no longer gate CALL vs NO_CALL at all
    # (operator directive, 2026-07-26): once a candidate clears the
    # trust-boundary checks (model state, staleness, provenance), it's a
    # real qualified call regardless of edge or today's exposure so far.
    assert low.reason_code == "QUALIFIED"
    assert capped.reason_code == "QUALIFIED"
    assert low.units > 0
    assert capped.units > 0
    # Stale data still can't be *trusted* as a real call, but it still gets a
    # real paper size for research tracking (operator directive, 2026-07-31).
    assert stale.units > 0
    assert low.units > 0
    assert capped.units > 0
    assert missing.units > 0  # qualified call gets positive units
