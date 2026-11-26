"""The buyer uses the exact Polymarket market and side the forecast priced against.

Live buys used to re-match Polymarket markets to a ledger row by team name at buy time
(dashboard/backtests.py::_pick_quote), which put a real-money order on the wrong team when the
exchange abbreviated a school ("UCA" for Central Arkansas, 2026-11-25). The NCAAB forecast already
knew the market and side: the slate's sides carry the exchange's own away/home ordering. It now
writes ``market_slug=<slug> token_side=<long|short>`` into the row's rationale (the established
place a row records its market, and a column the buyer's dashboard rows carry), and _pick_quote uses
that market and side when present. A row that recorded its market never falls back to name matching
(operator decision 2026-11-26): a market that was never captured, one captured but not usable, one
that does not line up with the row (another game, a half-game contract, a different line) and a
decisive name match that disagrees all refuse it, each with a reason. Only unpinned rows name-match.
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


def test_recorded_market_missing_from_the_snapshots_is_refused_not_name_matched(snapshots) -> None:
    """Operator decision 2026-11-26 (intended behaviour change; this test used to pin the fallback).
    A row that recorded its market has, by definition, a slug the day file lacks when that market was
    never captured, so name matching can only quote a DIFFERENT contract (the 11-25 UCA failure mode)."""
    snapshots(_REAL_UCA)
    gone = "asc-cbb-cark-jax-2026-11-21-neg-3pt5-gone"
    row = _pinned("away", -3.5, gone, "long")

    assert backtests._pick_quote(_row("away", -3.5))["market_slug"] == _NEG  # name matching would find it
    assert backtests._pick_quote(row) is None
    assert backtests._pick_quote_refusal(row) == (
        f"recorded market {gone}: never captured for this day; not name-matched"
    )


def test_recorded_market_whose_line_disagrees_with_the_row_is_refused(snapshots) -> None:
    """Deliberate contract change (2026-11-25): this used to fall back to name matching, which would
    have found the -3.5 market for a row whose own pin says +3.5. A captured market that contradicts
    the row is a corrupt pin or a wrong row, and either way the row is not bought. Only a market that
    was never captured falls back (the test above)."""
    snapshots(_REAL_UCA)

    # Recorded against the +3.5 market, but the row's line is -3.5.
    assert backtests._pick_quote(_pinned("away", -3.5, _POS, "long")) is None
    # Its short side is Jacksonville -3.5: a Jacksonville row at -3.5 agrees, so it is still quoted.
    assert backtests._pick_quote(_pinned("home", -3.5, _POS, "short"))["market_slug"] == _POS


def test_recorded_market_line_check_stands_alone_when_no_other_guard_applies(snapshots) -> None:
    """Names cannot match and no second contract exists, so only the line comparison can refuse."""
    snapshots([_spread(_POS, _UNKNOWN, 3.5, 0.99, 0.32)])

    assert backtests._pick_quote(_pinned("away", -3.5, _POS, "long")) is None
    assert backtests._pick_quote(_pinned("away", 3.5, _POS, "long"))["side"] == "long"

    snapshots([_total_snapshot()])
    assert backtests._pick_quote(_total_row("under", 141.5, "short")) is None
    assert backtests._pick_quote(_total_row("under", 140.5, "short"))["side"] == "short"


def test_recorded_total_whose_line_disagrees_with_the_row_is_refused(snapshots) -> None:
    other_line = "tsc-cbb-cark-jax-2026-11-21-141pt5"
    snapshots([_total_snapshot(), _total_snapshot(other_line, line=141.5)])

    assert backtests._pick_quote(_total_row("under", 140.5, "short"))["side"] == "short"
    # Recorded against the 140.5 slug, but the row says 141.5: name matching would find the 141.5
    # market, and must not be asked.
    assert backtests._pick_quote(_total_row("under", 141.5, "short")) is None


def test_recorded_market_with_an_unreadable_line_is_refused(snapshots) -> None:
    snapshots([{**_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51), "line": "n/a"}])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None


@pytest.mark.parametrize("pinned", [True, False])
def test_an_unreadable_line_on_a_known_team_is_refused_not_raised(snapshots, pinned) -> None:
    """A known team reaches the name matcher's float parse (the abbreviation case above never does);
    the name loop runs before the pin is consulted, so 'n/a' used to raise out of _pick_quote."""
    snapshots([{**_spread(_NEG, "Jacksonville", -3.5, 0.50, 0.51), "line": "n/a"}])
    row = _pinned("away", -3.5, _NEG, "long") if pinned else _row("away", -3.5)

    assert backtests._pick_quote(row) is None


@pytest.mark.parametrize("pinned", [True, False])
def test_an_unreadable_total_line_is_refused_not_raised(snapshots, pinned) -> None:
    snapshots([_total_snapshot(line="n/a")])
    row = (
        _total_row("under", 140.5, "short")
        if pinned
        else {**_total_row("under", 140.5, "short"), "rationale": "x"}
    )

    assert backtests._pick_quote(row) is None


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


_TOTAL = "tsc-cbb-cark-jax-2026-11-21-140pt5"


def _total_snapshot(slug: str = _TOTAL, **extra) -> dict:
    return {
        **_spread(slug, "", 140.5, 0.5, 0.5),
        "market_type": "total",
        "team": None,
        "long": {"description": "Over", "ask": 0.48},
        "short": {"description": "Under", "ask": 0.54},
        **extra,
    }


def _total_row(selection: str, line: float, side: str, slug: str = _TOTAL) -> dict:
    return {
        **_row(selection, line, f"x market_slug={slug} token_side={side}"),
        "market_type": "total",
    }


@pytest.mark.parametrize("row_event_id", ["", None])
def test_recorded_market_is_refused_when_the_row_has_no_event_id(snapshots, row_event_id) -> None:
    """The pin's only same-game check compares event ids, and it used to be skipped when either was
    empty, which left a recorded slug unchecked against the row's game."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)])

    assert backtests._pick_quote({**_pinned("away", -3.5, _NEG, "long"), "event_id": row_event_id}) is None


