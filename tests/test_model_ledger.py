from __future__ import annotations

import pytest

from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest
from model_prediction.eligibility import EligibilityResult, RecordType
from model_prediction.entities import CanonicalTeam
from model_prediction.model_ledger import (
    FIELDNAMES,
    INTEGRITY_FAILURE_REASONS,
    ModelLedger,
    compute_model_evidence,
    record_from_pick_request,
    settle_from_pick_row,
)

_AWAY = CanonicalTeam("mlb-nyy", League.MLB, "NYY", "NYY", True, None, None, ())
_HOME = CanonicalTeam("mlb-bos", League.MLB, "BOS", "BOS", True, None, None, ())


def _pick_request(**overrides) -> PickRequest:
    fields = {
        "event_start_utc": "2026-08-03T00:00:00Z",
        "event_id": "event-123",
        "league": League.MLB,
        "away_team": "NYY",
        "home_team": "BOS",
        "market_type": MarketType.MONEYLINE,
        "selection": "home",
        "line": None,
        "sportsbook": "polymarket_us",
        "american_odds": -140,
        "model_probability": 0.62,
        "model_uncertainty": 0.04,
        "model_version": "mlb-elo-trend-lr-v7",
        "rationale": "test",
        "risks": "",
        "model_origin": ModelOrigin.STATISTICAL_MODEL,
        "model_state": ModelState.SHADOW_QUALIFIED,
        "observed_at_utc": "2026-08-02T12:00:00Z",
        "model_artifact_hash": "hash1",
        "calibration_artifact_hash": "cal1",
        "code_revision": "rev1",
    }
    fields.update(overrides)
    return PickRequest(**fields)


def _eligibility(edge: float = 0.08) -> EligibilityResult:
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.5, 70, edge, edge, _AWAY, _HOME
    )


def _prediction(**overrides) -> dict:
    record = {
        "model_id": "mlb-moneyline-elo-trend-lr",
        "model_version": "mlb-elo-trend-lr-v7",
        "artifact_hash": "abc123",
        "code_revision": "def456",
        "feature_schema_version": "v3",
        "event_id": "event-1",
        "market_type": "moneyline",
        "selection": "home",
        "model_probability": 0.62,
        "model_uncertainty": 0.04,
        "decision_price": 0.58,
        "market_no_vig_probability": 0.55,
        "model_market_difference": 0.07,
        "observed_at_utc": "2026-08-02T12:00:00Z",
        "event_start_utc": "2026-08-02T23:00:00Z",
        "input_availability": "complete",
    }
    record.update(overrides)
    return record


