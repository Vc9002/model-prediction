"""Main-line selectors must not price a one-sided placeholder rung as the game's line.

The fixtures are the real Polymarket ladders that produced 98-99c prices in the ledger
(quick 261122-l17). A placeholder rung has a ~50c ask on one side and no real bid, so
the opposite side's ask (1 - bid) sits at 96-99c; ranking by the long ask alone chose it.
"""

from __future__ import annotations

from model_prediction.cli.forecast_market_select import (
    _select_nba_spread_market,
    _select_nba_total_market,
    _select_wnba_spread_market,
    _select_wnba_total_market,
)
from model_prediction.cli.forecast_nfl_support import _select_nfl_total_market
from model_prediction.data_sources.mlb_market_odds import _select_full_game_market


def _row(slug: str, line: float, long_ask: float, short_ask: float, observed: str, team=None) -> dict:
    return {
        "market_slug": slug,
        "line": line,
        "team": team,
        "long": {"bid": round(1 - short_ask, 2), "ask": long_ask},
        "short": {"bid": round(1 - long_ask, 2), "ask": short_ask},
        "observed_at_utc": observed,
    }


# BKN@MEM 2026-11-14 at the 05:35Z run: a real 227.5 market and a 2c/49c placeholder at 229.5.
BKN_MEM_TOTALS = [
    _row("tsc-nba-bkn-mem-2026-11-14-227pt5", 227.5, 0.47, 0.65, "2026-11-14T05:35:17.931650Z"),
    _row("tsc-nba-bkn-mem-2026-11-14-229pt5", 229.5, 0.49, 0.98, "2026-11-14T05:35:16.947754Z"),
]

# UTA@LAL 2026-11-18 at the 05:52Z run: every rung was a placeholder.
UTA_LAL_TOTALS = [
    _row("tsc-nba-uta-lal-2026-11-18-234pt5", 234.5, 0.48, 0.96, "2026-11-18T05:39:08.819698Z"),
    _row("tsc-nba-uta-lal-2026-11-18-236pt5", 236.5, 0.51, 0.99, "2026-11-18T05:48:46.175428Z"),
    _row("tsc-nba-uta-lal-2026-11-18-238pt5", 238.5, 0.51, 0.99, "2026-11-18T05:48:46.538361Z"),
    _row("tsc-nba-uta-lal-2026-11-18-242pt5", 242.5, 0.51, 0.99, "2026-11-18T05:39:08.819698Z"),
]

# PHI@DET 2026-11-04: only the far alternates were captured; +19.5 at 94c was priced as the spread.
PHI_DET_SPREADS = [
    _row(f"asc-nba-phi-det-2026-11-04-pos-{line}", float(line.replace("pt", ".")), la, sa, obs, "76ers")
    for line, la, sa, obs in (
        ("19pt5", 0.94, 0.10, "2026-11-04T05:49:21.529291Z"),
        ("21pt5", 0.95, 0.07, "2026-11-04T05:49:11.452831Z"),
        ("23pt5", 0.98, 0.09, "2026-11-04T05:49:01.389950Z"),
    )
]


def test_nba_total_prefers_the_two_sided_rung_over_a_closer_placeholder():
    chosen = _select_nba_total_market(BKN_MEM_TOTALS)
    assert chosen is not None and chosen["line"] == 227.5


def test_nba_total_declines_when_the_whole_ladder_is_placeholders():
    assert _select_nba_total_market(UTA_LAL_TOTALS) is None


def test_nba_spread_declines_when_only_far_alternates_are_quoted():
    assert _select_nba_spread_market(PHI_DET_SPREADS) is None


def test_nba_latest_placeholder_capture_is_not_replaced_by_an_older_two_sided_one():
    older = _row("tsc-nba-x-y-2026-11-14-229pt5", 229.5, 0.48, 0.55, "2026-11-14T04:00:00Z")
    newer = _row("tsc-nba-x-y-2026-11-14-229pt5", 229.5, 0.49, 0.98, "2026-11-14T05:35:00Z")
    assert _select_nba_total_market([older, newer]) is None


def test_rung_with_no_short_ask_is_declined():
    # A book with no bid on the long side has no short ask at all (polymarket_us._build_snapshot).
    row = _row("tsc-nba-x-y-2026-11-14-229pt5", 229.5, 0.49, 0.98, "2026-11-14T05:35:00Z")
    row["short"] = {"bid": None, "ask": None}
    assert _select_nba_total_market([row]) is None
    del row["short"]
    assert _select_wnba_total_market([row]) is None


def test_wnba_latest_placeholder_capture_is_not_replaced_by_an_older_two_sided_one():
    older = _row("asc-wnba-x-y-2026-08-01-neg-4pt5", -4.5, 0.48, 0.54, "2026-08-01T10:00:00Z")
    newer = _row("asc-wnba-x-y-2026-08-01-neg-4pt5", -4.5, 0.49, 0.98, "2026-08-01T16:00:00Z")
    assert _select_wnba_spread_market([older, newer]) is None


def test_wnba_selectors_apply_the_same_rule():
    # PHX@POR 2026-09-17 -7.5 rung: 17c bid / 48c ask, so the other side cost 83c.
    phx_por = _row("asc-wnba-phx-por-2026-09-17-neg-7pt5", -7.5, 0.48, 0.83, "2026-09-17T10:03:35Z")
    assert _select_wnba_spread_market([phx_por]) is None
    assert _select_wnba_total_market(BKN_MEM_TOTALS)["line"] == 227.5


def test_nfl_band_checks_the_short_side_too():
    # Long ask 49c passes the long-only band; the 98c short side shows nobody bids on it.
    placeholder = _row("tsc-nfl-a-b-2026-11-30-total-44pt5", 44.5, 0.49, 0.98, "2026-11-30T05:00:00Z")
    real = _row("tsc-nfl-a-b-2026-11-30-total-46pt5", 46.5, 0.45, 0.58, "2026-11-30T05:00:00Z")
    chosen = _select_nfl_total_market([placeholder, real])
    assert chosen is not None and chosen["line"] == 46.5
    assert _select_nfl_total_market([placeholder]) is None


def _mlb_market(slug: str, line: float, over: float, under: float) -> dict:
    return {
        "market_slug": slug,
        "market_type": "total",
        "line": line,
        "sides": [{"price_probability": over}, {"price_probability": under}],
    }


def test_mlb_single_inning_total_is_never_the_full_game_total():
    # tsc-mlb-cws-hou-2026-09-29-i4-0pt5 traded at a 47.5% mid, closer to even than the game total.
    event = {
        "markets": [
            _mlb_market("tsc-mlb-cws-hou-2026-09-29-i4-0pt5", 0.5, 0.49, 0.53),
            _mlb_market("tsc-mlb-cws-hou-2026-09-29-8pt5", 8.5, 0.46, 0.57),
        ]
    }
    assert _select_full_game_market(event, "total")["line"] == 8.5
