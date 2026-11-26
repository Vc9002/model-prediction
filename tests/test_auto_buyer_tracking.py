"""Auto-buyer tracking helpers: tip-time split, edge bucket, never-raising appends, the live
fill log wrapper and the cohort skip recorder."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from model_prediction.data_sources.polymarket_execute import ExecutionGateError, OrderSubmitUnknownError
from model_prediction.domain import parse_utc
from model_prediction.portfolio import auto_buyer_tracking as tracking
from model_prediction.portfolio.auto_executor import AutoExecutionResult, BuyCohort

COHORT = BuyCohort("ncaab-spread-v7", "ncaab", "spread", 0.05, 0.12, "live")


def _read(path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- tip time and bucket


@pytest.mark.parametrize(
    ("start", "window", "et"),
    [
        ("2026-11-25T21:59:59Z", "before_5pm_et", "2026-11-25T16:59:59-05:00"),
        ("2026-11-25T22:00:00Z", "5pm_or_later_et", "2026-11-25T17:00:00-05:00"),
        ("2026-11-26T00:00Z", "5pm_or_later_et", "2026-11-25T19:00:00-05:00"),
        # Summer time: 17:00 EDT is 21:00Z.
        ("2026-07-01T20:59:00Z", "before_5pm_et", "2026-07-01T16:59:00-04:00"),
        ("2026-07-01T21:00:00Z", "5pm_or_later_et", "2026-07-01T17:00:00-04:00"),
    ],
)
def test_tip_fields_cut_at_5pm_eastern(start, window, et):
    fields = tracking.tip_fields(start)
    assert fields["tip_window"] == window
    assert fields["tip_et"] == et
    assert fields["tip_date_et"] == et[:10]
    assert parse_utc(fields["tip_utc"]) == parse_utc(start)


@pytest.mark.parametrize("bad", [None, "", "not-a-time"])
def test_tip_fields_unknown_start_is_null_not_a_guess(bad):
    assert tracking.tip_fields(bad) == {
        "tip_utc": None,
        "tip_et": None,
        "tip_date_et": None,
        "tip_window": None,
    }


@pytest.mark.parametrize(
    ("edge", "bucket", "in_band"),
    [
        (0.049, "2-5%", False),
        (0.05, "5-8%", True),
        ("0.0799", "5-8%", True),
        (0.08, "8-12%", True),
        (0.1199, "8-12%", True),
        (0.12, "12-25%", False),
        (0.30, ">=25%", False),
        (-0.01, "<0%", False),
        ("nan", None, False),
        (None, None, False),
    ],
)
def test_edge_bucket_and_preregistered_band(edge, bucket, in_band):
    assert tracking.edge_bucket(edge) == bucket
    assert tracking.in_preregistered_band(edge) is in_band


# ---------------------------------------------------------------- never-raising append


def test_append_never_raises_and_reports_failure(tmp_path):
    blocked = tmp_path / "logs"
    blocked.write_text("a file where the logs directory should be", encoding="utf-8")
    assert tracking.append_jsonl_records(blocked / "x.jsonl", [{"a": 1}]) is False
    ok = tmp_path / "ok" / "x.jsonl"
    assert tracking.append_jsonl_records(ok, [{"a": 1}, {"b": float("nan")}]) is True
    assert tracking.append_jsonl_records(ok, []) is True
    lines = ok.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"a": 1}
    assert len(lines) == 2


# ---------------------------------------------------------------- live fill log


def _ticket(**overrides: Any) -> SimpleNamespace:
    base = {
        "pick_id": "p1",
        "market_slug": "asc-cbb-a-b-2026-11-25-neg-3pt5",
        "token_side": "long",
        "order_type": "limit_ioc",
        "price": 0.51,
        "size_shares": 19.6,
        "ioc_fallback_resting": True,
        "resting_expires_at_utc": "2026-11-25T21:58:30Z",
    }
    return SimpleNamespace(**{**base, **overrides})


def _payload() -> dict[str, Any]:
    return {
        "pick_id": "p1",
        "ticket_pick_id": "p1",
        "is_topup": False,
        "model_id": "ncaab-spread-v7",
        "market_slug": "asc-cbb-a-b-2026-11-25-neg-3pt5",
        "token_side": "long",
        "sport": "ncaab",
        "selection": "home",
        "market_type": "spread",
        "line": -3.5,
        "limit_price": 0.51,
        "shares": 19.6,
        "cost_usd": 10.0,
        "edge": 0.07,
        "event_start_utc": "2026-11-25T22:00:00Z",
        "timestamp_utc": "2026-11-25T21:40:00Z",
        "cohort": COHORT.label,
    }


def _pick_row() -> dict[str, Any]:
    return {
        "pick_id": "p1",
        "edge": "0.0712",
        "created_at_utc": "2026-11-25T21:30:00Z",
        "market_quote_observed_at_utc": "2026-11-25T21:20:00Z",
    }


def test_fill_log_records_a_submitted_order(tmp_path):
    log = tmp_path / "logs" / "fills.jsonl"
    sub = {
        "status": "submitted",
        "order_id": "O1",
        "order_ids": ["O1", "F1"],
        "order_state": "ORDER_STATE_PARTIALLY_FILLED",
        "filled_size_shares": 9.8,
        "estimated_filled_cost_usd": 4.998,
        "fallback_order_id": "F1",
        "fallback_status": "resting",
        "fallback_resting_shares": 9.8,
        "fill_known": True,
        "fee_usd": 0.05,
        "raw_response": {"order": {"avgPx": "0.505", "transactTime": "2026-11-25T21:40:02Z", "id": "O1"}},
    }

    def execute(**_kwargs: Any) -> dict[str, Any]:
        return sub

    out = tracking.execute_with_fill_log(
        execute, log_path=log, order_payload=_payload(), source_row=_pick_row(), ticket=_ticket()
    )
    assert out is sub
    (record,) = _read(log)
    assert record["outcome"] == "submitted"
    assert record["fill_path"] == "ioc_partial_plus_fallback"
    assert record["requested_shares"] == 19.6
    assert record["filled_shares"] == 9.8
    assert record["fill_rate"] == pytest.approx(0.5)
    assert record["avg_fill_price"] == pytest.approx(0.505)
    assert record["fee_usd"] == pytest.approx(0.05)
    assert record["order_ids"] == ["O1", "F1"]
    assert record["cohort"] == COHORT.label
    assert record["saved_edge"] == pytest.approx(0.0712)
    assert record["live_edge"] == pytest.approx(0.07)
    assert record["tip_window"] == "5pm_or_later_et"
    assert record["edge_bucket"] == "5-8%"
    assert record["pick_decision_utc"] == "2026-11-25T21:30:00Z"
    assert record["buyer_decision_utc"] == "2026-11-25T21:40:00Z"
    assert record["exchange_transact_utc"] == "2026-11-25T21:40:02Z"
    assert record["sent_utc"] <= record["returned_utc"]


def test_fill_log_without_exchange_price_or_fee_leaves_them_null(tmp_path):
    log = tmp_path / "fills.jsonl"
    sub = {"order_id": "O1", "filled_size_shares": 19.6, "estimated_filled_cost_usd": 9.996}
    tracking.execute_with_fill_log(
        lambda **_k: sub, log_path=log, order_payload=_payload(), source_row=_pick_row(), ticket=_ticket()
    )
    (record,) = _read(log)
    # estimated_filled_cost_usd is limit x shares, not an exchange average: never reported as one.
    assert record["avg_fill_price"] is None
    assert record["fee_usd"] is None
    assert record["fill_path"] == "ioc_full"


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ExecutionGateError("REFUSED: stale"), "refused"),
        (
            OrderSubmitUnknownError("no id", idempotency_key="k", market_slug="s", leg="primary"),
            "submit_unknown",
        ),
        (RuntimeError("boom"), "error"),
    ],
)
def test_fill_log_records_failures_and_reraises_the_same_exception(tmp_path, error, outcome):
    log = tmp_path / "fills.jsonl"

    def execute(**_kwargs: Any) -> dict[str, Any]:
        raise error

    with pytest.raises(type(error)) as caught:
        tracking.execute_with_fill_log(
            execute, log_path=log, order_payload=_payload(), source_row=_pick_row(), ticket=_ticket()
        )
    assert caught.value is error
    (record,) = _read(log)
    assert record["outcome"] == outcome
    assert record["filled_shares"] is None
    assert str(error)[:20] in record["error"]


def test_fill_log_failure_never_reaches_the_buy_path(tmp_path, monkeypatch):
    sub = {"order_id": "O1", "filled_size_shares": 1.0}
    monkeypatch.setattr(tracking, "_fill_record", lambda **_k: 1 / 0)
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    assert (
        tracking.execute_with_fill_log(
            lambda **_k: sub,
            log_path=blocked / "fills.jsonl",
            order_payload=_payload(),
            source_row=_pick_row(),
            ticket=_ticket(),
        )
        is sub
    )


# ---------------------------------------------------------------- skip recorder


def _cohort_row(pick_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "pick_id": pick_id,
        "model_version": "ncaab-spread-v7",
        "market_type": "spread",
        "selection": "home",
        "line": -3.5,
        "edge": "0.07",
        "created_at_utc": "2026-11-25T21:00:00Z",
        "event_start_utc": "2026-11-25T21:30:00Z",
        **extra,
    }


def test_skip_recorder_attributes_each_counter_to_its_row():
    now = parse_utc("2026-11-25T21:20:00Z")
    result = AutoExecutionResult()
    recorder = tracking.CohortSkipRecorder(book="live", enabled=True, now=now)
    recorder.observe(_cohort_row("a"), COHORT, result)
    result.rejected_stale_open += 1
    result.rejected_started += 1
    recorder.observe(_cohort_row("b"), COHORT, result)
    recorder.note(ask=0.53, market_slug="asc-x")
    recorder.note(live_edge=0.03)
    result.rejected_low_edge += 1
    recorder.observe(_cohort_row("bought"), COHORT, result)
    recorder.observe({"pick_id": "not-cohort"}, None, result)
    result.rejected_not_whitelisted += 1
    recorder.finish(result)
    records = result.cohort_skips
    assert [r["pick_id"] for r in records] == ["a", "b"]
    assert records[0]["reasons"] == ["rejected_stale_open", "rejected_started"]
    assert records[0]["reason"] == "rejected_started"
    assert records[1]["reason"] == "rejected_low_edge"
    assert records[1]["ask"] == 0.53
    assert records[1]["live_edge"] == 0.03
    assert records[1]["saved_edge"] == pytest.approx(0.07)
    assert records[1]["model_age_minutes"] == pytest.approx(20.0)
    assert records[1]["cohort"] == COHORT.label
    assert records[1]["book"] == "live"
    assert records[1]["tip_window"] == "before_5pm_et"
    assert records[1]["edge_bucket"] == "5-8%"
    assert records[1]["cycle_at_utc"] == "2026-11-25T21:20:00Z"


def test_skip_recorder_disabled_records_nothing():
    result = AutoExecutionResult()
    recorder = tracking.CohortSkipRecorder(book="paper", enabled=False, now=parse_utc("2026-11-25T21:20:00Z"))
    recorder.observe(_cohort_row("a"), COHORT, result)
    result.rejected_low_edge += 1
    recorder.finish(result)
    assert result.cohort_skips == []


def test_skip_recorder_reject_all_names_only_cohort_rows():
    result = AutoExecutionResult()
    recorder = tracking.CohortSkipRecorder(book="live", enabled=True, now=parse_utc("2026-11-25T21:20:00Z"))
    rows = [_cohort_row("a"), _cohort_row("b", status="settled"), {"pick_id": "x"}]
    recorder.reject_all(
        rows, lambda row: COHORT if row["pick_id"] != "x" else None, "rejected_daily_loss_breaker"
    )
    recorder.finish(result)
    assert [(r["pick_id"], r["reason"]) for r in result.cohort_skips] == [
        ("a", "rejected_daily_loss_breaker")
    ]