def test_append_prediction_writes_every_field_and_defaults_to_open(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")

    row = ledger.append_prediction(_prediction())

    assert row["status"] == "open"
    assert row["prediction_id"]
    assert row["model_id"] == "mlb-moneyline-elo-trend-lr"
    assert row["model_probability"] == "0.62"
    assert set(row.keys()) == set(FIELDNAMES)


def test_predictions_persist_across_ledger_instances(tmp_path) -> None:
    path = tmp_path / "wnba-moneyline-elo-trend-lr.xlsx"
    ModelLedger(path).append_prediction(_prediction(event_id="event-1"))
    ModelLedger(path).append_prediction(_prediction(event_id="event-2"))

    rows = ModelLedger(path).rows()

    assert len(rows) == 2
    assert {row["event_id"] for row in rows} == {"event-1", "event-2"}


def test_settle_updates_only_the_matching_row(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "nba-moneyline-elo-trend-lr.xlsx")
    first = ledger.append_prediction(_prediction(event_id="event-1"))
    second = ledger.append_prediction(_prediction(event_id="event-2"))

    settled = ledger.settle(first["prediction_id"], result="win", closing_price=0.60, pnl_units=0.75)

    assert settled["status"] == "settled"
    assert settled["result"] == "win"
    assert settled["pnl_units"] == "0.7500"
    assert settled["settled_at_utc"]
    rows = {row["prediction_id"]: row for row in ledger.rows()}
    assert rows[second["prediction_id"]]["status"] == "open"  # untouched


def test_settle_unknown_prediction_id_raises(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "nfl-moneyline-elo-trend-lr.xlsx")
    with pytest.raises(KeyError):
        ledger.settle("does-not-exist", result="win")


def test_append_failure_requires_a_recognized_integrity_reason(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "soccer-poisson-dc.xlsx")
    with pytest.raises(ValueError, match="unrecognized integrity failure reason"):
        ledger.append_failure(
            model_id="soccer-poisson-dc",
            model_version="v1",
            event_id="event-1",
            reason="model_just_felt_like_it",
        )


def test_append_failure_writes_a_real_row_not_a_silent_drop(tmp_path) -> None:
    """Operator directive: "write a failure record to that model's ledger.
    Do not silently drop the event and do not classify the model." """
    ledger = ModelLedger(tmp_path / "soccer-poisson-dc.xlsx")

    row = ledger.append_failure(
        model_id="soccer-poisson-dc",
        model_version="v1",
        event_id="event-1",
        reason="market_sides_unmapped",
    )

    assert row["status"] == "failed"
    assert row["failure_reason"] == "market_sides_unmapped"
    assert row["failure_reason"] in INTEGRITY_FAILURE_REASONS
    assert len(ledger.rows()) == 1


def test_record_operator_decision_never_touches_model_fields(tmp_path) -> None:
    """ "Not model promotion. It is an event-level decision... must not
    change the model's ledger, classification, historical statistics, or
    dashboard evidence." -- verifies the model's own fields are byte-for-
    byte unchanged after an operator decision is recorded."""
    ledger = ModelLedger(tmp_path / "lol-tiered-elo.xlsx")
    original = ledger.append_prediction(_prediction())

    decided = ledger.record_operator_decision(
        original["prediction_id"],
        decision="executed",
        selected_model="lol-tiered-elo",
        selected_market="moneyline",
        units=2.0,
        note="clean edge",
    )

    assert decided["operator_decision"] == "executed"
    assert decided["operator_selected_model"] == "lol-tiered-elo"
    assert decided["operator_units"] == "2.0"
    assert decided["operator_timestamp"]
    from model_prediction.model_ledger import MODEL_FIELDS

    for field in MODEL_FIELDS:
        assert decided[field] == original[field]


def test_record_operator_decision_unknown_prediction_id_raises(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "cs2-tiered-elo.xlsx")
    with pytest.raises(KeyError):
        ledger.record_operator_decision("does-not-exist", decision="executed")


def test_two_models_never_share_a_ledger_file(tmp_path) -> None:
    """The whole point of this schema: one file per model identity."""
    mlb_ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    ridge_ledger = ModelLedger(tmp_path / "mlb-total-score-ridge.xlsx")
    mlb_ledger.append_prediction(_prediction(model_id="mlb-moneyline-elo-trend-lr"))
    ridge_ledger.append_prediction(_prediction(model_id="mlb-total-score-ridge", market_type="total"))

    assert len(mlb_ledger.rows()) == 1
    assert len(ridge_ledger.rows()) == 1
    assert mlb_ledger.rows()[0]["model_id"] == "mlb-moneyline-elo-trend-lr"
    assert ridge_ledger.rows()[0]["model_id"] == "mlb-total-score-ridge"


def test_append_prediction_can_preserve_a_migrated_id(tmp_path) -> None:
    """Migration code passes the original ledger row's own pick_id here
    instead of minting a fresh one, so a migrated row keeps a stable,
    traceable identity rather than a disconnected new one."""
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")

    row = ledger.append_prediction(_prediction(), prediction_id="original-pick-id-123")

    assert row["prediction_id"] == "original-pick-id-123"


def test_append_prediction_refuses_a_duplicate_id(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    ledger.append_prediction(_prediction(), prediction_id="dupe-id")

    with pytest.raises(ValueError, match="already exists"):
        ledger.append_prediction(_prediction(event_id="event-2"), prediction_id="dupe-id")


def test_record_from_pick_request_writes_into_the_right_model_ledger(tmp_path) -> None:
    row = record_from_pick_request(tmp_path, _pick_request(), _eligibility())

    assert row is not None
    assert row["model_id"] == "mlb-moneyline-elo-trend-lr"
    assert (tmp_path / "mlb-moneyline-elo-trend-lr.xlsx").exists()
    assert row["model_market_difference"] == "0.08"


def test_record_from_pick_request_dedupes_the_same_decision(tmp_path) -> None:
    """Real bug fixed 2026-08-02: the dedupe key used to compare a raw
    pre-write value (line=None) against a value already read back from the
    file (line=""), and separately compared a sportsbook value that isn't
    even part of this schema against the .get() default for a missing key
    -- both permanently mismatched, so every "duplicate" call silently
    wrote a second row instead of being deduped. The same real decision
    getting logged to more than one old destination ledger (e.g. both Main
    and Flat) must produce exactly one row here, not one per destination."""
    request = _pick_request()
    eligibility = _eligibility()

    first = record_from_pick_request(tmp_path, request, eligibility)
    second = record_from_pick_request(tmp_path, request, eligibility)

    assert first is not None
    assert second is None  # deduped, not a second row
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    assert len(ledger.rows()) == 1


def test_record_from_pick_request_dedupes_a_line_bearing_market_too(tmp_path) -> None:
    request = _pick_request(market_type=MarketType.SPREAD, line=-1.5)
    eligibility = _eligibility()

    first = record_from_pick_request(tmp_path, request, eligibility)
    second = record_from_pick_request(tmp_path, request, eligibility)

    assert first is not None
    assert second is None
    ledger = ModelLedger(tmp_path / "mlb-spread-measured-edge.xlsx")
    assert len(ledger.rows()) == 1


def test_record_from_pick_request_does_not_dedupe_different_events(tmp_path) -> None:
    eligibility = _eligibility()
    record_from_pick_request(tmp_path, _pick_request(event_id="event-1"), eligibility)
    record_from_pick_request(tmp_path, _pick_request(event_id="event-2"), eligibility)

    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    assert len(ledger.rows()) == 2


def test_record_from_pick_request_does_not_dedupe_a_refreshed_forecast(tmp_path) -> None:
    """Real bug found live 2026-08-02: a still-open pick that gets replaced
    by a fresh forecast (same event/market/line/model_version, but new
    model_probability/decision_price and a new observed_at_utc) was being
    silently treated as the same decision already on file and dropped --
    the per-model track record kept a stale row instead of the real,
    current one. This must not happen: a different observed_at_utc always
    means a distinct real-world decision."""
    eligibility = _eligibility()
    first = record_from_pick_request(
        tmp_path, _pick_request(observed_at_utc="2026-08-02T12:00:00Z"), eligibility
    )
    second = record_from_pick_request(
        tmp_path,
        _pick_request(observed_at_utc="2026-08-02T15:00:00Z", model_probability=0.71),
        eligibility,
    )

    assert first is not None
    assert second is not None
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    assert len(ledger.rows()) == 2


def test_settle_from_pick_row_grades_every_reforecast_row_of_the_event(tmp_path) -> None:
    """Real bug found live 2026-08-18 on the WNBA spread ledger: settlement
    matched on the APPEND-side key, which carries `observed_at_utc`, so only
    the one row whose forecast timestamp equalled the settled pick's ever
    graded. Every re-forecast row for the same finished game stayed open
    forever -- 42 of 67 rows stuck, so hit-rate/Brier/calibration ran on 9
    rows instead of 51. An outcome belongs to the event, not to the moment
    we forecast it: all three rows here must grade off one real result."""
    eligibility = _eligibility()
    for observed_at in ("2026-08-02T12:00:00Z", "2026-08-02T15:00:00Z", "2026-08-02T18:00:00Z"):
        record_from_pick_request(tmp_path, _pick_request(observed_at_utc=observed_at), eligibility)
    ledger = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx")
    assert len(ledger.rows()) == 3

    settled = settle_from_pick_row(
        tmp_path,
        {
            "league": "MLB",
            "market_type": "moneyline",
            "event_id": "event-123",
            "line": None,
            "selection": "home",
            "model_version": "mlb-elo-trend-lr-v7",
            # deliberately NOT equal to any row's observed_at_utc
            "observed_at_utc": "2026-08-02T21:00:00Z",
            "result": "win",
            "pnl_units": 1.5,
            "probability_clv": 0.03,
            "closing_implied_probability": 0.66,
        },
    )

    assert len(settled) == 3
    rows = ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx").rows()
    assert [row["status"] for row in rows] == ["settled"] * 3
    assert {row["result"] for row in rows} == {"win"}
    # Operator decision 2026-08-18: economic fields land on every graded row.
    assert {row["pnl_units"] for row in rows} == {"1.5000"}
    assert {row["probability_clv"] for row in rows} == {"0.030000"}


def test_settle_from_pick_row_leaves_other_events_and_lines_alone(tmp_path) -> None:
    """Event-level grading must not become league-level grading."""
    eligibility = _eligibility()
    record_from_pick_request(tmp_path, _pick_request(event_id="event-1"), eligibility)
    record_from_pick_request(tmp_path, _pick_request(event_id="event-2"), eligibility)

    settled = settle_from_pick_row(
        tmp_path,
        {
            "league": "MLB",
            "market_type": "moneyline",
            "event_id": "event-1",
            "line": None,
            "selection": "home",
            "model_version": "mlb-elo-trend-lr-v7",
            "result": "win",
        },
    )

    assert len(settled) == 1
    rows = {row["event_id"]: row for row in ModelLedger(tmp_path / "mlb-moneyline-elo-trend-lr.xlsx").rows()}
    assert rows["event-1"]["status"] == "settled"
    assert rows["event-2"]["status"] == "open"


def test_settle_from_pick_row_never_grades_the_opposite_side(tmp_path) -> None:
    """away +L and home -L are opposite sides of one game and resolve
    oppositely. A re-forecast that crossed 0.5 and flipped sides must stay
    open rather than inherit the other side's result -- grading it from the
    same pick would record a backwards outcome as real evidence."""
    eligibility = _eligibility()
    record_from_pick_request(
        tmp_path,
        _pick_request(market_type=MarketType.SPREAD, selection="away", line=5.5),
        eligibility,
    )
    record_from_pick_request(
        tmp_path,
        _pick_request(
            market_type=MarketType.SPREAD,
            selection="home",
            line=5.5,
            observed_at_utc="2026-08-02T18:00:00Z",
        ),
        eligibility,
    )

    settled = settle_from_pick_row(
        tmp_path,
        {
            "league": "MLB",
            "market_type": "spread",
            "event_id": "event-123",
            "line": 5.5,
            "selection": "away",
            "model_version": "mlb-elo-trend-lr-v7",
            "result": "win",
        },
    )

    assert len(settled) == 1
    rows = {row["selection"]: row for row in ModelLedger(tmp_path / "mlb-spread-measured-edge.xlsx").rows()}
    assert rows["away"]["status"] == "settled"
    assert rows["home"]["status"] == "open"


def test_settle_from_pick_row_is_a_no_op_when_already_settled(tmp_path) -> None:
    """Re-running settlement must not restamp or double-count a graded row."""
    record_from_pick_request(tmp_path, _pick_request(), _eligibility())
    pick_row = {
        "league": "MLB",
        "market_type": "moneyline",
        "event_id": "event-123",
        "line": None,
        "selection": "home",
        "model_version": "mlb-elo-trend-lr-v7",
        "result": "win",
        "pnl_units": 1.5,
    }

    assert len(settle_from_pick_row(tmp_path, pick_row)) == 1
    assert settle_from_pick_row(tmp_path, pick_row) == []


def test_record_from_pick_request_returns_none_for_an_unmapped_league(tmp_path) -> None:
    """Must degrade gracefully -- this always runs alongside the real,
    working PickLedger write and can never break it."""
    request = _pick_request(
        league=League.TENNIS, market_type=MarketType.SPREAD
    )  # tennis has no spread mapping

    row = record_from_pick_request(tmp_path, request, _eligibility())

    assert row is None
    assert list(tmp_path.glob("*.xlsx")) == []


def test_compute_model_evidence_on_an_empty_ledger(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "empty.xlsx")

    evidence = compute_model_evidence(ledger)

    assert evidence["total"] == 0
    assert evidence["model_id"] is None
    assert evidence["clv_coverage"] is None
    assert evidence["mean_clv"] is None
    assert evidence["calibration"]["status"] == "insufficient_sample"


def test_compute_model_evidence_excludes_pushes_from_calibration(tmp_path) -> None:
    """Matches ledger.py's own existing calibration_rows filter exactly:
    settled win/loss only -- a push is not folded in as a loss."""
    ledger = ModelLedger(tmp_path / "kbo-tie-aware-elo.xlsx")
    win = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e1", "model_probability": 0.6}
    )
    loss = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e2", "model_probability": 0.6}
    )
    push = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e3", "model_probability": 0.6}
    )
    ledger.settle(win["prediction_id"], result="win")
    ledger.settle(loss["prediction_id"], result="loss")
    ledger.settle(push["prediction_id"], result="push")

    evidence = compute_model_evidence(ledger)

    assert evidence["settled"] == 3
    assert evidence["pushes"] == 1
    assert evidence["wins"] == 1
    assert evidence["losses"] == 1
    assert evidence["calibration"]["sample_size"] == 2  # push excluded, not counted as a loss


