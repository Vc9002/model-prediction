"""Tests for the real settlement-outcome determination logic
(mlb_settle_and_capture_closing.py, CLAUDE.md's next-phase Task 19):
determine_outcome() must correctly resolve moneyline/spread/total
WIN/LOSS/PUSH from a real final score against one evaluated side/line,
using each side's own signed line, not an assumed mirrored pair.

Also tests real_closing_quote() -- the real bridge from MarketStore's
actual parquet books to a validated "closing" observation, fixing a real
bug where the settlement script queried the shadow ledger's own
market_snapshots SQL table, which nothing in this codebase populates.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import mlb_settle_and_capture_closing as settlement_script
from mlb_settle_and_capture_closing import determine_outcome, real_closing_quote, real_event_market_date

from model_prediction.rebuild.shadow_ledger import ShadowLedger
from model_prediction.rebuild.storage import MarketStore


def test_settlement_main_records_settle_stage_and_terminal_run(tmp_path):
    result = {
        "settlements_recorded": 2,
        "skipped_not_final": 1,
        "closing_dates_attempted": 1,
        "closing_prices_recorded": 0,
        "outcome_counts": {"WIN": 1, "LOSS": 1, "PUSH": 0},
    }
    with (
        patch.object(settlement_script, "DATA_ROOT", str(tmp_path)),
        patch.object(
            settlement_script,
            "_run_settlement",
            return_value=result,
        ),
    ):
        settlement_script.main()

    ledger = ShadowLedger(tmp_path / "shadow.db")
    run = dict(ledger.conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone())
    stage = ledger.get_stage_result(run["run_id"], "settle")
    ledger.close()
    assert run["status"] == "SUCCESS"
    assert run["finished_at"] is not None
    assert stage["status"] == "SUCCESS"
    assert stage["row_count"] == 2
    assert stage["mode"] == "fresh"


class TestMoneylineOutcome:
    def test_home_wins_home_side_is_a_win(self):
        assert determine_outcome("moneyline", "home", None, home_score=5, away_score=3) == "WIN"

    def test_home_wins_away_side_is_a_loss(self):
        assert determine_outcome("moneyline", "away", None, home_score=5, away_score=3) == "LOSS"

    def test_away_wins_away_side_is_a_win(self):
        assert determine_outcome("moneyline", "away", None, home_score=2, away_score=7) == "WIN"

    def test_tie_is_a_push(self):
        # Doesn't happen in real MLB (no ties), but the formula must not
        # crash or silently mislabel a hypothetical tie.
        assert determine_outcome("moneyline", "home", None, home_score=4, away_score=4) == "PUSH"


class TestSpreadOutcome:
    def test_home_favored_and_covers(self):
        # home -1.5, wins by 3 -> covers.
        assert determine_outcome("spread", "home", -1.5, home_score=5, away_score=2) == "WIN"

    def test_home_favored_and_does_not_cover(self):
        # home -1.5, wins by only 1 -> does not cover.
        assert determine_outcome("spread", "home", -1.5, home_score=3, away_score=2) == "LOSS"

    def test_away_underdog_covers_on_a_loss(self):
        # away +2.5, loses by 1 -> still covers.
        assert determine_outcome("spread", "away", 2.5, home_score=3, away_score=2) == "WIN"

    def test_away_underdog_fails_to_cover(self):
        # away +1.5, loses by 3 -> does not cover.
        assert determine_outcome("spread", "away", 1.5, home_score=5, away_score=2) == "LOSS"

    def test_whole_integer_line_can_push(self):
        # home -2, wins by exactly 2 -> push.
        assert determine_outcome("spread", "home", -2.0, home_score=6, away_score=4) == "PUSH"

    def test_independent_lines_not_assumed_mirrored(self):
        # A real captured away line need not be the exact mirror of the
        # home line at the same market -- each side is evaluated on its
        # own real signed line, not a derived complement.
        assert determine_outcome("spread", "away", -2.5, home_score=1, away_score=6) == "WIN"


class TestTotalOutcome:
    def test_over_covers(self):
        assert determine_outcome("total", "over", 7.5, home_score=5, away_score=4) == "WIN"

    def test_over_fails(self):
        assert determine_outcome("total", "over", 9.5, home_score=2, away_score=1) == "LOSS"

    def test_under_covers(self):
        assert determine_outcome("total", "under", 9.5, home_score=2, away_score=1) == "WIN"

    def test_whole_integer_total_can_push(self):
        assert determine_outcome("total", "over", 8.0, home_score=5, away_score=3) == "PUSH"
        assert determine_outcome("total", "under", 8.0, home_score=5, away_score=3) == "PUSH"


def _write_books(store: MarketStore, rows: list[dict]) -> None:
    store.write_books("mlb", "2026-08-06", pl.DataFrame(rows), primary_key=[])


class TestRealClosingQuote:
    """real_closing_quote() must read MarketStore's actual parquet books
    (not the empty ledger table), and only accept a quote as real closing
    evidence when it is strictly later than the decision and no later than
    real event start -- never in-play data, never the same decision-time
    snapshot reused as a fake closing price."""

    def _store(self, tmp_path: Path) -> MarketStore:
        return MarketStore(str(tmp_path / "markets"))

    def test_no_file_for_the_date_returns_none(self, tmp_path: Path):
        store = self._store(tmp_path)
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result is None

    def test_later_pregame_quote_is_accepted_as_closing(self, tmp_path: Path):
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-06T20:00:00+00:00",
                    "executable_price": 0.55,
                }
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result == (0.55, "2026-08-06T20:00:00+00:00")

    def test_quote_at_or_before_decision_time_is_rejected(self, tmp_path: Path):
        # Reusing the same (or an earlier) snapshot already used at
        # decision time is not real closing-price evidence.
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-06T10:00:00+00:00",
                    "executable_price": 0.55,
                }
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result is None

    def test_in_play_quote_after_event_start_is_rejected(self, tmp_path: Path):
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-07T01:00:00+00:00",
                    "executable_price": 0.80,
                }
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result is None

    def test_picks_the_latest_of_multiple_valid_quotes(self, tmp_path: Path):
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-06T18:00:00+00:00",
                    "executable_price": 0.50,
                },
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-06T22:00:00+00:00",
                    "executable_price": 0.60,
                },
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result == (0.60, "2026-08-06T22:00:00+00:00")

    def test_wrong_market_id_is_ignored(self, tmp_path: Path):
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "different_market",
                    "team_or_side": "home",
                    "line": -1.5,
                    "observed_at_utc": "2026-08-06T20:00:00+00:00",
                    "executable_price": 0.55,
                }
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            -1.5,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result is None

    def test_moneyline_with_null_line_matches_null_line_rows(self, tmp_path: Path):
        store = self._store(tmp_path)
        _write_books(
            store,
            [
                {
                    "market_id": "m1",
                    "team_or_side": "home",
                    "line": None,
                    "observed_at_utc": "2026-08-06T20:00:00+00:00",
                    "executable_price": 0.55,
                }
            ],
        )
        result = real_closing_quote(
            store,
            "mlb",
            "2026-08-06",
            "m1",
            "home",
            None,
            "2026-08-06T10:00:00+00:00",
            "2026-08-06T23:00:00+00:00",
        )
        assert result == (0.55, "2026-08-06T20:00:00+00:00")


class TestRealEventMarketDate:
    """MLB-6 (multi-sport execution spec): real bug fix -- the market-store
    lookup must key off the event's own real event_start_utc date, never
    decision_time_utc's date. A decision made well before an event (or any
    event whose start crosses a UTC day boundary relative to decision
    time) can have decision_date != event_date; using the wrong one would
    silently look in the wrong real market-store file."""

    def test_uses_event_start_date_not_decision_date(self):
        # Real, concrete case this fixes: decision made two real calendar
        # days before the event (an early horizon).
        result = real_event_market_date("2026-08-11T01:00:00+00:00", "2026-08-09T13:00:00+00:00")
        assert result == "2026-08-11"

    def test_decision_date_and_event_date_genuinely_differ(self):
        result = real_event_market_date("2026-08-10T00:30:00+00:00", "2026-08-09T22:00:00+00:00")
        assert result == "2026-08-10"
        assert "2026-08-09T22:00:00+00:00"[:10] == "2026-08-09"
        assert result != "2026-08-09"

    def test_falls_back_to_decision_date_when_no_real_event_start_available(self):
        result = real_event_market_date(None, "2026-08-09T22:00:00+00:00")
        assert result == "2026-08-09"
