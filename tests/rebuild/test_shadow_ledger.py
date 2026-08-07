"""Tests for the append-only SQLite shadow ledger (shadow_ledger.py).

FOUNDATION_COMPLETION.md Phase 12 requires two properties that are easy to
get wrong and easy to silently break later, so both get a real, adversarial
test rather than a happy-path smoke test:

1. Idempotent reruns: rerunning the same job with identical inputs must not
   duplicate a trade_decision row. Tested here by inserting twice and
   asserting the table still has exactly one row -- not by reading the
   code and trusting the docstring.
2. Append-only corrections: a "correction" is a new row with supersedes_id
   set, never an edit. Tested here by inserting a prediction, "correcting"
   it, and asserting *both* rows are still present with the original row's
   values untouched -- plus a class-introspection test proving there is no
   UPDATE/DELETE-shaped method on ShadowLedger at all for these tables.

`_SportsForecast` / `_MarketEvaluation` / `_BetDecision` below are frozen
dataclasses that mirror model_prediction.rebuild.decision's real dataclasses
field-for-field (see that file's module docstring and definitions). They are
defined locally rather than imported because this worktree's checkout
predates the rebuild/ package entirely (see the session's own notes) -- the
point of duplicating the field shape here, instead of writing a dict test
only, is to prove shadow_ledger.py's record_* methods accept a real
dataclass instance directly, not just a same-shaped dict.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

import pytest

from model_prediction.rebuild.shadow_ledger import ShadowLedger, _as_dict


@dataclass(frozen=True)
class _SportsForecast:
    """Mirrors model_prediction.rebuild.decision.SportsForecast."""
    event_id: str
    predicted_winner: Literal["home", "away"]
    raw_probabilities: dict[str, float]
    calibrated_probabilities: dict[str, float]
    probability_lower: dict[str, float]
    probability_upper: dict[str, float]
    expected_home_score: float
    expected_away_score: float
    model_artifact_hash: str
    calibration_artifact_hash: str
    totals_probabilities: dict[float, dict[str, float]] = field(default_factory=dict)
    spread_probabilities: dict[float, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class _MarketEvaluation:
    """Mirrors model_prediction.rebuild.decision.MarketEvaluation."""
    market_id: str
    market_type: Literal["moneyline", "spread", "total"]
    team_or_side: str
    line: float | None
    executable_ask: float
    depth_adjusted_price: float
    quote_age_seconds: float
    available_depth: float


@dataclass(frozen=True)
class _BetDecision:
    """Mirrors model_prediction.rebuild.decision.BetDecision."""
    event_id: str
    action: Literal["BET", "NO_BET"]
    predicted_winner: str
    market_type: str
    selected_market: _MarketEvaluation | None
    units: float
    reason_code: str
    cost_adjusted_edge: float | None = None
    evaluated_market: _MarketEvaluation | None = None


def _forecast(**overrides) -> _SportsForecast:
    base = {
        "event_id": "mlb_2026-08-06_NYY_BOS",
        "predicted_winner": "home",
        "raw_probabilities": {"home": 0.58, "away": 0.42},
        "calibrated_probabilities": {"home": 0.60, "away": 0.40},
        "probability_lower": {"home": 0.55, "away": 0.37},
        "probability_upper": {"home": 0.65, "away": 0.45},
        "expected_home_score": 4.6,
        "expected_away_score": 3.9,
        "model_artifact_hash": "model-abc123",
        "calibration_artifact_hash": "calib-xyz789",
    }
    base.update(overrides)
    return _SportsForecast(**base)


def _evaluation(**overrides) -> _MarketEvaluation:
    base = {
        "market_id": "poly-market-1",
        "market_type": "moneyline",
        "team_or_side": "home",
        "line": None,
        "executable_ask": 0.52,
        "depth_adjusted_price": 0.53,
        "quote_age_seconds": 4.0,
        "available_depth": 200.0,
    }
    base.update(overrides)
    return _MarketEvaluation(**base)


def _decision(**overrides) -> _BetDecision:
    base = {
        "event_id": "mlb_2026-08-06_NYY_BOS",
        "action": "BET",
        "predicted_winner": "home",
        "market_type": "moneyline",
        "selected_market": None,
        "units": 1.5,
        "reason_code": "qualified",
        "cost_adjusted_edge": 0.03,
    }
    base.update(overrides)
    return _BetDecision(**base)


@pytest.fixture
def ledger(tmp_path: Path) -> ShadowLedger:
    led = ShadowLedger(tmp_path / "shadow.db")
    yield led
    led.close()


class TestSchemaCreation:
    """The plan requires all 16 named tables to exist even where only a
    handful have real insert/query methods -- the CREATE TABLE statements
    are not optional just because a table doesn't have a method yet."""

    def test_all_sixteen_required_tables_exist(self, ledger: ShadowLedger):
        required = {
            "runs", "raw_snapshots", "normalized_observations", "feature_snapshots",
            "dataset_manifests", "model_artifacts", "calibration_artifacts",
            "predictions", "market_snapshots", "market_evaluations", "trade_decisions",
            "paper_orders", "settlements", "closing_prices", "reviews", "audit_events",
        }
        rows = ledger.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        present = {r["name"] for r in rows}
        missing = required - present
        assert not missing, f"required tables missing from schema: {missing}"

    def test_reopening_the_same_db_is_idempotent(self, tmp_path: Path):
        # A fresh ShadowLedger() call runs CREATE TABLE IF NOT EXISTS again
        # on every open -- this must not raise or duplicate schema objects.
        db_path = tmp_path / "shadow.db"
        led1 = ShadowLedger(db_path)
        run_id = led1.record_run("mlb")
        led1.close()

        led2 = ShadowLedger(db_path)
        assert led2.get_run(run_id) is not None, "data from the first open must survive a reopen"
        led2.close()


