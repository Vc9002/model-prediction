from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.domain import League, MarketType, PickRequest, RecordType
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger
from model_prediction.units import edge_scaled_units


def request() -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        event_id="event-1",
        league=League.MLB,
        away_team="NYY",
        home_team="BOS",
        market_type=MarketType.TOTAL,
        selection="over",
        line=8.5,
        sportsbook="ExampleBook",
        american_odds=-110,
        model_probability=0.59,
        model_uncertainty=0.01,
        model_version="mlb-test-v1",
        rationale="Test rationale",
        risks="Test risk",
    )


def test_call_settle_loss_and_review(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx")
    logged = ledger.append_call(request(), 0.25, 70)
    assert ledger.report()["open"] == 1

    settled = ledger.settle(
        logged["pick_id"], away_score=2, home_score=3, closing_line=9, closing_american_odds=-115
    )
    assert settled["result"] == "loss"
    assert settled["review_status"] == "review_required"
    assert float(settled["pnl_units"]) == -0.25
    assert float(settled["probability_clv"]) > 0

    reviewed = ledger.review_loss(
        logged["pick_id"],
        "bad_luck",
        "Low-tail outcome inside forecast distribution",
        "No change; monitor cohort",
    )
    assert reviewed["review_status"] == "complete"
    assert ledger.report()["loss_reviews_required"] == 0


def test_duplicate_call_is_rejected(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx")
    ledger.append_call(request(), 0.25, 70)
    try:
        ledger.append_call(request(), 0.25, 70)
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate call was accepted")


def test_verified_closing_can_be_added_after_result_without_mutating_decision(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    logged = ledger.append_call(request(), 0.25, 70)
    settled = ledger.settle(logged["pick_id"], away_score=2, home_score=3)
    decision_before = {
        field: settled[field]
        for field in (
            "model_probability",
            "decision_american_odds",
            "decision_line",
            "rationale",
            "created_at_utc",
        )
    }
    updated = ledger.update_closing(logged["pick_id"], 9, -115)
    assert updated["closing_line"] == "9"
    assert updated["closing_american_odds"] == "-115"
    assert updated["probability_clv"]
    assert {field: updated[field] for field in decision_before} == decision_before


AWAY = CanonicalTeam("mlb-nyy", League.MLB, "NYY", "NYY", True, None, None, ())
HOME = CanonicalTeam("mlb-bos", League.MLB, "BOS", "BOS", True, None, None, ())


def _observation(reason_code: str, units: float) -> EligibilityResult:
    return EligibilityResult(
        RecordType.RESEARCH_OBSERVATION, "NO_CALL", reason_code, units, 55, 0.05, 0.05, AWAY, HOME
    )


def _qualified_call(units: float) -> EligibilityResult:
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", units, 70, 0.08, 0.08, AWAY, HOME
    )


def test_trade_candidate_reflects_positive_edge_not_record_type(tmp_path) -> None:
    """Operator directive, 2026-08-02: an honest label distinct from
    record_type. QUALIFIED_SHADOW_CALL (evaluate_eligibility, MLB/WNBA/NBA/
    NFL) no longer requires positive edge to become a real call (operator
    directive 2026-07-26 removed that gate) -- trade_candidate makes "the
    model favors this side" and "this is genuinely positive expected value"
    visibly distinct instead of both hiding behind one QUALIFIED label."""
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    positive_edge_call = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.5, 70, 0.08, 0.08, AWAY, HOME
    )
    negative_edge_call = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.5, 70, -0.03, -0.03, AWAY, HOME
    )
    zero_edge_call = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.5, 70, 0.0, 0.0, AWAY, HOME
    )

    positive_row = ledger.append_evaluated(request(), positive_edge_call)
    assert positive_row["trade_candidate"] == "True"

    negative_row = ledger.append_evaluated(
        replace(request(), event_id="event-2"), negative_edge_call
    )
    assert negative_row["record_type"] == RecordType.QUALIFIED_SHADOW_CALL.value
    assert negative_row["trade_candidate"] == "False"  # QUALIFIED but not actually positive EV

    zero_row = ledger.append_evaluated(replace(request(), event_id="event-3"), zero_edge_call)
    assert zero_row["trade_candidate"] == "False"  # edge > 0 strictly, not >=


def test_append_evaluated_also_writes_the_new_per_model_ledger(tmp_path) -> None:
    """Operator directive, 2026-08-02: every model also writes to its own
    per-model ledger going forward (data/model_ledgers/<model-id>.xlsx),
    additive alongside the existing PickLedger write, wired through the one
    chokepoint every sport's append_evaluated/append_call already shares."""
    from model_prediction.model_ledger import ModelLedger

    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")

    row = ledger.append_evaluated(request(), _qualified_call(1.5))

    model_ledger_path = tmp_path / "model_ledgers" / "mlb-total-measured-edge.xlsx"
    assert model_ledger_path.exists()
    rows = ModelLedger(model_ledger_path).rows()
    assert len(rows) == 1
    assert rows[0]["event_id"] == row["event_id"]
    assert float(rows[0]["model_market_difference"]) == pytest.approx(float(row["edge"]))


def test_a_model_ledger_write_failure_never_breaks_the_primary_ledger_write(
    tmp_path, monkeypatch
) -> None:
    """The primary PickLedger write is real, working, and already succeeded
    by the time the new-schema write happens -- a bug or lock timeout in the
    additive path must never turn into a lost/failed real pick."""
    import model_prediction.ledger as ledger_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated model_ledger failure")

    monkeypatch.setattr(ledger_module, "record_from_pick_request", _boom)
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")

    row = ledger.append_evaluated(request(), _qualified_call(1.5))

    assert row["pick_id"]
    assert ledger.report()["open"] == 1


