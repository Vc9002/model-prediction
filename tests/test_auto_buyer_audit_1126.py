"""Audit 2026-11-26, batch B3: auto_executor fixes found by the real-money review.

RM-2   the ticket-to-row binding is fed the row's own explicit ``market_slug=`` token (the old
       ``market_slug:{slug}`` text never matched the executor's extractor, so nothing was bound);
RM-5   the live book's daily spend and loss stop count a row whose timestamps are present but unreadable;
RO-2   a paper pass re-reads the clock after its slow quote step and refuses a game that has started;
HY-1   a start time beyond datetime's range refuses instead of raising out of the pass.

Reuses the cohort harness (test_auto_buyer_cohorts): ``FakeExecutor`` never trades, the decorated quote
is a stub, and ``_isolate`` (autouse) keeps the exchange out of reach.
B5x    an out-of-range timestamp (OverflowError, not ValueError) on the settle, reconcile and
       closing-price paths refuses or skips in the safe direction instead of ending the pass.
"""

from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from test_auto_buyer_cohorts import (  # noqa: F401 -- _isolate is an autouse fixture, decorate a fixture
    _REAL_TODAY_TOTALS,
    SPREAD_V7,
    FakeExecutor,
    _cycle_harness,
    _entry,
    _isolate,
    _live_buyer,
    _mixed_rows,
    _pending_fallback_row,
    _quote_from_row,
    _row,
    _state,
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


def test_the_executor_and_dashboard_slug_extractors_are_the_same_pattern():
    # Kept in sync by hand across two modules: a drift would bind one slug and display another.
    backtests = importlib.import_module("model_prediction.dashboard.backtests")
    assert auto_executor._RECORDED_SLUG_RE.pattern == backtests._RE_RECORDED_SLUG.pattern


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
    assert _totals(tmp_path, live_book=True)[0] == 10.0
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
    assert _totals(tmp_path, live_book=True)[0] == 10.0


def test_an_out_of_range_execution_time_counts_and_does_not_raise(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(status="open", executed_at_utc="0001-01-01T00:00:00+05:00", cost_usd=10.0)],
    )
    assert _totals(tmp_path, live_book=True)[0] == 10.0
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


@pytest.mark.parametrize("blank_start", ["", "   ", None])
def test_a_settled_live_loss_with_a_blank_start_and_unreadable_settlement_counts_today(tmp_path, blank_start):
    # The loss stop must not lose a real loss because the start is blank AND the settlement is corrupt.
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(
                executed_at_utc="",
                cost_usd=0.0,
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc="garbage",
                event_start_utc=blank_start,
            )
        ],
    )
    assert _totals(tmp_path, live_book=True)[1] == -7.0
    assert _totals(tmp_path, live_book=False)[1] == 0.0


def test_a_settled_live_loss_with_unreadable_start_and_a_readable_other_day_settlement_is_not_today(tmp_path):
    two_days_ago = iso_utc(utc_now() - timedelta(days=2))
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(
                executed_at_utc="",
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc=two_days_ago,
                event_start_utc="garbage",
            )
        ],
    )
    assert _totals(tmp_path, live_book=True)[1] == 0.0


def test_an_unreadable_row_counted_toward_the_live_book_is_named_in_a_warning(tmp_path, caplog):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(pick_id="spend-pick", status="open", executed_at_utc="garbage", cost_usd=10.0),
            _today_row(
                pick_id="loss-pick",
                executed_at_utc="",
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc="garbage",
                event_start_utc=None,
            ),
        ],
    )
    with caplog.at_level("WARNING", logger="model_prediction.auto_executor"):
        _totals(tmp_path, live_book=True)
    assert "spend-pick" in caplog.text
    assert "loss-pick" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING", logger="model_prediction.auto_executor"):
        _totals(tmp_path, live_book=False)
    assert caplog.text == ""