class TestNoMutationMethodsExist:
    """Real invariant, not a style preference: if an UPDATE/DELETE-shaped
    method ever gets added to this class for predictions/market_snapshots/
    market_evaluations/trade_decisions, the append-only guarantee silently
    stops being true. This test fails the moment such a method appears,
    regardless of whether anything calls it yet."""

    def test_no_update_or_delete_method_on_the_class(self):
        method_names = [
            name for name in dir(ShadowLedger)
            if not name.startswith("_") and callable(getattr(ShadowLedger, name))
        ]
        offending = [
            name for name in method_names
            if "update" in name.lower() or "delete" in name.lower()
        ]
        assert offending == [], f"append-only ledger must expose no update/delete methods, found: {offending}"


class TestRunRecording:
    def test_record_run_creates_a_row(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb", run_type="shadow", horizon="late")
        row = ledger.get_run(run_id)
        assert row is not None
        assert row["sport"] == "mlb"
        assert row["horizon"] == "late"

    def test_record_run_with_explicit_id_is_idempotent(self, ledger: ShadowLedger):
        ledger.record_run("mlb", run_id="fixed-run-1")
        ledger.record_run("mlb", run_id="fixed-run-1")
        count = ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE run_id=?", ("fixed-run-1",)
        ).fetchone()["n"]
        assert count == 1


class TestPredictionRecordingAcceptsRealDataclass:
    """Proves record_prediction takes a SportsForecast-shaped dataclass
    instance directly -- not a reinvented parallel dict schema."""

    def test_accepts_dataclass_instance_directly(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        forecast = _forecast()
        pred_id, created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id=forecast.event_id, horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", forecast=forecast,
        )
        assert created is True
        row = ledger.get_prediction(pred_id)
        assert row is not None
        assert row["predicted_winner"] == "home"
        assert row["model_artifact_hash"] == "model-abc123"

    def test_accepts_plain_dict_shaped_like_the_dataclass(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        forecast_dict = {
            "event_id": "mlb_2026-08-06_LAD_SF",
            "predicted_winner": "away",
            "raw_probabilities": {"home": 0.4, "away": 0.6},
            "calibrated_probabilities": {"home": 0.42, "away": 0.58},
            "probability_lower": {"home": 0.38, "away": 0.53},
            "probability_upper": {"home": 0.46, "away": 0.62},
            "expected_home_score": 3.1,
            "expected_away_score": 4.4,
            "model_artifact_hash": "model-dict-1",
            "calibration_artifact_hash": "calib-dict-1",
        }
        pred_id, created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_LAD_SF", horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", forecast=forecast_dict,
        )
        assert created is True
        row = ledger.get_prediction(pred_id)
        assert row["predicted_winner"] == "away"

    def test_as_dict_rejects_unrelated_types(self):
        with pytest.raises(TypeError):
            _as_dict(object())


