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
    SPREAD_V7_LIVE,
    TOTAL_V6,
    TOTAL_V6_LIVE,
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


def _settle_rows(tmp_path: Path, rows: list[dict[str, Any]], espn: Any = None) -> dict[str, Any]:
    from model_prediction.portfolio.auto_buyer_ledger import settle_auto_buyer_ledger

    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return settle_auto_buyer_ledger(data_root=tmp_path, espn=espn if espn is not None else MagicMock())


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
    espn = MagicMock()
    out = _settle_rows(tmp_path, [row], espn=espn)
    assert out["corrected"] == 0
    # The re-verification must be skipped, not merely fail to change the row.
    espn.scoreboard.assert_not_called()
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
    # Control: the same history extends a readable row, so the two Falses above are the stamp's doing.
    readable = {**pick, "created_at_utc": "2026-11-26T13:00:00Z"}
    assert auto_executor._OvernightAgeExtension([readable]).allows(readable) is True


def test_an_open_sibling_with_an_unreadable_created_at_blocks_the_slots_extension(monkeypatch):
    """The unreadable sibling may be the newest row of its slot, so the readable one cannot be shown to
    be the freshest pick; the extension is a relaxation and fails closed."""
    runs = [{"started_at_utc": "2026-11-26T12:00:00Z", "status": "ok"}]
    monkeypatch.setattr(auto_executor, "_read_daily_run_history", lambda: runs)
    slot = {"status": "open", "model_id": "m", "event_id": "e", "market_type": "spread"}
    readable = {**slot, "pick_id": "a", "created_at_utc": "2026-11-26T13:00:00Z"}
    broken = {**slot, "pick_id": "b", "created_at_utc": _OVERFLOW_START}
    assert auto_executor._OvernightAgeExtension([readable]).allows(readable) is True
    assert auto_executor._OvernightAgeExtension([readable, broken]).allows(readable) is False
    other_slot = {**readable, "event_id": "other"}
    assert auto_executor._OvernightAgeExtension([other_slot, broken]).allows(other_slot) is True
    # An ABSENT stamp (blank or missing) is not an unreadable one: it never took the extension away.
    for absent in ({**slot, "pick_id": "c", "created_at_utc": ""}, {**slot, "pick_id": "d"}):
        assert auto_executor._OvernightAgeExtension([readable, absent]).allows(readable) is True


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
    info = auto_executor._get_quarantined_underperforming_models(tmp_path)["m"]
    # All 16 rows, the unreadable-stamp one included, are in the sample.
    assert info["sample"] == 16 and info["pnl_usd"] == -160.0


def test_quarantine_keeps_an_unreadable_stamp_row_in_the_recent_sample(tmp_path):
    """A row with an unreadable settlement stamp sorted OLDEST, so it was the first one dropped from the
    recent-30 window and a losing row could vanish from the ROI the quarantine reads. It sorts newest."""

    def settled(stamp: str, pnl: float) -> dict[str, Any]:
        return {
            "model_id": "m",
            "status": "settled",
            "result": "win" if pnl > 0 else "loss",
            "cost_usd": 10.0,
            "pnl_usd": pnl,
            "settled_at_utc": stamp,
        }

    rows = [settled(f"2026-11-{day:02d}T12:00:00Z", 10.0) for day in range(1, 26)]
    rows += [settled(f"2026-11-26T{hour:02d}:00:00Z", 10.0) for hour in range(5)]
    rows.append(settled(_OVERFLOW_START, -10_000.0))
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", rows)
    info = auto_executor._get_quarantined_underperforming_models(tmp_path)["m"]
    assert info["sample"] == 30 and info["pnl_usd"] == 290.0 - 10_000.0


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

    settle_calls: list[Any] = []

    def _settle(data_root=None, **_kwargs):
        if data_root is not None:
            settle_calls.append(data_root)
            raise OverflowError("date value out of range")
        return {}

    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", _settle)
    summary = auto_executor.run_auto_buyer_cycle()  # warns and carries on
    assert settle_calls, "the paper settlement never ran, so the overflow was not exercised"
    assert (
        summary["cohort_passes"]["paper"]["orders_count"] >= 1
    )  # the pass after the failed settle still ran


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


