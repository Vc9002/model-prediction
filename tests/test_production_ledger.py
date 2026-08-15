"""Tests for the production ledger (append API, lifecycle transitions,
fail-soft wiring in cli_production).

Run: env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_production_ledger.py -q
"""

from __future__ import annotations

import sqlite3

import pytest

from model_prediction import cli_production
from model_prediction.production_ledger import SCHEMA_VERSION, ProductionLedger
from model_prediction.production_store import ProductionPredictionStore
from model_prediction.runtime_paths import RuntimePaths

# ── fixtures / helpers ──────────────────────────────────────────────────────


def _make_ledger(tmp_path) -> ProductionLedger:
    return ProductionLedger(tmp_path / "production" / "predictions.db")


def _record_sample(ledger: ProductionLedger, run_id: str = "run1",
                   event_id: str = "evt1") -> int:
    # predictions.run_id is FK-constrained to runs(run_id), so a run row
    # must exist first — the CLI flow does the same via start_run.
    ledger.start_run(run_id=run_id, git_sha="test-sha")
    return ledger.record_prediction(
        run_id, prediction_id=f"{run_id}:{event_id}", event_id=event_id,
        sport="WNBA", market="moneyline", model_id="wnba-elo-trend-lr-v4",
        probabilities={"home": 0.62, "away": 0.38},
        predicted_side="home",
    )


class _FakeCandidate:
    """Minimal stand-in for LearnedForwardCandidate.to_dict() output."""

    def __init__(self, event_id: str = "401690001") -> None:
        self._d = {
            "event_id": event_id,
            "event_start_utc": "2026-08-13T19:00:00Z",
            "away_team": "Away", "home_team": "Home",
            "selection": "home", "model_probability": 0.62,
            "home_probability": 0.62, "confidence_threshold": 0.55,
            "call": True, "action": "QUALIFIED_SHADOW_CALL",
            "reason": "CALL_LEARNED_CONFIDENCE", "model_version": "v4",
            "model_artifact_hash": "h", "model_qualified": True,
            "feature_basis": {}, "feature_snapshot_hash": "snap1",
            "unavailable_features": (),
        }

    def to_dict(self) -> dict[str, object]:
        return dict(self._d)


def _fake_scoreboard(events: tuple[str, ...] = ("401690001",)):
    def scoreboard(self, sport: str, date: str) -> dict[str, object]:
        return {
            "events": [{"id": eid, "date": "2026-08-13T19:00:00Z"} for eid in events]
        }

    return scoreboard


def _fake_slate(candidates: list[_FakeCandidate] | None = None):
    def build_slate(**kwargs) -> tuple[list[_FakeCandidate], list[dict[str, str]], int]:
        cands = candidates if candidates is not None else [_FakeCandidate()]
        return cands, [], len(cands)

    return build_slate


def _run_predict_with_fakes(tmp_path, monkeypatch) -> int:
    """Run the predict CLI with fake ESPN data and slate, real config.

    Feature data (FeatureStore) reads the monkeypatched repo data root;
    canary STATE (store + state file) resolves through `_paths()`, which
    is monkeypatched to the tmp runtime root so tests never touch the
    real production.db (a full-suite run once wrote fake rows into the
    live database when only `_resolve_data_root` was patched — see the
    2026-08-14 store migration notes in DEBUG.md).
    """
    monkeypatch.setattr(cli_production, "_resolve_data_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_production,
        "_paths",
        lambda: RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "data"),
    )
    monkeypatch.setattr(cli_production.ESPNClient, "scoreboard", _fake_scoreboard())
    monkeypatch.setattr(cli_production, "build_learned_moneyline_slate", _fake_slate())
    return cli_production.main(["predict"])


# ── append + idempotency ────────────────────────────────────────────────────