def test_settle_also_settles_the_new_per_model_ledger(tmp_path) -> None:
    """Real bug found live 2026-08-02: ModelLedger.settle() existed but was
    never called from anywhere -- model ledger rows stayed 'open' forever
    even after the primary ledger settled the equivalent real pick, so
    per-model hit-rate/Brier/calibration evidence never populated. Wired
    through the one chokepoint every sport's settle() call already shares."""
    from model_prediction.model_ledger import ModelLedger

    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    logged = ledger.append_evaluated(request(), _qualified_call(1.5))

    ledger.settle(logged["pick_id"], away_score=5, home_score=5)

    model_ledger_path = tmp_path / "model_ledgers" / "mlb-total-measured-edge.xlsx"
    rows = ModelLedger(model_ledger_path).rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "settled"
    assert rows[0]["result"] == "win"
    assert float(rows[0]["pnl_units"]) > 0


def test_a_model_ledger_settle_failure_never_breaks_the_primary_ledger_settle(
    tmp_path, monkeypatch
) -> None:
    import model_prediction.ledger as ledger_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated model_ledger settle failure")

    monkeypatch.setattr(ledger_module, "settle_from_pick_row", _boom)
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    logged = ledger.append_evaluated(request(), _qualified_call(1.5))

    settled = ledger.settle(logged["pick_id"], away_score=5, home_score=5)

    assert settled["result"] == "win"
    assert ledger.report()["open"] == 0


def test_recompute_research_sizing_fills_in_a_zero_unit_sizable_reason(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    assert float(row["units"]) == 0.0

    changed = ledger.recompute_research_sizing()
    assert changed == 1

    updated = ledger.rows()[0]
    expected = edge_scaled_units(0.59, 0.01, -110)
    assert float(updated["units"]) == expected


def test_recompute_research_sizing_zeroes_a_hard_zero_reason_that_had_leaked_units(tmp_path) -> None:
    """Simulates the too-broad mid-session bug: a structurally-untrustworthy
    reason (team banned) must never keep a non-zero size regardless of how
    it got there."""
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _observation("NO_CALL_TEAM_BANNED", 1.5))

    changed = ledger.recompute_research_sizing()
    assert changed == 1
    assert float(ledger.rows()[0]["units"]) == 0.0


def test_recompute_research_sizing_never_touches_a_real_qualified_call(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _qualified_call(1.75))

    changed = ledger.recompute_research_sizing()
    assert changed == 0
    assert float(ledger.rows()[0]["units"]) == 1.75


def test_recompute_research_sizing_recomputes_pnl_for_already_settled_rows(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    settled = ledger.settle(row["pick_id"], away_score=5, home_score=5)  # total 10 > 8.5 -> over wins
    assert float(settled["pnl_units"]) == 0.0  # old bug: zero units -> zero pnl regardless of result

    ledger.recompute_research_sizing()
    updated = ledger.rows()[0]
    expected_units = edge_scaled_units(0.59, 0.01, -110)
    assert float(updated["units"]) == expected_units
    assert float(updated["pnl_units"]) > 0  # win, so pnl now reflects the corrected non-zero size


def test_recompute_research_sizing_is_idempotent(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    ledger.recompute_research_sizing()
    changed_again = ledger.recompute_research_sizing()
    assert changed_again == 0


def test_a_retired_ledger_never_touches_disk(tmp_path) -> None:
    """2026-08-11: data/main was archived and its automated daily job
    paused specifically because a fresh PickLedger.initialize() would have
    silently recreated data/main/*.xlsx on the very next run. retired=True
    is the fix -- every read still works (an honest empty ledger, same
    contract the dashboard's picks reader already relies on post-archival),
    every write silently no-ops instead of touching the filesystem at all:
    no workbook, no parent directory, no .lock marker."""
    path = tmp_path / "main" / "mlb.xlsx"
    ledger = PickLedger(path, tmp_path / "events.jsonl", retired=True)

    ledger.initialize()
    assert not path.exists()
    assert not path.parent.exists()

    assert ledger.rows() == []
    assert not path.exists()
    assert not path.parent.exists()

    row = ledger.append_evaluated(request(), _qualified_call(1.5))
    assert row["pick_id"]  # real business logic still ran -- callers get a well-formed row
    assert row["record_type"] == RecordType.QUALIFIED_SHADOW_CALL.value
    assert not path.exists()  # ...but nothing was ever persisted
    assert not path.parent.exists()
    assert not path.with_suffix(".xlsx.lock").exists()

    # 2026-08-11 addendum: found during the first real live run after this
    # fix landed -- self.audit.append() isn't guarded by _write_rows() at
    # all, so a retired ledger still wrote real, permanent audit events for
    # picks that never persisted anywhere. Growing verify-chain's
    # created_but_absent_without_removal_event count every day, not a
    # bounded crash artifact like the module docstring's atomicity note
    # describes. _NullAuditLog closes this.
    events_path = tmp_path / "events.jsonl"
    assert not events_path.exists()

    # Control: the exact same path, not retired, does create the workbook
    # AND write real audit events -- proves the assertions above are
    # actually exercising retired's guard, not some unrelated reason
    # nothing got written.
    live = PickLedger(path, tmp_path / "events.jsonl")
    live.append_evaluated(replace(request(), event_id="event-live"), _qualified_call(1.5))
    assert path.exists()
    assert len(live.rows()) == 1
    assert events_path.exists()
    assert events_path.read_text(encoding="utf-8").strip()