# ----------------------------------------------------------------------------- B6 (non-finite dollars)
# A NaN pnl or cost made min(nan, 0.0) / `nan + cost > cap` False, which switched the loss stop or the
# spend cap off for the day; garbage text skipped a loss row or raised out of the totals. All of them now
# count at a cap, in the direction that can only trip a stop earlier.

_NON_FINITE = [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param(float("-inf"), id="-inf"),
    pytest.param("abc", id="garbage-text"),
    pytest.param(10**400, id="overflowing-int"),
    pytest.param({"v": 1}, id="dict"),
]


def _settled_today(pnl: Any, **extra: Any) -> dict[str, Any]:
    return _today_row(
        pick_id="bad-pnl",
        executed_at_utc="",
        cost_usd=0.0,
        result="loss",
        pnl_usd=pnl,
        event_start_utc=iso_utc(utc_now()),
        settled_at_utc=iso_utc(utc_now()),
        **extra,
    )


@pytest.mark.parametrize("bad", _NON_FINITE)
@pytest.mark.parametrize("live_book", [True, False])
def test_a_non_finite_pnl_on_a_settled_row_counts_as_a_loss_at_the_cap(tmp_path, bad, live_book):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_settled_today(bad)])
    named: list[str] = []
    _spend, pnl = _REAL_TODAY_TOTALS(
        tmp_path, live_book=live_book, unreadable_loss_usd=25.0, unreadable_rows=named
    )
    assert pnl == -25.0
    assert named == ["bad-pnl (pnl_usd)"]
    # No cap known (a display caller): refuse outright rather than guess a number.
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=live_book)[1] == float("-inf")


def test_a_readable_pnl_beside_an_unreadable_one_still_sums_exactly(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_settled_today(float("nan")), {**_settled_today(-7.0), "pick_id": "ok-loss"}],
    )
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=True, unreadable_loss_usd=25.0)[1] == -32.0
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=False, unreadable_loss_usd=25.0)[1] == -32.0


@pytest.mark.parametrize("bad", _NON_FINITE)
def test_a_non_finite_cost_on_todays_row_counts_as_spend_at_the_daily_cap(tmp_path, bad):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl", [_today_row(pick_id="bad-cost", status="open", cost_usd=bad)]
    )
    named: list[str] = []
    spend, _pnl = _REAL_TODAY_TOTALS(
        tmp_path, live_book=True, unreadable_spend_usd=500.0, unreadable_rows=named
    )
    assert spend == 500.0
    assert named == ["bad-cost (cost_usd)"]
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=True)[0] == float("inf")


def test_an_absent_or_zero_amount_is_still_the_zero_it_always_was(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(pick_id="a", status="open", cost_usd=None),
            _today_row(pick_id="b", status="open", cost_usd=""),
            {**_settled_today(None), "pick_id": "c"},
            {**_settled_today(""), "pick_id": "d"},
        ],
    )
    named: list[str] = []
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=True, unreadable_rows=named) == (0.0, 0.0)
    assert named == []


@pytest.mark.usefixtures("decorate")
@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_a_buy_is_refused_when_a_settled_loss_has_no_readable_pnl_and_the_row_is_named(
    monkeypatch, tmp_path, bad
):
    """The loss stop must not switch off for the day on a NaN: the row counts at the per-game cap and
    the breaker's skip record names it, so Telegram does not show a bare rejected_daily_loss_breaker."""
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_settled_today(bad)])
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    result = _live_buyer(tmp_path, max_daily_loss_usd=20.0).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_daily_loss_breaker == 1
    (record,) = result.cohort_skips
    assert record["reason"] == "rejected_daily_loss_breaker"
    assert "bad-pnl (pnl_usd)" in record["detail"]


@pytest.mark.usefixtures("decorate")
@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_a_buy_is_refused_when_a_row_of_today_has_no_readable_cost_and_the_row_is_named(
    monkeypatch, tmp_path, bad
):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl", [_today_row(pick_id="bad-cost", status="open", cost_usd=bad)]
    )
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_budget == 1
    (record,) = result.cohort_skips
    assert "bad-cost (cost_usd)" in record["detail"]


@pytest.mark.usefixtures("decorate")
def test_a_readable_ledger_still_buys_and_names_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_today_row(pick_id="fine", status="open", cost_usd=10.0), _settled_today(-3.0)],
    )
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    result = _live_buyer(tmp_path, max_daily_loss_usd=20.0).evaluate_and_execute([row])
    assert [t.pick_id for t, _ in FakeExecutor.executed] == ["in"]
    assert result.rejected_daily_loss_breaker == 0 and result.rejected_budget == 0


