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
from model_prediction.cli import forecast as cli_forecast
from model_prediction.data_sources import polymarket_us
from model_prediction.data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)
from model_prediction.domain import EASTERN, League, MarketType, ModelOrigin, ModelState, PickRequest
from model_prediction.eligibility import (
    evaluate_esports_eligibility,
    evaluate_gated_research_eligibility,
)
from model_prediction.entities import CanonicalTeam
from model_prediction.esports import NeutralElo, _fuzzy_match_team, _team_alias_index
from model_prediction.features.base import FeatureStore
from model_prediction.ledger import PickLedger
from model_prediction.units import Exposure, UnitPolicy


def _future_request(league=League.LOL, selection="home", **overrides) -> PickRequest:
    start = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    values = {
        "event_start_utc": start,
        "event_id": overrides.pop("event_id", "evt-1"),
        "league": league,
        "away_team": "Team Away",
        "home_team": "Team Home",
        "market_type": MarketType.MONEYLINE,
        "selection": selection,
        "line": None,
        "sportsbook": "polymarket_us",
        "american_odds": 100,
        "model_probability": 0.57,
        "model_uncertainty": None,
        "model_version": "lol-neutral-series-elo-v2",
        "rationale": "Neutral Elo baseline; executable ask 0.5000 (market_slug=aec-lol-a-b-2026).",
        "risks": "test",
        "model_origin": ModelOrigin.STATISTICAL_MODEL,
        "model_state": ModelState.SHADOW_QUALIFIED,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "model_artifact_hash": "hash",
        "calibration_method": "neutral_elo",
        "calibration_version": "lol-neutral-series-elo-v2",
        "calibration_artifact_hash": "hash",
        "code_revision": "lol-neutral-series-elo-v2",
    }
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


def test_archive_settled_rows_removes_settled_but_not_open(tmp_path):
    """archive_settled_rows is the deliberate counterpart to
    remove_open_rows -- it ONLY removes settled rows (the opposite
    filter), for a distinct real need (retired model versions), not a
    weakening of remove_open_rows's own settled-row refusal."""
    ledger, open_research, staked, settled = _seed_ledger(tmp_path)
    removed = ledger.archive_settled_rows(
        [open_research["pick_id"], staked["pick_id"], settled["pick_id"]],
        reason="retired model version test cleanup",
        archive_reference="data/archive/test-archive.xlsx",
    )
    assert [row["pick_id"] for row in removed] == [settled["pick_id"]]
    remaining = {row["pick_id"] for row in ledger.rows()}
    assert open_research["pick_id"] in remaining and staked["pick_id"] in remaining
    assert settled["pick_id"] not in remaining
    events = AuditLog(tmp_path / "events.jsonl").events()
    archive_events = [e for e in events if e["event_type"] == "settled_pick_archived"]
    assert len(archive_events) == 1
    assert archive_events[0]["subject_id"] == settled["pick_id"]
    assert archive_events[0]["payload"]["reason"] == "retired model version test cleanup"
    assert archive_events[0]["payload"]["archive_reference"] == "data/archive/test-archive.xlsx"
    # Full row content survives in the audit trail even after live removal.
    assert archive_events[0]["payload"]["archived_row"]["pick_id"] == settled["pick_id"]
    assert archive_events[0]["payload"]["archived_row"]["status"] == "settled"


