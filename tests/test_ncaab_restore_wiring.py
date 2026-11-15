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


def _ncaab_request(event_id="g1", selection="home") -> tuple[PickRequest, EligibilityResult]:
    request = PickRequest(
        event_start_utc="2099-01-01T00:00:00Z",
        event_id=event_id,
        league=League.NCAAB,
        away_team="Away",
        home_team="Home",
        market_type=MarketType.MONEYLINE,
        selection=selection,
        line=None,
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
    capture = source.index("_open_ncaab_rows(ledger)")
    clear = source.index("_clear_today_open(ledger, args.date")
    forecasts = source.index("research_futures_map = {")
    restore = source.index("_restore_unreplaced_ncaab(")
    settlement = source.index("if args.skip_settlement:")
    assert capture < clear < forecasts < restore < settlement
