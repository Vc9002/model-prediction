"""Regressions for runtime quote routing, dynamic replay and started picks."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction import runtime_paths
from model_prediction.cli.forecast import _nba_volatility_features, _read_polymarket_snapshot_rows
from model_prediction.learned_replay import snapshot_hash
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.saved_forecasts import (
    existing_started_picks,
    preserve_started_args,
    stored_slate_coverage,
)
from model_prediction.scalar_artifact_replay import build_snapshot, replay_snapshot


def isolated_roots(tmp_path, monkeypatch):
    paths = RuntimePaths.for_test(tmp_path)
    monkeypatch.setattr(runtime_paths, "_default_repo_root", lambda: paths.repo_root)
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    (paths.repo_root / ".env").write_text(
        f'MODEL_PREDICTION_RUNTIME_ROOT="{paths.runtime_root}"\n', encoding="utf-8"
    )
    return paths


def test_interactive_cli_reads_same_runtime_quotes_as_worker(tmp_path, monkeypatch):
    paths = isolated_roots(tmp_path, monkeypatch)
    assert RuntimePaths.resolve().runtime_root == paths.runtime_root
    archive = paths.runtime_root / "odds/nba/2026-10-25/polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps({"market_type": "spread", "line": 7.5}) + "\n")
    legacy = paths.repo_root / "data/odds/nba/2026-10-25/polymarket_snapshots.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"market_type": "spread", "line": 99.5}) + "\n")
    assert _read_polymarket_snapshot_rows(legacy)[0]["line"] == 7.5
    # Explicit isolated inputs must not borrow production data.
    fixture = tmp_path / "fixture/odds/nba/2026-10-25/polymarket_snapshots.jsonl"
    assert _read_polymarket_snapshot_rows(fixture) == []
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "override"))
    assert RuntimePaths.resolve().runtime_root == tmp_path / "override"


def test_dynamic_volatility_replays_engine_output_and_rejects_tampering():
    artifact = json.loads(Path("config/models/nba-spread-margin-v5.json").read_text())
    model = BasketballModel(
        sport="nba",
        version=artifact["model_version"],
        league="NBA",
        margin_sd=artifact["margin_sd"],
        total_sd=artifact["total_sd"],
        dynamic_volatility=True,
        offseason_gap_days=artifact["offseason_gap_days"],
        offseason_retention=artifact["offseason_retention"],
        isolate_postseason=artifact["isolate_postseason"],
    )
    game = UpcomingGame(
        event_id="regression",
        event_start_utc="2026-10-25T23:00:00Z",
        away_team="Away",
        home_team="Home",
        spread_away_line=7.5,
    )
    prediction = next(p for p in model.predict_games([], [game]) if p.market_type == "spread")
    assert prediction.inference_inputs["sd"] != artifact["margin_sd"]
    features = _nba_volatility_features(artifact, [], prediction)
    snap = build_snapshot(
        artifact,
        features,
        prediction.inference_inputs,
        event_id=game.event_id,
        event_start_utc=game.event_start_utc,
        observed_at_utc="2026-10-25T12:00:00Z",
    )
    assert replay_snapshot(snap)["away"] == prediction.probabilities["away"]
    tampered = copy.deepcopy(snap)
    tampered["inference_inputs"]["sd"] += 0.1
    tampered["snapshot_hash"] = snapshot_hash(tampered)
    with pytest.raises(ValueError, match="parameter_mismatch"):
        replay_snapshot(tampered)
    missing = copy.deepcopy(snap)
    del missing["features"]["volatility_away_consistency"]
    missing["snapshot_hash"] = snapshot_hash(missing)
    with pytest.raises(KeyError):
        replay_snapshot(missing)


def test_started_picks_kept_original_and_sqlite_read_only(tmp_path, monkeypatch):
    paths = isolated_roots(tmp_path, monkeypatch)
    db = paths.ledgers_db
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE ledger_records (
            pick_id TEXT, sport TEXT, ledger_tier TEXT, status TEXT,
            created_at_utc TEXT, event_start_utc TEXT, decision_payload_json TEXT)""")
        connection.executemany(
            "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?)",
            [
                (
                    "original",
                    "nfl",
                    "main",
                    "open",
                    "2026-10-25T12:00Z",
                    "2026-10-25T20:05Z",
                    json.dumps({"selection": "over", "line": 43.5, "model_probability": 0.56807}),
                ),
                ("superseded", "nfl", "main", "superseded", "2026-10-25T11:00Z", "2026-10-25T20:05Z", "{}"),
                ("future", "nfl", "main", "open", "2026-10-25T12:00Z", "2026-10-26T00:20Z", "{}"),
            ],
        )
    before = db.read_bytes()
    rows = existing_started_picks("2026-10-25", ["nfl"], now=datetime(2026, 10, 25, 21, tzinfo=UTC))
    assert len(rows) == 1
    assert rows[0]["pick_id"] == "original"
    assert rows[0]["line"] == 43.5 and rows[0]["model_probability"] == 0.56807
    assert rows[0]["refreshed"] is False
    assert db.read_bytes() == before
    # An explicitly supplied isolated ledger must never expose global picks.
    isolated = SimpleNamespace(rows=list)
    assert (
        existing_started_picks(
            "2026-10-25",
            ["nfl"],
            ledger=isolated,
            now=datetime(2026, 10, 25, 21, tzinfo=UTC),
            data_root=tmp_path / "custom",
        )
        == []
    )
    args = SimpleNamespace(force=True)
    assert preserve_started_args(args, rows).force is False
    assert args.force is True  # caller-owned args unchanged
    assert preserve_started_args(args, []).force is True
    assert preserve_started_args(args, [], rows).force is False