@pytest.mark.parametrize("snapshot_event_id", ["", None])
def test_recorded_market_is_refused_when_the_snapshot_has_no_event_id(snapshots, snapshot_event_id) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_id=snapshot_event_id)])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None


def test_recorded_total_of_another_game_at_the_same_line_is_refused(snapshots) -> None:
    """Same event id and the same 140.5 line, but the snapshot's title names a different game: the
    pin must not stand in for the game check name matching makes."""
    snapshots([_total_snapshot(event_title="Duke vs. Kentucky")])

    assert backtests._pick_quote(_total_row("under", 140.5, "short")) is None
    snapshots([_total_snapshot()])
    assert backtests._pick_quote(_total_row("under", 140.5, "short"))["side"] == "short"


def test_recorded_spread_whose_title_names_another_game_is_refused(snapshots) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_title="Duke vs. Kentucky")])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None


@pytest.mark.parametrize("marker", ["-1st-", "-h1-", "-h2-", "-fh-", "-sh-", "-f5-"])
def test_recorded_half_game_market_is_refused(snapshots, marker) -> None:
    slug = f"asc-cbb-cark-jax{marker}2026-11-21-neg-3pt5"
    snapshots([_spread(slug, _UNKNOWN, -3.5, 0.50, 0.51)])

    assert backtests._pick_quote(_pinned("away", -3.5, slug, "long")) is None


def test_recorded_market_is_refused_when_a_second_game_matches_the_row_by_name(snapshots) -> None:
    """The name path never guesses between two contracts for the same teams (a doubleheader); the
    pin must not either, even though its own event id still agrees."""
    other_game = _spread("asc-cbb-cark-jax-2026-11-21-b-neg-3pt5", "UCA", -3.5, 0.40, 0.61, event_id="999999")
    snapshots([_spread(_NEG, "UCA", -3.5, 0.50, 0.51), other_game])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None
    snapshots([_spread(_NEG, "UCA", -3.5, 0.50, 0.51)])
    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))["ask"] == 0.50


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


# ------------------------------------------- a captured pin that qualifies for nothing is refused

# Another contract of the same game, same team and line, pregame: what name matching finds when it is
# (wrongly) asked after the recorded market failed to qualify.
_ALT = "asc-cbb-cark-jax-2026-11-21-alt-neg-3pt5"


def _name_candidate() -> dict:
    return _spread(_ALT, "UCA", -3.5, 0.44, 0.57)


_POST_TIP = "2026-11-21T21:05:00Z"

