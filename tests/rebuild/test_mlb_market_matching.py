"""Tests for real MLB market-to-game matching (mlb_market_matching.py).

Three real bugs found running the Checkpoint 9 shadow script against a real
slate — see the module docstring and outputs/rebuild/takeover_status.md for
full live evidence.
"""

from __future__ import annotations

import polars as pl
import pytest

from model_prediction.rebuild.decision import MarketEvaluation
from model_prediction.rebuild.mlb_market_matching import (
    exclude_first_five_innings,
    real_market_candidates,
    real_market_snapshot_hash,
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

    def test_real_candidates_honestly_mark_depth_unavailable_not_fabricated(self):
        # Real bug fixed: this previously set available_depth=999.0, a
        # fabricated value that trivially cleared decision.py's depth gate
        # on every candidate (CLAUDE.md Part 3 SS2 explicitly forbids this —
        # "do not fabricate depth ... fail economic qualification"). The
        # underlying Polymarket source has no real depth endpoint, so every
        # real candidate must say so honestly via depth_available=False.
        df = pl.DataFrame([
            _row("70543", "moneyline", "home", None, 0.545, team="Seattle Mariners"),
            _row("70543", "total", "over", 6.5, 0.65),
        ])

        candidates = real_market_candidates(df, "Seattle Mariners", "Detroit Tigers")

        assert len(candidates) == 2
        assert all(c.depth_available is False for c in candidates)
        assert all(c.available_depth == 0.0 for c in candidates)


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


class TestRealMarketSnapshotHash:
    """Real bug found wiring the shadow ledger into scripts/mlb_shadow_run.py
    against a live slate: hashing quote_age_seconds (which is `now -
    observed_at_utc` and increases every second regardless of whether the
    book moved) meant an immediate rerun against byte-identical market data
    always produced a different market_snapshot_hash, defeating
    trade_decisions' idempotency guarantee -- a real rerun with unchanged
    books produced 32 duplicate rows instead of 0.
    """

    def test_hash_is_stable_across_different_quote_ages(self):
        m1 = MarketEvaluation(
            market_id="70543", market_type="moneyline", team_or_side="home", line=None,
            executable_ask=0.55, depth_adjusted_price=0.55, quote_age_seconds=5.0, available_depth=999.0,
        )
        m2 = MarketEvaluation(
            market_id="70543", market_type="moneyline", team_or_side="home", line=None,
            executable_ask=0.55, depth_adjusted_price=0.55, quote_age_seconds=4500.0, available_depth=999.0,
        )
        assert real_market_snapshot_hash("70543", [m1]) == real_market_snapshot_hash("70543", [m2]), (
            "the same real quote observed at two different wall-clock moments "
            "must hash identically -- only real market content should affect this hash"
        )

    def test_hash_changes_when_real_content_changes(self):
        m1 = MarketEvaluation(
            market_id="70543", market_type="moneyline", team_or_side="home", line=None,
            executable_ask=0.55, depth_adjusted_price=0.55, quote_age_seconds=5.0, available_depth=999.0,
        )
        m2 = MarketEvaluation(
            market_id="70543", market_type="moneyline", team_or_side="home", line=None,
            executable_ask=0.60, depth_adjusted_price=0.60, quote_age_seconds=5.0, available_depth=999.0,
        )
        assert real_market_snapshot_hash("70543", [m1]) != real_market_snapshot_hash("70543", [m2]), (
            "a real price move must still produce a different hash"
        )
