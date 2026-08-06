"""Tests for the winner-first value-betting decision engine
(src/model_prediction/rebuild/decision.py), matching CLAUDE.md's own
"Critical decision tests" list under Checkpoint 7 exactly:

    market price cannot change predicted winner
    60% winner at 70c returns NO_BET
    NO_BET returns 0 units
    60% winner at qualified price may return BET
    opponent cannot be selected for larger apparent edge
    winner-aligned spread may qualify
    stale quote fails closed
    insufficient depth fails closed
    contract mismatch fails closed
    totals freeze sports-only side before market evaluation
"""

from __future__ import annotations

from model_prediction.rebuild.decision import (
    INSUFFICIENT_DEPTH,
    NO_EDGE_AFTER_COSTS,
    NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE,
    NOT_ALIGNED_WITH_PREDICTED_WINNER,
    QUALIFIED,
    STALE_QUOTE,
    MarketEvaluation,
    SportsForecast,
    decide_team_market,
    decide_total,
    evaluate_game,
)
from model_prediction.rebuild.economic import SizeLimits


def _forecast(
    predicted_winner="home", home_prob=0.60, away_prob=0.40, lower_margin=0.05,
    totals_probabilities=None, spread_probabilities=None,
):
    return SportsForecast(
        event_id="game-1",
        predicted_winner=predicted_winner,
        raw_probabilities={"home": home_prob, "away": away_prob},
        calibrated_probabilities={"home": home_prob, "away": away_prob},
        probability_lower={"home": home_prob - lower_margin, "away": away_prob - lower_margin},
        probability_upper={"home": home_prob + lower_margin, "away": away_prob + lower_margin},
        expected_home_score=4.5, expected_away_score=4.0,
        model_artifact_hash="abc123", calibration_artifact_hash="def456",
        totals_probabilities=totals_probabilities or {},
        spread_probabilities=spread_probabilities or {},
    )


def _market(team_or_side="home", ask=0.70, depth_price=None, market_type="moneyline",
            line=None, quote_age=5.0, depth=5.0):
    return MarketEvaluation(
        market_id="mkt-1", market_type=market_type, team_or_side=team_or_side, line=line,
        executable_ask=ask, depth_adjusted_price=depth_price if depth_price is not None else ask,
        quote_age_seconds=quote_age, available_depth=depth,
    )


class TestMarketCannotChangePredictedWinner:
    def test_market_price_never_influences_predicted_winner_field(self):
        # A forecast's predicted_winner is fixed at construction — nothing in
        # the decision layer can touch it regardless of what price arrives.
        forecast = _forecast(predicted_winner="home", home_prob=0.60)
        cheap_underdog = _market(team_or_side="away", ask=0.10)  # wildly "cheap" opponent
        decision = decide_team_market(forecast, cheap_underdog)
        assert forecast.predicted_winner == "home"
        assert decision.predicted_winner == "home"


class TestOverpricedWinnerReturnsNoBet:
    def test_60_pct_winner_at_70_cents_returns_no_bet(self):
        # Model: home 60%. Market: home priced at 70c. Negative edge.
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        market = _market(team_or_side="home", ask=0.70, depth_price=0.70)
        decision = decide_team_market(forecast, market)
        assert decision.action == "NO_BET"
        assert decision.reason_code == NO_EDGE_AFTER_COSTS

    def test_no_bet_always_carries_zero_units(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60)
        market = _market(team_or_side="home", ask=0.70, depth_price=0.70)
        decision = decide_team_market(forecast, market)
        assert decision.units == 0.0


class TestQualifiedWinnerReturnsBet:
    def test_60_pct_winner_at_qualified_price_may_return_bet(self):
        # Model: home 60%, conservative 57%. Market: home priced at 52c —
        # a real, qualified edge. unit_rounding=0.0 here: quarter-Kelly on a
        # modest single-digit edge rounds to 0 at the default 0.25-unit
        # rounding granularity (that's edge_scaled_units' own tested
        # behavior, not this decision engine's) — disabling rounding isolates
        # what this test actually checks: BET vs NO_BET routing, not sizing
        # granularity policy.
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        market = _market(team_or_side="home", ask=0.52, depth_price=0.52)
        decision = decide_team_market(forecast, market, limits=SizeLimits(min_depth_units=0.0, unit_rounding=0.0))
        assert decision.action == "BET"
        assert decision.units > 0.0
        assert decision.reason_code == QUALIFIED