def test_archive_settled_rows_requires_reason_and_archive_reference(tmp_path):
    ledger, *_ = _seed_ledger(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        ledger.archive_settled_rows(["anything"], reason="  ", archive_reference="somewhere.xlsx")
    with pytest.raises(ValueError, match="archive_reference"):
        ledger.archive_settled_rows(["anything"], reason="valid reason", archive_reference="  ")


def test_archive_settled_rows_is_idempotent_on_retry(tmp_path):
    ledger, _open_research, _staked, settled = _seed_ledger(tmp_path)
    first = ledger.archive_settled_rows(
        [settled["pick_id"]], reason="cleanup", archive_reference="archive.xlsx"
    )
    assert len(first) == 1
    second = ledger.archive_settled_rows(
        [settled["pick_id"]], reason="cleanup", archive_reference="archive.xlsx"
    )
    assert second == []  # already gone -- retry is a safe no-op, not an error


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
def test_esports_settlement_maps_winner_to_correct_side(tmp_path, monkeypatch, selection, winner, expected):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(_future_request(selection=selection, event_id=f"e-{selection}-{winner}"), 0, 10)
    loser = "Team Away" if winner == "Team Home" else "Team Home"
    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", lambda: _ResolvedClient(winner, loser))
    result = _settle_esports_pick(row, ledger)
    assert result is not None and result.get("settled") is True
    assert result["result"] == expected


def test_esports_settlement_populates_clv_from_captured_closing_snapshot(tmp_path, monkeypatch):
    """Operator directive, 2026-07-31: CLV should be wired for every model,
    not just MLB. Esports settlement should look up the last pregame
    snapshot captured under data/odds/esports/{date}/ (the same file the
    daily slate capture already writes) and record it as the row's closing
    probability."""
    from model_prediction.data_sources.polymarket_us import PolymarketSnapshotStore

    event_start = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(
        _future_request(
            selection="home",
            event_id="clv-1",
            event_start_utc=event_start,
            rationale="Neutral Elo baseline; executable ask 0.5000 (market_slug=aec-lol-clv-2026).",
        ),
        0,
        10,
    )
    # Must match _closing_probability_for_moneyline_pick's own game_date
    # computation exactly (Eastern date, not raw UTC date) -- these can
    # differ near a day boundary, which made this test flaky in exactly
    # that way once real wall-clock time drifted close to one (found
    # 2026-08-01).
    game_date = datetime.fromisoformat(event_start).astimezone(EASTERN).date().isoformat()
    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "esports", game_date)
    store.append(
        {
            "provider": "polymarket_us",
            "market_slug": "aec-lol-clv-2026",
            "observed_at_utc": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "long": {"description": "Team Home", "ask": 0.62},
            "short": {"description": "Team Away", "ask": 0.41},
        }
    )
    monkeypatch.setattr(
        polymarket_us, "PolymarketUSClient", lambda: _ResolvedClient("Team Home", "Team Away")
    )
    result = _settle_esports_pick(row, ledger, data_root=tmp_path)
    assert result is not None and result.get("settled") is True
    settled_row = next(r for r in ledger.rows() if r["pick_id"] == row["pick_id"])
    assert settled_row["probability_clv"] != ""
    assert float(settled_row["closing_raw_implied_probability"]) == pytest.approx(0.62)


def test_esports_settlement_leaves_clv_blank_without_data_root(tmp_path, monkeypatch):
    """Existing callers that don't pass data_root keep working exactly as
    before -- CLV is opportunistic, never required for settlement."""
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(_future_request(event_id="no-clv-1"), 0, 10)
    monkeypatch.setattr(
        polymarket_us, "PolymarketUSClient", lambda: _ResolvedClient("Team Home", "Team Away")
    )
    result = _settle_esports_pick(row, ledger)
    assert result is not None and result.get("settled") is True
    settled_row = next(r for r in ledger.rows() if r["pick_id"] == row["pick_id"])
    assert settled_row["probability_clv"] == ""


def test_esports_settlement_stays_pending_until_terminal_state(tmp_path, monkeypatch):
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_call(_future_request(event_id="pending-1"), 0, 10)

    class _OpenClient(_ResolvedClient):
        def book(self, slug):
            return {"state": "MARKET_STATE_OPEN"}

    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", lambda: _OpenClient("Team Home", "Team Away"))
    assert _settle_esports_pick(row, ledger) is None


# ------------------------------------------------------ esports eligibility


class _StubBanList:
    """Registry-free ban stub: bans whichever team names are in the set."""

    def __init__(self, banned_teams: set[str]) -> None:
        self.banned_teams = banned_teams

    def check(self, league, team_input):
        team = CanonicalTeam(team_input, league, team_input, team_input, True, None, None, ())
        return team, team_input in self.banned_teams


def test_esports_eligibility_ban_check_arms_only_when_ban_list_provided():
    """2026-07-27 audit gap: registry-free sports never had ban enforcement.
    evaluate_esports_eligibility now checks first when a ban_list is passed
    (via bans.py's name-based registry-free fallback) and behaves exactly as
    before when callers don't thread one through."""
    request = _future_request()
    result = evaluate_esports_eligibility(
        request, Exposure(), UnitPolicy(), ban_list=_StubBanList({"Team Home"})
    )
    assert result.reason_code == "PAPER_CALL_TEAM_BANNED"

    # The banned team must not become a qualified call even with full
    # provenance; the other team is unaffected.
    result = evaluate_esports_eligibility(
        request, Exposure(), UnitPolicy(), ban_list=_StubBanList({"Nobody"})
    )
    assert result.record_type.value == "QUALIFIED_SHADOW_CALL"

    # No ban_list at all: unchanged legacy behavior.
    result = evaluate_esports_eligibility(_future_request(), Exposure(), UnitPolicy())
    assert result.record_type.value == "QUALIFIED_SHADOW_CALL"