_UNQUALIFIED_PINS = [
    pytest.param({"observed_at_utc": _POST_TIP}, id="only-post-tip"),
    pytest.param({"timestamp_valid": False}, id="timestamp-invalid"),
    pytest.param({"market_type": "total"}, id="other-market-type"),
]


@pytest.mark.parametrize("override", _UNQUALIFIED_PINS)
def test_captured_pin_that_nothing_qualifies_is_refused_not_name_matched(snapshots, override) -> None:
    """Operator decision 2026-11-26: the recorded slug HAS stored snapshots but none is pregame, valid
    and of the row's market type. That used to fall through to name matching, which quoted the
    same-teams contract below (a different market than the one the forecast priced)."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, **override), _name_candidate()])

    assert backtests._pick_quote(_row("away", -3.5))["market_slug"] == _ALT  # name matching would find it
    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None


@pytest.mark.parametrize("override", _UNQUALIFIED_PINS)
def test_refusal_of_a_captured_pin_names_the_slug_and_why(snapshots, override) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, **override), _name_candidate()])

    reason = backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long"))

    assert reason is not None
    assert _NEG in reason
    assert "no qualifying pregame snapshot" in reason
    assert backtests._pick_quote_refusal(_row("away", -3.5)) is None  # an unpinned row is not refused


def test_captured_total_pin_that_nothing_qualifies_is_refused_not_name_matched(snapshots) -> None:
    other = "tsc-cbb-cark-jax-2026-11-21-alt-140pt5"
    snapshots([_total_snapshot(observed_at_utc=_POST_TIP), _total_snapshot(other)])

    assert (
        backtests._pick_quote({**_total_row("under", 140.5, "short"), "rationale": "x"})["market_slug"]
        == other
    )
    assert backtests._pick_quote(_total_row("under", 140.5, "short")) is None
    assert _TOTAL in backtests._pick_quote_refusal(_total_row("under", 140.5, "short"))


def test_never_captured_pin_is_refused_with_a_reason_and_not_name_matched(snapshots) -> None:
    """Operator decision 2026-11-26: no snapshot of the recorded slug exists in the day file (or its
    neighbours). The same-teams contract below is what name matching would have quoted."""
    snapshots([_name_candidate()])
    slug = "asc-cbb-cark-jax-2026-11-21-never-neg-3pt5"
    row = _pinned("away", -3.5, slug, "long")

    assert backtests._pick_quote(_row("away", -3.5))["market_slug"] == _ALT  # name matching would find it
    assert backtests._pick_quote(row) is None
    assert backtests._pick_quote_refusal(row) == (
        f"recorded market {slug}: never captured for this day; not name-matched"
    )


def test_pinned_row_with_no_day_file_at_all_is_refused_with_a_reason(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backtests, "_resolve_snapshot_path", lambda _sport, _day: tmp_path / "absent.jsonl")

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is None
    assert "never captured" in backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long"))
    assert backtests._pick_quote_refusal(_row("away", -3.5)) is None  # an unpinned row has no pin to blame


@pytest.fixture
def day_files(monkeypatch, tmp_path: Path):
    """Snapshot files keyed by ET day, as the capture writes them (the exchange start's ET date)."""

    def write(by_day: dict[str, list[dict]]) -> None:
        paths = {}
        for day, items in by_day.items():
            path = tmp_path / f"snap-{day}.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")
            paths[day] = path
        monkeypatch.setattr(
            backtests,
            "_resolve_snapshot_path",
            lambda _sport, day: paths.get(day, tmp_path / f"snap-{day}-absent.jsonl"),
        )

    return write


@pytest.mark.parametrize("capture_day", ["2026-11-20", "2026-11-22"])
def test_pin_captured_in_an_adjacent_day_file_is_quoted_not_refused(day_files, capture_day) -> None:
    """The row's ET day is 2026-11-21 but a midnight straddle can file the capture under the next or
    previous ET day; the exact recorded slug is looked up there before refusing. The row's own day file
    holds a same-teams contract that must NOT be what gets quoted."""
    day_files(
        {
            "2026-11-21": [_name_candidate()],
            capture_day: [_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)],
        }
    )

    quote = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))

    assert quote is not None
    assert (quote["market_slug"], quote["side"], quote["ask"]) == (_NEG, "long", 0.50)
    assert backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long")) is None


