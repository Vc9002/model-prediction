"""NFL main-line selection must not price a placeholder rung as the game's spread or total."""

from __future__ import annotations

from model_prediction.cli.forecast_nfl_support import (
    _select_nfl_spread_market,
    _select_nfl_total_market,
)

OBS = "2026-11-12T05:54:19Z"


def _row(
    slug: str, line: float, long_ask: float, short_ask: float, team: str = "Washington Commanders"
) -> dict:
    return {
        "market_slug": slug,
        "market_type": "spread" if "asc" in slug else "total",
        "team": team,
        "line": line,
        "long": {"ask": long_ask},
        "short": {"ask": short_ask},
        "observed_at_utc": OBS,
    }


# The real 2026-11-12 pregame ladder for WAS at NYG: only alternates were listed.
PLACEHOLDER_SPREADS = [
    _row("asc-nfl-was-nyg-2026-11-12-pos-17pt5", 17.5, 0.91, 0.125),
    _row("asc-nfl-was-nyg-2026-11-12-pos-20pt5", 20.5, 0.94, 0.095),
    _row("asc-nfl-was-nyg-2026-11-12-pos-30pt5", 30.5, 0.99, 0.06),
]
PLACEHOLDER_TOTALS = [
    _row("tsc-nfl-was-nyg-2026-11-12-total-17pt5", 17.5, 0.99, 0.055, team=""),
    _row("tsc-nfl-was-nyg-2026-11-12-total-23pt5", 23.5, 0.94, 0.08, team=""),
]


def test_placeholder_rungs_are_never_the_main_line_even_without_a_reference():
    assert _select_nfl_spread_market(PLACEHOLDER_SPREADS) is None
    assert _select_nfl_total_market(PLACEHOLDER_TOTALS) is None


def test_reference_line_picks_the_nearest_plausible_rung():
    # Away team anchored: +2.5 for WAS is a home line of -2.5. The +6.5 is also plausible
    # and more "balanced" on this book, but it is 4 points from the consensus.
    rows = PLACEHOLDER_SPREADS + [
        _row("asc-nfl-was-nyg-2026-11-12-pos-2pt5", 2.5, 0.56, 0.47),
        _row("asc-nfl-was-nyg-2026-11-12-pos-6pt5", 6.5, 0.50, 0.52),
    ]
    picked = _select_nfl_spread_market(
        rows,
        reference_line=-2.5,
        row_line=lambda row: (
            -float(row["line"]) if row["team"] == "Washington Commanders" else float(row["line"])
        ),
    )
    assert picked["market_slug"].endswith("pos-2pt5")
    # Far from the reference: an alternate ladder with no main line present declines.
    assert (
        _select_nfl_spread_market(
            rows[:-2] + [_row("asc-nfl-was-nyg-2026-11-12-pos-12pt5", 12.5, 0.6, 0.42)],
            reference_line=-2.5,
            row_line=lambda row: -float(row["line"]),
        )
        is None
    )


def test_no_reference_keeps_the_balanced_rule_among_plausible_rows():
    rows = [
        _row("asc-nfl-a-b-2026-11-15-pos-3pt5", 3.5, 0.58, 0.45),
        _row("asc-nfl-a-b-2026-11-15-pos-2pt5", 2.5, 0.51, 0.51),
        _row("asc-nfl-a-b-2026-11-15-pos-13pt5", 13.5, 0.85, 0.17),
    ]
    assert _select_nfl_spread_market(rows)["market_slug"].endswith("pos-2pt5")


def test_total_reference_prefers_nearest_line_within_gap():
    rows = PLACEHOLDER_TOTALS + [
        _row("tsc-nfl-was-nyg-2026-11-12-total-40pt5", 40.5, 0.52, 0.50, team=""),
        _row("tsc-nfl-was-nyg-2026-11-12-total-44pt5", 44.5, 0.50, 0.52, team=""),
    ]
    assert _select_nfl_total_market(rows, reference_line=40.5)["line"] == 40.5
    assert _select_nfl_total_market(rows, reference_line=41.5)["line"] == 40.5
    assert _select_nfl_total_market(rows[:2], reference_line=40.5) is None
