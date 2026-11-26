"""The auto-buyer must gate edge on the price it actually sends.

The order goes out at ``limit_price = round(clob_ask, 2)``, up to half a cent above the quoted ask,
but the edge was computed as ``model_prob - clob_ask``. A pick whose edge cleared a window only
against the unrounded ask was bought at a price where it did not (2026-11-25 review).
"""

from __future__ import annotations

import pytest

from tests.test_auto_buyer_cohorts import (  # noqa: F401 -- fixtures used by name
    SPREAD_V7,
    FakeExecutor,
    _isolate,
    _live_buyer,
    _row,
    decorate,
)


@pytest.mark.usefixtures("decorate")
def test_edge_that_clears_the_window_only_against_the_unrounded_ask_is_not_bought(tmp_path):
    # Ask 49.51% goes out as a 50 cent limit: 54.60% - 49.51% = 5.09% clears the 5% floor,
    # 54.60% - 50% = 4.60% does not.
    row = _row(SPREAD_V7, pick_id="rounds-up", event="g1", edge="0.07", model_probability=0.546, ask=0.4951)

    result = _live_buyer(tmp_path).evaluate_and_execute([row])

    assert FakeExecutor.executed == []
    assert result.rejected_low_edge == 1


@pytest.mark.usefixtures("decorate")
def test_edge_above_the_window_at_the_sent_price_is_not_bought(tmp_path):
    # Ask 40.49% goes out as a 40 cent limit: 11.96% is inside the [5%, 12%) window, 12.45% is not.
    row = _row(
        SPREAD_V7, pick_id="rounds-down", event="g1", edge="0.07", model_probability=0.5245, ask=0.4049
    )

    result = _live_buyer(tmp_path).evaluate_and_execute([row])

    assert FakeExecutor.executed == []
    assert result.rejected_unrealistic_edge == 1


@pytest.mark.usefixtures("decorate")
def test_recorded_edge_is_against_the_limit_price(tmp_path):
    row = _row(SPREAD_V7, pick_id="ok", event="g1", edge="0.07", model_probability=0.58, ask=0.5051)

    result = _live_buyer(tmp_path).evaluate_and_execute([row])

    ((ticket, _),) = FakeExecutor.executed
    assert ticket.price == pytest.approx(0.51)
    assert result.submitted_orders[0]["edge"] == pytest.approx(0.07)