def test_refresh_does_not_supersede_started_pick(tmp_path, monkeypatch):
    from model_prediction.cli import commands

    monkeypatch.setattr(commands, "utc_now", lambda: datetime(2026, 10, 25, 21, tzinfo=UTC))
    row = {
        "pick_id": "keep",
        "status": "open",
        "league": "NFL",
        "created_at_utc": "2026-10-25T12:00Z",
        "event_start_utc": "2026-10-25T20:05Z",
    }
    ledger = SimpleNamespace(rows=lambda: [row])
    assert commands._clear_today_open(ledger, "2026-10-25", leagues={"nfl"}) == []
    assert ledger.rows() == [row]
    row["event_start_utc"] = "unverified"
    assert commands._clear_today_open(ledger, "2026-10-25", leagues={"nfl"}) == []


def test_full_nfl_slate_is_36_slots_independent_of_main_qualification(tmp_path, monkeypatch):
    isolated_roots(tmp_path, monkeypatch)
    rows = [
        {
            "pick_id": f"{event}-{market}",
            "event_id": str(event),
            "market_type": market,
            "league": "NFL",
            "status": "open",
            "created_at_utc": "2026-10-25T10:00Z",
            "event_start_utc": "2026-10-25T20:05Z",
        }
        for event in range(12)
        for market in ("moneyline", "spread", "total")
    ]
    main = SimpleNamespace(rows=lambda: rows[:13])
    flat = SimpleNamespace(rows=lambda: rows)
    coverage = stored_slate_coverage(
        "2026-10-25",
        "nfl",
        ledger=main,
        flat_ledger=flat,
        data_root=tmp_path / "isolated",
    )
    assert coverage["flat"] == {
        "picks": 36,
        "games": 12,
        "markets": {"moneyline": 12, "spread": 12, "total": 12},
        "duplicate_slots": [],
    }
    assert coverage["main"]["picks"] == 13