def test_pin_absent_from_the_row_day_and_its_neighbours_is_refused(day_files) -> None:
    far_slug = "asc-cbb-cark-jax-2026-11-21-far-neg-3pt5"
    day_files(
        {
            "2026-11-21": [_name_candidate()],
            "2026-11-22": [_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)],
            "2026-11-19": [_spread(far_slug, _UNKNOWN, -3.5, 0.5, 0.5)],
        }
    )

    far = _pinned("away", -3.5, far_slug, "long")  # captured two days away: not a neighbour

    assert backtests._pick_quote(far) is None
    assert "never captured" in backtests._pick_quote_refusal(far)


def test_adjacent_day_pin_that_nothing_qualifies_is_still_refused(day_files) -> None:
    day_files(
        {
            "2026-11-21": [_name_candidate()],
            "2026-11-22": [_spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, observed_at_utc=_POST_TIP)],
        }
    )
    row = _pinned("away", -3.5, _NEG, "long")

    assert backtests._pick_quote(row) is None
    assert "no qualifying pregame snapshot" in backtests._pick_quote_refusal(row)


def test_pin_without_an_executable_ask_on_its_side_is_refused_with_a_reason(snapshots) -> None:
    """The pinned snapshot qualifies and lines up, but the recorded side has no usable ask: a bare
    None would read as the generic "no mapping" in the buyer's skip log."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 1.0, 0.51)])
    row = _pinned("away", -3.5, _NEG, "long")

    assert backtests._pick_quote(row) is None
    assert backtests._pick_quote_refusal(row) == f"recorded market {_NEG}: no executable ask on long"


@pytest.mark.parametrize("observed", ["not-a-time", "", None])
def test_unreadable_observed_time_disqualifies_the_pinned_snapshot(snapshots, observed) -> None:
    """Fail closed: a snapshot whose observation time cannot be read cannot be shown to be pregame.
    Before, it was accepted and then crashed _quote_from_snapshot with UnboundLocalError."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, observed_at_utc=observed)])
    row = _pinned("away", -3.5, _NEG, "long")

    assert backtests._pick_quote(row) is None
    assert "no qualifying pregame snapshot" in backtests._pick_quote_refusal(row)


@pytest.mark.parametrize("observed", ["not-a-time", "", None])
def test_unreadable_observed_time_disqualifies_the_name_matched_snapshot(snapshots, observed) -> None:
    snapshots([_spread(_NEG, "UCA", -3.5, 0.50, 0.51, observed_at_utc=observed)])

    assert backtests._pick_quote(_row("away", -3.5)) is None


