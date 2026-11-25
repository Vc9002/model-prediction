"""Scheduled-daily NCAAB restore wiring (quick task 261115-1m3, item 4).

The daily job clears every not-yet-started open row before re-forecasting; a pick the refresh
does not replace used to vanish because only ``run_forecast`` restored (24 flat event-markets).
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest

from model_prediction.domain import League, MarketType, ModelOrigin, PickRequest, iso_utc
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import DuplicatePickError
from model_prediction.main_ledgers import MultiSportPickLedger


def _ncaab_request(
    event_id="g1", selection="home", league=League.NCAAB, market=MarketType.MONEYLINE
) -> tuple[PickRequest, EligibilityResult]:
    request = PickRequest(
        event_start_utc="2099-01-01T00:00:00Z",
        event_id=event_id,
        league=league,
        away_team="Away",
        home_team="Home",
        market_type=market,
        selection="over" if market is MarketType.TOTAL else selection,
        line={MarketType.SPREAD: -3.5, MarketType.TOTAL: 145.5}.get(market),
        sportsbook="polymarket_us_indicative",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="ncaab-spread-v6",
        rationale="test",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_enabled=True,
    )
    away = CanonicalTeam("Away", League.NCAAB, "Away", "AWA", True, None, None, ())
    home = CanonicalTeam("Home", League.NCAAB, "Home", "HOM", True, None, None, ())
    return request, EligibilityResult(
        False, "CALL", "PAPER_CALL_MODEL_UNVALIDATED", 1.0, 60, 0.05, 0.05, away, home
    )


@pytest.fixture
def flat_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "sqlite")
    monkeypatch.delenv("MODEL_PREDICTION_LEDGER_MIRROR", raising=False)
    # The runtime store resolves from data_root.parent; nest it so each test owns its database.
    return MultiSportPickLedger(tmp_path / "root" / "data", flat=True)


def _log_flat(ledger):
    request, eligibility = _ncaab_request()
    return ledger.append_evaluated(request, eligibility, now=datetime(2026, 11, 14, 10, tzinfo=UTC))


def test_refresh_without_replacement_restores_the_pick_every_time(flat_ledger):
    from model_prediction.cli.commands import _clear_today_open, _open_ncaab_rows, _restore_unreplaced_ncaab

    row = _log_flat(flat_ledger)
    for _ in range(2):  # clear -> restore -> clear -> restore must keep the row open
        prior = {"flat": _open_ncaab_rows(flat_ledger)}
        assert [r["pick_id"] for r in prior["flat"]] == [row["pick_id"]]
        assert _clear_today_open(flat_ledger, "2026-11-14", by_event_date=True) == [row["pick_id"]]
        assert _restore_unreplaced_ncaab({"flat": flat_ledger}, prior, []) == {"flat": 1}
        assert [r["status"] for r in flat_ledger.rows()] == ["open"]


def test_declined_slot_is_not_resurrected_by_restore(flat_ledger):
    from model_prediction.cli.commands import _clear_today_open, _open_ncaab_rows, _restore_unreplaced_ncaab

    row = _log_flat(flat_ledger)
    prior = {"flat": _open_ncaab_rows(flat_ledger)}
    _clear_today_open(flat_ledger, "2026-11-14", by_event_date=True)
    declined = [("NCAAB", "g1", "moneyline")]
    assert _restore_unreplaced_ncaab({"flat": flat_ledger}, prior, declined) == {"flat": 0}
    assert flat_ledger.rows() == []
    assert row["pick_id"] not in [r["pick_id"] for r in flat_ledger.rows()]


def test_scheduled_daily_path_captures_before_clearing_and_restores_after_forecasts():
    """run_daily is too heavy to execute here; pin the wiring that was missing.

    Before the fix only run_forecast called restore, so the scheduled daily job silently
    dropped every NCAAB pick its refresh did not replace (24 flat event-markets).
    """
    from model_prediction.cli import daily

    source = inspect.getsource(daily.run_daily)
    capture = source.index("_restorable_open_rows(ledger, config)")
    clear = source.index("_clear_today_open(ledger, args.date")
    forecasts = source.index("research_futures_map = {")
    evaluated = source.index("_evaluated_slots(forecast_result, restore_prior)")
    restore = source.index("_restore_unreplaced(")
    candidates_pop = source.index('result.pop("candidates", None)')
    settlement = source.index("if args.skip_settlement:")
    assert capture < clear < forecasts < evaluated < restore < candidates_pop < settlement
    # The clear spans every league, so the capture must not be NCAAB-only any more.
    assert "_open_ncaab_rows" not in source


# ---------------------------------------------------------------- every enabled league (261115-g9j)

NBA_CONFIG = {"models": {"NBA": {}, "NCAAB": {}, "MLB": {"daily_forecast_enabled": False}}}


def _log_league(ledger, league, event_id="n1", market=MarketType.MONEYLINE):
    request, eligibility = _ncaab_request(event_id=event_id, league=league, market=market)
    return ledger.append_evaluated(request, eligibility, now=datetime(2026, 11, 14, 10, tzinfo=UTC))


def test_capture_spans_enabled_leagues_and_skips_switched_off_ones(flat_ledger):
    from model_prediction.cli.commands import _restorable_open_rows

    _log_league(flat_ledger, League.NBA, "nba1")
    _log_league(flat_ledger, League.NCAAB, "cb1")
    _log_league(flat_ledger, League.MLB, "mlb1")  # daily_forecast_enabled: false in the config
    _log_league(flat_ledger, League.WNBA, "w1")  # no models.WNBA entry in the config
    captured = {row["league"] for row in _restorable_open_rows(flat_ledger, NBA_CONFIG)}
    assert captured == {"NBA", "NCAAB"}


def test_unreplaced_nba_pick_comes_back_after_the_daily_clear(flat_ledger):
    from model_prediction.cli.commands import (
        _clear_today_open,
        _evaluated_slots,
        _restorable_open_rows,
        _restore_unreplaced,
    )

    row = _log_league(flat_ledger, League.NBA, "nba1")
    prior = {"flat": _restorable_open_rows(flat_ledger, NBA_CONFIG)}
    _clear_today_open(flat_ledger, "2026-11-14", by_event_date=True)
    assert flat_ledger.rows() == []
    # The task errored: its result evaluated nothing, so the slot is reopened.
    excluded = _evaluated_slots({"nba": {"status": "error", "candidates": []}}, prior)
    assert _restore_unreplaced({"flat": flat_ledger}, prior, excluded) == {"flat": 1}
    assert [r["pick_id"] for r in flat_ledger.rows()] == [row["pick_id"]]


def test_slot_a_run_evaluated_but_did_not_emit_is_not_resurrected(flat_ledger):
    from model_prediction.cli.commands import (
        _clear_today_open,
        _evaluated_slots,
        _restorable_open_rows,
        _restore_unreplaced,
    )

    _log_league(flat_ledger, League.NBA, "nba1", MarketType.MONEYLINE)
    _log_league(flat_ledger, League.NBA, "nba2", MarketType.SPREAD)
    _log_league(flat_ledger, League.NBA, "nba3", MarketType.TOTAL)
    prior = {"flat": _restorable_open_rows(flat_ledger, NBA_CONFIG)}
    _clear_today_open(flat_ledger, "2026-11-14", by_event_date=True)
    results = {
        # Moneyline evaluated (candidates); spread priced (priced_contracts); total never seen.
        "nba": {"candidates": [{"event_id": "nba1", "market_type": "moneyline"}]},
        "nba_spread": {"priced_contracts": [{"event_id": "nba2", "market_type": "spread"}]},
        "nba_total": {"status": "error"},
    }
    excluded = _evaluated_slots(results, prior)
    assert {slot[1:] for slot in excluded} == {("nba1", "moneyline"), ("nba2", "spread")}
    assert _restore_unreplaced({"flat": flat_ledger}, prior, excluded) == {"flat": 1}
    assert [r["event_id"] for r in flat_ledger.rows()] == ["nba3"]


def test_gate_declined_ncaab_slot_stays_retired_next_to_a_restored_nba_pick(flat_ledger):
    from model_prediction.cli.commands import _clear_today_open, _restorable_open_rows, _restore_unreplaced

    _log_league(flat_ledger, League.NBA, "nba1")
    _log_league(flat_ledger, League.NCAAB, "cb1")
    prior = {"flat": _restorable_open_rows(flat_ledger, NBA_CONFIG)}
    _clear_today_open(flat_ledger, "2026-11-14", by_event_date=True)
    declined = [("NCAAB", "cb1", "moneyline")]
    assert _restore_unreplaced({"flat": flat_ledger}, prior, declined) == {"flat": 1}
    assert [r["league"] for r in flat_ledger.rows()] == ["NBA"]


# ---------------------------------------------------------------- ET run date vs UTC timestamps (2026-11-25)
# run_daily.ps1 passes the US-Eastern date; created_at_utc / event_start_utc are UTC. Timestamps below are the
# real 2026-11-23 slate (runs.db daily runs; ledger_records ncaab-spread-v7, event 248488).

RUN_1930 = datetime(2026, 11, 24, 0, 30, 1, tzinfo=UTC)  # 19:30 ET 11-23
RUN_2100 = datetime(2026, 11, 24, 2, 0, 1, tzinfo=UTC)  # 21:00 ET 11-23, still --date 2026-11-23
RUN_0030 = datetime(2026, 11, 24, 5, 30, 1, tzinfo=UTC)  # 00:30 ET 11-24, --date 2026-11-24
LATE_CREATED = datetime(2026, 11, 24, 0, 44, 16, 895371, tzinfo=UTC)  # 19:44 ET 11-23
LATE_TIP = datetime(2026, 11, 24, 4, 30, tzinfo=UTC)  # 23:30 ET 11-23
POST_MIDNIGHT_TIP = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)  # 01:00 ET 11-24


def _at(monkeypatch, now: datetime) -> None:
    from model_prediction.cli import commands

    monkeypatch.setattr(commands, "utc_now", lambda: now)


def _log_at(ledger, created, tip, *, league=League.NCAAB, market=MarketType.SPREAD, event_id="248488"):
    request, eligibility = _ncaab_request(event_id=event_id, league=league, market=market)
    request = dataclasses.replace(request, event_start_utc=iso_utc(tip))
    return ledger.append_evaluated(request, eligibility, now=created)


class _Rows:
    """Bare ledger: rows plus a supersede that reports what it was asked to retire."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    def rows(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._rows]

    def supersede_open_rows(self, ids, **_kwargs):
        return list(ids)


