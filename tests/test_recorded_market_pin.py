"""The buyer uses the exact Polymarket market and side the forecast priced against.

Live buys used to re-match Polymarket markets to a ledger row by team name at buy time
(dashboard/backtests.py::_pick_quote), which put a real-money order on the wrong team when the
exchange abbreviated a school ("UCA" for Central Arkansas, 2026-11-25). The NCAAB forecast already
knew the market and side: the slate's sides carry the exchange's own away/home ordering. It now
writes ``market_slug=<slug> token_side=<long|short>`` into the row's rationale (the established
place a row records its market, and a column the buyer's dashboard rows carry), and _pick_quote uses
that market and side when present. Name matching is the fallback when the recorded market was not
captured or its line no longer agrees, and a decisive name match that disagrees refuses the row.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.data_sources import polymarket_execute as pe

backtests = importlib.import_module("model_prediction.dashboard.backtests")

_START = "2026-11-21T21:00:00Z"
_OBSERVED = "2026-11-21T20:55:53.079472Z"


def _spread(slug: str, team: str, line: float, long_ask: float, short_ask: float, **extra) -> dict:
    # Shape and prices copied from the real odds/ncaab/2026-11-21/polymarket_snapshots.jsonl.
    return {
        "event_id": "240517",
        "event_title": "Central Arkansas vs. Jacksonville",
        "event_start_utc": _START,
        "observed_at_utc": _OBSERVED,
        "league": "NCAAB",
        "market_type": "spread",
        "market_state": "MARKET_STATE_OPEN",
        "market_slug": slug,
        "team": team,
        "line": line,
        "timestamp_valid": True,
        "long": {"description": f"{line:+.2f}", "ask": long_ask, "bid": long_ask - 0.02},
        "short": {"description": f"{-line:+.2f}", "ask": short_ask, "bid": short_ask - 0.02},
        **extra,
    }


_NEG = "asc-cbb-cark-jax-2026-11-21-neg-3pt5"
_POS = "asc-cbb-cark-jax-2026-11-21-pos-3pt5"
_REAL_UCA = [_spread(_NEG, "UCA", -3.5, 0.50, 0.51), _spread(_POS, "UCA", 3.5, 0.99, 0.32)]


def _row(selection: str, line: float, rationale: str = "Projected margin -1.6 (home), sd 10.7.") -> dict:
    return {
        "league": "NCAAB",
        "market_type": "spread",
        "event_id": "240517",
        "away_team": "Central Arkansas Bears",
        "home_team": "Jacksonville Dolphins",
        "selection": selection,
        "line": str(line),
        "event_start_utc": _START,
        "rationale": rationale,
    }


@pytest.fixture
def snapshots(monkeypatch, tmp_path: Path):
    def write(items: list[dict]) -> Path:
        path = tmp_path / f"snapshots-{len(list(tmp_path.iterdir()))}.jsonl"
        path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")
        monkeypatch.setattr(backtests, "_resolve_snapshot_path", lambda _sport, _day: path)
        return path

    return write


# ------------------------------------------------------------------ UCA, name matching (item 4)


@pytest.mark.parametrize(
    ("selection", "line", "slug", "side", "ask"),
    [
        ("away", -3.5, _NEG, "long", 0.50),  # Central Arkansas -3.5 is UCA -3.5, the long side
        ("home", 3.5, _NEG, "short", 0.51),  # Jacksonville +3.5, the real ledger row f6c64e88
        ("home", -3.5, _POS, "short", 0.32),  # Jacksonville -3.5
        ("away", 3.5, _POS, "long", 0.99),  # Central Arkansas +3.5
    ],
)
def test_uca_maps_to_the_right_market_and_side(snapshots, selection, line, slug, side, ask) -> None:
    """Before the alias every one of these was refused ("UCA" matched neither row team); before the
    894f9542 refusal the first one was bought as Jacksonville -3.5."""
    snapshots(_REAL_UCA)

    quote = backtests._pick_quote(_row(selection, line))

    assert quote is not None
    assert (quote["market_slug"], quote["side"], quote["ask"]) == (slug, side, ask)


def test_execution_gate_confirms_the_uca_long_side_on_a_live_snapshot() -> None:
    live = {"market_type": "spread", "team": "UCA", "line": -3.5}  # live snapshots carry no title

    assert pe._resolve_spread_side(_row("away", -3.5), live) == "long"
    assert pe._resolve_spread_side(_row("home", 3.5), live) == "short"
    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        pe._resolve_spread_side(_row("away", -3.5), {**live, "line": 3.5})


# Each Polymarket name below was checked in odds/ncaab/*/polymarket_snapshots.jsonl against the same
# event's title, event-slug code and moneyline mascot; the history/ESPN name is the one ledger rows carry.
_ALIASES = [
    ("Central Arkansas Bears", "UCA", ["Arkansas Razorbacks", "Jacksonville Dolphins"]),
    ("Northern Arizona Lumberjacks", "NAU", ["Arizona Wildcats", "Kentucky Wildcats"]),
    ("Eastern Illinois Panthers", "EIU", ["Illinois Fighting Illini", "Vanderbilt Commodores"]),
    ("UMass Lowell River Hawks", "UML", ["Cincinnati Bearcats"]),
    ("Tennessee State Tigers", "Tenn. State", ["Tennessee Volunteers", "South Florida Bulls"]),
    ("Oregon State Beavers", "Ore. State", ["Oregon Ducks", "San Francisco Dons"]),
    ("UNC Wilmington Seahawks", "UNCW", ["UNC Greensboro Spartans", "North Carolina Tar Heels"]),
    ("Fairleigh Dickinson Knights", "FDU", ["Iona Gaels", "Oklahoma Sooners"]),
    ("Pennsylvania Quakers", "Penn", ["Penn State Nittany Lions", "Drexel Dragons"]),
    ("St. Thomas Tommies", "St. Thomas (MN)", ["Army Black Knights", "Green Bay Phoenix"]),
    ("South Carolina Upstate Spartans", "USC Upstate", ["South Carolina Gamecocks", "USC Trojans"]),
    (
        "Arkansas-Pine Bluff Golden Lions",
        "ARK-Pine Bluff",
        ["Arkansas Razorbacks", "Arkansas State Red Wolves"],
    ),
    ("Michigan State Spartans", "Mich. State", ["Michigan Wolverines", "Detroit Mercy Titans"]),
]


@pytest.mark.parametrize(("row_name", "polymarket", "others"), _ALIASES)
def test_polymarket_abbreviation_matches_its_school_and_no_other(row_name, polymarket, others) -> None:
    assert backtests._team_matches(row_name, polymarket)
    assert pe._team_name_matches(row_name, polymarket)
    for other in others:
        assert not backtests._team_matches(other, polymarket), other


def test_umass_lowell_against_umass_still_refuses_rather_than_guess() -> None:
    """UMass is a whole-word prefix of UMass Lowell, so "UML" matches both row teams in the
    UMass Lowell vs UMass game: still refused (fail closed), never mapped to a side."""
    row = {
        "league": "NCAAB",
        "away_team": "UMass Lowell River Hawks",
        "home_team": "Massachusetts Minutemen",
        "selection": "away",
        "line": "-2.5",
    }
    with pytest.raises(pe.ExecutionGateError, match="matches both"):
        pe._resolve_spread_side(row, {"team": "UML", "line": -2.5})


# ------------------------------------------------------------------ recorded market (item 1)

_UNKNOWN = "C. Ark."  # an abbreviation no alias knows: name matching refuses it


def _pinned(selection: str, line: float, slug: str, side: str) -> dict:
    return _row(
        selection, line, f"Projected margin -1.6 (home), sd 10.7. market_slug={slug} token_side={side}"
    )


def test_recorded_market_and_side_are_used_when_names_cannot_match(snapshots) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51), _spread(_POS, _UNKNOWN, 3.5, 0.99, 0.32)])

    assert backtests._pick_quote(_row("away", -3.5)) is None  # the name path refuses
    quote = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))

    assert quote is not None
    assert (quote["market_slug"], quote["side"], quote["ask"]) == (_NEG, "long", 0.50)


def test_recorded_market_missing_from_the_snapshots_falls_back_to_name_matching(snapshots) -> None:
    snapshots(_REAL_UCA)

    quote = backtests._pick_quote(_pinned("away", -3.5, "asc-cbb-cark-jax-2026-11-21-neg-3pt5-gone", "long"))

    assert quote is not None
    assert (quote["market_slug"], quote["side"]) == (_NEG, "long")


def test_recorded_market_whose_line_no_longer_agrees_falls_back_to_the_exact_line(snapshots) -> None:
    snapshots(_REAL_UCA)

    # Recorded against the +3.5 market, but the row's line is -3.5: the pin is not trusted and the
    # exact-line name match finds the -3.5 market instead.
    quote = backtests._pick_quote(_pinned("away", -3.5, _POS, "long"))

    assert quote is not None
    assert (quote["market_slug"], quote["side"]) == (_NEG, "long")


def test_recorded_side_that_a_decisive_name_match_contradicts_is_refused(snapshots) -> None:
    # The pin says this Central Arkansas -3.5 row is the market's own team at -3.5 (long). By name the
    # market's team is Jacksonville, so long would be Jacksonville -3.5: the opposite bet.
    snapshots([_spread(_NEG, "Jacksonville", -3.5, 0.50, 0.51)])
    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None

    # Same pin when the name says nothing: the recorded side is used.
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)])
    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))["side"] == "long"


def test_recorded_moneyline_side_that_names_contradict_is_refused(snapshots) -> None:
    moneyline = {
        **_spread("aec-cbb-cark-jax-2026-11-21", None, 0.0, 0.5, 0.5),
        "market_type": "moneyline",
        "line": None,
        "long": {"description": "Dolphins", "ask": 0.41},
        "short": {"description": "Bears", "ask": 0.61},
    }
    snapshots([moneyline])
    row = {**_row("away", 0.0, "x market_slug=aec-cbb-cark-jax-2026-11-21 token_side=short"), "line": None}
    row["market_type"] = "moneyline"

    assert backtests._pick_quote(row)["side"] == "short"  # Bears, Central Arkansas: agrees
    assert backtests._pick_quote({**row, "rationale": row["rationale"].replace("short", "long")}) is None


def test_recorded_market_from_another_event_is_refused(snapshots) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_id="999999")])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None


def test_recorded_market_ignores_quotes_from_after_the_start(snapshots) -> None:
    late = _spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, observed_at_utc="2026-11-21T21:05:00Z")
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51), late])

    quote = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))

    assert quote is not None
    assert quote["ask"] == 0.50


def test_recorded_total_side_must_say_the_selection(snapshots) -> None:
    total = {
        **_spread("tsc-cbb-cark-jax-2026-11-21-140pt5", "", 140.5, 0.5, 0.5),
        "market_type": "total",
        "team": None,
        "long": {"description": "Over", "ask": 0.48},
        "short": {"description": "Under", "ask": 0.54},
    }
    snapshots([total])
    row = {
        **_row("under", 140.5, "x market_slug=tsc-cbb-cark-jax-2026-11-21-140pt5 token_side=short"),
        "market_type": "total",
    }

    assert backtests._pick_quote(row)["side"] == "short"
    wrong = {**row, "rationale": "x market_slug=tsc-cbb-cark-jax-2026-11-21-140pt5 token_side=long"}
    assert backtests._pick_quote(wrong) is None


def test_recorded_market_needs_both_tokens(snapshots) -> None:
    """Other sports already write ``market_slug=`` alone; without a recorded side the row keeps the
    name-matching path exactly as before."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)])

    assert backtests._pick_quote(_row("away", -3.5, f"x market_slug={_NEG}")) is None


def test_recorded_quote_has_the_same_shape_as_the_name_matched_one(snapshots) -> None:
    snapshots(_REAL_UCA)

    by_name = backtests._pick_quote(_row("away", -3.5))
    pinned = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))

    assert by_name is not None and pinned is not None
    assert {k: v for k, v in pinned.items() if k != "age_seconds"} == {
        k: v for k, v in by_name.items() if k != "age_seconds"
    }
    assert datetime.now(UTC) - datetime.fromisoformat(pinned["observed_at_utc"]) > timedelta(0)
