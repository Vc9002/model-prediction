"""Per-model auto-buyer cohorts (quick-261125-31d).

A cohort is an operator-set state-file entry that lets one model's rows inside a saved-edge window
through the buyer's categorical gates, in exactly one book: ``paper`` (simulated fills) or ``live``
(real orders, scheduled). These tests pin the fail-closed parse, the row window, the config
invariant that keeps a paper cohort out of an executing config, the submission guard, and the
cycle wiring that keeps the legacy whitelist (``nba-spread-margin-v9``) paper while cohorts exist.

Cycle-test harness: ``auto_executor.DATA``, ``PAPER_AUTO_BUYER_DATA_ROOT`` (= DATA / "paper_auto_buyer")
and ``AUTO_BUYER_CYCLE_HISTORY_PATH`` point at tmp; ``load_auto_buyer_state`` returns the test state;
``save_auto_buyer_state`` and ``_append_auto_buyer_cycle_history`` are recorders;
``_read_cohort_candidate_picks`` and the dashboard pick readers return fixed rows;
``model_prediction.dashboard.orders._decorate_pick`` returns a fresh open quote keyed by the row;
``_capture_missing_active_snapshot_slates`` returns []; ``_load_pick_input_flags`` returns {};
``PolymarketExecutor`` is a fake that counts constructions and ``execute`` calls; the ledger's
``reconcile_pending_auto_buyer_fallbacks`` and ``settle_auto_buyer_ledger`` are recorders. No test
here can reach the exchange: ``PolymarketUSClient`` raises.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

import pytest

from model_prediction.audit import AuditLog
from model_prediction.calibration_pipeline import ServingCalibration
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger, auto_executor
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    BuyCohort,
    _parse_cohorts,
    _row_in_cohort,
)

ML_V7M = "ncaab-moneyline-v7m"
SPREAD_V7 = "ncaab-spread-v7"
TOTAL_V6 = "ncaab-total-v6"
NBA_SPREAD = "nba-spread-margin-v9"

SPREAD_V7_LIVE = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "live")
ML_V7M_PAPER = BuyCohort(ML_V7M, "ncaab", "moneyline", 0.12, None, "paper")
TOTAL_V6_PAPER = BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.08, "paper")


def _entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "model_id": SPREAD_V7,
        "sport": "ncaab",
        "market": "spread",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.12,
        "mode": "live",
        "enabled": True,
    }
    return {**base, **overrides}


OPERATOR_COHORTS = [
    _entry(
        model_id=ML_V7M,
        market="moneyline",
        min_saved_edge=0.12,
        max_saved_edge_exclusive=None,
        mode="paper",
        note="D-01",
        approved_by="operator",
        approved_at_utc="2026-11-25",
    ),
    _entry(model_id=TOTAL_V6, market="total", max_saved_edge_exclusive=0.08, mode="paper", note="D-03"),
    _entry(note="D-02", approved_by="operator"),
]


class FakeExecutor:
    """Stands in for PolymarketExecutor; counts constructions and submissions. Never trades."""

    constructed: ClassVar[int] = 0
    executed: ClassVar[list[Any]] = []
    rest_only: ClassVar[bool] = False  # IOC fills nothing; the whole ticket rests as a fallback
    rest_state: ClassVar[str] = "ORDER_STATE_PARTIALLY_FILLED"

    def __init__(self, **_kwargs: Any) -> None:
        FakeExecutor.constructed += 1

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"positions": {}}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        FakeExecutor.executed.append((ticket, dict(pick_row)))
        if FakeExecutor.rest_only:
            return {
                "status": "submitted",
                "order_id": f"sim-{ticket.pick_id}",
                "order_ids": [f"sim-{ticket.pick_id}"],
                "order_state": FakeExecutor.rest_state,
                "filled_size_shares": 0.0,
                "estimated_filled_cost_usd": 0.0,
                "fallback_order_id": f"fb-{ticket.pick_id}",
                "fallback_status": "resting",
                "fallback_resting_shares": ticket.size_shares,
            }
        return {
            "status": "submitted",
            "order_id": f"sim-{ticket.pick_id}",
            "order_ids": [f"sim-{ticket.pick_id}"],
            "order_state": "ORDER_STATE_FILLED",
            "filled_size_shares": ticket.size_shares,
            "estimated_filled_cost_usd": ticket.estimated_cost_usd,
        }


class _NoExchange:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        raise RuntimeError("tests must never reach the exchange")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    FakeExecutor.constructed = 0
    FakeExecutor.executed = []
    FakeExecutor.rest_only = False
    FakeExecutor.rest_state = "ORDER_STATE_PARTIALLY_FILLED"
    monkeypatch.delenv("POLYMARKET_KEY_ID", raising=False)
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", lambda *_a, **_k: (0.0, 0.0))
    monkeypatch.setattr(
        auto_executor,
        "calibrate_for_serving",
        lambda _model, _market, probability, **_k: ServingCalibration(
            probability, probability, False, "identity", "test_isolation"
        ),
    )
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", FakeExecutor)
    monkeypatch.setattr(auto_executor, "_load_pick_input_flags", lambda *_a, **_k: {})
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", _NoExchange)


def _row(
    model_id: str,
    *,
    pick_id: str,
    event: str,
    market: str = "spread",
    selection: str | None = None,
    edge: Any = "0.07",
    model_probability: float = 0.58,
    ask: float = 0.51,
    line: Any = -3.5,
    units: Any = 2.0,
    league: str = "ncaab",
    start_minutes: float = 20,
    **extra: Any,
) -> dict[str, Any]:
    now = utc_now()
    prefix = {"moneyline": "aec", "spread": "asc", "total": "tsc"}[market]
    sport_slug = "cbb" if league == "ncaab" else league
    suffix = {"moneyline": "", "spread": "-neg-3pt5", "total": "-140pt5"}[market]
    row = {
        "pick_id": pick_id,
        "event_id": event,
        "model_version": model_id,
        "league": league,
        "market_type": market,
        "selection": selection or ("over" if market == "total" else "home"),
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": edge,
        "line": None if market == "moneyline" else line,
        "model_probability": model_probability,
        # Raw and served agree: the B7 guard needs no canonical ledger read.
        "model_probability_raw": model_probability,
        "calibration_method": "identity",
        "market_probability": ask,
        "units": units,
        "home_team": f"{event}-home",
        "away_team": f"{event}-away",
        "market_slug": f"{prefix}-{sport_slug}-{event}-2026-11-25{suffix}",
        "token_side": "long",
        "test_ask": ask,
        "created_at_utc": iso_utc(now - timedelta(minutes=1)),
        "event_start_utc": iso_utc(now + timedelta(minutes=start_minutes)),
        "market_quote_provenance": "archived_two_sided_bbo",
        "market_quote_timestamp_valid": "True",
        "market_quote_observed_at_utc": iso_utc(now - timedelta(minutes=2)),
        "market_snapshot_hash": "abc",
        "market_snapshot_archive_path": "archive.jsonl",
    }
    if units is None:
        row.pop("units")
    row.update(extra)
    return row


def _quote_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "quote": {
            "market_slug": row["market_slug"],
            "side": "long",
            "ask": row.get("test_ask"),
            "fresh": row.get("test_fresh", True),
            "age_seconds": row.get("test_age_seconds", 5),
            "market_state": row.get("test_market_state", "MARKET_STATE_OPEN"),
            # The exchange snapshot's own start; a live buy needs it to agree with the row's tip time.
            "event_start_utc": row.get("event_start_utc"),
        },
    }


@pytest.fixture
def decorate(monkeypatch):
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _quote_from_row)


# ---------------------------------------------------------------- parse


def test_operator_cohorts_parse_to_three():
    parsed = _parse_cohorts({"cohorts": OPERATOR_COHORTS})
    assert parsed.errors == ()
    assert set(parsed.active) == {ML_V7M_PAPER, TOTAL_V6_PAPER, SPREAD_V7_LIVE}
    ml = next(c for c in parsed.active if c.model_id == ML_V7M)
    assert ml.max_saved_edge_exclusive is None
    assert SPREAD_V7_LIVE.label == "ncaab-spread-v7:spread:[0.05,0.12):live"
    assert ML_V7M_PAPER.label == "ncaab-moneyline-v7m:moneyline:[0.12,inf):paper"


def test_absent_cohorts_key_is_silent_and_empty():
    assert _parse_cohorts({"mode": "live"}).active == ()
    assert _parse_cohorts({"mode": "live"}).errors == ()


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-dict",
        {k: v for k, v in _entry().items() if k != "max_saved_edge_exclusive"},
        {**{k: v for k, v in _entry().items() if k != "max_saved_edge_exclusive"}, "max_saved_edge": 0.12},
        _entry(min_saved_edge=True),
        _entry(min_saved_edge=float("nan")),
        _entry(min_saved_edge="0.05"),
        _entry(min_saved_edge=0.12, max_saved_edge_exclusive=0.12),
        _entry(min_saved_edge=0.13, max_saved_edge_exclusive=0.12),
        _entry(min_saved_edge=-0.01),
        _entry(max_saved_edge_exclusive=1.5),
        _entry(mode="Live"),
        _entry(mode="both"),
        _entry(market="nrfi"),
        _entry(model_id=""),
        _entry(model_id="   "),
        _entry(max_saved_edge_exclusive=None),
        _entry(enabled="true"),
        _entry(sport=""),
    ],
)
def test_malformed_cohort_is_ignored_and_named(bad):
    parsed = _parse_cohorts({"cohorts": [bad]})
    assert parsed.active == ()
    assert len(parsed.errors) == 1


def test_disabled_cohort_is_inactive_not_an_error():
    parsed = _parse_cohorts({"cohorts": [_entry(enabled=False)]})
    assert parsed.active == ()
    assert parsed.errors == ()


@pytest.mark.parametrize("value", [None, {}, "cohorts", {"model_id": SPREAD_V7}])
def test_cohorts_value_not_a_list_gives_zero_cohorts(value):
    parsed = _parse_cohorts({"cohorts": value})
    assert parsed.active == ()
    assert parsed.errors


def test_overlapping_cohorts_of_one_model_are_both_dropped():
    parsed = _parse_cohorts(
        {
            "cohorts": [
                _entry(),
                _entry(min_saved_edge=0.10, max_saved_edge_exclusive=0.20, mode="paper"),
                OPERATOR_COHORTS[1],
            ]
        }
    )
    assert parsed.active == (TOTAL_V6_PAPER,)
    assert len(parsed.errors) == 1


def test_adjacent_windows_of_one_model_both_kept():
    parsed = _parse_cohorts(
        {"cohorts": [_entry(), _entry(min_saved_edge=0.12, max_saved_edge_exclusive=None, mode="paper")]}
    )
    assert parsed.errors == ()
    assert len(parsed.active) == 2


def test_overlap_with_a_disabled_cohort_is_not_an_overlap():
    parsed = _parse_cohorts({"cohorts": [_entry(), _entry(mode="paper", enabled=False)]})
    assert parsed.active == (SPREAD_V7_LIVE,)
    assert parsed.errors == ()


# ---------------------------------------------------------------- window


def test_row_window_and_exclusions():
    row = _row(SPREAD_V7, pick_id="p", event="g1", edge="0.05")
    assert _row_in_cohort(row, SPREAD_V7_LIVE)
    assert not _row_in_cohort({**row, "edge": "0.12"}, SPREAD_V7_LIVE)
    assert _row_in_cohort({**row, "edge": 0.1199}, SPREAD_V7_LIVE)
    assert not _row_in_cohort({**row, "edge": 0.0499}, SPREAD_V7_LIVE)
    ml = _row(ML_V7M, pick_id="m", event="g1", market="moneyline", edge=0.50)
    assert _row_in_cohort(ml, ML_V7M_PAPER)
    for change in (
        {"units": 0},
        {"units": "0"},
        {"units": "abc"},
        {"units": -1.0},
        {"model_version": "ncaab-spread-v6"},
        {"league": "nba"},
        {"market_type": "total"},
        {"status": "superseded"},
        {"market_quote_provenance": None},
        {"market_quote_timestamp_valid": "False"},
        {"market_snapshot_hash": None},
        {"line": None},
        {"edge": None},
        {"edge": "nan"},
    ):
        assert not _row_in_cohort({**row, **change}, SPREAD_V7_LIVE), change
    assert _row_in_cohort(_row(SPREAD_V7, pick_id="p", event="g1", units=None), SPREAD_V7_LIVE)
    assert _row_in_cohort({**row, "units": ""}, SPREAD_V7_LIVE)
    indicative = {
        **row,
        "market_quote_provenance": "indicative_slate",
        "market_quote_timestamp_valid": "False",
        "market_quote_observed_at_utc": None,
        "market_snapshot_hash": None,
        "market_snapshot_archive_path": None,
    }
    assert _row_in_cohort(indicative, SPREAD_V7_LIVE)
    assert not _row_in_cohort({**indicative, "market_quote_timestamp_valid": "True"}, SPREAD_V7_LIVE)


# ---------------------------------------------------------------- config invariant


def test_executing_config_cannot_carry_a_paper_cohort():
    with pytest.raises(ValueError):
        AutoExecutionConfig(execute_live=True, cohorts=(ML_V7M_PAPER,))
    with pytest.raises(ValueError):
        AutoExecutionConfig(mode="paper", cohorts=(SPREAD_V7_LIVE,))
    assert AutoExecutionConfig(mode="paper", cohorts=(ML_V7M_PAPER,)).execute_live is False
    assert AutoExecutionConfig(mode="live", execute_live=True, cohorts=(SPREAD_V7_LIVE,)).cohorts


# ---------------------------------------------------------------- buyer, live cohort


def _live_buyer(tmp_path, **overrides: Any) -> AutoPolymarketBuyer:
    kwargs: dict[str, Any] = {
        "mode": "live",
        "execute_live": True,
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 250.0,
        "whitelisted_models": (),
        "disabled_sport_markets": (("ncaab", "spread"),),
        "cohorts": (SPREAD_V7_LIVE,),
        "edge_scaled_sizing_enabled": False,
        "require_model_timestamp": True,
        "capture_missing_slates": False,
    }
    kwargs.update(overrides)
    return AutoPolymarketBuyer(
        config=AutoExecutionConfig(**kwargs), audit=AuditLog(tmp_path / "audit.jsonl"), data_root=tmp_path
    )


def test_live_cohort_buys_only_inside_saved_and_live_window(tmp_path, decorate):
    assert SPREAD_V7 in auto_executor.EXPLICIT_BLACKLIST_MODELS
    rows = [
        _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51),
        _row(SPREAD_V7, pick_id="live-high", event="g2", edge="0.07", model_probability=0.64, ask=0.51),
        _row(SPREAD_V7, pick_id="saved-low", event="g3", edge="0.04", model_probability=0.58, ask=0.51),
        _row(SPREAD_V7, pick_id="no-units", event="g4", units=0),
    ]
    result = _live_buyer(tmp_path).evaluate_and_execute(rows)
    assert [ticket.pick_id for ticket, _ in FakeExecutor.executed] == ["in"]
    assert result.cohort_matched == 2
    assert result.rejected_unrealistic_edge == 1
    assert result.rejected_blacklist == 2
    order = result.submitted_orders[0]
    assert order["cohort"] == SPREAD_V7_LIVE.label
    assert order["cost_usd"] == pytest.approx(10.0, abs=0.06)


def test_live_cohort_below_window_live_edge_is_low_edge(tmp_path, decorate):
    row = _row(SPREAD_V7, pick_id="live-low", event="g1", edge="0.07", model_probability=0.53, ask=0.51)
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_low_edge == 1


def test_live_cohort_keeps_timing_quote_and_market_guards(tmp_path, decorate):
    rows = [
        _row(SPREAD_V7, pick_id="started", event="g1", start_minutes=-5),
        _row(SPREAD_V7, pick_id="stale", event="g2", test_fresh=False, test_age_seconds=1000),
        _row(SPREAD_V7, pick_id="closed", event="g3", test_market_state="MARKET_STATE_CLOSED"),
        _row(SPREAD_V7, pick_id="far", event="g4", start_minutes=60 * 30),
    ]
    result = _live_buyer(tmp_path).evaluate_and_execute(rows)
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0
    assert result.rejected_started == 1
    assert result.rejected_stale_quote == 1
    assert result.rejected_closed_market == 1
    assert result.rejected_future_slate == 1


def test_submission_guard_refuses_whitelisted_row_in_cohort_config(tmp_path, decorate):
    nba = _row(
        NBA_SPREAD, pick_id="nba", event="n1", league="nba", edge="0.07", model_probability=0.58, ask=0.51
    )
    buyer = _live_buyer(tmp_path, whitelisted_models=(NBA_SPREAD,), disabled_sport_markets=())
    with pytest.raises(RuntimeError, match="nba-spread-margin-v9"):
        buyer.evaluate_and_execute([nba])
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0


# ---------------------------------------------------------------- cycle


def _cycle_harness(monkeypatch, tmp_path, state: dict[str, Any], rows: list[dict[str, Any]]):
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(
        auto_executor, "AUTO_BUYER_CYCLE_HISTORY_PATH", tmp_path / "logs" / "auto_buyer_cycle_history.jsonl"
    )
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    calls: dict[str, list[Any]] = {
        "saved": [],
        "history": [],
        "reconciled": [],
        "reconcile_kwargs": [],
        "settled": [],
        "configs": [],
        "cohort_reads": [],
    }
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda s: calls["saved"].append(s))
    monkeypatch.setattr(
        auto_executor,
        "_append_auto_buyer_cycle_history",
        lambda summary, history_path=None: calls["history"].append((summary, history_path)),
    )
    monkeypatch.setattr(
        auto_executor,
        "_read_cohort_candidate_picks",
        lambda: calls["cohort_reads"].append(1) or [dict(r) for r in rows],
    )
    monkeypatch.setattr(
        "model_prediction.dashboard.picks.read_picks", lambda *a, **k: [dict(r) for r in rows]
    )
    monkeypatch.setattr("model_prediction.dashboard.picks.read_flat_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks._parse_research_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _quote_from_row)
    monkeypatch.setattr(auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: [])
    monkeypatch.setattr(
        auto_buyer_ledger,
        "reconcile_pending_auto_buyer_fallbacks",
        lambda **kwargs: calls["reconciled"].append(1) or calls["reconcile_kwargs"].append(kwargs) or {},
    )
    monkeypatch.setattr(
        auto_buyer_ledger,
        "settle_auto_buyer_ledger",
        lambda *a, **k: calls["settled"].append(k.get("data_root")) or {},
    )
    original = AutoPolymarketBuyer.evaluate_and_execute

    def spy(self, picks=None):
        calls["configs"].append(self.config)
        return original(self, picks)

    monkeypatch.setattr(AutoPolymarketBuyer, "evaluate_and_execute", spy)
    return calls


def _state(cohorts: Any, *, enabled: bool, mode: str = "live") -> dict[str, Any]:
    return {
        "enabled": enabled,
        "mode": mode,
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 250.0,
        "max_daily_loss_usd": 250.0,
        "max_event_lookahead_hours": 0.5,
        "whitelist_models": [NBA_SPREAD],
        "disabled_sport_markets": ["ncaab:spread", "ncaab:moneyline", "ncaab:total", "nba:total"],
        "cohorts": cohorts,
    }


def _mixed_rows() -> list[dict[str, Any]]:
    return [
        _row(
            NBA_SPREAD, pick_id="nba", event="n1", league="nba", edge="0.07", model_probability=0.58, ask=0.51
        ),
        _row(SPREAD_V7, pick_id="spread", event="g1", edge="0.07", model_probability=0.58, ask=0.51),
    ]


def test_cycle_live_cohort_only_reaches_executor_and_global_pass_is_paper(monkeypatch, tmp_path):
    calls = _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _mixed_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert [row["model_version"] for _, row in FakeExecutor.executed] == [SPREAD_V7]
    global_configs = [c for c in calls["configs"] if not c.cohorts]
    assert len(global_configs) == 1
    assert global_configs[0].execute_live is False
    assert global_configs[0].mode == "paper"
    assert calls["reconciled"] == [1]
    assert summary["cohorts_governed"] is True
    assert summary["cohorts_active"] == [SPREAD_V7_LIVE.label]
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_EXECUTION"
    assert summary["cohort_passes"]["live"]["cohort_matched"] == 1


@pytest.mark.parametrize(("enabled", "override"), [(False, None), (False, True), (True, False)])
def test_cycle_kill_switch_and_override_never_execute_cohorts(monkeypatch, tmp_path, enabled, override):
    calls = _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=enabled), _mixed_rows())
    summary = auto_executor.run_auto_buyer_cycle(execute_override=override)
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0
    assert all(c.execute_live is False for c in calls["configs"])
    assert calls["reconciled"] == []
    assert summary["cohort_passes"]["live"]["mode"] == ("LIVE_DRY_RUN" if enabled else "LIVE_DISABLED")


def test_cycle_malformed_cohorts_still_force_global_paper(monkeypatch, tmp_path):
    calls = _cycle_harness(monkeypatch, tmp_path, _state("not-a-list", enabled=True), _mixed_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert all(c.execute_live is False for c in calls["configs"])
    assert summary["cohorts_governed"] is True
    assert summary["cohort_config_errors"]
    assert summary["cohorts_active"] == []


# ---------------------------------------------------------------- Task 2: paper pass, books, preview

NBA_V9_PAPER = BuyCohort(NBA_SPREAD, "nba", "spread", 0.12, None, "paper")
TOTAL_V6_LIVE = BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.08, "live")

# Operator cohorts as of 2026-11-25 (late): two live, two paper.
OPERATOR_COHORTS_FINAL = [
    _entry(
        model_id=ML_V7M, market="moneyline", min_saved_edge=0.12, max_saved_edge_exclusive=None, mode="paper"
    ),
    _entry(
        model_id=NBA_SPREAD,
        sport="nba",
        market="spread",
        min_saved_edge=0.12,
        max_saved_edge_exclusive=None,
        mode="paper",
    ),
    _entry(),
    _entry(model_id=TOTAL_V6, market="total", max_saved_edge_exclusive=0.08, mode="live"),
]

_REAL_TODAY_TOTALS = auto_executor._today_auto_buyer_totals


def _ledger_rows(path) -> list[dict[str, Any]]:
    return auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=path)


def _write_ledger(path, rows: list[dict[str, Any]]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_operator_final_cohorts_parse_to_four():
    parsed = _parse_cohorts({"cohorts": OPERATOR_COHORTS_FINAL})
    assert parsed.errors == ()
    assert set(parsed.active) == {ML_V7M_PAPER, NBA_V9_PAPER, SPREAD_V7_LIVE, TOTAL_V6_LIVE}


def test_live_cohorts_never_cross_match():
    total = _row(TOTAL_V6, pick_id="t", event="g1", market="total", edge="0.06")
    spread = _row(SPREAD_V7, pick_id="s", event="g1", edge="0.09")
    config = AutoExecutionConfig(mode="live", cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE))
    assert auto_executor._match_cohort(total, config) == TOTAL_V6_LIVE
    assert auto_executor._match_cohort(spread, config) == SPREAD_V7_LIVE
    # A 9% total is inside the spread window but must never borrow it.
    assert auto_executor._match_cohort({**total, "edge": "0.09"}, config) is None
    assert auto_executor._match_cohort({**spread, "model_version": TOTAL_V6}, config) is None


def test_nba_spread_paper_cohort_bypasses_nba_floor_and_max_edge(tmp_path, decorate):
    rows = [
        _row(
            NBA_SPREAD,
            pick_id="nba13",
            event="n1",
            league="nba",
            edge="0.13",
            model_probability=0.64,
            ask=0.51,
        ),
        _row(
            NBA_SPREAD,
            pick_id="nba10",
            event="n2",
            league="nba",
            edge="0.10",
            model_probability=0.61,
            ask=0.51,
        ),
    ]
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            mode="paper",
            cohorts=(NBA_V9_PAPER,),
            whitelisted_models=(),
            disabled_sport_markets=(("nba", "spread"),),
            capture_missing_slates=False,
            require_model_timestamp=True,
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )
    result = buyer.evaluate_and_execute(rows)
    assert [o["pick_id"] for o in result.dry_run_orders] == ["nba13"]
    assert result.dry_run_orders[0]["status"] == "paper_filled"
    assert result.dry_run_orders[0]["cohort"] == NBA_V9_PAPER.label
    assert result.rejected_not_whitelisted == 1
    assert FakeExecutor.constructed == 0
    assert FakeExecutor.executed == []


def test_cycle_paper_cohorts_record_paper_fills_and_never_touch_executor(monkeypatch, tmp_path):
    rows = [
        _row(
            ML_V7M,
            pick_id="ml-dog",
            event="g1",
            market="moneyline",
            edge="0.15",
            model_probability=0.45,
            ask=0.30,
        ),
        _row(
            ML_V7M,
            pick_id="ml-fav",
            event="g2",
            market="moneyline",
            edge="0.15",
            model_probability=0.85,
            ask=0.70,
        ),
        _row(
            ML_V7M,
            pick_id="ml-phantom",
            event="g3",
            market="moneyline",
            edge="0.15",
            model_probability=0.60,
            ask=0.30,
        ),
        _row(
            NBA_SPREAD,
            pick_id="nba13",
            event="n1",
            league="nba",
            edge="0.13",
            model_probability=0.64,
            ask=0.51,
        ),
    ]
    cohorts = [OPERATOR_COHORTS_FINAL[0], OPERATOR_COHORTS_FINAL[1]]
    calls = _cycle_harness(
        monkeypatch, tmp_path, {**_state(cohorts, enabled=True), "whitelist_models": []}, rows
    )
    summary = auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.constructed == 0
    assert FakeExecutor.executed == []
    paper = _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in paper) == ["ml-dog", "ml-fav", "nba13"]
    assert not (tmp_path / "auto_buyer_ledger.jsonl").exists()
    paper_pass = summary["cohort_passes"]["paper"]
    assert paper_pass["mode"] == "PAPER_TRADING"
    assert paper_pass["rejected_unrealistic_edge"] == 1
    assert "live" not in summary["cohort_passes"]
    assert all(c.execute_live is False for c in calls["configs"])
    passes = [summary_row.get("pass") for summary_row, _ in calls["history"]]
    assert sorted(passes) == ["cohort_paper", "global"]


def _mixed_four_rows() -> list[dict[str, Any]]:
    return [
        _row(SPREAD_V7, pick_id="spread9", event="g1", edge="0.09", model_probability=0.60, ask=0.51),
        _row(
            TOTAL_V6,
            pick_id="total6",
            event="g2",
            market="total",
            edge="0.06",
            model_probability=0.57,
            ask=0.51,
        ),
        _row(
            TOTAL_V6,
            pick_id="total9",
            event="g3",
            market="total",
            edge="0.09",
            model_probability=0.60,
            ask=0.51,
        ),
        _row(
            ML_V7M,
            pick_id="ml15",
            event="g4",
            market="moneyline",
            edge="0.15",
            model_probability=0.55,
            ask=0.40,
        ),
        _row(
            NBA_SPREAD,
            pick_id="nba13",
            event="n1",
            league="nba",
            edge="0.13",
            model_probability=0.64,
            ask=0.51,
        ),
    ]


def test_cycle_two_live_cohorts_and_two_paper_cohorts(monkeypatch, tmp_path):
    state = {**_state(OPERATOR_COHORTS_FINAL, enabled=True), "whitelist_models": []}
    calls = _cycle_harness(monkeypatch, tmp_path, state, _mixed_four_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert sorted(t.pick_id for t, _ in FakeExecutor.executed) == ["spread9", "total6"]
    live = _ledger_rows(tmp_path / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in live) == ["spread9", "total6"]
    paper = _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in paper) == ["ml15", "nba13"]
    assert summary["cohort_passes"]["live"]["cohort_matched"] == 2
    assert summary["cohort_passes"]["live"]["orders_count"] == 2
    assert calls["reconciled"] == [1]


def test_cycle_disabled_four_cohorts_does_nothing(monkeypatch, tmp_path):
    state = {**_state(OPERATOR_COHORTS_FINAL, enabled=False), "whitelist_models": []}
    _cycle_harness(monkeypatch, tmp_path, state, _mixed_four_rows())
    summary = auto_executor.run_auto_buyer_cycle(execute_override=True)
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0
    assert not (tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl").exists()
    assert not (tmp_path / "auto_buyer_ledger.jsonl").exists()
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_DISABLED"


LEGACY_V6_BUCKET_STATE = {
    "enabled": True,
    "min_saved_edge": 0.05,
    "max_saved_edge_exclusive": 0.08,
}
V6_ID = "ncaab-spread-v6"


def test_old_state_keys_for_the_removed_v6_bucket_and_whitelist_load_and_buy_nothing_extra(
    monkeypatch, tmp_path
):
    """A state file written before 2026-11-26 still carries ``ncaab_spread_v6_live_bucket`` and
    ``whitelist_models``; both are ignored, so the cycle buys exactly what the cohorts buy."""
    state = {
        **_state(OPERATOR_COHORTS_FINAL, enabled=True),
        "whitelist_models": [],
        "ncaab_spread_v6_live_bucket": LEGACY_V6_BUCKET_STATE,
    }
    v6_row = _row(V6_ID, pick_id="legacy6", event="g7", edge="0.06", model_probability=0.57, ask=0.51)
    calls = _cycle_harness(monkeypatch, tmp_path, state, [*_mixed_four_rows(), v6_row])
    auto_executor.run_auto_buyer_cycle()
    assert sorted(t.pick_id for t, _ in FakeExecutor.executed) == ["spread9", "total6"]
    live = _ledger_rows(tmp_path / "auto_buyer_ledger.jsonl")
    paper = _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in live) == ["spread9", "total6"]
    assert sorted(r["pick_id"] for r in paper) == ["ml15", "nba13"]
    assert all(c.ncaab_spread_v6_live_bucket is None for c in calls["configs"])


def test_v6_bucket_state_key_cannot_arm_a_live_config(tmp_path, decorate):
    """Before 2026-11-26 this key turned a live config's ncaab-spread-v6 rows at 5-8% saved edge
    into a buyable cohort that bypassed the blacklist and the disabled sport-market gate."""
    state = {"ncaab_spread_v6_live_bucket": LEGACY_V6_BUCKET_STATE, "whitelist_models": []}
    kwargs = auto_executor._base_auto_execution_kwargs_from_state(state)
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            **{
                **kwargs,
                "disabled_sport_markets": (("ncaab", "spread"),),
                "blacklisted_models": (V6_ID,),
                "edge_scaled_sizing_enabled": False,
            },
            mode="live",
            execute_live=True,
            require_model_timestamp=True,
            capture_missing_slates=False,
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )
    row = _row(V6_ID, pick_id="legacy6", event="g1", edge="0.06", model_probability=0.57, ask=0.51)
    result = buyer.evaluate_and_execute([row])
    assert FakeExecutor.executed == [] and FakeExecutor.constructed == 0
    assert result.rejected_blacklist == 1


def test_live_per_game_cap_is_shared_across_spread_and_total(tmp_path, decorate):
    rows = [
        _row(SPREAD_V7, pick_id="s", event="g1", edge="0.09", model_probability=0.60, ask=0.51, units=3.0),
        _row(
            TOTAL_V6,
            pick_id="t",
            event="g1",
            market="total",
            edge="0.06",
            model_probability=0.57,
            ask=0.51,
            units=3.0,
        ),
    ]
    result = _live_buyer(tmp_path, cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE)).evaluate_and_execute(rows)
    costs = [t.estimated_cost_usd for t, _ in FakeExecutor.executed]
    assert len(costs) == 2
    assert costs[0] == pytest.approx(15.0, abs=0.06)
    assert sum(costs) <= 25.0 + 0.10
    assert result.total_spend_usd <= 25.10


def test_live_per_game_cap_counts_ledger_exposure(tmp_path, decorate):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            {
                "pick_id": "old",
                "event_id": "g1",
                "selection": "over",
                "market_type": "total",
                "status": "open",
                "cost_usd": 24.8,
                "shares": 48,
                "market_slug": "tsc-cbb-g1-2026-11-25-140pt5",
                "token_side": "long",
            }
        ],
    )
    row = _row(SPREAD_V7, pick_id="s", event="g1", edge="0.09", model_probability=0.60, ask=0.51)
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert FakeExecutor.executed == []
    assert result.rejected_budget == 1


def _caps_state() -> dict[str, Any]:
    return {**_state([OPERATOR_COHORTS_FINAL[0], _entry()], enabled=True), "whitelist_models": []}


def _caps_rows() -> list[dict[str, Any]]:
    return [
        _row(SPREAD_V7, pick_id="spread9", event="g1", edge="0.09", model_probability=0.60, ask=0.51),
        _row(
            ML_V7M,
            pick_id="ml15",
            event="g4",
            market="moneyline",
            edge="0.15",
            model_probability=0.55,
            ask=0.40,
        ),
    ]


def _today_row(**fields: Any) -> dict[str, Any]:
    now = iso_utc(utc_now())
    return {
        "pick_id": "prior",
        "event_id": "other",
        "selection": "home",
        "market_type": "spread",
        "status": "settled",
        "executed_at_utc": now,
        **fields,
    }


@pytest.mark.parametrize(
    ("book", "prior", "live_bought", "paper_bought", "reason"),
    [
        ("live", {"cost_usd": 250.0}, False, True, "rejected_budget"),
        (
            "live",
            {"settled_at_utc": "NOW", "result": "loss", "pnl_usd": -250.0},
            False,
            True,
            "rejected_daily_loss_breaker",
        ),
        ("paper", {"cost_usd": 250.0}, True, False, "rejected_budget"),
    ],
)
def test_caps_and_breaker_are_per_book(monkeypatch, tmp_path, book, prior, live_bought, paper_bought, reason):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    prior = {k: (iso_utc(utc_now()) if v == "NOW" else v) for k, v in prior.items()}
    root = tmp_path if book == "live" else tmp_path / "paper_auto_buyer"
    _write_ledger(root / "auto_buyer_ledger.jsonl", [_today_row(**prior)])
    _cycle_harness(monkeypatch, tmp_path, _caps_state(), _caps_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert bool(FakeExecutor.executed) is live_bought
    paper = [r["pick_id"] for r in _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")]
    assert ("ml15" in paper) is paper_bought
    blocked = "live" if book == "live" else "paper"
    assert summary["cohort_passes"][blocked][reason] >= 1


def test_dedup_is_per_book_for_cohort_passes(monkeypatch, tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            {
                "pick_id": "live-old",
                "event_id": "g1",
                "selection": "home",
                "market_type": "spread",
                "status": "open",
                "cost_usd": 10.0,
                "shares": 20,
                "market_slug": "asc-cbb-g1-2026-11-25-neg-3pt5",
                "token_side": "long",
            }
        ],
    )
    _write_ledger(
        tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [
            {
                "pick_id": "paper-old",
                "event_id": "g2",
                "selection": "home",
                "market_type": "moneyline",
                "status": "open",
                "cost_usd": 10.0,
                "shares": 20,
                "market_slug": "aec-cbb-g2-2026-11-25",
                "token_side": "long",
            }
        ],
    )
    rows = [
        _row(
            ML_V7M,
            pick_id="ml-g1",
            event="g1",
            market="moneyline",
            edge="0.15",
            model_probability=0.55,
            ask=0.40,
        ),
        _row(SPREAD_V7, pick_id="spread-g2", event="g2", edge="0.09", model_probability=0.60, ask=0.51),
    ]
    # Before the cycle adds its own fills: the legacy index reads both books, a cohort book its own.
    legacy = AutoPolymarketBuyer(
        config=AutoExecutionConfig(), audit=AuditLog(tmp_path / "audit.jsonl"), data_root=tmp_path
    )._build_bought_index()
    assert {("g1", "home"), ("g2", "home")} <= legacy["event_selections"]
    own = AutoPolymarketBuyer(
        config=AutoExecutionConfig(mode="paper", dedup_own_ledger_only=True),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )._build_bought_index()
    assert ("g2", "home") in own["event_selections"]
    assert ("g1", "home") not in own["event_selections"]
    _cycle_harness(monkeypatch, tmp_path, _caps_state(), rows)
    auto_executor.run_auto_buyer_cycle()
    assert [t.pick_id for t, _ in FakeExecutor.executed] == ["spread-g2"]
    paper = [r["pick_id"] for r in _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")]
    assert "ml-g1" in paper


@pytest.mark.parametrize("enabled", [False, True])
def test_preview_has_no_side_effects(monkeypatch, tmp_path, enabled):
    recorded: list[Any] = []
    monkeypatch.setattr(auto_buyer_ledger, "record_auto_buy_execution", lambda *a, **k: recorded.append(1))
    later = _row(
        SPREAD_V7,
        pick_id="later",
        event="g9",
        edge="0.09",
        model_probability=0.60,
        ask=0.51,
        start_minutes=180,
    )
    state = {**_state(OPERATOR_COHORTS_FINAL, enabled=enabled), "whitelist_models": [NBA_SPREAD]}
    calls = _cycle_harness(monkeypatch, tmp_path, state, [*_mixed_four_rows(), later])
    summary = auto_executor.run_auto_buyer_cycle(preview=True)
    assert FakeExecutor.constructed == 0
    assert FakeExecutor.executed == []
    assert recorded == []
    assert calls["reconciled"] == [] and calls["settled"] == []
    assert calls["saved"] == [] and calls["history"] == []
    assert summary["preview"] is True
    live_orders = summary["cohort_passes"]["live"]["orders"]
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_PREVIEW"
    assert sorted(o["pick_id"] for o in live_orders) == ["spread9", "total6"]
    assert all(not o["model_id"].startswith("nba-") for o in live_orders)
    assert sorted(o["pick_id"] for o in summary["cohort_passes"]["paper"]["orders"]) == ["ml15", "nba13"]
    assert all(c.execute_live is False for c in calls["configs"])

    widened = auto_executor.run_auto_buyer_cycle(preview=True, preview_lookahead_hours=12)
    assert "later" in [o["pick_id"] for o in widened["cohort_passes"]["live"]["orders"]]
    assert all(c.capture_missing_slates is False for c in calls["configs"][-3:])
    assert FakeExecutor.executed == [] and recorded == [] and calls["saved"] == []


def test_wiped_state_is_paper_with_no_cohorts(monkeypatch, tmp_path):
    monkeypatch.setattr(
        auto_executor, "AUTO_BUYER_STATE_FILE", tmp_path / "missing" / "auto_buyer_state.json"
    )
    state = auto_executor.load_auto_buyer_state()
    assert state["mode"] == "paper"
    assert "cohorts" not in state
    assert _parse_cohorts(state).active == ()
    default = AutoExecutionConfig()
    assert default.cohorts == () and default.execute_live is False


def test_fallback_buyer_from_governed_live_state_is_paper(monkeypatch, tmp_path):
    monkeypatch.setattr(
        auto_executor,
        "load_auto_buyer_state",
        lambda: _state(OPERATOR_COHORTS_FINAL, enabled=True, mode="live"),
    )
    buyer = AutoPolymarketBuyer(data_root=tmp_path)
    assert buyer.config.execute_live is False
    assert buyer.config.mode == "paper"
    assert buyer.config.cohorts == ()


def test_decision_time_executable_quote_counts_as_quote_evidence():
    # Real NBA spread v9 Flat rows carry the forecast's own Polymarket decision-time quote
    # (cli/forecast.py), not the NCAAB archive provenance; checked on live Flat rows 2026-11-25.
    nba = _row(
        NBA_SPREAD,
        pick_id="nba",
        event="n1",
        league="nba",
        edge="0.13",
        market_quote_provenance="decision_time_executable_quote",
    )
    assert _row_in_cohort(nba, NBA_V9_PAPER)
    for change in (
        {"market_quote_timestamp_valid": "False"},
        {"market_snapshot_hash": None},
        {"market_snapshot_archive_path": None},
        {"market_quote_observed_at_utc": None},
        {"market_quote_provenance": "decision_time_sportsbook_quote"},
    ):
        assert not _row_in_cohort({**nba, **change}, NBA_V9_PAPER), change


# ---------------------------------------------------------------- review cycle 1


def _pending_fallback_row() -> dict[str, Any]:
    return {
        "pick_id": "resting",
        "event_id": "old-game",
        "selection": "home",
        "market_type": "spread",
        "status": "open",
        "cost_usd": 0.0,
        "shares": 0.0,
        "entry_price": 0.5,
        "fallback_order_id": "fb-1",
        "fallback_reconciled": False,
        "fallback_resting_shares": 50.0,
        "executed_at_utc": iso_utc(utc_now()),
        "market_slug": "asc-cbb-old-game-2026-11-25-neg-3pt5",
        "token_side": "long",
    }


@pytest.mark.parametrize("raw", ["false", "true", 1, "yes"])
def test_enabled_must_be_the_boolean_true(monkeypatch, tmp_path, raw):
    calls = _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=raw), _mixed_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0
    assert all(c.execute_live is False for c in calls["configs"])
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DISABLED"


def test_ungoverned_string_enabled_never_executes_legacy_live(monkeypatch, tmp_path):
    state = {**_state([], enabled="true"), "mode": "live"}
    state.pop("cohorts")
    calls = _cycle_harness(monkeypatch, tmp_path, state, _mixed_rows())
    auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert calls["reconciled"] == []
    assert all(c.execute_live is False for c in calls["configs"])
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    assert AutoPolymarketBuyer(data_root=tmp_path).config.execute_live is False


def test_toggle_stores_a_real_bool_and_refuses_ambiguous_payloads(monkeypatch, tmp_path):
    import json

    state_file = tmp_path / "auto_buyer_state.json"
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", state_file)
    # toggle_auto_buyer(False) also reads DATA's live ledger for resting fallbacks to cancel.
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    state_file.write_text(json.dumps({"enabled": "false", "mode": "paper"}), encoding="utf-8")
    for bad in ("false", "true", 1, 0, "yes"):
        with pytest.raises(ValueError):
            auto_executor.toggle_auto_buyer(bad)
    # A bare toggle is refused: only an explicit boolean may change the master switch.
    with pytest.raises(ValueError):
        auto_executor.toggle_auto_buyer()
    assert json.loads(state_file.read_text(encoding="utf-8"))["enabled"] == "false"
    assert auto_executor.toggle_auto_buyer(True)["enabled"] is True
    assert json.loads(state_file.read_text(encoding="utf-8"))["enabled"] is True
    assert auto_executor.toggle_auto_buyer(False)["enabled"] is False


@pytest.mark.parametrize("rest_state", ["ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_CANCELED"])
def test_daily_cap_counts_resting_fallbacks_within_a_cycle(tmp_path, decorate, rest_state):
    FakeExecutor.rest_only = True
    FakeExecutor.rest_state = rest_state
    rows = [
        _row(SPREAD_V7, pick_id=f"r{i:02d}", event=f"g{i:02d}", model_probability=0.60, ask=0.51, units=5.0)
        for i in range(30)
    ]
    result = _live_buyer(tmp_path, max_daily_spend_usd=500.0).evaluate_and_execute(rows)
    assert len(FakeExecutor.executed) == 20
    assert result.rejected_budget == 10
    assert result.total_spend_usd <= 500.0
    # What the real recorder wrote must carry the exposure into the next cycle.
    ledger = _ledger_rows(tmp_path / "auto_buyer_ledger.jsonl")
    assert len(ledger) == 20
    assert _REAL_TODAY_TOTALS(tmp_path)[0] == pytest.approx(500.0, abs=0.5)
    assert auto_executor._live_book_has_pending_orders(tmp_path / "auto_buyer_ledger.jsonl")
    exposure = _live_buyer(tmp_path)._build_bought_index()["game_exposure"]
    assert len(exposure) == 20
    assert all(sum(picks.values()) == pytest.approx(25.0, abs=0.05) for picks in exposure.values())
    # The next cycle's per-game cap sees the resting $25 on g00 (a total on the same game).
    more = _row(TOTAL_V6, pick_id="t00", event="g00", market="total", model_probability=0.57, ask=0.51)
    FakeExecutor.executed = []
    later = _live_buyer(tmp_path, cohorts=(SPREAD_V7_LIVE, TOTAL_V6_LIVE)).evaluate_and_execute([more])
    assert FakeExecutor.executed == []
    assert later.rejected_budget == 1


def test_daily_totals_count_unreconciled_resting_exposure(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _pending_fallback_row(),
            {**_pending_fallback_row(), "pick_id": "done", "cost_usd": 10.0, "fallback_reconciled": True},
        ],
    )
    spend, _pnl = _REAL_TODAY_TOTALS(tmp_path)
    # The live book's caps are fee-inclusive: the 50 resting shares count at limit plus modeled fee.
    assert spend == pytest.approx(35.0 + 50 * auto_buyer_ledger.taker_fee_per_share(0.5), abs=0.01)


@pytest.mark.parametrize(
    ("enabled", "cohorts"),
    [(True, [_entry(enabled=False)]), (True, "not-a-list"), (False, [_entry()]), (False, [])],
)
def test_governed_live_book_is_reconciled_whenever_it_has_resting_orders(
    monkeypatch, tmp_path, enabled, cohorts
):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_pending_fallback_row()])
    calls = _cycle_harness(monkeypatch, tmp_path, _state(cohorts, enabled=enabled), _mixed_rows())
    auto_executor.run_auto_buyer_cycle()
    assert calls["reconciled"] == [1]
    # D-02: while disabled, the cycle's reconcile cancels every resting live fallback by id.
    assert calls["reconcile_kwargs"] == [{"cancel_all_resting": not enabled}]
    assert None in calls["settled"]
    assert FakeExecutor.executed == []


def test_governed_live_book_without_resting_orders_is_left_alone(monkeypatch, tmp_path):
    calls = _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=False), _mixed_rows())
    auto_executor.run_auto_buyer_cycle()
    assert calls["reconciled"] == []


def test_reconcile_failure_without_live_buying_does_not_stop_the_cycle(monkeypatch, tmp_path):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_pending_fallback_row()])
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=False), _mixed_rows())
    monkeypatch.setattr(
        auto_buyer_ledger,
        "reconcile_pending_auto_buyer_fallbacks",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("exchange unavailable")),
    )
    summary = auto_executor.run_auto_buyer_cycle()
    assert summary["reconciliation"]["status"] == "error"
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _mixed_rows())
    monkeypatch.setattr(
        auto_buyer_ledger,
        "reconcile_pending_auto_buyer_fallbacks",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("exchange unavailable")),
    )
    with pytest.raises(RuntimeError, match="pre-cycle reconciliation failed"):
        auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []


@pytest.mark.parametrize(
    ("state", "pending", "decoupled", "expected"),
    [
        ({"enabled": False, "mode": "paper", "cohorts": []}, True, False, False),
        ({"enabled": False, "mode": "paper", "cohorts": []}, True, True, True),
        ({"enabled": True, "mode": "paper"}, True, False, False),
        ({"enabled": True, "mode": "paper", "cohorts": []}, True, False, True),
        ({"enabled": True, "mode": "live"}, False, False, True),
        ({"enabled": "true", "mode": "live"}, False, False, False),
    ],
)
def test_daily_defers_live_reconcile_only_to_a_cycle_that_does_it(
    monkeypatch, tmp_path, state, pending, decoupled, expected
):
    from model_prediction.cli import daily

    if pending:
        _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_pending_fallback_row()])
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    monkeypatch.setattr(daily, "_auto_buyer_is_decoupled", lambda: decoupled)
    monkeypatch.setattr(daily, "_skip_auto_buyer_for_verification", lambda: False)
    assert daily._auto_buyer_cycle_owns_live_reconcile() is expected


@pytest.mark.parametrize(
    "bad",
    [
        _entry(sport="ncaaf"),
        _entry(sport="nhl"),
        _entry(model_id="mlb-moneyline-v9-residual", sport="mlb", market="moneyline"),
        _entry(model_id="nba-anchor-v10", sport="nba", market="total", mode="live"),
        _entry(max_saved_edge_exclusive=0.30),
        _entry(min_saved_edge=0.25, max_saved_edge_exclusive=None, mode="paper"),
    ],
)
def test_parse_rejects_blocked_sports_markets_and_wide_windows(bad):
    parsed = _parse_cohorts({"cohorts": [bad]})
    assert parsed.active == ()
    assert len(parsed.errors) == 1


def test_nba_total_cohort_is_paper_only():
    # Operator 2026-11-25: NBA totals re-opened for paper cohorts (nba-anchor-v10); live stays blocked.
    paper = _entry(
        model_id="nba-anchor-v10",
        sport="nba",
        market="total",
        min_saved_edge=0.0,
        max_saved_edge_exclusive=0.25,
        mode="paper",
    )
    parsed = _parse_cohorts({"cohorts": [paper]})
    assert parsed.errors == ()
    assert [c.label for c in parsed.active] == ["nba-anchor-v10:total:[0,0.25):paper"]
    row = _row("nba-anchor-v10", pick_id="t1", event="n1", market="total", league="nba")
    assert _row_in_cohort(row, parsed.active[0])
    live = BuyCohort("nba-anchor-v10", "nba", "total", 0.0, 0.25, "live")
    assert not _row_in_cohort(row, live)


def test_nba_v10_and_ml_paper_cohorts_buy_near_zero_edge_rows(tmp_path, decorate):
    # Operator cohorts 2026-11-25: v10 spread/total [0, 25%), NBA ML v5 [5%, 25%), paper, with the
    # state file's edge-scaled sizing (min_edge 3.5%, max 12%, floor 0.5). A v10 call below 0 is not bought.
    v10_spread = BuyCohort("nba-anchor-v10", "nba", "spread", 0.0, 0.25, "paper")
    v10_total = BuyCohort("nba-anchor-v10", "nba", "total", 0.0, 0.25, "paper")
    ml = BuyCohort("nba-elo-trend-lr-v5", "nba", "moneyline", 0.05, 0.25, "paper")
    common = {"league": "nba", "units": 1.0, "ask": 0.51}
    rows = [
        _row("nba-anchor-v10", pick_id="sp", event="n1", edge="0.005", model_probability=0.515, **common),
        _row(
            "nba-anchor-v10",
            pick_id="to",
            event="n1",
            market="total",
            edge="0.005",
            model_probability=0.515,
            **common,
        ),
        _row("nba-anchor-v10", pick_id="neg", event="n2", edge="-0.01", model_probability=0.50, **common),
        _row(
            "nba-elo-trend-lr-v5",
            pick_id="ml",
            event="n3",
            market="moneyline",
            edge="0.06",
            model_probability=0.57,
            **common,
        ),
    ]
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            mode="paper",
            cohorts=(v10_spread, v10_total, ml),
            whitelisted_models=(),
            disabled_sport_markets=(("nba", "total"),),
            capture_missing_slates=False,
            require_model_timestamp=True,
            edge_scaled_sizing_enabled=True,
            edge_scaling_min_fraction=0.5,
            min_edge=0.035,
            max_edge=0.12,
            unit_value_usd=5.0,
            max_game_stake_usd=25.0,
            max_daily_spend_usd=500.0,
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )
    result = buyer.evaluate_and_execute(rows)
    orders = {o["pick_id"]: o for o in result.dry_run_orders}
    assert sorted(orders) == ["ml", "sp", "to"]
    assert all(o["status"] == "paper_filled" for o in orders.values())
    assert orders["sp"]["cohort"] == v10_spread.label
    assert orders["to"]["cohort"] == v10_total.label
    # 1U x $5 at the 0.5 sizing floor (edge below min_edge), and the same on the game's total.
    assert 2.0 <= float(orders["sp"]["cost_usd"]) <= 2.6
    assert 2.0 <= float(orders["to"]["cost_usd"]) <= 2.6
    assert FakeExecutor.executed == []


def test_hand_built_cohort_cannot_reach_a_blocked_sport_or_skip_the_edge_ceiling(tmp_path, decorate):
    ncaaf = BuyCohort("cfb-structural-v5.2", "ncaaf", "spread", 0.05, 0.12, "live")
    row = _row("cfb-structural-v5.2", pick_id="cfb", event="c1", league="ncaaf")
    assert not _row_in_cohort(row, ncaaf)
    wide = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.60, "live")
    phantom = _row(SPREAD_V7, pick_id="ph", event="g1", edge="0.30", model_probability=0.81, ask=0.51)
    result = _live_buyer(tmp_path, cohorts=(wide,)).evaluate_and_execute([phantom])
    assert FakeExecutor.executed == []
    assert result.rejected_unrealistic_edge == 1


def test_no_cohort_pass_quarantines_the_legacy_pass_still_does(monkeypatch, tmp_path):
    # D-06 (operator decision 2026-11-25) supersedes the live-pass quarantine this test pinned: the
    # live book gets an advisory drawdown warning instead. The legacy global pass keeps it.
    calls = _cycle_harness(
        monkeypatch, tmp_path, _state(OPERATOR_COHORTS_FINAL, enabled=True), _mixed_four_rows()
    )
    auto_executor.run_auto_buyer_cycle()
    by_mode = {c.mode: c for c in calls["configs"] if c.cohorts}
    assert by_mode["paper"].auto_quarantine_underperforming is False
    assert by_mode["live"].auto_quarantine_underperforming is False
    legacy = [c for c in calls["configs"] if not c.cohorts]
    assert legacy and all(c.auto_quarantine_underperforming is True for c in legacy)


def test_disabled_cycle_runs_no_cohort_buy_pass(monkeypatch, tmp_path):
    captures: list[Any] = []
    calls = _cycle_harness(
        monkeypatch, tmp_path, _state(OPERATOR_COHORTS_FINAL, enabled=False), _mixed_four_rows()
    )
    monkeypatch.setattr(
        auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: captures.append(k) or []
    )
    summary = auto_executor.run_auto_buyer_cycle()
    assert calls["cohort_reads"] == []
    assert [c for c in calls["configs"] if c.cohorts] == []
    assert all(not k["config"].cohorts for k in captures)
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DISABLED"
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_DISABLED"


def test_dashboard_manual_run_refuses_while_the_writer_lock_is_held(monkeypatch, tmp_path):
    import json
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.daily_lock import acquire_lock
    from model_prediction.dashboard import routes
    from model_prediction.dashboard.common import _DASHBOARD_TOKEN

    lock_path = tmp_path / "locks" / "daily.lock"
    monkeypatch.setattr(routes, "_auto_buyer_run_lock_path", lambda: lock_path)
    ran: list[Any] = []
    monkeypatch.setattr(routes, "run_auto_buyer_cycle", lambda **k: ran.append(k) or {"status": "ran"})

    def post() -> tuple[int, dict[str, Any]]:
        payload = json.dumps({"confirm": True}).encode("utf-8")
        handler = routes.Handler.__new__(routes.Handler)
        handler.path = "/api/auto-buyer/run"
        handler.headers = {"Content-Length": str(len(payload)), "X-Dashboard-Token": _DASHBOARD_TOKEN}
        handler.rfile = BytesIO(payload)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        return handler.send_response.call_args[0][0], json.loads(handler.wfile.getvalue().decode("utf-8"))

    held = acquire_lock(lock_path)
    assert held is not None
    try:
        code, body = post()
    finally:
        held.close()
    assert code == 409
    assert body["status"] == "refused"
    assert ran == []
    code, body = post()
    assert code == 200
    assert body == {"status": "ran"}
    assert ran == [{}]


# ---------------------------------------------------------------- tracking: paper twins, skip log, fill log


class AuditingFakeExecutor(FakeExecutor):
    """FakeExecutor that writes the audit events the real PolymarketExecutor writes for a buy."""

    def __init__(self, audit: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._audit = audit

    def execute(self, ticket: Any, pick_row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        event_fields = {
            "event_id": pick_row.get("event_id"),
            "selection": str(pick_row.get("selection") or "").casefold() or None,
            "market_type": pick_row.get("market_type"),
        }
        self._audit.append(
            "order_intent",
            ticket.pick_id,
            {
                "intent_id": f"i-{ticket.pick_id}",
                "market_slug": ticket.market_slug,
                "token_side": ticket.token_side,
                "action": "buy",
                **event_fields,
            },
        )
        sub = super().execute(ticket, pick_row, **kwargs)
        self._audit.append(
            "order_executed",
            ticket.pick_id,
            {
                "intent_id": f"i-{ticket.pick_id}",
                "market_slug": ticket.market_slug,
                "token_side": ticket.token_side,
                "action": "buy",
                **event_fields,
            },
        )
        return sub


TWIN_SPREAD = _entry(mode="paper", paper_twin=True, note="paper twin of D-02")
TWIN_TOTAL = _entry(model_id=TOTAL_V6, market="total", mode="paper", paper_twin=True)
LIVE_TOTAL = _entry(model_id=TOTAL_V6, market="total", mode="live")
SPREAD_V7_TWIN = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "paper")


def test_paper_twin_of_a_live_cohort_parses_and_keeps_the_live_cohort():
    parsed = _parse_cohorts({"cohorts": [_entry(), TWIN_SPREAD, LIVE_TOTAL, TWIN_TOTAL]})
    assert parsed.errors == ()
    assert set(parsed.active) == {
        SPREAD_V7_LIVE,
        SPREAD_V7_TWIN,
        BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.12, "live"),
        BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.12, "paper"),
    }


@pytest.mark.parametrize(
    "bad",
    [
        _entry(paper_twin=True),  # a live cohort cannot be a twin
        _entry(mode="paper", paper_twin="true"),
        _entry(mode="paper", paper_twin=1),
    ],
)
def test_malformed_paper_twin_is_ignored_and_named(bad):
    parsed = _parse_cohorts({"cohorts": [bad]})
    assert parsed.active == ()
    assert len(parsed.errors) == 1


def test_twin_overlapping_another_paper_cohort_is_still_dropped():
    parsed = _parse_cohorts(
        {
            "cohorts": [
                _entry(),
                TWIN_SPREAD,
                {**TWIN_SPREAD, "min_saved_edge": 0.08, "max_saved_edge_exclusive": 0.1},
            ]
        }
    )
    # Two twins of one model overlap each other (same book): both go, the live cohort stays.
    assert parsed.active == (SPREAD_V7_LIVE,)
    assert len(parsed.errors) == 1


def test_paper_cohort_index_ignores_live_audit_and_manual_order_sources(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(
        "order_executed",
        "spread9",
        {
            "market_slug": "asc-cbb-g1-2026-11-25-neg-3pt5",
            "token_side": "long",
            "action": "buy",
            "event_id": "g1",
            "selection": "home",
            "market_type": "spread",
        },
    )
    paper = AutoPolymarketBuyer(
        config=AutoExecutionConfig(mode="paper", dedup_own_ledger_only=True, cohorts=(SPREAD_V7_TWIN,)),
        audit=audit,
        data_root=tmp_path,
    )._build_bought_index()
    assert "spread9" not in paper["pick_ids"]
    assert ("asc-cbb-g1-2026-11-25-neg-3pt5", "long") not in paper["market_sides"]
    live = AutoPolymarketBuyer(
        config=AutoExecutionConfig(mode="live", dedup_own_ledger_only=True, cohorts=(SPREAD_V7_LIVE,)),
        audit=audit,
        data_root=tmp_path,
    )._build_bought_index()
    assert "spread9" in live["pick_ids"]
    assert ("g1", "home") in live["event_selections"]


def _twin_state() -> dict[str, Any]:
    return {**_state([_entry(), TWIN_SPREAD, LIVE_TOTAL, TWIN_TOTAL], enabled=True), "whitelist_models": []}


def _twin_rows() -> list[dict[str, Any]]:
    return [
        _row(SPREAD_V7, pick_id="spread9", event="g1", edge="0.09", model_probability=0.60, ask=0.51),
        _row(
            TOTAL_V6,
            pick_id="total6",
            event="g2",
            market="total",
            edge="0.06",
            model_probability=0.57,
            ask=0.51,
        ),
    ]


def test_cycle_paper_twin_buys_the_same_picks_as_live(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", AuditingFakeExecutor)
    _cycle_harness(monkeypatch, tmp_path, _twin_state(), _twin_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert sorted(t.pick_id for t, _ in FakeExecutor.executed) == ["spread9", "total6"]
    live = _ledger_rows(tmp_path / "auto_buyer_ledger.jsonl")
    paper = _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in live) == ["spread9", "total6"]
    assert sorted(r["pick_id"] for r in paper) == ["spread9", "total6"]
    assert summary["cohort_passes"]["paper"]["rejected_dedup"] == 0
    # A second cycle buys nothing more in either book: each book's own dedup still holds.
    auto_executor.run_auto_buyer_cycle()
    assert len(FakeExecutor.executed) == 2
    assert len(_ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")) == 2


def test_paper_twin_fills_never_block_or_cap_the_live_book(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    # A paper twin already bought both picks, at a cost above every live cap.
    _write_ledger(
        tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [
            {
                **_today_row(
                    pick_id=row["pick_id"], event_id=row["event_id"], market_type=row["market_type"]
                ),
                "status": "open",
                "cost_usd": 600.0,
                "shares": 1000,
                "market_slug": row["market_slug"],
                "token_side": "long",
                "selection": row["selection"],
            }
            for row in _twin_rows()
        ],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_state(), _twin_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert sorted(t.pick_id for t, _ in FakeExecutor.executed) == ["spread9", "total6"]
    live_pass = summary["cohort_passes"]["live"]
    assert live_pass["rejected_dedup"] == 0 and live_pass["rejected_budget"] == 0


def test_cycle_writes_cohort_skip_reasons_outside_state(monkeypatch, tmp_path):
    rows = [
        _row(SPREAD_V7, pick_id="spread9", event="g1", edge="0.09", model_probability=0.60, ask=0.51),
        _row(SPREAD_V7, pick_id="live-low", event="g2", edge="0.07", model_probability=0.53, ask=0.51),
        _row(SPREAD_V7, pick_id="far", event="g3", edge="0.07", start_minutes=300),
    ]
    calls = _cycle_harness(monkeypatch, tmp_path, _twin_state(), rows)
    auto_executor.run_auto_buyer_cycle()
    skips = _skip_records(tmp_path)
    by_book = {(s["book"], s["pick_id"]): s for s in skips}
    assert set(by_book) == {("live", "live-low"), ("live", "far"), ("paper", "live-low"), ("paper", "far")}
    low = by_book[("live", "live-low")]
    assert low["reason"] == "rejected_low_edge"
    assert low["ask"] == pytest.approx(0.51)
    assert low["live_edge"] == pytest.approx(0.02)
    assert low["cohort"] == SPREAD_V7_LIVE.label
    assert low["tip_window"] in {"before_5pm_et", "5pm_or_later_et"}
    assert by_book[("live", "far")]["reason"] == "rejected_future_slate"
    saved = calls["saved"][-1]["last_run"]
    assert all("skip_records" not in p for p in saved["cohort_passes"].values())
    assert all("skip_records" not in s for s, _ in calls["history"])


def test_preview_writes_no_tracking_logs(monkeypatch, tmp_path):
    _cycle_harness(monkeypatch, tmp_path, _twin_state(), _twin_rows())
    auto_executor.run_auto_buyer_cycle(preview=True)
    assert not (tmp_path / "logs").exists()


def test_live_order_attempt_is_written_to_the_fill_log(tmp_path, decorate):
    import json

    rows = [
        _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51),
        _row(SPREAD_V7, pick_id="live-low", event="g2", edge="0.07", model_probability=0.53, ask=0.51),
    ]
    _live_buyer(tmp_path).evaluate_and_execute(rows)
    (record,) = [
        json.loads(line)
        for line in (tmp_path / "logs" / "auto_buyer_live_fills.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert record["pick_id"] == "in"
    assert record["outcome"] == "submitted"
    assert record["cohort"] == SPREAD_V7_LIVE.label
    assert record["fill_path"] == "ioc_full"
    assert record["order_ids"] == ["sim-in"]


def test_live_and_paper_rows_carry_the_send_time_after_the_pass_start(tmp_path, monkeypatch):
    import itertools

    from model_prediction.domain import parse_utc

    def quote_with_observed(row):
        decorated = _quote_from_row(row)
        return {**decorated, "quote": {**decorated["quote"], "observed_at_utc": "2026-11-25T12:00:00Z"}}

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", quote_with_observed)
    # A clock that advances on every read, so "pass start" and "send time" are distinct instants.
    base = utc_now()
    ticks = itertools.count()
    monkeypatch.setattr(auto_executor, "utc_now", lambda: base + timedelta(seconds=next(ticks)))
    row = _row(SPREAD_V7, pick_id="in", event="g1", edge="0.07", model_probability=0.58, ask=0.51)
    live = _live_buyer(tmp_path).evaluate_and_execute([row])
    (live_order,) = live.submitted_orders
    assert parse_utc(live_order["submitted_at_utc"]) > parse_utc(live_order["timestamp_utc"])
    paper = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            mode="paper",
            cohorts=(SPREAD_V7_TWIN,),
            whitelisted_models=(),
            edge_scaled_sizing_enabled=False,
            require_model_timestamp=True,
            capture_missing_slates=False,
            unit_value_usd=5.0,
            max_game_stake_usd=25.0,
            max_daily_spend_usd=250.0,
        ),
        audit=AuditLog(tmp_path / "paper-audit.jsonl"),
        data_root=tmp_path / "paper",
    ).evaluate_and_execute([row])
    (paper_order,) = paper.dry_run_orders
    assert parse_utc(paper_order["submitted_at_utc"]) > parse_utc(paper_order["timestamp_utc"])
    # What the ledger keeps, so the report can anchor on it.
    rows = _ledger_rows(tmp_path / "auto_buyer_ledger.jsonl")
    assert rows[0]["submitted_at_utc"] == live_order["submitted_at_utc"]
    assert rows[0]["quote_observed_at_utc"] == "2026-11-25T12:00:00Z"
    assert rows[0]["executed_at_utc"] == live_order["timestamp_utc"]


def _skip_records(tmp_path) -> list[dict[str, Any]]:
    import json

    folder = tmp_path / "logs" / "auto_buyer_skip_reasons"
    return [
        json.loads(line)
        for path in sorted(folder.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _three_rows() -> list[dict[str, Any]]:
    return [
        _row(
            SPREAD_V7, pick_id=name, event=f"g{n}", edge="0.07", model_probability=0.58, start_minutes=minutes
        )
        for n, (name, minutes) in enumerate([("first", 10), ("second", 15), ("third", 20)], start=1)
    ]


def test_kill_switch_mid_pass_records_the_row_it_stopped_on_and_the_rows_not_reached(
    tmp_path, decorate, monkeypatch
):
    answers = iter([True, False])
    monkeypatch.setattr(auto_executor, "_live_buying_still_enabled", lambda: next(answers))
    buyer = _live_buyer(tmp_path, recheck_enabled_before_submit=True)
    result = buyer.evaluate_and_execute(_three_rows())
    assert result.live_halt_reason == "kill_switch"
    assert [ticket.pick_id for ticket, _ in FakeExecutor.executed] == ["first"]
    assert [(r["pick_id"], r["reason"]) for r in result.cohort_skips] == [
        ("second", "halted:kill_switch"),
        ("third", "halted:kill_switch"),
    ]


def test_a_gate_refusal_is_a_recorded_reason_not_silence(tmp_path, decorate, monkeypatch):
    from model_prediction.data_sources.polymarket_execute import ExecutionGateError

    original = FakeExecutor.execute

    def refusing(self, ticket, pick_row, **kwargs):
        if ticket.pick_id == "first":
            raise ExecutionGateError("REFUSED: market is not open")
        return original(self, ticket, pick_row, **kwargs)

    monkeypatch.setattr(FakeExecutor, "execute", refusing)
    result = _live_buyer(tmp_path).evaluate_and_execute(_three_rows())
    assert [ticket.pick_id for ticket, _ in FakeExecutor.executed] == ["second", "third"]
    (record,) = result.cohort_skips
    assert (record["pick_id"], record["reason"]) == ("first", "gate_refused")
    assert "market is not open" in record["detail"]
    assert record["ask"] == pytest.approx(0.51)


def test_a_refused_pinned_market_is_a_named_skip_not_a_crash_or_a_name_match(tmp_path, monkeypatch):
    """A recorded market that no snapshot qualifies is refused by the quote lookup (quote None plus a
    reason). The buyer skips that row with the reason in its skip log and carries on with the rest."""
    reason = "recorded market asc-cbb-x: stored snapshots but no qualifying pregame snapshot"

    def refuse_first(row):
        if row["pick_id"] == "first":
            return {**row, "quote": None, "quote_refusal_reason": reason}
        return _quote_from_row(row)

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", refuse_first)
    result = _live_buyer(tmp_path).evaluate_and_execute(_three_rows())

    assert [ticket.pick_id for ticket, _ in FakeExecutor.executed] == ["second", "third"]
    assert result.rejected_unmapped_market == 1
    (record,) = result.cohort_skips
    assert record["pick_id"] == "first"
    assert record["reasons"] == ["rejected_unmapped_market", "pinned_market_refused"]
    assert record["detail"] == reason


def test_a_live_pass_the_live_switch_skipped_records_why_its_rows_were_not_bought(monkeypatch, tmp_path):
    state = {**_twin_state(), "live_enabled": False}
    _cycle_harness(monkeypatch, tmp_path, state, _twin_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DISABLED"
    live = [s for s in _skip_records(tmp_path) if s["book"] == "live"]
    assert sorted((s["pick_id"], s["reason"]) for s in live) == [
        ("spread9", "pass_skipped:live_disabled"),
        ("total6", "pass_skipped:live_disabled"),
    ]
    assert all(s["detail"] for s in live)
    assert FakeExecutor.executed == []


# ---------------------------------------------------------------- paper twins keep their own books


PLAIN_ML = _entry(
    model_id=ML_V7M, market="moneyline", min_saved_edge=0.12, max_saved_edge_exclusive=None, mode="paper"
)
TWIN_LABEL = SPREAD_V7_TWIN.label


def _paper_row(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        **_today_row(pick_id=row["pick_id"], event_id=row["event_id"], market_type=row["market_type"]),
        "status": "open",
        "shares": 1000,
        "market_slug": row["market_slug"],
        "token_side": "long",
        "selection": row["selection"],
        **extra,
    }


def test_parse_names_the_paper_twins():
    parsed = _parse_cohorts({"cohorts": [_entry(), TWIN_SPREAD, PLAIN_ML]})
    assert parsed.errors == ()
    assert parsed.twin_labels == frozenset({TWIN_LABEL})
    assert _parse_cohorts({"cohorts": [_entry()]}).twin_labels == frozenset()


def test_ledger_scope_owns_labelled_rows_and_unlabelled_rows_of_its_model_and_market():
    scope = auto_executor.LedgerScope(
        labels=frozenset({TWIN_LABEL}),
        model_markets=frozenset({(SPREAD_V7, "spread")}),
        plain_labels=frozenset({ML_V7M_PAPER.label}),
    )
    assert scope.owns({"cohort": TWIN_LABEL, "model_id": "anything"})
    # A current plain cohort's label is theirs even on a twin's model and market.
    assert not scope.owns({"cohort": ML_V7M_PAPER.label, "model_id": SPREAD_V7, "market_type": "spread"})
    # Written before the label existed: owned on model and market, or the twin would re-buy it.
    assert scope.owns({"model_id": SPREAD_V7, "market_type": "Spread"})
    assert not scope.owns({"model_id": NBA_SPREAD, "market_type": "spread"})


# A plain paper cohort and a twin share a model and market on disjoint windows.
_TWIN_WINDOW = (SPREAD_V7, "spread", 0.05, 0.12)
_OLD_PLAIN_LABEL = f"{SPREAD_V7}:spread:[0.12,inf):paper"  # the plain cohort's window was edited since


def _shared_model_scope() -> auto_executor.LedgerScope:
    return auto_executor.LedgerScope(
        labels=frozenset({TWIN_LABEL}),
        model_markets=frozenset({(SPREAD_V7, "spread")}),
        plain_labels=frozenset({f"{SPREAD_V7}:spread:[0.15,inf):paper"}),
        twin_windows=(_TWIN_WINDOW,),
    )


def test_an_edited_plain_cohorts_old_rows_stay_the_plain_cohorts_beside_a_twin_on_its_model():
    """The old label is no current cohort's, so ownership falls back to model and market, which the
    twin also names. The row's recorded window (disjoint from the twin's) says it was the plain one's;
    otherwise the plain buyer stops seeing its own rows and can buy them again."""
    scope = _shared_model_scope()
    old_plain = {
        "cohort": _OLD_PLAIN_LABEL,
        "cohort_min_edge": 0.12,
        "cohort_max_edge": None,
        "model_id": SPREAD_V7,
        "market_type": "spread",
    }
    assert not scope.owns(old_plain)
    assert scope.others().owns(old_plain)


def test_an_edited_twins_old_rows_stay_the_twins_and_unwindowed_rows_keep_model_and_market():
    scope = _shared_model_scope()
    base = {"model_id": SPREAD_V7, "market_type": "spread"}
    narrowed_twin = {
        **base,
        "cohort": f"{SPREAD_V7}:spread:[0.05,0.12):paper-old",
        "cohort_min_edge": 0.05,
        "cohort_max_edge": 0.10,
    }
    assert scope.owns(narrowed_twin)  # still overlaps the twin's window, so the twin does not re-buy
    assert scope.owns({**base, "cohort": "gone:spread:[0,1):paper"})  # no recorded window
    assert scope.owns({**base, "cohort_min_edge": "n/a", "cohort": "gone"})  # unreadable window


def test_the_twin_pass_hands_its_windows_to_the_ledger_scope(monkeypatch):
    seen: list[Any] = []

    class _Spy:
        def __init__(self, config):
            seen.append(config.ledger_scope)

        def evaluate_and_execute(self, rows):
            return auto_executor.AutoExecutionResult()

    monkeypatch.setattr(auto_executor, "AutoPolymarketBuyer", _Spy)
    plain = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.15, None, "paper")
    state = _state([], enabled=True)
    auto_executor._evaluate_paper_cohorts(
        state, (SPREAD_V7_TWIN, plain), frozenset({SPREAD_V7_TWIN.label}), [], {}
    )
    assert {scope.twin_windows for scope in seen} == {((SPREAD_V7, "spread", 0.05, 0.12),)}


def test_a_scope_is_refused_on_a_live_config():
    scope = auto_executor.LedgerScope(labels=frozenset(), model_markets=frozenset())
    with pytest.raises(ValueError, match="paper"):
        AutoExecutionConfig(mode="live", execute_live=True, ledger_scope=scope)


def test_totals_count_only_the_scoped_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    row = _twin_rows()[0]
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _paper_row(row, pick_id="mine", cohort=TWIN_LABEL, cost_usd=40.0),
            _paper_row(row, pick_id="other", cohort=ML_V7M_PAPER.label, cost_usd=300.0),
        ],
    )
    scope = auto_executor.LedgerScope(labels=frozenset({TWIN_LABEL}), model_markets=frozenset())
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=False)[0] == pytest.approx(340.0)
    assert _REAL_TODAY_TOTALS(tmp_path, live_book=False, row_scope=scope.owns)[0] == pytest.approx(40.0)


def _twin_cycle_state() -> dict[str, Any]:
    return {**_state([_entry(), TWIN_SPREAD, PLAIN_ML], enabled=True), "whitelist_models": []}


def test_other_paper_cohorts_rows_never_cap_or_block_the_twin(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", AuditingFakeExecutor)
    spread = _twin_rows()[0]
    # The plain paper cohort holds $600 on the very game and contract (above every cap).
    _write_ledger(
        tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [_paper_row(spread, pick_id="ml-on-g1", cohort=ML_V7M_PAPER.label, cost_usd=600.0)],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [spread])
    summary = auto_executor.run_auto_buyer_cycle()
    paper = _ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert sorted(r["pick_id"] for r in paper) == ["ml-on-g1", "spread9"]
    assert [r["cohort"] for r in paper if r["pick_id"] == "spread9"] == [TWIN_LABEL]
    assert summary["cohort_passes"]["paper"]["rejected_budget"] == 0
    assert summary["cohort_passes"]["paper"]["rejected_dedup"] == 0


def test_the_twins_own_spend_caps_it_like_the_live_book(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    spread = _twin_rows()[0]
    other_game = _row(SPREAD_V7, pick_id="held", event="g9", edge="0.07", model_probability=0.58)
    # The twin already spent above the daily cap on another game.
    _write_ledger(
        tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [_paper_row(other_game, cohort=TWIN_LABEL, cost_usd=600.0)],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [spread])
    summary = auto_executor.run_auto_buyer_cycle()
    twin_pass = summary["cohort_passes"]["paper"]
    assert twin_pass["rejected_budget"] == 1 and twin_pass["orders_count"] == 0
    assert len(_ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")) == 1


def test_a_paper_row_from_before_the_label_existed_still_blocks_its_twin(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    spread = _twin_rows()[0]
    # No `cohort` on the row: owned by the twin on model and market, or the twin would re-buy it.
    _write_ledger(
        tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [_paper_row(spread, model_id=SPREAD_V7, cost_usd=5.0)],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [spread])
    summary = auto_executor.run_auto_buyer_cycle()
    twin_pass = summary["cohort_passes"]["paper"]
    assert twin_pass["rejected_dedup"] == 1 and twin_pass["orders_count"] == 0
    assert len(_ledger_rows(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")) == 1


def test_dedup_of_a_pick_the_book_already_holds_is_not_logged_but_a_rival_pick_is(tmp_path, decorate):
    held = _row(SPREAD_V7, pick_id="held", event="g1", edge="0.07", model_probability=0.58, start_minutes=10)
    rival = _row(
        SPREAD_V7, pick_id="rival", event="g1", edge="0.07", model_probability=0.58, start_minutes=15
    )
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_paper_row(held, status="open", shares=20.0, cost_usd=10.0, order_state="ORDER_STATE_FILLED")],
    )
    result = _live_buyer(tmp_path).evaluate_and_execute([held, rival])
    assert FakeExecutor.executed == []
    assert result.rejected_dedup == 2
    assert [(r["pick_id"], r["reason"]) for r in result.cohort_skips] == [("rival", "rejected_dedup")]


# ------------------------------------------- isolation runs both ways between twins and plain cohorts

PAPER_LEDGER = "paper_auto_buyer"


def _ml_row(**extra: Any) -> dict[str, Any]:
    return _row(
        ML_V7M,
        pick_id="ml1",
        event="g1",
        market="moneyline",
        edge="0.15",
        model_probability=0.66,
        ask=0.51,
        **extra,
    )


def _spread_row() -> dict[str, Any]:
    return _row(SPREAD_V7, pick_id="spread9", event="g1", edge="0.09", model_probability=0.60, ask=0.51)


def _paper_ledger_rows(tmp_path) -> list[dict[str, Any]]:
    return _ledger_rows(tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl")


@pytest.mark.parametrize(
    ("prior", "reason"),
    [
        ({"cost_usd": 600.0}, "rejected_budget"),
        ({"settled_at_utc": "NOW", "result": "loss", "pnl_usd": -250.0}, "rejected_daily_loss_breaker"),
        # The twin's open position on the very game and side the plain cohort wants.
        ({"cost_usd": 25.0, "event_id": "g1"}, "rejected_dedup"),
        # The other side of that game at the per-game cap: the cap is not shared with the twin.
        ({"cost_usd": 25.0, "event_id": "g1", "selection": "away"}, "rejected_budget"),
    ],
)
def test_twins_at_their_cap_never_block_a_plain_paper_cohort(monkeypatch, tmp_path, prior, reason):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    prior = {k: (iso_utc(utc_now()) if v == "NOW" else v) for k, v in prior.items()}
    spread = _spread_row()
    _write_ledger(
        tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl",
        [_paper_row(spread, pick_id="twin-held", cohort=TWIN_LABEL, model_id=SPREAD_V7, **prior)],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [_ml_row()])
    summary = auto_executor.run_auto_buyer_cycle()
    bought = [r["pick_id"] for r in _paper_ledger_rows(tmp_path) if r.get("cohort") == ML_V7M_PAPER.label]
    assert bought == ["ml1"]
    assert summary["cohort_passes"]["paper"][reason] == 0


def test_plain_cohorts_at_their_loss_stop_never_block_the_twin(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    _write_ledger(
        tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl",
        [
            _paper_row(
                _ml_row(),
                cohort=ML_V7M_PAPER.label,
                model_id=ML_V7M,
                settled_at_utc=iso_utc(utc_now()),
                result="loss",
                pnl_usd=-300.0,
                cost_usd=300.0,
            )
        ],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [_spread_row()])
    summary = auto_executor.run_auto_buyer_cycle()
    bought = [r["pick_id"] for r in _paper_ledger_rows(tmp_path) if r.get("cohort") == TWIN_LABEL]
    assert bought == ["spread9"]
    assert summary["cohort_passes"]["paper"]["rejected_daily_loss_breaker"] == 0


def test_global_pass_and_unlabelled_other_model_rows_still_count_for_the_plain_cohorts(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    ml = _ml_row()
    # A legacy-pass row (no cohort label, another model) and an unlabelled older twin-model row.
    _write_ledger(
        tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl",
        [
            _paper_row(
                ml,
                pick_id="legacy-nba",
                event_id="g8",
                model_id=NBA_SPREAD,
                market_type="spread",
                cost_usd=200.0,
                market_slug="legacy-slug",
            ),
            _paper_row(_spread_row(), pick_id="old-twin", event_id="g7", model_id=SPREAD_V7, cost_usd=400.0),
        ],
    )
    scope = auto_executor.LedgerScope(
        labels=frozenset({TWIN_LABEL}),
        model_markets=frozenset({(SPREAD_V7, "spread")}),
        plain_labels=frozenset({ML_V7M_PAPER.label}),
    ).others()
    plain_spend = _REAL_TODAY_TOTALS(tmp_path / PAPER_LEDGER, live_book=False, row_scope=scope.owns)[0]
    assert plain_spend == pytest.approx(200.0)
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [ml])
    summary = auto_executor.run_auto_buyer_cycle()
    # The $200 legacy row is counted and the $400 twin row is not, so the plain cohort still buys.
    assert summary["cohort_passes"]["paper"]["rejected_budget"] == 0
    bought = [r["pick_id"] for r in _paper_ledger_rows(tmp_path) if r.get("cohort") == ML_V7M_PAPER.label]
    assert bought == ["ml1"]


def test_a_legacy_row_over_the_cap_still_stops_the_plain_cohorts(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    ml = _ml_row()
    _write_ledger(
        tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl",
        [
            _paper_row(
                ml,
                pick_id="legacy-nba",
                event_id="g8",
                model_id=NBA_SPREAD,
                market_type="spread",
                cost_usd=600.0,
                market_slug="legacy-slug",
            )
        ],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [ml])
    summary = auto_executor.run_auto_buyer_cycle()
    assert summary["cohort_passes"]["paper"]["rejected_budget"] == 1
    assert len(_paper_ledger_rows(tmp_path)) == 1


def test_the_complement_scope_is_the_exact_opposite_of_the_twin_scope():
    scope = auto_executor.LedgerScope(
        labels=frozenset({TWIN_LABEL}),
        model_markets=frozenset({(SPREAD_V7, "spread")}),
        plain_labels=frozenset({ML_V7M_PAPER.label}),
    )
    rows = [
        {"cohort": TWIN_LABEL},
        {"cohort": ML_V7M_PAPER.label},
        {"cohort": ML_V7M_PAPER.label, "model_id": SPREAD_V7, "market_type": "spread"},
        {"cohort": "ncaab-spread-v7:spread:[0.05,0.1):paper", "model_id": SPREAD_V7, "market_type": "spread"},
        {"cohort": "gone:moneyline:[0,1):paper", "model_id": ML_V7M, "market_type": "moneyline"},
        {"model_id": SPREAD_V7, "market_type": "spread"},
        {"model_id": NBA_SPREAD, "market_type": "spread"},
        {},
    ]
    assert [scope.owns(r) for r in rows] == [True, False, False, True, False, True, False, False]
    assert [scope.others().owns(r) for r in rows] == [not scope.owns(r) for r in rows]
    assert scope.others().others() == scope


def test_a_complement_scope_is_still_refused_on_a_live_config():
    scope = auto_executor.LedgerScope(labels=frozenset(), model_markets=frozenset()).others()
    with pytest.raises(ValueError, match="paper"):
        AutoExecutionConfig(mode="live", execute_live=True, ledger_scope=scope)
    with pytest.raises(ValueError, match="paper"):
        AutoExecutionConfig(mode="live", ledger_scope=scope, dedup_own_ledger_only=True)


def test_editing_a_twins_window_does_not_re_buy_the_pick_it_already_holds(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_executor, "_today_auto_buyer_totals", _REAL_TODAY_TOTALS)
    spread = _spread_row()
    # Bought under the twin's earlier window: the label no longer names any current cohort.
    _write_ledger(
        tmp_path / PAPER_LEDGER / "auto_buyer_ledger.jsonl",
        [
            _paper_row(
                spread,
                model_id=SPREAD_V7,
                cohort="ncaab-spread-v7:spread:[0.05,0.1):paper",
                cost_usd=5.0,
            )
        ],
    )
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), [spread])
    summary = auto_executor.run_auto_buyer_cycle()
    assert summary["cohort_passes"]["paper"]["rejected_dedup"] == 1
    assert [r["pick_id"] for r in _paper_ledger_rows(tmp_path)] == ["spread9"]


def test_one_plain_row_and_one_twin_row_are_each_bought_once_and_both_counted(monkeypatch, tmp_path):
    rows = [_spread_row(), _ml_row()]
    _cycle_harness(monkeypatch, tmp_path, _twin_cycle_state(), rows)
    summary = auto_executor.run_auto_buyer_cycle()
    paper = _paper_ledger_rows(tmp_path)
    assert sorted((r["pick_id"], r["cohort"]) for r in paper) == [
        ("ml1", ML_V7M_PAPER.label),
        ("spread9", TWIN_LABEL),
    ]
    # Every input row is evaluated exactly once across the two buyers.
    assert summary["cohort_passes"]["paper"]["total_evaluated"] == len(rows)
    auto_executor.run_auto_buyer_cycle()
    assert len(_paper_ledger_rows(tmp_path)) == 2