def test_record_prediction_appends_and_round_trips(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    row_id = _record_sample(ledger)
    rows = ledger.get_predictions()
    assert [r["id"] for r in rows] == [row_id]
    row = rows[0]
    assert row["status"] == "predicted"
    assert row["sport"] == "WNBA" and row["market"] == "moneyline"
    assert row["model_id"] == "wnba-elo-trend-lr-v4"
    assert row["predicted_side"] == "home"
    assert row["probabilities"] == {"home": 0.62, "away": 0.38}
    assert row["schema_version"] == SCHEMA_VERSION
    ledger.close()


def test_record_prediction_idempotent_on_rerun(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    first = _record_sample(ledger)
    second = _record_sample(ledger)  # identical (run_id, event, sport, market, model)
    assert second == first
    assert len(ledger.get_predictions()) == 1
    ledger.close()


def test_supersede_bypasses_idempotency_and_replaces_view(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    old_id = _record_sample(ledger)
    ledger.supersede_prediction(old_id, note="line moved")
    new_id = ledger.record_prediction(
        "run1", prediction_id="run1:evt1", event_id="evt1",
        sport="WNBA", market="moneyline", model_id="wnba-elo-trend-lr-v4",
        probabilities={"home": 0.71, "away": 0.29},
        supersedes_id=old_id,  # correction flow: bypasses the idempotency index
    )
    assert new_id != old_id
    rows = ledger.get_predictions()
    assert [r["id"] for r in rows] == [new_id]
    assert rows[0]["supersedes_id"] == old_id
    superseded = ledger.get_prediction(old_id)
    assert superseded["status"] == "superseded"
    assert superseded["settled_at_utc"] is not None
    ledger.close()


def test_rerun_after_supersede_returns_correction_not_stale_row(tmp_path) -> None:
    """Regression: with the SCHEMA_VERSION 1 index predicate, a superseded
    row kept supersedes_id NULL and still occupied the idempotency slot, so
    a re-fired identical cycle collided with the OLD row and returned its
    stale id. After supersede + correction, the re-run must return the
    correction row's id and append nothing."""
    ledger = _make_ledger(tmp_path)
    old_id = _record_sample(ledger)
    ledger.supersede_prediction(old_id, note="line moved")
    correction_id = ledger.record_prediction(
        "run1", prediction_id="run1:evt1", event_id="evt1",
        sport="WNBA", market="moneyline", model_id="wnba-elo-trend-lr-v4",
        probabilities={"home": 0.71, "away": 0.29},
        supersedes_id=old_id,
    )
    rerun_id = _record_sample(ledger)  # identical identity, no supersedes_id
    assert rerun_id == correction_id
    assert len(ledger.get_predictions()) == 1
    assert ledger.get_predictions()[0]["id"] == correction_id
    ledger.close()


def test_old_index_predicate_is_migrated(tmp_path) -> None:
    """Regression: databases created before the status='predicted' predicate
    keep their old index through CREATE IF NOT EXISTS — the init migration
    must drop and recreate it, or superseded rows stay stuck in the slot."""
    ledger = _make_ledger(tmp_path)
    # Rewrite the index to the legacy SCHEMA_VERSION 1 predicate.
    ledger.conn.execute("DROP INDEX idx_predictions_idempotent")
    ledger.conn.execute(
        """CREATE UNIQUE INDEX idx_predictions_idempotent
           ON predictions(run_id, event_id, sport, market, model_id)
           WHERE supersedes_id IS NULL"""
    )
    ledger.conn.commit()
    ledger.close()

    reopened = _make_ledger(tmp_path)
    index_sql = reopened.conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_predictions_idempotent'"
    ).fetchone()[0]
    assert "status = 'predicted'" in index_sql
    reopened.close()


# ── lifecycle transitions ───────────────────────────────────────────────────


def test_settle_records_outcome_and_timestamp(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    row_id = _record_sample(ledger)
    row = ledger.settle_prediction(row_id, "won", note="confirmed final")
    assert row["status"] == "settled"
    assert row["resolved_outcome"] == "won"
    assert row["settled_at_utc"] is not None
    assert row["note"] == "confirmed final"
    ledger.close()


def test_settle_void_outcome_maps_to_voided_status(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    row_id = _record_sample(ledger)
    row = ledger.settle_prediction(row_id, "void", note="game postponed")
    assert row["status"] == "voided"
    assert row["resolved_outcome"] == "void"
    ledger.close()


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("void_prediction", "voided"),
        ("supersede_prediction", "superseded"),
        ("mark_prediction_error", "error"),
    ],
)
def test_terminal_transitions_open_to_terminal(tmp_path, method, expected) -> None:
    ledger = _make_ledger(tmp_path)
    row_id = _record_sample(ledger)
    row = getattr(ledger, method)(row_id)
    assert row["status"] == expected
    assert row["settled_at_utc"] is not None
    ledger.close()


def test_invalid_transitions_rejected(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    row_id = _record_sample(ledger)
    ledger.settle_prediction(row_id, "won")

    # Terminal -> anything is rejected, including a different terminal.
    with pytest.raises(ValueError, match="already 'settled'"):
        ledger.settle_prediction(row_id, "lost")
    with pytest.raises(ValueError, match="already 'settled'"):
        ledger.void_prediction(row_id)
    with pytest.raises(ValueError, match="already 'settled'"):
        ledger.mark_prediction_error(row_id)

    # Settle requires a valid outcome.
    fresh = _record_sample(ledger, event_id="evt2")
    with pytest.raises(ValueError, match="requires outcome"):
        ledger.settle_prediction(fresh, "half")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires outcome"):
        ledger.transition_prediction(fresh, "settled")

    # outcome is rejected outside settle.
    with pytest.raises(ValueError, match="only valid when settling"):
        ledger.transition_prediction(fresh, "voided", outcome="won")

    # Unknown status target and unknown row.
    with pytest.raises(ValueError, match="not a terminal status"):
        ledger.transition_prediction(fresh, "predicted")
    with pytest.raises(ValueError, match="no prediction row"):
        ledger.settle_prediction(999_999, "won")
    ledger.close()


def test_transition_missing_row_raises(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    with pytest.raises(ValueError, match="no prediction row"):
        ledger.void_prediction(42)
    ledger.close()


# ── runs lifecycle ──────────────────────────────────────────────────────────


def test_complete_run_transition_is_guarded(tmp_path) -> None:
    ledger = _make_ledger(tmp_path)
    run_id = ledger.start_run(git_sha="abc123")
    ledger.complete_run(run_id, "completed", note="3 predictions")
    row = ledger.conn.execute(
        "SELECT status, completed_at_utc, note FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at_utc"] is not None
    assert row["note"] == "3 predictions"

    # Already-terminal: idempotent no-op, must not re-stamp.
    first_stamp = row["completed_at_utc"]
    ledger.complete_run(run_id, "completed")
    row = ledger.conn.execute(
        "SELECT status, completed_at_utc FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["completed_at_utc"] == first_stamp

    with pytest.raises(ValueError, match="no run with run_id"):
        ledger.complete_run("nope", "completed")
    with pytest.raises(ValueError, match="must be one of"):
        ledger.complete_run(run_id, "archived")
    ledger.close()


# ── migration of pre-existing databases ─────────────────────────────────────


def test_migration_adds_lifecycle_columns_to_old_db(tmp_path) -> None:
    """A DB created before the lifecycle columns must get them added."""
    path = tmp_path / "production" / "predictions.db"
    ledger = ProductionLedger(path)
    row_id = _record_sample(ledger)
    ledger.close()
    # Simulate an old-schema database (predates the columns).
    conn = sqlite3.connect(str(path))
    conn.execute("ALTER TABLE predictions DROP COLUMN resolved_outcome")
    conn.execute("ALTER TABLE predictions DROP COLUMN settled_at_utc")
    conn.commit()
    conn.close()
    # Reopen: migration must restore the columns without touching rows.
    ledger = ProductionLedger(path)
    cols = {r[1] for r in ledger.conn.execute("PRAGMA table_info(predictions)")}
    assert "resolved_outcome" in cols
    assert "settled_at_utc" in cols
    row = ledger.settle_prediction(row_id, "won")
    assert row["status"] == "settled"
    ledger.close()


# ── fail-soft wiring in cli_production ──────────────────────────────────────


def test_predict_succeeds_when_ledger_unavailable(tmp_path, monkeypatch) -> None:
    """A store open failure must not fail the prediction command."""

    def _boom_init(self, paths: object) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(
        cli_production.ProductionPredictionStore, "__init__", _boom_init
    )
    assert _run_predict_with_fakes(tmp_path, monkeypatch) == 0


def test_predict_succeeds_when_ledger_writes_fail(tmp_path, monkeypatch) -> None:
    """start_run/append_prediction/finish_run failures are fail-soft."""

    class _RaisingStore:
        def __init__(self, paths: object) -> None:
            pass

        def start_run(self, *args, **kwargs) -> str:
            raise RuntimeError("disk full")

        def append_prediction(self, *args, **kwargs) -> int | None:
            raise RuntimeError("disk full")

        def finish_run(self, *args, **kwargs) -> None:
            raise RuntimeError("disk full")

    monkeypatch.setattr(cli_production, "ProductionPredictionStore", _RaisingStore)
    assert _run_predict_with_fakes(tmp_path, monkeypatch) == 0


def test_predict_records_to_ledger(tmp_path, monkeypatch) -> None:
    """Happy path: predict mirrors candidates and finishes the run row."""
    assert _run_predict_with_fakes(tmp_path, monkeypatch) == 0
    store = ProductionPredictionStore(
        RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "data")
    )
    rows, _ = store.get_predictions()
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "401690001"
    assert row["sport"] == "WNBA"
    assert row["market"] == "moneyline"
    assert row["market_type"] == "moneyline"
    assert row["model_id"] == "wnba-elo-trend-lr-v4"
    assert row["status"] == "predicted"
    assert row["predicted_side"] == "home"
    assert row["probabilities"]["home"] == pytest.approx(0.62)
    run = store._conn.execute("SELECT status, note FROM runs").fetchone()
    assert run["status"] == "completed"
    assert run["note"] == "1 predictions"
    store.close()


# ── CLI lifecycle subcommands ───────────────────────────────────────────────


def test_cli_settle_and_invalid_retransition(tmp_path, monkeypatch, capsys) -> None:
    assert _run_predict_with_fakes(tmp_path, monkeypatch) == 0
    store = ProductionPredictionStore(
        RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "data")
    )
    row_id = store.get_predictions()[0][0]["id"]
    store.close()

    assert cli_production.main(["settle", str(row_id), "won", "--note", "final"]) == 0
    out = capsys.readouterr().out
    assert "settled" in out and "outcome=won" in out

    # Re-transitioning a settled row is rejected loudly (exit 1).
    assert cli_production.main(["settle", str(row_id), "lost"]) == 1
    assert "terminal" in capsys.readouterr().err

    # Bad args are rejected.
    assert cli_production.main(["settle", str(row_id)]) == 1


def test_cli_void_supersede_error_commands(tmp_path, monkeypatch, capsys) -> None:
    def _fresh_row_id() -> int:
        assert _run_predict_with_fakes(tmp_path, monkeypatch) == 0
        store = ProductionPredictionStore(
            RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "data")
        )
        row_id = store.get_predictions()[0][0]["id"]
        store.close()
        return row_id

    assert cli_production.main(["void", str(_fresh_row_id())]) == 0
    assert "voided" in capsys.readouterr().out

    assert cli_production.main(
        ["supersede", str(_fresh_row_id()), "--note", "line moved"]
    ) == 0
    assert "superseded" in capsys.readouterr().out

    assert cli_production.main(["error", str(_fresh_row_id()), "--note", "bad data"]) == 0
    assert "error" in capsys.readouterr().out
