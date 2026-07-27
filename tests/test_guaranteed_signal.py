from datetime import UTC, datetime

from model_prediction.features.guaranteed_signal import SignalInputs, evaluate_high_confidence

_BASE_KWARGS = {
    "edge_vs_executable_ask": 0.10,
    "momentum_percentile": 0.97,
    "bid_ask_spread": 0.02,
    "injury_news_clear_last_24h": True,
    "team_banned": False,
    "historical_observations_for_market": 40,
    "passed_standard_gates": True,
}


def test_future_observed_at_utc_fails_freshness_check() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    inputs = SignalInputs(
        observed_at_utc="2026-07-27T13:00:00Z",  # one hour in the future
        **_BASE_KWARGS,
    )

    result = evaluate_high_confidence(inputs, now=now)

    assert result["checks"]["data_fresh_under_6h"] is False
    assert result["qualifies"] is False


def test_recent_past_observed_at_utc_passes_freshness_check() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    inputs = SignalInputs(
        observed_at_utc="2026-07-27T11:00:00Z",  # one hour ago
        **_BASE_KWARGS,
    )

    result = evaluate_high_confidence(inputs, now=now)

    assert result["checks"]["data_fresh_under_6h"] is True
    assert result["qualifies"] is True
