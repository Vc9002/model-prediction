"""The execution re-check (polymarket_execute._resolve_spread_side) must follow the same rule as the
quote mapper fixed in 894f9542 (dashboard/backtests.py::_spread_side_for_row): a spread snapshot's
``team`` names only the market's LONG side, so the market team must match exactly one of the row's
two teams before long or short can be chosen.

Real-money bug 2026-11-25: Polymarket's "UCA" matched neither "Central Arkansas" nor "Jacksonville",
the execute-side check treated it as the opponent, and a "Central Arkansas -3.5" pick resolved to the
short side of asc-cbb-cark-jax-2026-11-21-pos-3pt5, which is Jacksonville -3.5: the opposite bet,
confirmed by the very check meant to catch it. A team matching both row teams ("Louisiana" vs
"Louisiana-Monroe") is the same hazard in reverse. Both must be REFUSED.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from model_prediction.audit import AuditLog
from model_prediction.data_sources import polymarket_execute as pe
from model_prediction.domain import iso_utc, utc_now


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


_UCA_SLUG = "asc-cbb-cark-jax-2026-11-21-pos-3pt5"
_UCA_SNAPSHOT = _spread_snapshot("Central Arkansas vs. Jacksonville", "UCA", 3.5, _UCA_SLUG)


def test_market_team_matching_neither_row_team_is_refused() -> None:
    """The real case: "Central Arkansas -3.5" against a UCA +3.5 market must never resolve to the
    short side (Jacksonville -3.5)."""
    row = _ncaab_row("Central Arkansas", "Jacksonville", "away", -3.5)

    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        pe._resolve_spread_side(row, _UCA_SNAPSHOT)


def test_market_team_matching_neither_row_team_is_refused_for_either_pick() -> None:
    # Jacksonville -3.5 happens to be the right short side, but nothing in the snapshot proves it.
    row = _ncaab_row("Central Arkansas", "Jacksonville", "home", -3.5)

    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        pe._resolve_spread_side(row, _UCA_SNAPSHOT)


@pytest.mark.parametrize(
    ("away", "home", "market_team", "selection", "row_line", "market_line"),
    [
        # "Louisiana" is a token prefix of "Louisiana-Monroe", so it matches both row teams.
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

    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        pe._resolve_spread_side(row, snapshot)


@pytest.mark.parametrize(
    ("market_team", "market_line", "selection", "row_line", "expected"),
    [
        # Market team is the AWAY team (Central Arkansas +3.5).
        ("Central Arkansas", 3.5, "away", 3.5, "long"),
        ("Central Arkansas", 3.5, "home", -3.5, "short"),
        # Market team is the HOME team (Jacksonville -3.5).
        ("Jacksonville", -3.5, "home", -3.5, "long"),
        ("Jacksonville", -3.5, "away", 3.5, "short"),
    ],
)
def test_unambiguous_market_team_still_maps_long_and_short(
    market_team: str, market_line: float, selection: str, row_line: float, expected: str
) -> None:
    snapshot = _spread_snapshot(
        "Central Arkansas vs. Jacksonville", market_team, market_line, "asc-cbb-cark-jax-2026-11-21"
    )
    row = _ncaab_row("Central Arkansas", "Jacksonville", selection, row_line)

    assert pe._resolve_spread_side(row, snapshot) == expected


@pytest.mark.parametrize(
    ("market_team", "market_line", "selection", "row_line"),
    [
        # A line mismatch never matches either side.
        ("Jacksonville", -3.5, "away", -3.5),
        ("Jacksonville", -3.5, "home", 3.5),
    ],
)
def test_line_mismatch_is_still_refused(
    market_team: str, market_line: float, selection: str, row_line: float
) -> None:
    snapshot = _spread_snapshot(
        "Central Arkansas vs. Jacksonville", market_team, market_line, "asc-cbb-cark-jax-2026-11-21"
    )
    row = _ncaab_row("Central Arkansas", "Jacksonville", selection, row_line)

    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        pe._resolve_spread_side(row, snapshot)


def test_mlb_full_name_market_team_still_maps_both_sides() -> None:
    snapshot = _spread_snapshot(
        "Los Angeles Dodgers vs. Chicago Cubs",
        "Los Angeles Dodgers",
        -1.5,
        "asc-mlb-lad-chc-2026-08-04-neg-1pt5",
    )
    base = {"league": "MLB", "away_team": "Los Angeles Dodgers", "home_team": "Chicago Cubs"}

    assert pe._resolve_spread_side({**base, "selection": "away", "line": "-1.5"}, snapshot) == "long"
    assert pe._resolve_spread_side({**base, "selection": "home", "line": "1.5"}, snapshot) == "short"


def test_execute_refuses_the_uca_ticket_and_sends_nothing(tmp_path, monkeypatch) -> None:
    """End to end through the executor gate: the wrong-side short ticket is refused before any POST."""
    start = utc_now() + timedelta(minutes=30)
    snapshot = {**_UCA_SNAPSHOT, "observed_at_utc": iso_utc(utc_now()), "event_start_utc": iso_utc(start)}
    row = {
        **_ncaab_row("Central Arkansas", "Jacksonville", "away", -3.5),
        "pick_id": "uca-1",
        "status": "open",
        "event_id": "ev-uca",
        "event_start_utc": iso_utc(start),
        "rationale": f"auto_buy market_slug:{_UCA_SLUG}",
    }
    sent: list[object] = []
    monkeypatch.setattr(pe.httpx, "request", lambda *a, **k: sent.append((a, k)))
    executor = pe.PolymarketExecutor(
        AuditLog(tmp_path / "audit.jsonl"),
        confirm=lambda _prompt: "Y",
        environ={"POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "A" * 43 + "="},
        live_quote=lambda _slug: snapshot,
    )
    ticket = pe.OrderTicket(
        market_slug=_UCA_SLUG,
        token_side="short",
        action="buy",
        order_type="limit_ioc",
        price=0.50,
        size_shares=10.0,
        pick_id="uca-1",
        estimated_cost_usd=5.0,
    )

    with pytest.raises(pe.ExecutionGateError, match="REFUSED"):
        executor.execute(ticket, row, execute_flag=True, user_command=True)
    assert sent == []
    assert [e["event_type"] for e in AuditLog(tmp_path / "audit.jsonl").events()] == []