def test_stale_but_pregame_pin_is_still_quoted_and_marked_not_fresh(snapshots) -> None:
    """Age is not a qualification test here: the downstream freshness check and the buyer's live refresh
    handle it, on the pinned slug. Only the absence of any usable pregame snapshot refuses."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, observed_at_utc="2026-11-21T14:00:00Z")])

    quote = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))

    assert quote is not None
    assert (quote["market_slug"], quote["fresh"]) == (_NEG, False)
    assert backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long")) is None


def test_every_pin_refusal_carries_a_reason(snapshots) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, 3.5, 0.50, 0.51)])  # recorded market's line contradicts the row
    assert "does not line up" in backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long"))

    other_game = _spread("asc-cbb-cark-jax-2026-11-21-b-neg-3pt5", "UCA", -3.5, 0.40, 0.61, event_id="999999")
    snapshots([_spread(_NEG, "UCA", -3.5, 0.50, 0.51), other_game])  # a second game matches by name
    assert "another contract" in backtests._pick_quote_refusal(_pinned("away", -3.5, _NEG, "long"))


def test_decorated_pick_surfaces_the_pin_refusal_as_its_block_reason(monkeypatch) -> None:
    orders = importlib.import_module("model_prediction.dashboard.orders")

    def refused(_row, refusals=None):
        refusals.append("recorded market x: refused")

    monkeypatch.setattr(orders, "_pick_quote", refused)
    row = {
        **_pinned("away", -3.5, _NEG, "long"),
        "status": "open",
        "units": 1.0,
        "pick_id": "p1",
        "pick_tag": "qualified",
    }

    decorated = orders._decorate_pick(row, {}, {}, set())

    assert decorated["quote"] is None
    assert decorated["quote_refusal_reason"] == "recorded market x: refused"
    assert (decorated["buy_ready"], decorated["buy_block_reason"]) == (False, "recorded market x: refused")

    monkeypatch.setattr(orders, "_pick_quote", lambda _row, refusals=None: None)
    unmapped = orders._decorate_pick(row, {}, {}, set())
    assert unmapped["quote_refusal_reason"] is None
    assert unmapped["buy_block_reason"] == "no exact executable Polymarket US market mapping"


def test_decorating_a_refused_pinned_pick_resolves_the_quote_once(monkeypatch, snapshots) -> None:
    """The quote and its refusal reason come from one resolver pass, not two."""
    orders = importlib.import_module("model_prediction.dashboard.orders")
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, observed_at_utc=_POST_TIP)])
    real = backtests._resolve_pick_quote
    calls: list[int] = []

    def counting(row, refusals):
        calls.append(1)
        return real(row, refusals)

    monkeypatch.setattr(backtests, "_resolve_pick_quote", counting)
    row = {
        **_pinned("away", -3.5, _NEG, "long"),
        "status": "open",
        "units": 1.0,
        "pick_id": "p1",
        "pick_tag": "qualified",
    }

    decorated = orders._decorate_pick(row, {}, {}, set())

    assert len(calls) == 1
    assert "no qualifying pregame snapshot" in decorated["quote_refusal_reason"]
    assert decorated["buy_block_reason"] == decorated["quote_refusal_reason"]


def test_scan_prices_recaptures_the_blocking_pinned_slug(monkeypatch, snapshots) -> None:
    """A pinned row with no usable quote still names its recorded slug, so "scan prices" re-captures
    exactly the contract that is blocking it."""
    orders = importlib.import_module("model_prediction.dashboard.orders")
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.97, 0.03, observed_at_utc=_POST_TIP)])
    row = {
        **_pinned("away", -3.5, _NEG, "long"),
        "status": "open",
        "pick_id": "p1",
    }
    unpinned = {**_row("home", 3.5), "event_id": "999999", "status": "open", "pick_id": "p2"}
    monkeypatch.setattr(orders, "_resolve_runner", lambda: ["model-prediction"])
    monkeypatch.setattr(orders, "_load_archive", lambda: {"pick_ids": []})
    monkeypatch.setattr(orders, "_all_ledger_rows_for_price_scan", lambda: [row, unpinned])
    monkeypatch.setattr(orders, "_pick_quote", lambda _row, refusals=None: None)

    command = orders._action_command("refresh_prices", {"date": "2026-11-21"})

    assert command == [
        "model-prediction",
        "polymarket-ledger-prices",
        "--date",
        "2026-11-21",
        "--contract",
        f"ncaab@2026-11-21={_NEG}",
    ]


# ------------------------------------------------------ the exchange's own start bounds "pregame"

_EARLIER = "2026-11-21T20:00:00Z"  # the exchange moved the game an hour before the row's start


@pytest.mark.parametrize(
    ("observed", "quoted"),
    [("2026-11-21T20:18:00Z", False), ("2026-11-21T19:55:00Z", True)],
)
def test_pin_is_pregame_only_before_the_earlier_of_row_and_exchange_start(
    snapshots, observed, quoted
) -> None:
    """The buy gates only look at the row's start. A quote taken after the exchange's earlier tip but
    before the row's start must not pass as pregame (it is buyable in-play)."""
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_start_utc=_EARLIER, observed_at_utc=observed)])
    row = _pinned("away", -3.5, _NEG, "long")

    assert (backtests._pick_quote(row) is not None) is quoted
    if not quoted:
        assert "no qualifying pregame snapshot" in backtests._pick_quote_refusal(row)


@pytest.mark.parametrize("start", ["not-a-time", "", None, "2026-11-21T21:00:00"], ids=str)
def test_pin_snapshot_without_a_readable_aware_exchange_start_does_not_qualify(snapshots, start) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_start_utc=start)])
    row = _pinned("away", -3.5, _NEG, "long")

    assert backtests._pick_quote(row) is None
    assert "no qualifying pregame snapshot" in backtests._pick_quote_refusal(row)