def _row(created: str, event: str, pick_id: str = "p") -> dict[str, str]:
    return {
        "pick_id": pick_id,
        "status": "open",
        "league": "NCAAB",
        "created_at_utc": created,
        "event_start_utc": event,
    }


def test_2100_run_reprices_the_1944_pick_for_a_2330_tip(flat_ledger, monkeypatch):
    # The 21:00 ET run (--date 2026-11-23) must retire the 19:44 ET pick (00:44Z on 11-24) so the same-line
    # re-price lands. Prefix matching saw "2026-11-24" on both timestamps, kept the row, and the ledger's
    # duplicate key (event, market, |line|, book, model) rejected the re-price.
    from model_prediction.cli.commands import _clear_today_open

    old = _log_at(flat_ledger, LATE_CREATED, LATE_TIP)
    _at(monkeypatch, RUN_2100)
    assert _clear_today_open(flat_ledger, "2026-11-23", by_event_date=True) == [old["pick_id"]]
    new = _log_at(flat_ledger, datetime(2026, 11, 24, 2, 10, 54, tzinfo=UTC), LATE_TIP)
    assert [r["pick_id"] for r in flat_ledger.rows()] == [new["pick_id"]]


def test_without_the_clear_the_same_line_reprice_is_a_duplicate(flat_ledger):
    # Why a missed clear freezes the row: the re-price at the same line is refused.
    _log_at(flat_ledger, LATE_CREATED, LATE_TIP)
    with pytest.raises(DuplicatePickError):
        _log_at(flat_ledger, datetime(2026, 11, 24, 2, 10, 54, tzinfo=UTC), LATE_TIP)