@pytest.mark.usefixtures("decorate")
def test_a_non_finite_total_from_any_source_trips_the_breaker_and_the_budget_check(monkeypatch, tmp_path):
    """Defence in depth: the comparisons themselves must not read NaN as 'within the limit'."""
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", lambda *_a, **_k: (0.0, float("nan")))
    result = _live_buyer(tmp_path, max_daily_loss_usd=50.0).evaluate_and_execute([row])
    assert FakeExecutor.executed == [] and result.rejected_daily_loss_breaker == 1
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", lambda *_a, **_k: (float("nan"), 0.0))
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == [] and result.rejected_budget == 1


def _held_spread_on_g1(**fields: Any) -> dict[str, Any]:
    """An open live spread on game g1, executed long ago (or never stamped), so the daily spend ignores it
    and only the per-game exposure scan can see it."""
    return {
        "pick_id": "held",
        "event_id": "g1",
        "selection": "home",
        "market_type": "spread",
        "status": "open",
        "order_id": "ioc-held",
        "order_state": "ORDER_STATE_FILLED",
        "fill_known": True,
        "shares": 20.0,
        "entry_price": 0.5,
        "executed_at_utc": "",
        "market_slug": "asc-cbb-g1-2026-11-25-neg-3pt5",
        "token_side": "long",
        **fields,
    }


@pytest.mark.usefixtures("decorate")
@pytest.mark.parametrize(
    ("held_cost", "buys"),
    [(1.0, True), (25.0, False), (float("nan"), False), ("abc", False), (10**400, False)],
    ids=[
        "readable-small-buys",
        "readable-at-cap-refused",
        "nan-refused",
        "garbage-refused",
        "overflow-refused",
    ],
)
def test_a_game_whose_held_row_has_no_readable_cost_reads_as_full_not_as_empty(tmp_path, held_cost, buys):
    """The per-game exposure scan read a NaN cost as `nan + pending > 0` False and dropped the game from
    the cap, so a second market on the same game bought past it; garbage text or an overflowing int raised
    out of the whole pass. The row now counts at the per-game cap. (The held row is not stamped, so the
    DAILY spend cannot be what refuses the candidate.)"""
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_held_spread_on_g1(cost_usd=held_cost)])
    more = _row(TOTAL_V6, pick_id="t1", event="g1", market="total", model_probability=0.57, ask=0.51)
    buyer = _live_buyer(tmp_path, cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE))
    result = buyer.evaluate_and_execute([more])
    assert bool(FakeExecutor.executed) is buys
    assert result.rejected_budget == (0 if buys else 1)


def test_the_dashboard_drawdown_alert_counts_unreadable_rows_at_the_states_own_caps(monkeypatch):
    """The alert must show the loss the buyer counts (a NaN row at the per-game cap), not infinity."""
    dashboard_status = importlib.import_module(
        "model_prediction.dashboard.status"
    )  # the package re-exports a function of that name

    seen: dict[str, Any] = {}

    def _totals_stub(*_args: Any, **kwargs: Any) -> tuple[float, float]:
        seen.update(kwargs)
        return 0.0, -kwargs["unreadable_loss_usd"]

    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _totals_stub)
    monkeypatch.setattr(
        auto_executor,
        "load_auto_buyer_state",
        lambda: {"max_daily_spend_usd": 500.0, "max_game_stake_usd": 25.0, "max_daily_loss_usd": 40.0},
    )
    alert = dashboard_status._auto_buyer_daily_loss_alert()
    assert seen == {"unreadable_spend_usd": 500.0, "unreadable_loss_usd": 25.0}
    assert alert is not None and alert["level"] == "warn" and "$-25.00" in alert["text"]


# ----------------------------------------------------------------------------- B6 review: pending exposure
# An order that may be live must never read as 0 because a field is NaN, garbage or overflowing: that let
# the buyer buy MORE (daily spend and per-game cap under-counted) or raised OverflowError out of the scan.