class TestPredictionAppendOnlyCorrections:
    """Required test #4: a correction is a new row with supersedes_id set,
    the old row is never edited or removed."""

    def test_correction_creates_a_new_row_and_preserves_the_original(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        original = _forecast(calibrated_probabilities={"home": 0.60, "away": 0.40})

        original_id, created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id=original.event_id, horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", forecast=original,
        )
        assert created is True

        # A correction: same identity, but a fixed calibrated probability
        # (e.g. a bug found in the calibrator after the fact).
        corrected = _forecast(calibrated_probabilities={"home": 0.63, "away": 0.37})
        corrected_id, created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id=corrected.event_id, horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", forecast=corrected,
            supersedes_id=original_id,
        )
        assert created is True
        assert corrected_id != original_id

        all_rows = ledger.predictions_for_event("mlb", original.event_id)
        ids = {r["id"] for r in all_rows}
        assert {original_id, corrected_id}.issubset(ids), "both the original and the correction must exist"
        assert len(all_rows) == 2, "correcting a prediction must not remove or overwrite the original row"

        original_row = ledger.get_prediction(original_id)
        assert original_row is not None, "the original row must still be readable"
        assert '"home": 0.6' in original_row["calibrated_probabilities_json"], (
            "the original row's data must be untouched by the later correction"
        )

        corrected_row = ledger.get_prediction(corrected_id)
        assert corrected_row["supersedes_id"] == original_id