def test_post_midnight_tip_priced_on_the_prior_slate_is_repriced_by_its_slate(flat_ledger, monkeypatch):
    # A 01:00 ET 11-24 tip on an ESPN 2026-11-23 slate, priced by the 19:30 ET run: the 21:00 ET run of the
    # same slate re-prices it, matched through the pick's ET creation date.
    from model_prediction.cli.commands import _clear_today_open

    row = _log_at(
        flat_ledger, datetime(2026, 11, 24, 0, 35, tzinfo=UTC), POST_MIDNIGHT_TIP, league=League.NBA
    )
    _at(monkeypatch, RUN_2100)
    assert _clear_today_open(flat_ledger, "2026-11-23", by_event_date=True) == [row["pick_id"]]


def test_next_date_run_that_does_not_reforecast_a_post_midnight_tip_restores_it(flat_ledger, monkeypatch):
    # The 00:30 ET 11-24 run matches the same row by its ET event date but forecasts the 11-24 ESPN slate,
    # which does not list it. Nothing evaluates the slot, so restore reopens the original row untouched.
    from model_prediction.cli.commands import (
        _clear_today_open,
        _evaluated_slots,
        _restorable_open_rows,
        _restore_unreplaced,
    )

    row = _log_at(flat_ledger, RUN_2100, POST_MIDNIGHT_TIP, league=League.NBA, event_id="nba-late")
    prior = {"flat": _restorable_open_rows(flat_ledger, NBA_CONFIG)}
    _at(monkeypatch, RUN_0030)
    assert _clear_today_open(flat_ledger, "2026-11-24", by_event_date=True) == [row["pick_id"]]
    excluded = _evaluated_slots(
        {"nba": {"candidates": [{"event_id": "other", "market_type": "spread"}]}}, prior
    )
    assert _restore_unreplaced({"flat": flat_ledger}, prior, excluded) == {"flat": 1}
    (restored,) = flat_ledger.rows()
    assert (restored["pick_id"], restored["status"]) == (row["pick_id"], "open")
    assert restored["created_at_utc"] == row["created_at_utc"]