def test_compute_model_evidence_tracks_open_settled_and_failed_separately(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "cs2-tiered-elo.xlsx")
    ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e1", "model_probability": 0.6}
    )
    settled = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e2", "model_probability": 0.6}
    )
    ledger.settle(settled["prediction_id"], result="win")
    ledger.append_failure(model_id="x", model_version="v1", event_id="e3", reason="event_already_started")

    evidence = compute_model_evidence(ledger)

    assert evidence["total"] == 3
    assert evidence["open"] == 1
    assert evidence["settled"] == 1
    assert evidence["failed"] == 1


def test_compute_model_evidence_computes_real_mean_clv_and_pnl(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "dota2-tiered-elo.xlsx")
    a = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e1", "model_probability": 0.6}
    )
    b = ledger.append_prediction(
        {"model_id": "x", "model_version": "v1", "event_id": "e2", "model_probability": 0.6}
    )
    ledger.settle(a["prediction_id"], result="win", pnl_units=1.0, probability_clv=0.02)
    ledger.settle(b["prediction_id"], result="loss", pnl_units=-0.5, probability_clv=-0.01)

    evidence = compute_model_evidence(ledger)

    assert evidence["pnl_units"] == pytest.approx(0.5)
    assert evidence["mean_clv"] == pytest.approx(0.005)
    assert evidence["clv_coverage"] == pytest.approx(1.0)