def test_a_settled_row_with_a_readable_start_on_another_day_is_not_today(tmp_path):
    two_days_ago = iso_utc(utc_now() - timedelta(days=2))
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(
                executed_at_utc="",
                result="loss",
                pnl_usd=-7.0,
                settled_at_utc="garbage",
                event_start_utc=two_days_ago,
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


# ----------------------------------------------------------------------------- B5x (OverflowError)

# parse_utc raises OverflowError (not ValueError) for these: a year-1 / year-9999 value with an offset.
_OVERFLOW_START = "0001-01-01T00:00:00+05:00"


def test_earliest_utc_skips_an_out_of_range_value():
    from model_prediction.portfolio import auto_buyer_ledger as ledger

    assert ledger._earliest_utc(_OVERFLOW_START, "2026-11-26T10:00:00Z") == "2026-11-26T10:00:00Z"
    assert ledger._earliest_utc(_OVERFLOW_START) is None


def _netting_row(**fields: Any) -> dict[str, Any]:
    return {
        "token_side": "long",
        "shares": 10.0,
        "entry_price": 0.5,
        "executed_at_utc": "2026-11-26T10:00:00Z",
        **fields,
    }


def test_offset_exits_ignore_an_out_of_range_entry_or_fill_time():
    from model_prediction.portfolio import auto_buyer_ledger as ledger

    fill = {
        "intent": ledger._OFFSET_INTENT["long"],
        "time": "2026-11-26T11:00:00Z",
        "shares": 4.0,
        "price": 0.6,
    }
    sentinel = {"shares_sold": 0.0, "marker": "unchanged"}
    # An unreadable entry time leaves the exit exactly as it was.
    assert (
        ledger._with_offset_exits(sentinel, [fill], _netting_row(executed_at_utc=_OVERFLOW_START)) is sentinel
    )
    # An unreadable fill time is skipped, a readable one still folds in.
    bad = {**fill, "time": _OVERFLOW_START}
    merged = ledger._with_offset_exits(None, [bad, fill], _netting_row())
    assert merged is not None and merged["shares_sold"] == 4.0
    assert ledger._with_offset_exits(None, [bad], _netting_row()) is None


def test_closing_probability_lookup_tolerates_an_out_of_range_start(tmp_path):
    from model_prediction.portfolio import auto_buyer_ledger as ledger

    for sport in ("NCAAB", "NBA"):
        assert (
            ledger._resolve_closing_probability_for_auto_buyer(
                tmp_path,
                sport,
                "asc-cbb-g1-2026-11-25-neg-3pt5",
                _OVERFLOW_START,
                "long",
                selection="home",
                home_team="Home",
                away_team="Away",
                decided_at_utc="2026-11-25T10:00:00Z",
            )
            is None
        )


def _settle_rows(tmp_path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from model_prediction.portfolio.auto_buyer_ledger import settle_auto_buyer_ledger

    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return settle_auto_buyer_ledger(data_root=tmp_path, espn=MagicMock())


def _open_ncaab_row(**fields: Any) -> dict[str, Any]:
    return {
        "order_id": "PAPER-x",
        "market_slug": "tsc-cbb-cencon-dart-2026-11-08-140pt5",
        "sport": "NCAAB",
        "market_type": "total",
        "line": 140.5,
        "selection": "over",
        "token_side": "long",
        "away_team": "Central Connecticut Blue Devils",
        "home_team": "Dartmouth Big Green",
        "event_start_utc": "2026-11-08T19:00:00Z",
        "executed_at_utc": "2026-11-08T18:40:00Z",
        "shares": 10.0,
        "entry_price": 0.50,
        "cost_usd": 5.0,
        "unit_value_usd": 5.0,
        "units": 1.0,
        "status": "open",
        "result": "open",
        "fill_known": True,
        **fields,
    }


def _first_ledger_row(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "auto_buyer_ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])


def test_settle_keeps_an_open_row_with_an_out_of_range_start_open(tmp_path):
    out = _settle_rows(tmp_path, [_open_ncaab_row(event_start_utc=_OVERFLOW_START)])
    assert out["newly_settled"] == 0
    row = _first_ledger_row(tmp_path)
    assert row["status"] == "open" and row["result"] == "open"


@pytest.mark.parametrize("bad_start", [_OVERFLOW_START, "not-a-time"])
def test_settle_skips_tennis_reverification_when_the_start_is_unreadable(tmp_path, bad_start):
    row = _open_ncaab_row(
        sport="TENNIS",
        market_slug="tsc-atp-a-b-2026-11-08",
        status="settled",
        result="win",
        pnl_usd=5.0,
        settled_at_utc="2026-11-08T22:00:00Z",
        event_start_utc=bad_start,
    )
    out = _settle_rows(tmp_path, [row])
    assert out["corrected"] == 0
    kept = _first_ledger_row(tmp_path)
    assert kept["status"] == "settled" and kept["result"] == "win"


@pytest.mark.parametrize("live", [True, False])
def test_precycle_overflow_in_reconcile_is_handled_like_any_reconcile_failure(monkeypatch, tmp_path, live):
    from model_prediction.portfolio import auto_buyer_ledger

    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_pending_fallback_row()])
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=live), _mixed_rows())
    monkeypatch.setattr(
        auto_buyer_ledger,
        "reconcile_pending_auto_buyer_fallbacks",
        lambda **_kwargs: (_ for _ in ()).throw(OverflowError("date value out of range")),
    )
    if live:
        with pytest.raises(RuntimeError, match="pre-cycle reconciliation failed"):
            auto_executor.run_auto_buyer_cycle()
    else:
        summary = auto_executor.run_auto_buyer_cycle()
        assert summary["reconciliation"]["status"] == "error"
    assert FakeExecutor.executed == []