@pytest.mark.parametrize(
    ("run_date", "created", "event", "cleared"),
    [
        # The 11-23 slate's 19:44 ET pick for a 23:30 ET tip: a 2026-11-24 run never touches it.
        ("2026-11-24", "2026-11-24T00:44:16.895371Z", "2026-11-24T04:30:00Z", False),
        ("2026-11-23", "2026-11-24T00:44:16.895371Z", "2026-11-24T04:30:00Z", True),
        # Created 18:16 ET 11-23 (23:16Z): one ET date either way.
        ("2026-11-23", "2026-11-23T23:16:30.495832Z", "2026-11-24T04:30:00Z", True),
        ("2026-11-22", "2026-11-23T23:16:30.495832Z", "2026-11-24T04:30:00Z", False),
        # A pick written the day before its game is matched by event date only.
        ("2026-11-24", "2026-11-23T16:00:00Z", "2026-11-24T23:00:00Z", True),
        # EDT (UTC-4): 00:35 EDT 7-26 is 7-26, not 7-25; 00:30 EDT 7-27 is 7-27.
        ("2026-07-26", "2026-07-26T04:35:00Z", "2026-07-27T17:05:00Z", True),
        ("2026-07-25", "2026-07-26T04:35:00Z", "2026-07-27T17:05:00Z", False),
        ("2026-07-26", "2026-07-25T16:00:00Z", "2026-07-27T04:30:00Z", False),
        ("2026-07-26", "2026-07-25T16:00:00Z", "2026-07-27T03:30:00Z", True),
        # Unparseable or naive timestamps never match, even when their text starts with the date.
        ("2026-11-23", "2026-11-23T20:00:00", "2026-11-25T04:30:00Z", False),
        ("2026-11-23", "2026-11-23", "2026-11-25T04:30:00Z", False),
    ],
)
def test_clear_matches_eastern_dates_of_parsed_timestamps(monkeypatch, run_date, created, event, cleared):
    from model_prediction.cli.commands import _clear_today_open

    _at(monkeypatch, datetime(2026, 7, 1, tzinfo=UTC))  # nothing below has started
    assert _clear_today_open(_Rows([_row(created, event)]), run_date, by_event_date=True) == (
        ["p"] if cleared else []
    )


def test_created_arm_alone_without_by_event_date(monkeypatch):
    from model_prediction.cli.commands import _clear_today_open

    _at(monkeypatch, datetime(2026, 11, 23, 12, tzinfo=UTC))
    late = _Rows([_row("2026-11-24T00:44:16.895371Z", "2026-11-26T04:30:00Z")])
    assert _clear_today_open(late, "2026-11-23") == ["p"]
    assert _clear_today_open(late, "2026-11-24") == []
    assert _clear_today_open(late, "2026-11-24", by_event_date=True) == []