def test_rejects_a_non_xlsx_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="must be an .xlsx workbook"):
        ModelLedger(tmp_path / "model.csv")


def test_settle_event_never_grades_a_failed_integrity_row(tmp_path) -> None:
    """A `failed` row is an append_failure record saying the model never made
    a call. Settling it with a win/loss and pnl would fabricate a bet that
    never existed -- found by code review 2026-08-19."""
    ledger = ModelLedger(tmp_path / "wnba-spread-margin.xlsx")
    good = ledger.append_prediction(
        _prediction(
            model_id="wnba-spread-margin",
            market_type="spread",
            line="5.5",
            selection="away",
        )
    )
    # The failure record must carry the SAME contract fields at append
    # time -- a failure can be raised for a real event/market/line, and it
    # must never be graded as a bet regardless.
    failure = ledger.append_failure(
        model_id="wnba-spread-margin",
        model_version="mlb-elo-trend-lr-v7",
        event_id="event-1",
        reason="market_sides_unmapped",
        market_type="spread",
        line="5.5",
        selection="away",
    )
    from model_prediction.model_ledger import _event_settlement_key

    key = _event_settlement_key(good)

    settled = ledger.settle_event(key, result="win", pnl_units=1.5)

    assert len(settled) == 1
    rows = {r["prediction_id"]: r for r in ledger.rows()}
    assert rows[good["prediction_id"]]["status"] == "settled"
    assert rows[failure["prediction_id"]]["status"] == "failed"


def test_settle_event_does_not_grade_a_sign_flipped_line(tmp_path) -> None:
    """away +1.5 and away -1.5 are opposite contracts: the selected side
    receives vs. gives points. abs()-normalization would collapse them and
    grade the flipped re-forecast backwards -- found by code review
    2026-08-19."""
    ledger = ModelLedger(tmp_path / "wnba-spread-margin.xlsx")
    plus = ledger.append_prediction(
        _prediction(
            model_id="wnba-spread-margin",
            market_type="spread",
            line="1.5",
            selection="away",
        )
    )
    minus = ledger.append_prediction(
        _prediction(
            model_id="wnba-spread-margin",
            market_type="spread",
            line="-1.5",
            selection="away",
            observed_at_utc="2026-08-02T18:00:00Z",
        )
    )
    from model_prediction.model_ledger import _event_settlement_key

    settled = ledger.settle_event(_event_settlement_key(plus), result="win", pnl_units=1.0)

    assert [r["prediction_id"] for r in settled] == [plus["prediction_id"]]
    rows = {r["prediction_id"]: r for r in ledger.rows()}
    assert rows[minus["prediction_id"]]["status"] == "open"
