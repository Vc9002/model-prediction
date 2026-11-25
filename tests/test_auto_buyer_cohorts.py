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

    def __init__(self, **_kwargs: Any) -> None:
        FakeExecutor.constructed += 1

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"positions": {}}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        FakeExecutor.executed.append((ticket, dict(pick_row)))
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
    selection: str = "home",
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
        "selection": selection,
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": edge,
        "line": None if market == "moneyline" else line,
        "model_probability": model_probability,
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
    calls: dict[str, list[Any]] = {"saved": [], "history": [], "reconciled": [], "settled": [], "configs": []}
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda s: calls["saved"].append(s))
    monkeypatch.setattr(
        auto_executor,
        "_append_auto_buyer_cycle_history",
        lambda summary, history_path=None: calls["history"].append((summary, history_path)),
    )
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", lambda: [dict(r) for r in rows])
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
        lambda: calls["reconciled"].append(1) or {},
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
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DRY_RUN"


def test_cycle_malformed_cohorts_still_force_global_paper(monkeypatch, tmp_path):
    calls = _cycle_harness(monkeypatch, tmp_path, _state("not-a-list", enabled=True), _mixed_rows())
    summary = auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert all(c.execute_live is False for c in calls["configs"])
    assert summary["cohorts_governed"] is True
    assert summary["cohort_config_errors"]
    assert summary["cohorts_active"] == []