def test_esports_eligibility_qualifies_only_with_full_provenance():
    result = evaluate_esports_eligibility(_future_request(model_probability=0.58), Exposure(), UnitPolicy())
    assert result.record_type.value == "QUALIFIED_SHADOW_CALL"
    assert result.units > 0

    incomplete = _future_request(model_artifact_hash="")
    result = evaluate_esports_eligibility(incomplete, Exposure(), UnitPolicy())
    assert result.record_type.value == "RESEARCH_OBSERVATION"
    # Still gets a real paper size -- every logged pick has units and pnl
    # (operator directive, 2026-07-31).
    assert result.units > 0


def test_esports_eligibility_fails_closed_on_stale_data():
    stale = _future_request(observed_at_utc=(datetime.now(UTC) - timedelta(hours=13)).isoformat())
    result = evaluate_esports_eligibility(stale, Exposure(), UnitPolicy())
    assert result.decision == "CALL"
    assert result.reason_code == "PAPER_CALL_STALE_DATA"
    # Still gets a real paper size (operator directive, 2026-07-31); it just
    # can't become a real CALL.
    assert result.units > 0


def test_esports_eligibility_fails_closed_on_future_data():
    """P1-4: a timestamp ahead of `now` used to pass the freshness check
    outright -- only the "too old" direction was ever guarded. Mirrors
    test_esports_eligibility_fails_closed_on_stale_data but for the other
    side of the clock."""
    future = _future_request(observed_at_utc=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    result = evaluate_esports_eligibility(future, Exposure(), UnitPolicy())
    assert result.decision == "CALL"
    assert result.reason_code == "PAPER_CALL_STALE_DATA"
    assert result.units > 0


def test_esports_eligibility_no_longer_gates_on_exposure_caps():
    """Exposure caps no longer block CALL at all (operator directive,
    2026-07-26) -- a saturated exposure state still produces a real
    qualified call, sized via the edge-scaled method."""
    saturated = Exposure(daily_units=5.0, league_daily_units=3.0)
    result = evaluate_esports_eligibility(_future_request(), saturated, UnitPolicy())
    assert result.decision == "CALL"
    assert result.reason_code == "QUALIFIED"
    assert result.units > 0


def test_gated_research_eligibility_centrally_enforces_edge_and_inputs():
    negative_edge = _future_request(model_probability=0.58, american_odds=-150)
    result = evaluate_gated_research_eligibility(
        negative_edge,
        Exposure(),
        UnitPolicy(),
        model_inputs_valid=True,
        minimum_edge=0.02,
    )
    assert result.decision == "CALL"
    assert result.reason_code == "PAPER_CALL_LOW_EDGE"
    # Downgraded from Gated Research only -- it still gets a real paper size
    # for the Research ledger (operator directive, 2026-07-31).
    assert result.units > 0

    invalid_inputs = _future_request(model_probability=0.62, american_odds=100)
    result = evaluate_gated_research_eligibility(
        invalid_inputs,
        Exposure(),
        UnitPolicy(),
        model_inputs_valid=False,
        minimum_edge=0.02,
    )
    assert result.decision == "CALL"
    assert result.reason_code == "PAPER_CALL_MODEL_UNVALIDATED"
    assert result.units > 0

    valid = evaluate_gated_research_eligibility(
        invalid_inputs,
        Exposure(),
        UnitPolicy(),
        model_inputs_valid=True,
        minimum_edge=0.02,
    )
    assert valid.decision == "CALL"
    assert valid.units > 0


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


def test_match_executable_quote_output_passes_archive_lineage_check(tmp_path):
    """A quote matched from a genuine archived snapshot must carry the
    provenance fields (`reconstructed`, `usage`) that
    ``cli.forecast._canonical_market_snapshot_lineage`` requires -- dropping
    them silently blocked every real MLB Main-ledger call while still
    matching the quote for pricing (2026-08-24)."""
    odds_dir = tmp_path / "odds/mlb/2026-07-17"
    odds_dir.mkdir(parents=True)
    snapshot_path = odds_dir / "polymarket_snapshots.jsonl"
    snapshot_path.write_text(
        json.dumps(
            {
                "market_type": "moneyline",
                "market_slug": "aec-mlb-nyy-nym-1",
                "long": {"description": "Yankees", "ask": 0.55},
                "short": {"description": "Mets", "ask": 0.47},
                "observed_at_utc": "2026-07-17T12:00:00Z",
                "timestamp_valid": True,
                "reconstructed": False,
                "usage": "prospective_executable_bbo",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class _Candidate:
        away_team = "New York Yankees"
        home_team = "New York Mets"
        selection = "home"
        event_start_utc = "2026-07-17T19:00:00Z"

    quote = learned_forward.match_executable_quote(tmp_path, "mlb", "2026-07-17", _Candidate())
    assert quote is not None
    lineage = cli_forecast._canonical_market_snapshot_lineage(quote, snapshot_path)
    assert lineage is not None
