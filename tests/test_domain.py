"""Tests for domain.py -- PickRequest.validate() is the single gate that
stops a malformed pick from ever entering a ledger; previously only
exercised incidentally through ledger.py's own tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from model_prediction.domain import (
    BASELINE_IDENTIFIERS,
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
)

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def _request(**overrides) -> PickRequest:
    base = {
        "event_start_utc": "2026-07-14T00:00:00Z",
        "event_id": "game-1",
        "league": League.MLB,
        "away_team": "BOS",
        "home_team": "NYY",
        "market_type": MarketType.MONEYLINE,
        "selection": "home",
        "line": None,
        "sportsbook": "Book",
        "american_odds": -110,
        "model_probability": 0.6235,
        "model_uncertainty": 0.05,
        "model_version": "v1",
        "rationale": "test",
        "risks": "",
    }
    base.update(overrides)
    return PickRequest(**base)


def test_valid_moneyline_request_does_not_raise() -> None:
    _request().validate(now=NOW)


def test_event_already_started_is_rejected() -> None:
    with pytest.raises(ValueError, match="event has started"):
        _request(event_start_utc="2026-07-13T00:00:00Z").validate(now=NOW)


def test_event_starting_at_exactly_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="event has started"):
        _request(event_start_utc="2026-07-13T12:00:00Z").validate(now=NOW)


@pytest.mark.parametrize("field", ["event_id", "away_team", "home_team"])
def test_blank_identity_fields_are_rejected(field) -> None:
    with pytest.raises(ValueError, match="event id and both teams"):
        _request(**{field: "   "}).validate(now=NOW)


def test_away_and_home_team_cannot_be_the_same() -> None:
    with pytest.raises(ValueError, match="must differ"):
        _request(away_team="NYY", home_team="NYY").validate(now=NOW)


def test_away_and_home_team_case_insensitive_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="must differ"):
        _request(away_team="nyy", home_team="NYY").validate(now=NOW)


def test_moneyline_rejects_a_line() -> None:
    with pytest.raises(ValueError, match="must not have a line"):
        _request(market_type=MarketType.MONEYLINE, line=1.5).validate(now=NOW)


def test_moneyline_allows_draw_selection() -> None:
    _request(market_type=MarketType.MONEYLINE, selection="draw").validate(now=NOW)


def test_moneyline_rejects_over_under_selection() -> None:
    with pytest.raises(ValueError, match="selection must be one of"):
        _request(market_type=MarketType.MONEYLINE, selection="over").validate(now=NOW)


def test_spread_requires_a_line() -> None:
    with pytest.raises(ValueError, match="require a selection-relative line"):
        _request(market_type=MarketType.SPREAD, selection="home", line=None).validate(now=NOW)


def test_spread_rejects_draw_selection() -> None:
    with pytest.raises(ValueError, match="selection must be one of"):
        _request(market_type=MarketType.SPREAD, selection="draw", line=-1.5).validate(now=NOW)


def test_total_requires_a_positive_line() -> None:
    with pytest.raises(ValueError, match="require a positive line"):
        _request(market_type=MarketType.TOTAL, selection="over", line=0).validate(now=NOW)


def test_total_rejects_negative_line() -> None:
    with pytest.raises(ValueError, match="require a positive line"):
        _request(market_type=MarketType.TOTAL, selection="over", line=-8.5).validate(now=NOW)


def test_total_requires_over_under_selection() -> None:
    with pytest.raises(ValueError, match="selection must be one of"):
        _request(market_type=MarketType.TOTAL, selection="home", line=8.5).validate(now=NOW)


@pytest.mark.parametrize("odds", [0, 50, -50, 99, -99])
def test_american_odds_in_dead_zone_is_rejected(odds) -> None:
    with pytest.raises(ValueError, match="American odds"):
        _request(american_odds=odds).validate(now=NOW)


@pytest.mark.parametrize("odds", [100, -100, 500, -500])
def test_american_odds_valid_range_is_accepted(odds) -> None:
    _request(american_odds=odds).validate(now=NOW)


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.1])
def test_model_probability_out_of_open_unit_interval_is_rejected(probability) -> None:
    with pytest.raises(ValueError, match="model probability"):
        _request(model_probability=probability).validate(now=NOW)


@pytest.mark.parametrize("uncertainty", [-0.01, 0.5, 0.6])
def test_model_uncertainty_out_of_range_is_rejected(uncertainty) -> None:
    with pytest.raises(ValueError, match="model uncertainty"):
        _request(model_uncertainty=uncertainty).validate(now=NOW)


def test_model_uncertainty_none_is_allowed() -> None:
    _request(model_uncertainty=None).validate(now=NOW)


@pytest.mark.parametrize("field", ["model_version", "rationale"])
def test_blank_version_or_rationale_is_rejected(field) -> None:
    with pytest.raises(ValueError, match="model version and rationale"):
        _request(**{field: "  "}).validate(now=NOW)


def test_market_baseline_origin_requires_a_recognized_identifier() -> None:
    with pytest.raises(ValueError, match="market baseline requires"):
        _request(model_origin=ModelOrigin.MARKET_BASELINE, baseline_identifier=None).validate(now=NOW)


def test_market_baseline_origin_rejects_an_unrecognized_identifier() -> None:
    with pytest.raises(ValueError, match="market baseline requires"):
        _request(
            model_origin=ModelOrigin.MARKET_BASELINE, baseline_identifier="MADE_UP_BASELINE"
        ).validate(now=NOW)


@pytest.mark.parametrize("identifier", sorted(BASELINE_IDENTIFIERS))
def test_market_baseline_origin_accepts_every_recognized_identifier(identifier) -> None:
    _request(model_origin=ModelOrigin.MARKET_BASELINE, baseline_identifier=identifier).validate(now=NOW)


def test_non_baseline_origin_rejects_a_baseline_identifier() -> None:
    with pytest.raises(ValueError, match="valid only for market_baseline"):
        _request(
            model_origin=ModelOrigin.STATISTICAL_MODEL,
            baseline_identifier="BASELINE_BOOK_RAW",
        ).validate(now=NOW)


def test_observed_at_in_the_future_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be in the future"):
        _request(observed_at_utc="2026-07-13T13:00:00Z").validate(now=NOW)


def test_observed_at_exactly_now_is_accepted() -> None:
    _request(observed_at_utc="2026-07-13T12:00:00Z").validate(now=NOW)


@pytest.mark.parametrize(
    "field", ["decision_no_vig_probability", "decision_consensus_probability"]
)
@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.1])
def test_decision_probabilities_out_of_range_are_rejected(field, probability) -> None:
    with pytest.raises(ValueError, match=field):
        _request(**{field: probability}).validate(now=NOW)


def test_as_dict_includes_core_and_feature_attribution_fields() -> None:
    payload = _request(
        elo_probability=0.55,
        trend_gap=-0.1,
        unavailable_features="pitcher_era_gap",
    ).as_dict()
    assert payload["event_id"] == "game-1"
    assert payload["league"] == "MLB"
    assert payload["selection"] == "home"
    assert payload["elo_probability"] == 0.55
    assert payload["trend_gap"] == -0.1
    assert payload["unavailable_features"] == "pitcher_era_gap"
    # Correlation tags serialize as a list, not the internal tuple.
    assert isinstance(payload["correlation_tags"], list)


def test_model_state_defaults_to_research() -> None:
    request = _request()
    assert request.model_state is ModelState.RESEARCH
