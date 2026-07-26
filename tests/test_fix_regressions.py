"""Regression tests for the 2026-07-21 integrity/safety fixes.

Covers the previously untested glue paths where the audited bugs lived:
audited ledger removal, esports settlement score mapping, esports eligibility
gates, executor tick refusal, Eastern point-in-time cutoff, and esports Elo
update semantics.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction import learned_forward
from model_prediction.audit import AuditLog
from model_prediction.cli import _settle_esports_pick
from model_prediction.data_sources import polymarket_us
from model_prediction.data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)
from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest
from model_prediction.eligibility import evaluate_esports_eligibility
from model_prediction.esports import NeutralElo, _fuzzy_match_team, _team_alias_index
from model_prediction.features.base import FeatureStore
from model_prediction.ledger import PickLedger
from model_prediction.units import Exposure, UnitPolicy


def _future_request(league=League.LOL, selection="home", **overrides) -> PickRequest:
    start = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    values = dict(
        event_start_utc=start,
        event_id=overrides.pop("event_id", "evt-1"),
        league=league,
        away_team="Team Away",
        home_team="Team Home",
        market_type=MarketType.MONEYLINE,
        selection=selection,
        line=None,
        sportsbook="polymarket_us",
        american_odds=100,
        model_probability=0.57,
        model_uncertainty=None,
        model_version="lol-neutral-series-elo-v2",
        rationale="Neutral Elo baseline; executable ask 0.5000 (market_slug=aec-lol-a-b-2026).",
        risks="test",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
        observed_at_utc=datetime.now(UTC).isoformat(),
        model_artifact_hash="hash",
        calibration_method="neutral_elo",
        calibration_version="lol-neutral-series-elo-v2",
        calibration_artifact_hash="hash",
        code_revision="lol-neutral-series-elo-v2",
    )
    values.update(overrides)
    return PickRequest(**values)


# ---------------------------------------------------------------- ledger


def _seed_ledger(tmp_path):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    open_research = ledger.append_call(_future_request(event_id="r1"), 0, 10)
    staked = ledger.append_call(_future_request(event_id="s1"), 1.0, 50)
    settled = ledger.append_call(_future_request(event_id="d1"), 0, 10)
    ledger.settle(settled["pick_id"], 0, 1)
    return ledger, open_research, staked, settled


def test_remove_open_rows_is_audited_and_refuses_staked_and_settled(tmp_path):
    ledger, open_research, staked, settled = _seed_ledger(tmp_path)
    removed = ledger.remove_open_rows(
        [open_research["pick_id"], staked["pick_id"], settled["pick_id"]],
        reason="test cleanup",
    )
    assert removed == [open_research["pick_id"]]
    remaining = {row["pick_id"] for row in ledger.rows()}
    assert staked["pick_id"] in remaining and settled["pick_id"] in remaining
    assert open_research["pick_id"] not in remaining
    events = AuditLog(tmp_path / "events.jsonl").events()
    removal_events = [e for e in events if e["event_type"] == "pick_removed"]
    assert len(removal_events) == 1
    assert removal_events[0]["subject_id"] == open_research["pick_id"]
    assert removal_events[0]["payload"]["reason"] == "test cleanup"


def test_remove_open_rows_requires_reason(tmp_path):
    ledger, *_ = _seed_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.remove_open_rows(["anything"], reason="  ")


# ------------------------------------------------------- esports settlement


class _ResolvedClient:
    def __init__(self, winner: str, loser: str):
        self._sides = [
            {"description": winner, "price": "1"},
            {"description": loser, "price": "0"},
        ]

    def market(self, slug):
        return {"marketSides": self._sides}

    def book(self, slug):
        return {"state": "MARKET_STATE_EXPIRED"}


@pytest.mark.parametrize(
    "selection,winner,expected",
    [
        ("home", "Team Home", "win"),
        ("home", "Team Away", "loss"),
        ("away", "Team Away", "win"),
        ("away", "Team Home", "loss"),
    ],
)
def test_esports_settlement_maps_winner_to_correct_side(
    tmp_path, monkeypatch, selection, winner, expected
):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(
        _future_request(selection=selection, event_id=f"e-{selection}-{winner}"), 0, 10
    )
    loser = "Team Away" if winner == "Team Home" else "Team Home"
    monkeypatch.setattr(
        polymarket_us, "PolymarketUSClient", lambda: _ResolvedClient(winner, loser)
    )
    result = _settle_esports_pick(row, ledger)
    assert result is not None and result.get("settled") is True
    assert result["result"] == expected


def test_esports_settlement_stays_pending_until_terminal_state(tmp_path, monkeypatch):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(_future_request(event_id="pending-1"), 0, 10)

    class _OpenClient(_ResolvedClient):
        def book(self, slug):
            return {"state": "MARKET_STATE_OPEN"}

    monkeypatch.setattr(
        polymarket_us, "PolymarketUSClient", lambda: _OpenClient("Team Home", "Team Away")
    )
    assert _settle_esports_pick(row, ledger) is None


# ------------------------------------------------------ esports eligibility


def test_esports_eligibility_qualifies_only_with_full_provenance():
    result = evaluate_esports_eligibility(
        _future_request(model_probability=0.58), Exposure(), UnitPolicy()
    )
    assert result.record_type.value == "QUALIFIED_SHADOW_CALL"
    assert result.units > 0

    incomplete = _future_request(model_artifact_hash="")
    result = evaluate_esports_eligibility(incomplete, Exposure(), UnitPolicy())
    assert result.record_type.value == "RESEARCH_OBSERVATION"
    assert result.units == 0


def test_esports_eligibility_fails_closed_on_stale_data():
    stale = _future_request(
        observed_at_utc=(datetime.now(UTC) - timedelta(hours=13)).isoformat()
    )
    result = evaluate_esports_eligibility(stale, Exposure(), UnitPolicy())
    assert result.reason_code == "NO_CALL_STALE_DATA"
    assert result.units == 0


def test_esports_eligibility_no_longer_gates_on_exposure_caps():
    """Exposure caps no longer block CALL at all (operator directive,
    2026-07-26) -- a saturated exposure state still produces a real
    qualified call, sized via the edge-scaled method."""
    saturated = Exposure(daily_units=5.0, league_daily_units=3.0)
    result = evaluate_esports_eligibility(_future_request(), saturated, UnitPolicy())
    assert result.decision == "CALL"
    assert result.reason_code == "QUALIFIED"
    assert result.units > 0


# ------------------------------------------------------------- executor


def test_executor_refuses_subcent_price(tmp_path):
    ticket = OrderTicket(
        market_slug="aec-lol-a-b-2026",
        token_side="long",
        action="buy",
        order_type="limit_gtc",
        price=0.545,
        size_shares=10,
        pick_id="p1",
        estimated_cost_usd=5.45,
        maximum_cost_usd=10.0,
    )
    executor = PolymarketExecutor(
        AuditLog(tmp_path / "events.jsonl"),
        confirm=lambda prompt: "Y",
        environ={"POLYMARKET_KEY_ID": "k", "POLYMARKET_SECRET_KEY": "s"},
    )
    row = {"record_type": "QUALIFIED_SHADOW_CALL", "status": "open"}
    with pytest.raises(ExecutionGateError, match="whole-cent"):
        executor.execute(ticket, row, execute_flag=True, user_command=True)


# ----------------------------------------------------- Eastern cutoff (B8)


def test_games_before_uses_eastern_midnight(tmp_path):
    path = tmp_path / "processed/mlb/games.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {  # 9pm ET on Jul 16 = 01:00 UTC Jul 17 — must be included for Jul 17
            "event_id": "late-et-evening",
            "event_start_utc": "2026-07-17T01:00:00Z",
            "league": "MLB",
            "away_team": "A",
            "home_team": "B",
            "away_score": 1,
            "home_score": 2,
        },
        {  # 1pm ET on Jul 17 — same-ET-day, must be excluded
            "event_id": "same-et-day",
            "event_start_utc": "2026-07-17T17:00:00Z",
            "league": "MLB",
            "away_team": "A",
            "home_team": "B",
            "away_score": 3,
            "home_score": 4,
        },
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    games = FeatureStore(tmp_path).games_before("mlb", "2026-07-17")
    assert [g.event_id for g in games] == ["late-et-evening"]


# ------------------------------------------------------------ esports Elo


def test_neutral_elo_updates_against_raw_expectation():
    book = NeutralElo(k=32.0, ratings={"fav": 1700.0, "dog": 1300.0})
    raw = book.raw_probability("fav", "dog")
    assert raw > 0.85  # far outside the shrunk [0.25, 0.75] band
    before = book.ratings["fav"]
    book.update({"team1_id": "fav", "team2_id": "dog", "winner_id": "fav"})
    gained = book.ratings["fav"] - before
    assert gained == pytest.approx(32.0 * (1.0 - raw))
    assert gained < 32.0 * 0.15  # no phantom surprise from the shrunk prediction


def test_fuzzy_match_requires_unambiguous_candidate():
    teams = {
        "t1": {"team_id": "t1", "name": "Team Liquid", "slug": "team-liquid", "acronym": "TL"},
        "t2": {"team_id": "t2", "name": "Liquid Academy", "slug": "liquid-academy", "acronym": "LA"},
    }
    aliases = _team_alias_index(teams)
    assert _fuzzy_match_team("Liquid", teams, aliases) is None
    assert _fuzzy_match_team("Team Liquid", teams, aliases) == "t1"


# --------------------------------------------------- quote side ambiguity


def test_match_executable_quote_skips_doubleheaders_and_ambiguous_sides(tmp_path):
    odds_dir = tmp_path / "odds/mlb/2026-07-17"
    odds_dir.mkdir(parents=True)

    def snap(slug, long_desc, short_desc):
        return {
            "market_type": "moneyline",
            "market_slug": slug,
            "long": {"description": long_desc, "ask": 0.55},
            "short": {"description": short_desc, "ask": 0.47},
            "observed_at_utc": "2026-07-17T12:00:00Z",
        }

    class _Candidate:
        away_team = "New York Yankees"
        home_team = "New York Mets"
        selection = "home"
        event_start_utc = "2026-07-17T19:00:00Z"

    # Doubleheader: two distinct contracts for the same pair -> no match
    (odds_dir / "polymarket_snapshots.jsonl").write_text(
        json.dumps(snap("aec-mlb-nyy-nym-1", "Yankees", "Mets"))
        + "\n"
        + json.dumps(snap("aec-mlb-nyy-nym-2", "Yankees", "Mets"))
        + "\n",
        encoding="utf-8",
    )
    assert learned_forward.match_executable_quote(tmp_path, "mlb", "2026-07-17", _Candidate()) is None

    # Ambiguous side descriptions ("New York" matches both teams) -> no match
    (odds_dir / "polymarket_snapshots.jsonl").write_text(
        json.dumps(snap("aec-mlb-nyy-nym-1", "New York", "New York")) + "\n",
        encoding="utf-8",
    )
    assert learned_forward.match_executable_quote(tmp_path, "mlb", "2026-07-17", _Candidate()) is None

    # Unambiguous nicknames resolve normally
    (odds_dir / "polymarket_snapshots.jsonl").write_text(
        json.dumps(snap("aec-mlb-nyy-nym-1", "Yankees", "Mets")) + "\n", encoding="utf-8"
    )
    quote = learned_forward.match_executable_quote(tmp_path, "mlb", "2026-07-17", _Candidate())
    assert quote is not None and quote["side"] == "short"