def test_overnight_age_extension_treats_an_out_of_range_stamp_as_no_extension(monkeypatch):
    pick = {
        "status": "open",
        "model_id": "m",
        "event_id": "e",
        "market_type": "spread",
        "created_at_utc": _OVERFLOW_START,
    }
    runs = [{"started_at_utc": _OVERFLOW_START, "status": "ok"}]
    monkeypatch.setattr(auto_executor, "_read_daily_run_history", lambda: runs)
    assert auto_executor._OvernightAgeExtension([pick]).allows(pick) is False
    runs[:] = [{"started_at_utc": "2026-11-26T12:00:00Z", "status": "ok"}]
    assert auto_executor._OvernightAgeExtension([pick]).allows(pick) is False


def test_quarantine_sorts_past_a_row_with_an_out_of_range_settlement_stamp(tmp_path):
    def settled(stamp: str) -> dict[str, Any]:
        return {
            "model_id": "m",
            "status": "settled",
            "result": "loss",
            "cost_usd": 10.0,
            "pnl_usd": -10.0,
            "settled_at_utc": stamp,
        }

    rows = [settled(f"2026-11-2{day % 6}T1{day % 10}:00:00Z") for day in range(14)]
    rows.append(settled(_OVERFLOW_START))
    rows.append(settled("2026-11-26T09:00:00Z"))
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", rows)
    assert "m" in auto_executor._get_quarantined_underperforming_models(tmp_path)


@pytest.mark.usefixtures("decorate")
def test_an_out_of_range_model_timestamp_is_a_stale_model_not_a_crash(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1")
    row["created_at_utc"] = _OVERFLOW_START
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_stale_model == 1


@pytest.mark.usefixtures("decorate")
def test_an_out_of_range_source_quote_stamp_is_a_stale_quote_not_a_crash(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1")
    row["created_at_utc"] = iso_utc(utc_now() - timedelta(minutes=1))
    row["market_quote_observed_at_utc"] = _OVERFLOW_START
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_stale_quote == 1


def test_an_out_of_range_live_refresh_stamp_leaves_the_quote_stale_not_a_crash(tmp_path, monkeypatch):
    stale = {
        "quote": {
            "market_slug": "aec-example",
            "side": "long",
            "ask": 0.50,
            "fresh": False,
            "age_seconds": 600,
            "market_state": "MARKET_STATE_OPEN",
        }
    }
    live = {"observed_at_utc": _OVERFLOW_START, "market_state": "MARKET_STATE_OPEN", "long": {"ask": 0.40}}
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", lambda _row: stale)

    class _LiveClient:
        def snapshot(self, _slug: str) -> dict[str, Any]:
            return live

    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", _LiveClient)
    pick = {
        "pick_id": "stale-refresh",
        "model_id": "wnba-elo-trend-lr-v4",
        "status": "open",
        "market_type": "moneyline",
        "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
    }
    buyer = AutoPolymarketBuyer(data_root=tmp_path)
    result = buyer.evaluate_and_execute([pick])
    assert result.rejected_stale_quote == 1
    assert result.dry_run_orders == []


def test_precycle_overflow_in_paper_settlement_does_not_end_the_cycle(monkeypatch, tmp_path):
    from model_prediction.portfolio import auto_buyer_ledger

    _cycle_harness(monkeypatch, tmp_path, _state([_entry(mode="paper")], enabled=True), _mixed_rows())

    def _settle(data_root=None, **_kwargs):
        if data_root is not None:
            raise OverflowError("date value out of range")
        return {}

    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", _settle)
    auto_executor.run_auto_buyer_cycle()  # warns and carries on


def test_cli_settle_survives_an_overflow_in_the_auto_buyer_settlement(monkeypatch, tmp_path):
    from model_prediction.cli import settle as settle_cli
    from model_prediction.portfolio import auto_buyer_ledger

    @contextmanager
    def _hold(*_a: Any, **_k: Any):
        yield object()

    monkeypatch.setattr("model_prediction.auto_buyer_lock.hold_auto_buyer_lock", _hold)
    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda *_a, **_k: {})

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise OverflowError("date value out of range")

    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", _boom)
    out = settle_cli._settle_auto_buyer_books(tmp_path)
    assert out == {"auto_buyer_fallback_reconciliation": {}}
