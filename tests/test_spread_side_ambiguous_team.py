"""A spread snapshot's ``team`` names only the market's LONG side, so
_spread_side_for_row must know which of the row's two teams that is before it
can pick long or short. Real-money bug 2026-11-25: when the Polymarket team
matched neither row team (abbreviations such as "UCA") the code assumed it was
the opponent and returned the short side, so a "Central Arkansas -3.5" pick
mapped to Jacksonville -3.5 on asc-cbb-cark-jax-2026-11-21-pos-3pt5. A team
matching both row teams ("Louisiana" vs "Louisiana-Monroe") is the same hazard
in reverse. Both must fail closed (None, the buyer rejects the pick as
unmapped)."""

from __future__ import annotations

import pytest

from model_prediction.dashboard.backtests import _spread_side_for_row


def _ncaab_row(away: str, home: str, selection: str, line: float) -> dict:
    return {
        "league": "NCAAB",
        "market_type": "spread",
        "away_team": away,
        "home_team": home,
        "selection": selection,
        "line": str(line),
        "event_start_utc": "2026-11-21T21:00:00Z",
    }


def _spread_snapshot(event_title: str, team: str, line: float, slug: str) -> dict:
    # Shape copied from a real odds/ncaab/2026-11-21/polymarket_snapshots.jsonl row.
    return {
        "event_title": event_title,
        "event_start_utc": "2026-11-21T21:00:00Z",
        "observed_at_utc": "2026-11-21T05:37:40.464227Z",
        "league": "NCAAB",
        "market_type": "spread",
        "market_state": "MARKET_STATE_OPEN",
        "market_slug": slug,
        "team": team,
        "line": line,
        "timestamp_valid": True,
        "long": {"description": f"{line:+.2f}", "ask": 0.52},
        "short": {"description": f"{-line:+.2f}", "ask": 0.50},
    }


_UCA_SNAPSHOT = _spread_snapshot(
    "Central Arkansas vs. Jacksonville", "UCA", 3.5, "asc-cbb-cark-jax-2026-11-21-pos-3pt5"
)


def test_market_team_matching_neither_row_team_is_refused() -> None:
    """The real case: "Central Arkansas -3.5" against a UCA +3.5 market. The
    old code called UCA "not my team", so it returned the short side, which
    is Jacksonville -3.5: the opposite bet."""
    row = _ncaab_row("Central Arkansas", "Jacksonville", "away", -3.5)

    assert _spread_side_for_row(row, _UCA_SNAPSHOT) is None


def test_market_team_matching_neither_row_team_is_refused_for_either_pick() -> None:
    # Jacksonville -3.5 happens to be the right short side, but nothing in the
    # snapshot proves it, so it fails closed too.
    row = _ncaab_row("Central Arkansas", "Jacksonville", "home", -3.5)

    assert _spread_side_for_row(row, _UCA_SNAPSHOT) is None


@pytest.mark.parametrize(
    ("away", "home", "market_team", "selection", "row_line", "market_line"),
    [
        # "Louisiana" is a token prefix of "Louisiana-Monroe": the old code
        # treated a ULM -2.5 pick as Louisiana's own -2.5 (long), the wrong team.
        ("Louisiana-Monroe", "Louisiana", "Louisiana", "away", -2.5, -2.5),
        ("Louisiana-Monroe", "Louisiana", "Louisiana", "home", -2.5, -2.5),
        ("Texas Southern", "Texas", "Texas", "away", -4.5, -4.5),
    ],
)
def test_market_team_matching_both_row_teams_is_refused(
    away: str, home: str, market_team: str, selection: str, row_line: float, market_line: float
) -> None:
    snapshot = _spread_snapshot(f"{away} vs. {home}", market_team, market_line, "asc-cbb-x-y-2026-11-21")
    row = _ncaab_row(away, home, selection, row_line)

    assert _spread_side_for_row(row, snapshot) is None


@pytest.mark.parametrize(
    ("market_team", "market_line", "selection", "row_line", "expected"),
    [
        # Market team is the AWAY team (Central Arkansas +3.5).
        ("Central Arkansas", 3.5, "away", 3.5, "long"),
        ("Central Arkansas", 3.5, "home", -3.5, "short"),
        # Market team is the HOME team (Jacksonville -3.5).
        ("Jacksonville", -3.5, "home", -3.5, "long"),
        ("Jacksonville", -3.5, "away", 3.5, "short"),
        # Line mismatch never matches either side.
        ("Jacksonville", -3.5, "away", -3.5, None),
        ("Jacksonville", -3.5, "home", 3.5, None),
    ],
)
def test_unambiguous_market_team_still_maps_long_and_short(
    market_team: str, market_line: float, selection: str, row_line: float, expected: str | None
) -> None:
    snapshot = _spread_snapshot(
        "Central Arkansas vs. Jacksonville", market_team, market_line, "asc-cbb-cark-jax-2026-11-21"
    )
    row = _ncaab_row("Central Arkansas", "Jacksonville", selection, row_line)

    assert _spread_side_for_row(row, snapshot) == expected


def test_mlb_full_name_market_team_still_maps_both_sides() -> None:
    snapshot = _spread_snapshot(
        "Los Angeles Dodgers vs. Chicago Cubs",
        "Los Angeles Dodgers",
        -1.5,
        "asc-mlb-lad-chc-2026-08-04-neg-1pt5",
    )
    base = {"league": "MLB", "away_team": "Los Angeles Dodgers", "home_team": "Chicago Cubs"}

    assert _spread_side_for_row({**base, "selection": "away", "line": "-1.5"}, snapshot) == "long"
    assert _spread_side_for_row({**base, "selection": "home", "line": "1.5"}, snapshot) == "short"