class TestTradeDecisionIdempotency:
    """Required test #3: rerunning the same job with identical inputs must
    not create duplicate decision rows."""

    IDEMPOTENCY_KWARGS: ClassVar[dict] = {
        "sport": "mlb", "event_id": "mlb_2026-08-06_NYY_BOS", "horizon": "late",
        "decision_time_utc": "2026-08-06T23:00:00+00:00",
        "model_artifact_hash": "model-abc123", "market_snapshot_hash": "mkt-snap-hash-1",
        "decision_policy_version": "policy-v1",
    }

    def test_inserting_the_same_decision_twice_yields_exactly_one_row(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        decision = _decision()

        id1, created1 = ledger.record_trade_decision(
            run_id=run_id, decision=decision, **self.IDEMPOTENCY_KWARGS,
        )
        id2, created2 = ledger.record_trade_decision(
            run_id=run_id, decision=decision, **self.IDEMPOTENCY_KWARGS,
        )

        assert created1 is True
        assert created2 is False, "the second identical-key insert must be recognized as a rerun, not a new row"
        assert id1 == id2

        count = ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM trade_decisions WHERE sport=? AND event_id=?",
            ("mlb", "mlb_2026-08-06_NYY_BOS"),
        ).fetchone()["n"]
        assert count == 1, f"rerunning the identical job must not duplicate the row, found {count}"

    def test_the_real_db_unique_index_is_what_actually_enforces_this(self, ledger: ShadowLedger):
        # Belt-and-suspenders check: even bypassing record_trade_decision's
        # own pre-check and inserting raw SQL directly must be rejected by
        # the UNIQUE index -- proving the guarantee is a real constraint,
        # not just application-level discipline that a future caller could
        # accidentally route around.
        run_id = ledger.record_run("mlb")
        ledger.record_trade_decision(run_id=run_id, decision=_decision(), **self.IDEMPOTENCY_KWARGS)

        with pytest.raises(sqlite3.IntegrityError):
            ledger.conn.execute(
                """INSERT INTO trade_decisions(
                    created_at, run_id, sport, event_id, horizon, schema_version, supersedes_id,
                    decision_time_utc, model_artifact_hash, market_snapshot_hash,
                    decision_policy_version, action, predicted_winner, market_type, units,
                    reason_code, cost_adjusted_edge, selected_market_evaluation_id,
                    evaluated_market_evaluation_id
                ) VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?, '1', NULL,
                          ?, ?, ?, ?, 'BET', 'home', 'moneyline', 1.0, 'qualified', 0.03, NULL, NULL)""",
                (
                    run_id, self.IDEMPOTENCY_KWARGS["sport"], self.IDEMPOTENCY_KWARGS["event_id"],
                    self.IDEMPOTENCY_KWARGS["horizon"], self.IDEMPOTENCY_KWARGS["decision_time_utc"],
                    self.IDEMPOTENCY_KWARGS["model_artifact_hash"],
                    self.IDEMPOTENCY_KWARGS["market_snapshot_hash"],
                    self.IDEMPOTENCY_KWARGS["decision_policy_version"],
                ),
            )

    def test_different_decision_policy_version_is_a_distinct_decision(self, ledger: ShadowLedger):
        # The idempotency key must actually discriminate on every one of its
        # fields -- not silently collapse different policy runs together.
        run_id = ledger.record_run("mlb")
        kwargs_v1 = dict(self.IDEMPOTENCY_KWARGS)
        kwargs_v2 = dict(self.IDEMPOTENCY_KWARGS, decision_policy_version="policy-v2")

        id1, created1 = ledger.record_trade_decision(run_id=run_id, decision=_decision(), **kwargs_v1)
        id2, created2 = ledger.record_trade_decision(run_id=run_id, decision=_decision(), **kwargs_v2)

        assert created1 is True
        assert created2 is True
        assert id1 != id2

    def test_multiple_real_markets_for_one_game_all_persist(self, ledger: ShadowLedger):
        # Real bug found wiring this ledger into scripts/mlb_shadow_run.py
        # against a live slate: one real game produces one BetDecision per
        # candidate market (moneyline, each spread line, each total line) --
        # 16 real decisions for a single game in the live run that exposed
        # this. All 16 share the exact same sport/event_id/horizon/
        # decision_time_utc/model_artifact_hash/market_snapshot_hash/
        # decision_policy_version, because they come from one forecast
        # evaluated against one market snapshot. Before this fix, the
        # idempotency key didn't include the evaluated market's own
        # identity, so only the first decision was ever inserted -- every
        # other real, distinct decision for that game silently "deduped"
        # against it and was lost. 32 real decisions from a real 2-game
        # slate produced only 2 ledger rows.
        run_id = ledger.record_run("mlb")
        moneyline_home = _decision(
            market_type="moneyline",
            evaluated_market=_evaluation(market_id="ml-1", market_type="moneyline", team_or_side="home", line=None),
        )
        spread_home = _decision(
            market_type="spread",
            evaluated_market=_evaluation(market_id="sp-1", market_type="spread", team_or_side="home", line=-1.5),
        )
        spread_away = _decision(
            market_type="spread",
            evaluated_market=_evaluation(market_id="sp-1", market_type="spread", team_or_side="away", line=1.5),
        )
        total_over = _decision(
            market_type="total",
            evaluated_market=_evaluation(market_id="tot-1", market_type="total", team_or_side="over", line=8.5),
        )

        ids = [
            ledger.record_trade_decision(run_id=run_id, decision=d, **self.IDEMPOTENCY_KWARGS)
            for d in (moneyline_home, spread_home, spread_away, total_over)
        ]

        assert all(created for _id, created in ids), "every distinct real market decision must be a real new row"
        assert len({_id for _id, _ in ids}) == 4, "four distinct markets must yield four distinct rows"

        count = ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM trade_decisions WHERE sport=? AND event_id=?",
            (self.IDEMPOTENCY_KWARGS["sport"], self.IDEMPOTENCY_KWARGS["event_id"]),
        ).fetchone()["n"]
        assert count == 4, f"expected 4 real distinct decisions to persist, found {count}"

    def test_rerunning_the_same_multi_market_game_is_still_idempotent(self, ledger: ShadowLedger):
        # The fix above must not reopen the door to duplicating a genuine
        # rerun -- the same two distinct decisions submitted twice must
        # still collapse to two rows, not four.
        run_id = ledger.record_run("mlb")
        moneyline_home = _decision(
            market_type="moneyline",
            evaluated_market=_evaluation(market_id="ml-1", market_type="moneyline", team_or_side="home", line=None),
        )
        spread_home = _decision(
            market_type="spread",
            evaluated_market=_evaluation(market_id="sp-1", market_type="spread", team_or_side="home", line=-1.5),
        )

        for d in (moneyline_home, spread_home, moneyline_home, spread_home):
            ledger.record_trade_decision(run_id=run_id, decision=d, **self.IDEMPOTENCY_KWARGS)

        count = ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM trade_decisions WHERE sport=? AND event_id=?",
            (self.IDEMPOTENCY_KWARGS["sport"], self.IDEMPOTENCY_KWARGS["event_id"]),
        ).fetchone()["n"]
        assert count == 2

    def test_explicit_supersedes_id_always_appends_even_with_identical_key(self, ledger: ShadowLedger):
        # A genuine correction to a trade_decision (same inputs, e.g. fixing
        # a bug found in decision persistence itself) must still be able to
        # append a new row -- the partial unique index only blocks
        # non-superseding duplicates.
        run_id = ledger.record_run("mlb")
        id1, _ = ledger.record_trade_decision(run_id=run_id, decision=_decision(), **self.IDEMPOTENCY_KWARGS)
        id2, created2 = ledger.record_trade_decision(
            run_id=run_id, decision=_decision(units=2.0), supersedes_id=id1,
            **self.IDEMPOTENCY_KWARGS,
        )
        assert created2 is True
        assert id2 != id1
        count = ledger.conn.execute(
            "SELECT COUNT(*) AS n FROM trade_decisions WHERE sport=? AND event_id=?",
            (self.IDEMPOTENCY_KWARGS["sport"], self.IDEMPOTENCY_KWARGS["event_id"]),
        ).fetchone()["n"]
        assert count == 2


