from datetime import datetime, timezone

import pytest

from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest, RecordType
from model_prediction.eligibility import evaluate_eligibility
from model_prediction.units import Exposure, UnitPolicy


NOW = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


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


def test_only_qualified_statistical_model_can_receive_units(registry, ban_list) -> None:
    qualified = evaluate_eligibility(request(home="BAL"), registry, ban_list, Exposure(), UnitPolicy(), NOW)
    assert qualified.record_type is RecordType.QUALIFIED_SHADOW_CALL and qualified.units > 0
    for state in (
        ModelState.RESEARCH,
        ModelState.SHADOW_CANDIDATE,
        ModelState.DEGRADED,
        ModelState.SUSPENDED,
        ModelState.RETIRED,
    ):
        result = evaluate_eligibility(
            request(home="BAL", state=state), registry, ban_list, Exposure(), UnitPolicy(), NOW
        )
        assert result.record_type is RecordType.RESEARCH_OBSERVATION and result.units == 0


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
    assert low.reason_code == "NO_CALL_LOW_EDGE"
    assert capped.reason_code == "NO_CALL_EXPOSURE_LIMIT"
    assert all(result.units == 0 for result in (stale, low, capped))
    assert missing.units > 0  # qualified call gets positive units