class TestOpponentNeverSubstituted:
    def test_opponent_not_selected_even_with_a_much_larger_apparent_edge(self):
        # Model: home 60% (predicted winner), away 40%. Market prices away
        # at 20c — a huge raw apparent edge (40% - 20% = 20%), far bigger
        # than home's own edge. The engine must still never recommend away.
        forecast = _forecast(predicted_winner="home", home_prob=0.60, away_prob=0.40)
        away_market = _market(team_or_side="away", ask=0.20, depth_price=0.20)
        decision = decide_team_market(forecast, away_market)
        assert decision.action == "NO_BET"
        assert decision.reason_code == NOT_ALIGNED_WITH_PREDICTED_WINNER
        assert decision.units == 0.0


class TestWinnerAlignedSpreadMayQualify:
    def test_spread_for_predicted_winner_can_be_bet(self):
        # Real spread cover probability (0.55), not the moneyline win
        # probability (0.60) — this is the fixed, correct behavior.
        forecast = _forecast(
            predicted_winner="home", home_prob=0.60, lower_margin=0.03,
            spread_probabilities={-1.5: {"home": 0.55, "away": 0.45}},
        )
        spread_market = _market(
            team_or_side="home", ask=0.50, depth_price=0.50, market_type="spread", line=-1.5,
        )
        decision = decide_team_market(forecast, spread_market, limits=SizeLimits(min_depth_units=0.0, unit_rounding=0.0))
        assert decision.action == "BET"
        assert decision.market_type == "spread"

    def test_spread_for_the_opposing_team_is_rejected_even_if_priced_well(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, away_prob=0.40)
        opposing_spread = _market(
            team_or_side="away", ask=0.20, depth_price=0.20, market_type="spread", line=1.5,
        )
        decision = decide_team_market(forecast, opposing_spread)
        assert decision.action == "NO_BET"
        assert decision.reason_code == NOT_ALIGNED_WITH_PREDICTED_WINNER

    def test_spread_edge_uses_cover_probability_not_moneyline_probability(self):
        """Real, confirmed bug (see outputs/rebuild/takeover_status.md
        Checkpoint 9): a real spread market produced a fabricated +23.5%
        edge because the moneyline win probability (70%) was used to price
        a spread whose real cover probability was much lower (40%).
        Exact case from the review that caught this: winner moneyline 70%,
        winner -2.5 cover probability 40%, spread ask 45c -> must be
        NO_BET with a negative edge, not a positive one derived from 70%.
        """
        forecast = _forecast(
            predicted_winner="home", home_prob=0.70, lower_margin=0.0,
            spread_probabilities={-2.5: {"home": 0.40, "away": 0.60}},
        )
        spread_market = _market(
            team_or_side="home", ask=0.45, depth_price=0.45, market_type="spread", line=-2.5,
        )

        decision = decide_team_market(forecast, spread_market)

        assert decision.action == "NO_BET"
        assert decision.units == 0.0
        assert decision.cost_adjusted_edge is not None
        assert decision.cost_adjusted_edge < 0, (
            f"edge should be 0.40 - 0.45 = -0.05, not derived from the 70% moneyline "
            f"probability (which would wrongly give +0.25); got {decision.cost_adjusted_edge}"
        )

    def test_spread_with_no_real_forecast_for_that_line_fails_closed(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, spread_probabilities={})
        spread_market = _market(
            team_or_side="home", ask=0.50, depth_price=0.50, market_type="spread", line=-1.5,
        )
        decision = decide_team_market(forecast, spread_market)
        assert decision.action == "NO_BET"
        assert decision.units == 0.0