class TestMarketSnapshotRecording:
    def test_append_and_idempotent_duplicate(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        kwargs = {
            "run_id": run_id, "sport": "mlb", "event_id": "mlb_2026-08-06_NYY_BOS",
            "market_id": "poly-market-1", "side_id": "home", "line": None, "period": "FULL_GAME",
            "observed_at_utc": "2026-08-06T22:00:00+00:00", "best_bid": 0.51, "best_ask": 0.53,
        }
        id1, created1 = ledger.record_market_snapshot(**kwargs)
        id2, created2 = ledger.record_market_snapshot(**kwargs)
        assert created1 is True
        assert created2 is False
        assert id1 == id2

    def test_different_content_same_key_fails_closed(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        common = {
            "run_id": run_id, "sport": "mlb", "event_id": "mlb_2026-08-06_NYY_BOS",
            "market_id": "poly-market-1", "side_id": "home", "line": None, "period": "FULL_GAME",
            "observed_at_utc": "2026-08-06T22:00:00+00:00",
        }
        ledger.record_market_snapshot(best_bid=0.51, best_ask=0.53, **common)
        with pytest.raises(ValueError, match="fail closed"):
            ledger.record_market_snapshot(best_bid=0.60, best_ask=0.62, **common)

        # The failed-closed conflict must leave an audit trail.
        events = ledger.audit_events_all()
        assert any(e["event_type"] == "market_snapshot_conflict" for e in events)

    def test_a_real_price_change_at_a_new_timestamp_appends(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        base = {
            "run_id": run_id, "sport": "mlb", "event_id": "mlb_2026-08-06_NYY_BOS",
            "market_id": "poly-market-1", "side_id": "home", "line": None, "period": "FULL_GAME",
        }
        ledger.record_market_snapshot(observed_at_utc="2026-08-06T20:00:00+00:00",
                                       best_bid=0.50, best_ask=0.52, **base)
        ledger.record_market_snapshot(observed_at_utc="2026-08-06T21:00:00+00:00",
                                       best_bid=0.55, best_ask=0.57, **base)
        rows = ledger.market_snapshots_for_event("mlb", "mlb_2026-08-06_NYY_BOS")
        assert len(rows) == 2


class TestMarketEvaluationRecording:
    def test_accepts_dataclass_instance_directly(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        eval_id = ledger.record_market_evaluation(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS",
            evaluation=_evaluation(),
        )
        row = ledger.get_market_evaluation(eval_id)
        assert row["market_type"] == "moneyline"
        assert row["team_or_side"] == "home"


class TestTradeDecisionReferencesMarketEvaluation:
    def test_trade_decision_can_reference_a_recorded_evaluation(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        eval_id = ledger.record_market_evaluation(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS",
            evaluation=_evaluation(),
        )
        decision_id, _ = ledger.record_trade_decision(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS", horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", model_artifact_hash="model-abc123",
            market_snapshot_hash="mkt-snap-hash-1", decision_policy_version="policy-v1",
            decision=_decision(), selected_market_evaluation_id=eval_id,
            evaluated_market_evaluation_id=eval_id,
        )
        row = ledger.get_trade_decision(decision_id)
        assert row["selected_market_evaluation_id"] == eval_id


class TestPaperOrderAndSettlement:
    def test_paper_order_and_settlement_round_trip(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        decision_id, _ = ledger.record_trade_decision(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS", horizon="late",
            decision_time_utc="2026-08-06T23:00:00+00:00", model_artifact_hash="model-abc123",
            market_snapshot_hash="mkt-snap-hash-1", decision_policy_version="policy-v1",
            decision=_decision(),
        )
        order_id = ledger.record_paper_order(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS",
            trade_decision_id=decision_id, market_id="poly-market-1", side="home",
            requested_units=1.5, avg_fill_price=0.53, filled_units=1.5, status="FILLED",
        )
        order_row = ledger.get_paper_order(order_id)
        assert order_row["trade_decision_id"] == decision_id
        assert order_row["status"] == "FILLED"

        settlement_id = ledger.record_settlement(
            run_id=run_id, sport="mlb", event_id="mlb_2026-08-06_NYY_BOS",
            paper_order_id=order_id, trade_decision_id=decision_id,
            outcome="WIN", settled_price=1.0, pnl=0.705,
        )
        settlement_row = ledger.get_settlement(settlement_id)
        assert settlement_row["outcome"] == "WIN"
        assert settlement_row["pnl"] == pytest.approx(0.705)


class TestAuditEventChain:
    def test_events_form_a_hash_chain(self, ledger: ShadowLedger):
        id1 = ledger.record_audit_event("run_started", details={"sport": "mlb"})
        id2 = ledger.record_audit_event("run_finished", details={"sport": "mlb"})
        events = ledger.audit_events_all()
        by_uuid = {e["audit_uuid"]: e for e in events}
        assert by_uuid[id2]["previous_hash"] == by_uuid[id1]["event_hash"]
        assert by_uuid[id1]["previous_hash"] == ""


class TestNoProductionLedgerTouched:
    """Sanity check that this module never imports or references anything
    from the legacy (non-rebuild) ledger modules -- Phase 12 explicitly
    requires the shadow ledger to be a new, isolated SQLite file."""

    def test_module_has_no_legacy_ledger_import(self):
        import model_prediction.rebuild.shadow_ledger as mod
        source = Path(mod.__file__).read_text()
        assert "model_prediction.ledger" not in source
        assert "model_prediction.main_ledgers" not in source


class TestRealDecisionModuleIntegration:
    """This module was built in an isolated worktree whose checkout
    predates the rebuild/ package entirely, so it could only be tested
    against locally-mirrored _SportsForecast/_MarketEvaluation/_BetDecision
    dataclasses (see the module docstring above) -- never proven to work
    against the real model_prediction.rebuild.decision types it's meant to
    persist. Closing that loop here: same record_prediction/
    record_market_evaluation/record_trade_decision calls, but with genuine
    SportsForecast/MarketEvaluation/BetDecision instances constructed
    exactly the way decision.py's own decide_team_market()/decide_total()
    build them.
    """

    def test_records_a_real_bet_decision_end_to_end(self, ledger: ShadowLedger):
        from model_prediction.rebuild.decision import BetDecision, MarketEvaluation, SportsForecast

        forecast = SportsForecast(
            event_id="e1", predicted_winner="home",
            raw_probabilities={"home": 0.6, "away": 0.4},
            calibrated_probabilities={"home": 0.6, "away": 0.4},
            probability_lower={"home": 0.55, "away": 0.35},
            probability_upper={"home": 0.65, "away": 0.45},
            expected_home_score=4.5, expected_away_score=4.0,
            model_artifact_hash="abc", calibration_artifact_hash="def",
        )
        market = MarketEvaluation(
            market_id="m1", market_type="moneyline", team_or_side="home", line=None,
            executable_ask=0.55, depth_adjusted_price=0.55,
            quote_age_seconds=10.0, available_depth=999.0,
        )
        decision = BetDecision(
            event_id="e1", action="BET", predicted_winner="home", market_type="moneyline",
            selected_market=market, units=0.5, reason_code="qualified",
            cost_adjusted_edge=0.05, evaluated_market=market,
        )

        run_id = ledger.record_run("mlb")
        pred_id, pred_created = ledger.record_prediction(
            run_id=run_id, sport="mlb", event_id="e1", horizon="late",
            decision_time_utc="2026-08-06T20:00:00Z", forecast=forecast,
        )
        eval_id = ledger.record_market_evaluation(
            run_id=run_id, sport="mlb", event_id="e1", evaluation=market,
        )
        dec_id, dec_created = ledger.record_trade_decision(
            run_id=run_id, sport="mlb", event_id="e1", horizon="late",
            decision_time_utc="2026-08-06T20:00:00Z", model_artifact_hash="abc",
            market_snapshot_hash="xyz", decision_policy_version="v1", decision=decision,
            evaluated_market_evaluation_id=eval_id,
        )

        assert pred_created and dec_created
        assert ledger.get_prediction(pred_id)["predicted_winner"] == "home"
        stored_decision = ledger.get_trade_decision(dec_id)
        assert stored_decision["action"] == "BET"
        assert stored_decision["units"] == 0.5
        assert stored_decision["evaluated_market_evaluation_id"] == eval_id


class TestRawSnapshots:
    def test_record_and_dedupe_on_snapshot_hash(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_raw_snapshot(
            run_id=run_id, sport="mlb", source="espn_public", source_record_id="401",
            observed_at_utc="2026-08-06T10:00:00Z", snapshot_hash="abc123",
        )
        id2, created2 = ledger.record_raw_snapshot(
            run_id=run_id, sport="mlb", source="espn_public", source_record_id="401",
            observed_at_utc="2026-08-06T11:00:00Z", snapshot_hash="abc123",
        )
        assert created1 and not created2
        assert id1 == id2

    def test_different_hash_creates_a_new_row(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, _ = ledger.record_raw_snapshot(
            run_id=run_id, sport="mlb", source="espn_public", source_record_id="401",
            observed_at_utc="2026-08-06T10:00:00Z", snapshot_hash="hash1", event_id="401",
        )
        id2, created2 = ledger.record_raw_snapshot(
            run_id=run_id, sport="mlb", source="espn_public", source_record_id="401",
            observed_at_utc="2026-08-06T22:00:00Z", snapshot_hash="hash2", event_id="401",
        )
        assert created2
        assert id1 != id2
        assert len(ledger.raw_snapshots_for_event("mlb", "401")) == 2


class TestNormalizedObservations:
    def test_record_and_dedupe_on_identical_content(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_normalized_observation(
            run_id=run_id, sport="mlb", table_name="scoreboard",
            primary_key={"event_id": "401"}, observed_at_utc="2026-08-06T10:00:00Z",
            payload={"status": "STATUS_SCHEDULED"},
        )
        id2, created2 = ledger.record_normalized_observation(
            run_id=run_id, sport="mlb", table_name="scoreboard",
            primary_key={"event_id": "401"}, observed_at_utc="2026-08-06T10:00:00Z",
            payload={"status": "STATUS_SCHEDULED"},
        )
        assert created1 and not created2
        assert id1 == id2

    def test_a_real_state_change_at_the_same_key_creates_a_new_row(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, _ = ledger.record_normalized_observation(
            run_id=run_id, sport="mlb", table_name="scoreboard",
            primary_key={"event_id": "401"}, observed_at_utc="2026-08-06T10:00:00Z",
            payload={"status": "STATUS_SCHEDULED"},
        )
        id2, created2 = ledger.record_normalized_observation(
            run_id=run_id, sport="mlb", table_name="scoreboard",
            primary_key={"event_id": "401"}, observed_at_utc="2026-08-06T10:00:00Z",
            payload={"status": "STATUS_FINAL"},
        )
        assert created2
        assert id1 != id2


class TestFeatureSnapshotsLedger:
    def test_record_and_dedupe_on_dataset_hash(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_feature_snapshot(
            run_id=run_id, sport="mlb", horizon="late", dataset_hash="hash1", row_count=5,
        )
        id2, created2 = ledger.record_feature_snapshot(
            run_id=run_id, sport="mlb", horizon="late", dataset_hash="hash1", row_count=5,
        )
        assert created1 and not created2
        assert id1 == id2
        assert len(ledger.feature_snapshots_for_horizon("mlb", "late")) == 1


class TestDatasetManifestsLedger:
    def test_record_and_dedupe_on_dataset_hash(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_dataset_manifest(
            run_id=run_id, sport="mlb", dataset_hash="hash1",
            final_test_start="2026-08-02", final_test_end="2026-08-04", final_test_consumed=True,
        )
        id2, created2 = ledger.record_dataset_manifest(
            run_id=run_id, sport="mlb", dataset_hash="hash1",
        )
        assert created1 and not created2
        assert id1 == id2


class TestModelArtifactsLedger:
    def test_record_and_dedupe_on_artifact_hash(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_model_artifact(
            run_id=run_id, sport="mlb", model_name="mlb-two-head-v1", model_version="1",
            artifact_hash="hash1",
        )
        id2, created2 = ledger.record_model_artifact(
            run_id=run_id, sport="mlb", model_name="mlb-two-head-v1", model_version="1",
            artifact_hash="hash1",
        )
        assert created1 and not created2
        assert id1 == id2
        assert ledger.get_model_artifact_by_hash("mlb", "hash1")["model_name"] == "mlb-two-head-v1"


class TestCalibrationArtifactsLedger:
    def test_record_and_dedupe_bound_to_model_hash(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_calibration_artifact(
            run_id=run_id, sport="mlb", model_artifact_hash="model1", calibration_hash="calib1",
            method="bootstrap_uncertainty",
        )
        id2, created2 = ledger.record_calibration_artifact(
            run_id=run_id, sport="mlb", model_artifact_hash="model1", calibration_hash="calib1",
        )
        assert created1 and not created2
        assert id1 == id2


class TestClosingPricesLedger:
    def test_record_and_dedupe_on_identical_price(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        id1, created1 = ledger.record_closing_price(
            run_id=run_id, sport="mlb", market_id="m1", side_id="home", closing_price=0.55,
        )
        id2, created2 = ledger.record_closing_price(
            run_id=run_id, sport="mlb", market_id="m1", side_id="home", closing_price=0.55,
        )
        assert created1 and not created2
        assert id1 == id2

    def test_conflicting_closing_price_at_the_same_key_fails_closed(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        ledger.record_closing_price(run_id=run_id, sport="mlb", market_id="m1", side_id="home", closing_price=0.55)
        with pytest.raises(ValueError, match="conflicting closing_price"):
            ledger.record_closing_price(run_id=run_id, sport="mlb", market_id="m1", side_id="home", closing_price=0.60)


class TestReviewsLedger:
    def test_record_and_query_by_subject(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        review_id = ledger.record_review(
            run_id=run_id, subject_table="trade_decisions", subject_id=1,
            verdict="approved", reviewer="operator", notes="looks right",
        )
        reviews = ledger.reviews_for_subject("trade_decisions", 1)
        assert len(reviews) == 1
        assert reviews[0]["id"] == review_id
        assert reviews[0]["verdict"] == "approved"

    def test_multiple_reviews_of_the_same_subject_all_persist(self, ledger: ShadowLedger):
        run_id = ledger.record_run("mlb")
        ledger.record_review(run_id=run_id, subject_table="trade_decisions", subject_id=1, verdict="approved")
        ledger.record_review(run_id=run_id, subject_table="trade_decisions", subject_id=1, verdict="flagged")
        assert len(ledger.reviews_for_subject("trade_decisions", 1)) == 2
