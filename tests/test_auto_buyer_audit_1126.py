"""Audit 2026-11-26, batch B3: auto_executor fixes found by the real-money review.

RM-2   the ticket-to-row binding is fed the row's own explicit ``market_slug=`` token (the old
       ``market_slug:{slug}`` text never matched the executor's extractor, so nothing was bound);
RM-5   the live book's daily spend and loss stop count a row whose timestamps are present but unreadable;
RO-2   a paper pass re-reads the clock after its slow quote step and refuses a game that has started;
HY-1   a start time beyond datetime's range refuses instead of raising out of the pass.

Reuses the cohort harness (test_auto_buyer_cohorts): ``FakeExecutor`` never trades, the decorated quote
is a stub, and ``_isolate`` (autouse) keeps the exchange out of reach.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from test_auto_buyer_cohorts import (  # noqa: F401 -- _isolate is an autouse fixture, decorate a fixture
    _REAL_TODAY_TOTALS,
    SPREAD_V7,
    FakeExecutor,
    _isolate,
    _live_buyer,
    _quote_from_row,
    _row,
    _today_row,
    _write_ledger,
    decorate,
)

from model_prediction.audit import AuditLog
from model_prediction.data_sources import polymarket_execute as pe
from model_prediction.domain import iso_utc, parse_utc, utc_now
from model_prediction.portfolio import auto_executor
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    BuyCohort,
    _tip_time_refusal,
)

SLUG = "asc-cbb-g1-2026-11-25-neg-3pt5"
OTHER_SLUG = "asc-cbb-g1-2026-11-25-neg-6pt5"


def _real_executor_with_fake_credentials(monkeypatch) -> list[Any]:
    """The real PolymarketExecutor with made-up credentials; any HTTP it attempts is recorded."""
    sent: list[Any] = []

    def _no_network(*args: Any, **kwargs: Any) -> None:
        sent.append((args, kwargs))
        raise AssertionError("the gate must refuse before any request leaves the process")

    monkeypatch.setattr(pe.httpx, "request", _no_network)
    real = pe.PolymarketExecutor

    def _factory(**kwargs: Any) -> pe.PolymarketExecutor:
        return real(environ={"POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "A" * 43 + "="}, **kwargs)

    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _factory)
    return sent


# ----------------------------------------------------------------------------- RM-2


@pytest.mark.usefixtures("decorate")
def test_a_pinned_row_binds_its_recorded_slug_into_the_ticket_row(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1", rationale=f"edge 7%; market_slug={SLUG} token_side=long")
    assert row["market_slug"] == SLUG
    _live_buyer(tmp_path).evaluate_and_execute([row])
    assert len(FakeExecutor.executed) == 1
    _ticket, captured = FakeExecutor.executed[0]
    assert pe._extract_market_slug(captured["rationale"]) == SLUG


@pytest.mark.usefixtures("decorate")
def test_a_live_quote_on_another_slug_than_the_recorded_one_is_a_gate_refusal(monkeypatch, tmp_path):
    sent = _real_executor_with_fake_credentials(monkeypatch)
    row = _row(
        SPREAD_V7,
        pick_id="p",
        event="g1",
        rationale=f"edge 7%; market_slug={OTHER_SLUG} token_side=long",
    )
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert sent == []
    assert [o["status"] for o in result.submitted_orders] == ["refused"]
    assert "actually priced against" in result.submitted_orders[0]["error"]
    assert OTHER_SLUG in result.submitted_orders[0]["error"]


@pytest.mark.usefixtures("decorate")
def test_a_row_with_no_slug_token_is_not_bound_and_still_executes(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1", rationale=f"edge 7% ask 0.51 ({SLUG})")
    _live_buyer(tmp_path).evaluate_and_execute([row])
    assert len(FakeExecutor.executed) == 1
    _ticket, captured = FakeExecutor.executed[0]
    # A legacy "(slug)" form is deliberately not bound: its suffixes are not contracts.
    assert pe._extract_market_slug(captured["rationale"]) is None


# ----------------------------------------------------------------------------- RM-5 (spend)


def _totals(root, *, live_book: bool) -> tuple[float, float]:
    return _REAL_TODAY_TOTALS(root, live_book=live_book)


def test_live_spend_counts_a_row_whose_execution_time_is_present_but_unreadable(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(status="open", executed_at_utc="garbage", cost_usd=10.0)],
    )
    assert _totals(tmp_path, live_book=True)[0] >= 10.0
    assert _totals(tmp_path, live_book=False)[0] == 0.0


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_execution_time_is_still_not_counted(tmp_path, blank):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(status="open", executed_at_utc=blank, cost_usd=10.0)],
    )
    assert _totals(tmp_path, live_book=True)[0] == 0.0


def test_a_naive_execution_time_counts_on_the_live_book(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(status="open", executed_at_utc="2026-11-26T10:00:00", cost_usd=10.0)],
    )
    assert _totals(tmp_path, live_book=True)[0] >= 10.0


def test_an_out_of_range_execution_time_counts_and_does_not_raise(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(status="open", executed_at_utc="0001-01-01T00:00:00+05:00", cost_usd=10.0)],
    )
    assert _totals(tmp_path, live_book=True)[0] >= 10.0
    assert _totals(tmp_path, live_book=False)[0] == 0.0


def test_a_settled_live_loss_with_both_dates_unreadable_counts_today(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(
                executed_at_utc="",
                cost_usd=0.0,
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc="garbage",
                event_start_utc="also garbage",
            )
        ],
    )
    assert _totals(tmp_path, live_book=True)[1] == -7.0
    assert _totals(tmp_path, live_book=False)[1] == 0.0


def test_a_settled_row_with_a_readable_start_on_another_day_is_not_today(tmp_path):
    yesterday = iso_utc(utc_now() - timedelta(days=2))
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(
                executed_at_utc="",
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc="garbage",
                event_start_utc=yesterday,
            )
        ],
    )
    assert _totals(tmp_path, live_book=True)[1] == 0.0


# ----------------------------------------------------------------------------- RO-2


def _paper_buyer(tmp_path) -> AutoPolymarketBuyer:
    cohort = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "paper")
    return AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            mode="paper",
            cohorts=(cohort,),
            whitelisted_models=(),
            disabled_sport_markets=(("ncaab", "spread"),),
            capture_missing_slates=False,
            require_model_timestamp=True,
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )


class _AdvancingClock:
    """utc_now() that only moves when the quote step moves it, so the pass start is fixed and the
    recheck after the quote is later."""

    def __init__(self) -> None:
        self.current = utc_now()

    def __call__(self):
        return self.current


def _install_quote_that_advances(monkeypatch, clock: _AdvancingClock, to) -> None:
    def _decorate(row: dict[str, Any]) -> dict[str, Any]:
        clock.current = to  # the slow live-quote step: time passes while the pass is mid-row
        decorated = _quote_from_row(row)
        decorated["quote"]["observed_at_utc"] = iso_utc(to)
        return decorated

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _decorate)


def test_a_paper_row_whose_game_starts_during_the_quote_step_is_not_filled(monkeypatch, tmp_path):
    clock = _AdvancingClock()
    monkeypatch.setattr(auto_executor, "utc_now", clock)
    row = _row(SPREAD_V7, pick_id="p", event="g1", start_minutes=20)
    row["created_at_utc"] = iso_utc(clock.current - timedelta(minutes=1))
    row["event_start_utc"] = iso_utc(clock.current + timedelta(minutes=20))
    _install_quote_that_advances(monkeypatch, clock, clock.current + timedelta(minutes=21))
    result = _paper_buyer(tmp_path).evaluate_and_execute([row])
    assert result.dry_run_orders == []
    assert result.rejected_started == 1
    assert result.rejected_stale_quote == 0


def test_the_same_paper_row_is_filled_when_the_game_has_not_started(monkeypatch, tmp_path):
    clock = _AdvancingClock()
    monkeypatch.setattr(auto_executor, "utc_now", clock)
    row = _row(SPREAD_V7, pick_id="p", event="g1", start_minutes=20)
    row["created_at_utc"] = iso_utc(clock.current - timedelta(minutes=1))
    row["event_start_utc"] = iso_utc(clock.current + timedelta(minutes=20))
    _install_quote_that_advances(monkeypatch, clock, clock.current + timedelta(minutes=5))
    result = _paper_buyer(tmp_path).evaluate_and_execute([row])
    assert [o["pick_id"] for o in result.dry_run_orders] == ["p"]
    assert result.rejected_started == 0


def test_a_paper_row_is_refused_on_the_exchanges_earlier_start(monkeypatch, tmp_path):
    clock = _AdvancingClock()
    monkeypatch.setattr(auto_executor, "utc_now", clock)
    start = clock.current
    row = _row(SPREAD_V7, pick_id="p", event="g1", start_minutes=20)
    row["created_at_utc"] = iso_utc(start - timedelta(minutes=1))
    row["event_start_utc"] = iso_utc(start + timedelta(minutes=20))

    def _decorate(candidate: dict[str, Any]) -> dict[str, Any]:
        clock.current = start + timedelta(minutes=5)
        decorated = _quote_from_row(candidate)
        # The exchange's own start is earlier than the row's and has passed by the recheck.
        decorated["quote"]["event_start_utc"] = iso_utc(start + timedelta(minutes=3))
        decorated["quote"]["observed_at_utc"] = iso_utc(clock.current)
        return decorated

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _decorate)
    result = _paper_buyer(tmp_path).evaluate_and_execute([row])
    assert result.dry_run_orders == []
    assert result.rejected_started == 1


def test_a_paper_row_with_no_exchange_start_is_still_filled(monkeypatch, tmp_path):
    clock = _AdvancingClock()
    monkeypatch.setattr(auto_executor, "utc_now", clock)
    row = _row(SPREAD_V7, pick_id="p", event="g1", start_minutes=20)
    row["created_at_utc"] = iso_utc(clock.current - timedelta(minutes=1))
    row["event_start_utc"] = iso_utc(clock.current + timedelta(minutes=20))

    def _decorate(candidate: dict[str, Any]) -> dict[str, Any]:
        decorated = _quote_from_row(candidate)
        decorated["quote"]["event_start_utc"] = None
        return decorated

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _decorate)
    result = _paper_buyer(tmp_path).evaluate_and_execute([row])
    assert [o["pick_id"] for o in result.dry_run_orders] == ["p"]


# ----------------------------------------------------------------------------- HY-1


def test_an_out_of_range_exchange_start_refuses_a_live_buy_instead_of_raising():
    now = utc_now()
    refusal = _tip_time_refusal(now + timedelta(minutes=20), "0001-01-01T00:00:00+05:00", now)
    assert isinstance(refusal, str)
    assert "no event start" in refusal


@pytest.mark.usefixtures("decorate")
def test_an_out_of_range_row_start_is_counted_started_not_raised(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1")
    row["event_start_utc"] = "0001-01-01T00:00:00+05:00"
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_started == 1


def test_the_slate_capture_pre_filter_skips_an_out_of_range_start(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1")
    row["event_start_utc"] = "0001-01-01T00:00:00+05:00"

    class _NoClient:
        def sport_slate(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("a row with no readable start captures nothing")

    config = _live_buyer(tmp_path).config
    assert (
        auto_executor._capture_missing_active_snapshot_slates(
            [row], config=config, now=utc_now(), data_root=tmp_path, client=_NoClient()
        )
        == []
    )


def test_the_run_date_helper_tolerates_an_out_of_range_timestamp():
    assert auto_executor._daily_clear_retires(
        "2026-11-26", "0001-01-01T00:00:00+05:00", iso_utc(parse_utc("2026-11-26T15:00:00Z"))
    )