_OPEN = {"status": "open", "fallback_reconciled": False}
_EXPOSURE_ROWS = {
    "submit_unknown": (
        {**_OPEN, "submit_unknown": True, "requested_cost_usd": 7.5, "entry_price": 0.5},
        ["requested_cost_usd"],
        7.5,
    ),
    "fallback_unknown": (
        {**_OPEN, "fallback_submit_unknown": True, "fallback_resting_shares": 10.0, "entry_price": 0.5},
        ["fallback_resting_shares", "entry_price"],
        5.0,
    ),
    "own_primary": (
        {
            **_OPEN,
            "order_id": "a",
            "fallback_order_id": "a",
            "fallback_resting_shares": 10.0,
            "shares": 4.0,
            "entry_price": 0.5,
        },
        ["fallback_resting_shares", "shares", "entry_price"],
        3.0,
    ),
    "resting_fallback": (
        {
            **_OPEN,
            "order_id": "a",
            "fallback_order_id": "b",
            "fallback_resting_shares": 10.0,
            "fallback_filled_shares": 4.0,
            "entry_price": 0.5,
        },
        ["fallback_resting_shares", "fallback_filled_shares", "entry_price"],
        3.0,
    ),
}
_BAD_AMOUNTS = [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param("abc", id="garbage"),
    pytest.param(10**400, id="overflow"),
    pytest.param({"v": 1}, id="dict"),
]


@pytest.mark.parametrize("kind", list(_EXPOSURE_ROWS))
def test_a_readable_pending_row_costs_exactly_what_it_always_did(kind):
    row, _fields, expected = _EXPOSURE_ROWS[kind]
    assert auto_executor._pending_exposure(row, unreadable_usd=25.0) == (expected, False)
    assert auto_executor._pending_exposure_cost(row) == expected
    # A settled row with no pending order costs nothing, whatever its other fields hold.
    done = {"status": "settled", "fallback_reconciled": True, "entry_price": "abc", "shares": float("nan")}
    assert auto_executor._pending_exposure(done, unreadable_usd=25.0) == (0.0, False)


@pytest.mark.parametrize("bad", _BAD_AMOUNTS)
@pytest.mark.parametrize(
    ("kind", "field"),
    [(kind, field) for kind, (_row, fields, _cost) in _EXPOSURE_ROWS.items() for field in fields],
)
def test_an_unreadable_pending_field_counts_at_the_cap_not_as_zero(kind, field, bad):
    row = {**_EXPOSURE_ROWS[kind][0], field: bad}
    assert auto_executor._pending_exposure(row, unreadable_usd=25.0) == (25.0, True)
    assert auto_executor._pending_exposure_cost(row) == float("inf")  # no cap known: refuse outright
    # And the fee-inclusive form agrees.
    assert auto_executor._pending_exposure(row, with_fee=True, unreadable_usd=25.0) == (25.0, True)


@pytest.mark.parametrize("kind", list(_EXPOSURE_ROWS))
def test_an_absent_pending_field_is_still_zero(kind):
    row, fields, _cost = _EXPOSURE_ROWS[kind]
    for field in fields:
        value, unreadable = auto_executor._pending_exposure({**row, field: None}, unreadable_usd=25.0)
        assert unreadable is False and value >= 0.0


@pytest.mark.parametrize("bad", _BAD_AMOUNTS)
def test_the_daily_spend_counts_an_unreadable_pending_row_at_the_cap_and_names_it(tmp_path, bad):
    row = _today_row(
        pick_id="pend",
        order_id="a",
        fallback_order_id="a",
        fallback_reconciled=False,
        status="open",
        cost_usd=5.0,
        shares=4.0,
        entry_price=0.5,
        fallback_resting_shares=bad,
    )
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [row])
    named: list[str] = []
    spend, _pnl = _REAL_TODAY_TOTALS(
        tmp_path, live_book=True, unreadable_spend_usd=500.0, unreadable_rows=named
    )
    assert spend >= 500.0
    assert named == ["pend (pending_exposure)"]


@pytest.mark.usefixtures("decorate")
@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_a_game_with_an_unreadable_pending_order_reads_as_full_and_the_refusal_names_the_row(tmp_path, bad):
    held = _held_spread_on_g1(
        cost_usd=1.0,
        order_id="a",
        fallback_order_id="a",
        fallback_reconciled=False,
        fill_known=False,
        entry_price=0.5,
        fallback_resting_shares=bad,
    )
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [held])
    more = _row(TOTAL_V6, pick_id="t1", event="g1", market="total", model_probability=0.57, ask=0.51)
    result = _live_buyer(tmp_path, cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE)).evaluate_and_execute([more])
    assert FakeExecutor.executed == []
    assert result.rejected_budget == 1
    (record,) = result.cohort_skips
    assert "held (pending_exposure)" in record["detail"]