class TestStaleQuoteFailsClosed:
    def test_quote_older_than_max_age_fails_closed(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        stale_market = _market(team_or_side="home", ask=0.52, depth_price=0.52, quote_age=999999)
        decision = decide_team_market(forecast, stale_market, limits=SizeLimits(max_quote_age_seconds=300))
        assert decision.action == "NO_BET"
        assert decision.reason_code == STALE_QUOTE


class TestInsufficientDepthFailsClosed:
    def test_depth_below_minimum_fails_closed(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        thin_market = _market(team_or_side="home", ask=0.52, depth_price=0.52, depth=0.01)
        decision = decide_team_market(forecast, thin_market, limits=SizeLimits(min_depth_units=1.0))
        assert decision.action == "NO_BET"
        assert decision.reason_code == INSUFFICIENT_DEPTH


class TestContractMismatchFailsClosed:
    def test_mismatched_side_fails_closed_via_evaluate_game(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, away_prob=0.40)
        candidates = [_market(team_or_side="away", ask=0.30, depth_price=0.30)]
        decisions = evaluate_game(forecast, candidates)
        assert len(decisions) == 1
        assert decisions[0].action == "NO_BET"
        assert decisions[0].reason_code == NOT_ALIGNED_WITH_PREDICTED_WINNER


class TestTotalsFreezeBeforeMarket:
    def test_frozen_side_decided_from_sports_distribution_alone(self):
        # Sports-only distribution favors OVER for this line (58% vs 42%).
        forecast = _forecast(
            totals_probabilities={8.5: {"over": 0.58, "under": 0.42}},
        )
        assert forecast.frozen_totals_side(8.5) == "over"

    def test_under_side_market_rejected_when_over_is_frozen(self):
        forecast = _forecast(totals_probabilities={8.5: {"over": 0.58, "under": 0.42}})
        under_market = _market(team_or_side="under", ask=0.40, depth_price=0.40, market_type="total", line=8.5)
        decision = decide_total(forecast, under_market)
        assert decision.action == "NO_BET"
        assert decision.reason_code == NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE

    def test_frozen_over_side_can_be_bet_when_qualified(self):
        forecast = _forecast(totals_probabilities={8.5: {"over": 0.58, "under": 0.42}})
        over_market = _market(team_or_side="over", ask=0.50, depth_price=0.50, market_type="total", line=8.5)
        decision = decide_total(forecast, over_market, limits=SizeLimits(min_depth_units=0.0, unit_rounding=0.0))
        assert decision.action == "BET"
        assert decision.market_type == "total"

    def test_no_forecast_for_an_unlisted_line_fails_closed(self):
        forecast = _forecast(totals_probabilities={8.5: {"over": 0.58, "under": 0.42}})
        unlisted_line_market = _market(
            team_or_side="over", ask=0.50, depth_price=0.50, market_type="total", line=9.5,
        )
        decision = decide_total(forecast, unlisted_line_market)
        assert decision.action == "NO_BET"


class TestEveryCandidateGetsADecision:
    def test_no_bets_are_still_returned_not_dropped(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, away_prob=0.40)
        candidates = [
            _market(team_or_side="home", ask=0.70, depth_price=0.70),   # overpriced -> NO_BET
            _market(team_or_side="away", ask=0.20, depth_price=0.20),   # wrong side -> NO_BET
        ]
        decisions = evaluate_game(forecast, candidates)
        assert len(decisions) == 2
        assert all(d.action == "NO_BET" for d in decisions)


class TestRejectedMarketIdentityIsPreserved:
    """Real audit-trail gap fixed: NO_BET always set selected_market=None,
    so a report couldn't show *which* market/side/line/ask was actually
    rejected — only that something was, with every field showing null.
    evaluated_market preserves the exact candidate regardless of outcome.
    """

    def test_no_bet_still_records_which_market_was_evaluated(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        market = _market(team_or_side="home", ask=0.70, depth_price=0.70, market_type="moneyline")

        decision = decide_team_market(forecast, market)

        assert decision.action == "NO_BET"
        assert decision.selected_market is None
        assert decision.evaluated_market is market

    def test_bet_records_the_same_market_in_both_fields(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, lower_margin=0.03)
        market = _market(team_or_side="home", ask=0.52, depth_price=0.52, market_type="moneyline")

        decision = decide_team_market(forecast, market, limits=SizeLimits(min_depth_units=0.0, unit_rounding=0.0))

        assert decision.action == "BET"
        assert decision.evaluated_market is market
        assert decision.selected_market is market

    def test_wrong_side_rejection_still_records_the_rejected_candidate(self):
        forecast = _forecast(predicted_winner="home", home_prob=0.60, away_prob=0.40)
        away_market = _market(team_or_side="away", ask=0.20, depth_price=0.20)

        decision = decide_team_market(forecast, away_market)

        assert decision.action == "NO_BET"
        assert decision.evaluated_market is away_market
        assert decision.evaluated_market.team_or_side == "away"
        assert decision.evaluated_market.executable_ask == 0.20