def test_ordinary_logging_writes_learned_flat_companion(tmp_path, monkeypatch):
    from model_prediction.cli import forecast

    main = SimpleNamespace(rows=list)
    flat = SimpleNamespace(rows=list)
    monkeypatch.setattr(forecast, "MultiSportPickLedger", lambda *a, **kw: flat)
    monkeypatch.setattr(forecast, "ledger_path", lambda config: tmp_path / "picks.xlsx")
    monkeypatch.setattr(forecast, "_forecast_nba_spread_sport", lambda **kw: {})
    monkeypatch.setattr(forecast, "_forecast_nba_total_sport", lambda **kw: {})
    calls = []

    def learned(*args, **kwargs):
        calls.append((args[6], kwargs))
        return {"candidates": []}

    monkeypatch.setattr(forecast, "_forecast_learned_sport", learned)
    args = SimpleNamespace(command="forecast", sport="nba", date="2026-10-25", log=True, force=True)
    forecast.run_forecast(args, {"project": {}}, None, None, main, None, tmp_path)
    assert len(calls) == 2
    assert calls[0][0] is main and calls[1][0] is flat
    assert calls[0][1]["flat_mode"] is False and calls[1][1]["flat_mode"] is True
    assert calls[0][1]["observed_at"] == calls[1][1]["observed_at"]
    assert calls[0][1]["observed_at"] == datetime(2026, 10, 25, tzinfo=UTC)


def test_model_coverage_does_not_hide_missing_flat_behind_main(tmp_path):
    import yaml

    from model_prediction.ledger_coverage import build_report

    config = tmp_path / "config"
    config.mkdir()
    (config / "production.yaml").write_text(
        yaml.safe_dump({"prediction_service": {"champions": {"NFL": {"moneyline": "nfl-v1"}}}})
    )
    (config / "model.yaml").write_text("models: {}\n")
    db = tmp_path / "ledger.db"
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE ledger_records (
            model_id TEXT, market_type TEXT, event_start_utc TEXT, event_id TEXT,
            selection TEXT, line REAL, ledger_tier TEXT, status TEXT,
            reason_code TEXT, created_at_utc TEXT, decision TEXT)""")
        connection.execute(
            "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "nfl-v1",
                "moneyline",
                "2026-10-25T20:05:00Z",
                "e1",
                "home",
                None,
                "main",
                "open",
                "test",
                "2026-10-25T12:00:00Z",
                "NO_CALL",
            ),
        )
    run = {
        "step2_3_forecast_and_log": {
            "nfl": {
                "candidates": [
                    {"event_id": "e1", "market_type": "moneyline"},
                    {"event_id": "e2", "market_type": "moneyline"},
                ]
            }
        }
    }
    report = build_report(tmp_path, db, "2026-10-25", run)
    assert report["missing_flat_lanes"] == 1
    assert report["main_non_call_records"] == 1
    assert report["lanes"][0]["flat_coverage"]["missing_priced_event_ids"] == ["e1", "e2"]
    assert report["status"] == "error"
    from model_prediction.cli.daily import _material_daily_errors

    assert "ledger_coverage" in _material_daily_errors({"ledger_coverage": report})
    # A started Main-only event is still a gap when another Flat event exists
    # and fresh pricing no longer contains the started event.
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "nfl-v1",
                "moneyline",
                "2026-10-25T20:05:00Z",
                "e1",
                "home",
                None,
                "flat",
                "open",
                "test",
                "2026-10-25T12:00:00Z",
                "CALL",
            ),
        )
        connection.execute(
            "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "nfl-v1",
                "moneyline",
                "2026-10-25T20:05:00Z",
                "e2",
                "home",
                None,
                "main",
                "open",
                "test",
                "2026-10-25T12:00:00Z",
                "CALL",
            ),
        )
    report = build_report(tmp_path, db, "2026-10-25", {})
    assert report["missing_flat_lanes"] == 1
    assert report["lanes"][0]["flat_coverage"]["main_without_flat_event_ids"] == ["e2"]