@pytest.mark.usefixtures("decorate")
@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_a_game_cap_refusal_names_a_row_whose_cost_is_unreadable(tmp_path, bad):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_held_spread_on_g1(cost_usd=bad)])
    more = _row(TOTAL_V6, pick_id="t1", event="g1", market="total", model_probability=0.57, ask=0.51)
    result = _live_buyer(tmp_path, cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE)).evaluate_and_execute([more])
    assert FakeExecutor.executed == []
    (record,) = result.cohort_skips
    assert "held (cost_usd)" in record["detail"]


@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_an_unreadable_share_count_locks_the_row_instead_of_raising_or_reading_as_dead(tmp_path, bad):
    """OverflowError escaped the scan raw; NaN or garbage must not let a row that may hold shares read as a
    dead one or as a partial fill to top up."""
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_held_spread_on_g1(cost_usd=1.0, shares=bad)])
    index = _live_buyer(tmp_path)._build_bought_index()
    assert "held" in index["pick_ids"] and "held" not in index["partially_filled"]


@pytest.mark.parametrize("bad", [float("nan"), "abc", 10**400])
def test_an_unreadable_manual_exit_count_is_a_sale_lockout_not_a_top_up(tmp_path, bad):
    held = _held_spread_on_g1(
        cost_usd=10.0, order_state="ORDER_STATE_PARTIALLY_FILLED", fallback_reconciled=True
    )
    _write_ledger(tmp_path / "control" / "auto_buyer_ledger.jsonl", [held])
    assert "held" in _live_buyer(tmp_path / "control")._build_bought_index()["partially_filled"]  # control
    _write_ledger(tmp_path / "bad" / "auto_buyer_ledger.jsonl", [{**held, "manual_exit_shares": bad}])
    index = _live_buyer(tmp_path / "bad")._build_bought_index()
    assert "held" in index["pick_ids"] and "held" not in index["partially_filled"]


def test_the_row_fee_survives_an_overflowing_value_as_zero():
    from model_prediction.portfolio.auto_buyer_ledger import row_fee_usd

    assert row_fee_usd({"fee_usd": 10**400}) == 0.0
    assert row_fee_usd({"fee_usd": 0.07}) == 0.07


@pytest.mark.usefixtures("decorate")
def test_the_breaker_detail_names_only_the_loss_rows_not_the_spend_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(pick_id="spend-bad", status="open", cost_usd="abc"),
            {**_settled_today(float("nan")), "pick_id": "loss-bad"},
        ],
    )
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    result = _live_buyer(tmp_path, max_daily_loss_usd=20.0).evaluate_and_execute([row])
    (record,) = result.cohort_skips
    assert record["reason"] == "rejected_daily_loss_breaker"
    assert "loss-bad (pnl_usd)" in record["detail"] and "spend-bad" not in record["detail"]


def test_a_pending_cost_that_overflows_from_readable_inputs_counts_at_the_cap():
    row = {**_EXPOSURE_ROWS["fallback_unknown"][0], "fallback_resting_shares": 1e308, "entry_price": 10.0}
    assert auto_executor._pending_exposure(row, unreadable_usd=25.0) == (25.0, True)


@pytest.mark.usefixtures("decorate")
def test_the_budget_detail_names_only_the_spend_rows_not_the_loss_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _today_row(pick_id="spend-bad", status="open", cost_usd="abc"),
            {**_settled_today(float("nan")), "pick_id": "loss-bad"},
        ],
    )
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert result.rejected_budget == 1
    (record,) = result.cohort_skips
    assert "spend-bad (cost_usd)" in record["detail"] and "loss-bad" not in record["detail"]


def test_an_overflow_inside_the_ledger_scan_fails_the_index_closed_not_raw(monkeypatch, tmp_path):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_held_spread_on_g1(cost_usd=1.0)])

    def _boom(_row: dict[str, Any]) -> str:
        raise OverflowError("int too large to convert to float")

    monkeypatch.setattr(auto_executor, "_game_key", _boom)
    with pytest.raises(auto_executor.BoughtIndexUnavailableError):
        _live_buyer(tmp_path)._build_bought_index()
