"""Scheduled-daily NCAAB restore wiring (quick task 261115-1m3, item 4).

The daily job clears every not-yet-started open row before re-forecasting; a pick the refresh
does not replace used to vanish because only ``run_forecast`` restored (24 flat event-markets).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from model_prediction.domain import League, MarketType, ModelOrigin, PickRequest
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
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
