"""Tests for real MLB market-to-game matching (mlb_market_matching.py).

Three real bugs found running the Checkpoint 9 shadow script against a real
slate — see the module docstring and outputs/rebuild/takeover_status.md for
full live evidence.
"""

from __future__ import annotations

import polars as pl
import pytest

from model_prediction.rebuild.mlb_market_matching import (
    exclude_first_five_innings,
    real_market_candidates,
    real_quote_age_seconds,
    real_total_lines,
    resolve_polymarket_event_id,
)


def _row(
    event_id: str, market_type: str, team_or_side: str, line: float | None,
    executable_price: float, *, team: str | None = None, is_f5: bool = False, market_id: str = "m1",
) -> dict:
    return {
        "event_id": event_id, "market_id": market_id, "market_type": market_type,
        "team_or_side": team_or_side, "team": team, "line": line,
        "executable_price": executable_price, "is_first_five_innings": is_f5,
    }


class TestResolvePolymarketEventId:
    def test_resolves_via_full_team_names_not_abbreviations(self):
        # Real bug: passing Statcast-style abbreviations ("SEA") here
        # silently matched zero rows, since Polymarket's `team` field is
        # the real full display name.
        df = pl.DataFrame([
            _row("70543", "moneyline", "home", None, 0.545, team="Seattle Mariners"),
            _row("70543", "moneyline", "away", None, 0.46, team="Detroit Tigers"),
        ])

        assert resolve_polymarket_event_id(df, "Seattle Mariners", "Detroit Tigers") == "70543"
        assert resolve_polymarket_event_id(df, "SEA", "DET") is None, (
            "abbreviations must not silently match — Polymarket's team field is the full name"
        )

    def test_ambiguous_match_returns_none(self):
        df = pl.DataFrame([
            _row("A", "moneyline", "home", None, 0.5, team="Seattle Mariners"),
            _row("B", "moneyline", "home", None, 0.5, team="Seattle Mariners"),
        ])
        assert resolve_polymarket_event_id(df, "Seattle Mariners", "Detroit Tigers") is None


class TestExcludeFirstFiveInnings:
    def test_f5_rows_are_dropped(self):
        df = pl.DataFrame([
            _row("70543", "total", "over", 6.5, 0.65, is_f5=False),
            _row("70543", "total", "over", 6.5, 0.25, is_f5=True),
        ])

        result = exclude_first_five_innings(df)

        assert result.height == 1
        assert result["executable_price"][0] == 0.65

    def test_missing_column_returns_unfiltered(self):
        df = pl.DataFrame({"event_id": ["1"], "market_type": ["total"]})
        assert exclude_first_five_innings(df).height == 1


class TestRealTotalLines:
    def test_returns_distinct_real_lines_for_this_event_only(self):
        df = pl.DataFrame([
            _row("70543", "total", "over", 6.5, 0.65),
            _row("70543", "total", "under", 6.5, 0.35),
            _row("70543", "total", "over", 7.5, 0.5),
            _row("99999", "total", "over", 2.5, 0.9),  # a different game entirely
        ])

        lines = real_total_lines(df, "70543")

        assert lines == [6.5, 7.5]


class TestRealMarketCandidates:
    def test_totals_from_other_events_are_excluded(self):
        # Real bug: totals were previously filtered by market_type alone
        # with zero event isolation — every total market from the whole
        # date's collection (176 rows) got attached to every single game.
        df = pl.DataFrame([
            _row("70543", "moneyline", "home", None, 0.545, team="Seattle Mariners"),
            _row("70543", "moneyline", "away", None, 0.46, team="Detroit Tigers"),
            _row("70543", "total", "over", 6.5, 0.65),
            _row("99999", "total", "over", 2.5, 0.9),  # unrelated game's total
        ])

        candidates = real_market_candidates(df, "Seattle Mariners", "Detroit Tigers")

        assert len(candidates) == 3
        assert all(c.market_id != "" for c in candidates)
        totals = [c for c in candidates if c.market_type == "total"]
        assert len(totals) == 1
        assert totals[0].line == 6.5

    def test_unresolvable_event_returns_no_candidates_not_a_guess(self):
        df = pl.DataFrame([
            _row("70543", "moneyline", "home", None, 0.5, team="Some Other Team"),
        ])
        assert real_market_candidates(df, "Seattle Mariners", "Detroit Tigers") == []


class TestRealQuoteAgeSeconds:
    """Real bug fixed: quote_age_seconds was hardcoded to 0.0 for every
    candidate even though every market row already carries a real
    observed_at_utc timestamp (see storage.py's provenance_row) — age was
    never actually blocked on a missing data source the way depth is.
    Fixing this immediately exposed that mlb_shadow_run.py never
    re-collected fresh market data itself, silently relying on however
    stale whatever was already on disk happened to be — see
    outputs/rebuild/takeover_status.md.
    """

    def test_computes_real_elapsed_time(self):
        from datetime import timedelta

        from model_prediction.rebuild.storage import utc_now

        now = utc_now()
        observed = (now - timedelta(seconds=45)).isoformat()

        age = real_quote_age_seconds(observed, now=now)

        assert age == pytest.approx(45.0, abs=0.01)

    def test_missing_timestamp_fails_closed_not_fresh(self):
        assert real_quote_age_seconds(None) == float("inf"), (
            "an unknown-age quote must not be treated as instantly fresh"
        )

    def test_unparseable_timestamp_fails_closed_not_fresh(self):
        assert real_quote_age_seconds("not-a-real-timestamp") == float("inf")