def test_name_matching_also_stops_at_the_exchanges_earlier_start(snapshots) -> None:
    after_exchange_start = _spread(_ALT, "UCA", -3.5, 0.44, 0.57, event_start_utc=_EARLIER)
    after_exchange_start["observed_at_utc"] = "2026-11-21T20:18:00Z"
    snapshots([after_exchange_start])
    assert backtests._pick_quote(_row("away", -3.5)) is None

    before = {**after_exchange_start, "observed_at_utc": "2026-11-21T19:55:00Z"}
    snapshots([before])
    assert backtests._pick_quote(_row("away", -3.5))["market_slug"] == _ALT


def test_quote_carries_the_exchange_start_and_readiness_uses_the_earlier_start(snapshots) -> None:
    orders = importlib.import_module("model_prediction.dashboard.orders")
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    past = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    row = {
        **_pinned("away", -3.5, _NEG, "long"),
        "status": "open",
        "units": 1.0,
        "pick_tag": "qualified",
        "event_start_utc": future,
    }
    quote = {"fresh": True, "market_state": "MARKET_STATE_OPEN", "event_start_utc": past, "ask": 0.5}

    assert orders._event_already_started(row) is False
    assert orders._event_already_started(row, quote) is True
    assert orders._event_already_started(row, {**quote, "event_start_utc": future}) is False
    assert orders._order_readiness(row, quote) == (False, "game has already started")

    snapshots(
        [
            _spread(
                _NEG,
                _UNKNOWN,
                -3.5,
                0.50,
                0.51,
                event_start_utc=_EARLIER,
                observed_at_utc="2026-11-21T19:55:00Z",
            )
        ]
    )
    exposed = backtests._pick_quote(_pinned("away", -3.5, _NEG, "long"))
    assert exposed["event_start_utc"] == _EARLIER


@pytest.mark.parametrize(
    ("exchange_start", "quoted"),
    [("2026-11-21T21:04:00Z", True), ("2026-11-21T21:06:00Z", False), ("2026-11-21T22:00:00Z", False)],
)
def test_pin_found_under_a_neighbour_day_needs_the_exchange_start_within_tolerance(
    day_files, exchange_start, quoted
) -> None:
    """A capture filed under another ET day than the row is only the same game when the exchange's
    start agrees with the row's within the live buyer's tip-time tolerance (300 s)."""
    day_files(
        {
            "2026-11-21": [_name_candidate()],
            "2026-11-22": [_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_start_utc=exchange_start)],
        }
    )
    row = _pinned("away", -3.5, _NEG, "long")

    assert (backtests._pick_quote(row) is not None) is quoted
    if not quoted:
        assert "captured under another ET day" in backtests._pick_quote_refusal(row)


def test_same_day_pin_keeps_no_tolerance_check_beyond_pregame(snapshots) -> None:
    snapshots([_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51, event_start_utc="2026-11-21T22:00:00Z")])

    assert backtests._pick_quote(_pinned("away", -3.5, _NEG, "long")) is not None


# ------------------------------------------------ neighbour probes never poison the negative cache


def test_a_next_day_file_created_after_a_probe_is_seen(monkeypatch, tmp_path: Path) -> None:
    paths = {day: tmp_path / f"snap-{day}.jsonl" for day in ("2026-11-20", "2026-11-21", "2026-11-22")}
    paths["2026-11-21"].write_text(json.dumps(_name_candidate()) + "\n", encoding="utf-8")
    monkeypatch.setattr(backtests, "_resolve_snapshot_path", lambda _sport, day: paths[day])
    row = _pinned("away", -3.5, _NEG, "long")

    assert backtests._pick_quote(row) is None  # probes 11-20 and 11-22: neither exists yet
    assert not {paths["2026-11-20"], paths["2026-11-22"]} & backtests._MISSING_PATHS

    paths["2026-11-22"].write_text(
        json.dumps(_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)) + "\n", encoding="utf-8"
    )

    assert backtests._pick_quote(row)["market_slug"] == _NEG


# ------------------------------------------------------------- timezone-naive times fail closed


@pytest.mark.parametrize("pinned", [True, False])
def test_naive_observed_time_disqualifies_the_snapshot(snapshots, pinned) -> None:
    snapshots([_spread(_NEG, "UCA", -3.5, 0.50, 0.51, observed_at_utc="2026-11-21T20:55:53")])
    row = _pinned("away", -3.5, _NEG, "long") if pinned else _row("away", -3.5)

    assert backtests._pick_quote(row) is None  # was a TypeError out of the observed >= start compare


