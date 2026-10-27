from datetime import UTC, date, datetime

import pytest

from model_prediction.models.nfl_market_anchor_v1 import (
    SettledQuote,
    asof_market_weight,
    market_anchored_probability,
)


def test_weight_uses_only_prior_complete_game_dates() -> None:
    history = [
        SettledQuote(date(2026, 10, 11), 0.7, 0.4, 0, datetime(2026, 10, 11, 23, tzinfo=UTC)),
        SettledQuote(date(2026, 10, 12), 0.7, 0.4, 1, datetime(2026, 10, 13, 4, tzinfo=UTC)),
    ]
    assert asof_market_weight(
        history, date(2026, 10, 12), datetime(2026, 10, 12, 3, tzinfo=UTC)
    ) == pytest.approx(1)
    assert asof_market_weight(
        history, date(2026, 10, 13), datetime(2026, 10, 13, 5, tzinfo=UTC)
    ) == pytest.approx(2 / 3)
    assert asof_market_weight(
        history, date(2026, 10, 13), datetime(2026, 10, 13, 3, tzinfo=UTC)
    ) == pytest.approx(1)
    assert market_anchored_probability(0.7, 0.4, 2 / 3) == pytest.approx(0.5)


def test_weight_fails_closed_without_prior_evidence() -> None:
    with pytest.raises(ValueError, match="no_prior"):
        asof_market_weight([], date(2026, 10, 11), datetime(2026, 10, 11, tzinfo=UTC))
    with pytest.raises(ValueError):
        market_anchored_probability(0.6, float("nan"), 0.5)
