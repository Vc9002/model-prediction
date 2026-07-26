from __future__ import annotations

from datetime import UTC, datetime, timedelta

import dashboard_server
from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest
from model_prediction.ledger import PickLedger
from model_prediction.research_ledgers import (
    existing_research_ledgers,
    research_ledger,
    research_ledger_path,
)


def _request(event_id: str = "event-1") -> PickRequest:
    now = datetime.now(UTC)
    return PickRequest(
        event_start_utc=(now + timedelta(days=1)).isoformat(),
        event_id=event_id,
        league=League.CS2,
        away_team="Away",
        home_team="Home",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="polymarket_us",
        american_odds=100,
        model_probability=0.60,
        model_uncertainty=0.05,
        model_version="cs2-tiered-elo-v4",
        rationale="test",
        risks="test",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
        observed_at_utc=now.isoformat(),
        model_artifact_hash="artifact",
        calibration_method="neutral_elo",
        calibration_version="cs2-tiered-elo-v4",
        calibration_artifact_hash="artifact",
        code_revision="revision",
    )


def test_research_ledger_paths_are_separate_and_share_global_audit(tmp_path) -> None:
    cs2 = research_ledger(tmp_path, "CS2")
    gated_lol = research_ledger(tmp_path, "lol", gated=True)

    assert cs2.path == tmp_path / "research" / "cs2.xlsx"
    assert gated_lol.path == tmp_path / "gated_research" / "lol.xlsx"
    assert cs2.audit.path == tmp_path / "events.jsonl"
    assert gated_lol.audit.path == tmp_path / "events.jsonl"
    assert research_ledger_path(tmp_path, "npb") == tmp_path / "research" / "npb.xlsx"


def test_import_rows_is_idempotent_and_preserves_pick_identity(tmp_path) -> None:
    source = PickLedger(tmp_path / "legacy.xlsx", tmp_path / "events.jsonl")
    row = source.append_call(_request(), 1.0, 75)
    destination = research_ledger(tmp_path, "cs2")

    first = destination.import_rows(
        [row],
        source="legacy.xlsx",
        reason="split by sport",
    )
    second = destination.import_rows(
        [row],
        source="legacy.xlsx",
        reason="split by sport",
    )

    assert first == [row["pick_id"]]
    assert second == []
    assert destination.rows()[0]["pick_id"] == row["pick_id"]
    assert destination.audit.events()[-1]["event_type"] == "ledger_rows_imported"


def test_existing_research_ledgers_and_dashboard_aggregate_per_sport(
    tmp_path,
    monkeypatch,
) -> None:
    research_ledger(tmp_path, "cs2").append_call(_request("cs2-event"), 1.0, 75)
    lol_request = _request("lol-event")
    lol_request = PickRequest(**{**lol_request.__dict__, "league": League.LOL})
    research_ledger(tmp_path, "lol").append_call(lol_request, 1.0, 75)

    assert [ledger.path.stem for ledger in existing_research_ledgers(tmp_path)] == [
        "cs2",
        "lol",
    ]
    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)
    rows = dashboard_server._parse_research_picks()
    assert {row["event_id"] for row in rows} == {"cs2-event", "lol-event"}


def test_dashboard_decoration_preserves_zero_unit_research_no_call(
    monkeypatch,
) -> None:
    row = {
        "pick_id": "research-no-call",
        "status": "open",
        "record_type": "RESEARCH_OBSERVATION",
        "decision": "NO_CALL",
        "reason_code": "NO_CALL_LOW_EDGE",
        "units": 0.0,
        "pnl_units": 0.0,
        "research_score_units": None,
        "research_pnl_units": None,
        "result": None,
    }
    monkeypatch.setattr(dashboard_server, "_pick_quote", lambda _: None)
    monkeypatch.setattr(
        dashboard_server,
        "_order_readiness",
        lambda *_: (False, "model has no positive edge"),
    )
    monkeypatch.setattr(dashboard_server, "_latest_order_for_pick", lambda *_: None)
    monkeypatch.setattr(
        dashboard_server,
        "_manual_research_eligibility",
        lambda _: (False, "no"),
    )
    monkeypatch.setattr(dashboard_server, "_filled_entry_for_pick", lambda *_: None)
    monkeypatch.setattr(dashboard_server, "_suggested_units", lambda _: 1.5)
    monkeypatch.setattr(dashboard_server, "_unit_value_usd", lambda: 5.0)

    decorated = dashboard_server._decorate_pick(row)

    assert decorated["units"] == 0.0
    assert decorated["pnl_units"] == 0.0
    assert decorated["display_units"] == 1.5