@pytest.mark.parametrize("start", ["2026-11-21T21:00:00", "not-a-time", ""])
def test_row_without_an_aware_event_start_gets_no_quote_and_a_pinned_row_a_reason(snapshots, start) -> None:
    snapshots(_REAL_UCA)
    pinned = {**_pinned("away", -3.5, _NEG, "long"), "event_start_utc": start}
    unpinned = {**_row("away", -3.5), "event_start_utc": start}

    assert backtests._pick_quote(pinned) is None
    assert backtests._pick_quote_refusal(pinned) == (
        f"recorded market {_NEG}: row has no timezone-aware event start"
    )
    assert backtests._pick_quote(unpinned) is None
    assert backtests._pick_quote_refusal(unpinned) is None


# ----------------------------------------------- scan prices names the day whose file holds the slug


def _scan_command(monkeypatch, orders, rows) -> list[str]:
    monkeypatch.setattr(orders, "_resolve_runner", lambda: ["model-prediction"])
    monkeypatch.setattr(orders, "_load_archive", lambda: {"pick_ids": []})
    monkeypatch.setattr(orders, "_all_ledger_rows_for_price_scan", lambda: rows)
    return orders._action_command("refresh_prices", {"date": "2026-11-21"})


def test_scan_prices_names_the_day_of_the_file_that_holds_the_slug(monkeypatch, day_files) -> None:
    orders = importlib.import_module("model_prediction.dashboard.orders")
    day_files({"2026-11-21": [_name_candidate()], "2026-11-22": [_spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)]})
    row = {**_pinned("away", -3.5, _NEG, "long"), "status": "open", "pick_id": "p1"}

    command = _scan_command(monkeypatch, orders, [row])

    # The quote resolves from the 11-22 file, so the refresh must name 11-22, not the row's 11-21.
    assert command[-2:] == ["--contract", f"ncaab@2026-11-22={_NEG}"]


def test_scan_prices_skips_a_pin_that_no_day_file_holds(monkeypatch, day_files) -> None:
    orders = importlib.import_module("model_prediction.dashboard.orders")
    day_files({"2026-11-21": [_name_candidate()]})
    row = {**_pinned("away", -3.5, _NEG, "long"), "status": "open", "pick_id": "p1"}

    assert "--contract" not in _scan_command(monkeypatch, orders, [row])


def test_scan_prices_contract_is_refreshable_through_the_real_refresh_function(
    monkeypatch, tmp_path: Path
) -> None:
    """Run the emitted --contract through refresh_contract_snapshots against real stores: it only
    extends a slug already in the named day's file, so a wrong day shows up as a failure."""
    from model_prediction.data_sources.polymarket_us import (
        PolymarketSnapshotStore,
        refresh_contract_snapshots,
    )

    orders = importlib.import_module("model_prediction.dashboard.orders")
    stored = _spread(_NEG, _UNKNOWN, -3.5, 0.50, 0.51)
    PolymarketSnapshotStore.for_sport_date(tmp_path, "ncaab", "2026-11-22").append(stored)
    PolymarketSnapshotStore.for_sport_date(tmp_path, "ncaab", "2026-11-21").append(_name_candidate())
    monkeypatch.setattr(
        backtests,
        "_resolve_snapshot_path",
        lambda sport, day: PolymarketSnapshotStore.for_sport_date(tmp_path, sport, day).path,
    )
    row = {**_pinned("away", -3.5, _NEG, "long"), "status": "open", "pick_id": "p1"}

    command = _scan_command(monkeypatch, orders, [row])
    contracts = []
    for value in command[command.index("--contract") :][1::2]:
        sport_day, _, slug = value.partition("=")
        sport, _, day = sport_day.partition("@")
        contracts.append({"sport": sport, "market_slug": slug, "game_date": day})

    class Client:
        def snapshot(self, slug: str) -> dict:
            return {**stored, "market_slug": slug, "observed_at_utc": _OBSERVED}

    result = refresh_contract_snapshots(Client(), contracts, tmp_path, "2026-11-21")

    assert (result["refreshed"], result["status"]) == (1, "ok")
