import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from model_prediction.learned_replay import build_snapshot
from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.research_generation import file_hash
from scripts import capture_research_pairs as runner
from tests.test_research_forward import fixture, history, models  # noqa: F401


@pytest.fixture
def prepared(models, tmp_path, monkeypatch):  # noqa: F811
    generation = tmp_path / "generation"
    shutil.copytree(models / "moneyline", generation / "models/nba-moneyline")
    root = Path(__file__).parents[1]
    registered = next(
        m
        for m in yaml.safe_load((root / "config/production.yaml").read_text())["prediction_service"]["models"]
        if m["sport"] == "NBA" and m["market"] == "moneyline"
    )
    artifact = LearnedMarketArtifact.load(root / registered["artifact"])
    model = {
        "sport": "NBA",
        "market": "moneyline",
        "incumbent": artifact.version,
        "model_id": "nba-moneyline-research-20260909-v1",
        "status": "TRAINED_RESEARCH_ONLY",
        "artifact_sha256": file_hash(generation / "models/nba-moneyline/model.joblib"),
    }
    (generation / "manifest.json").write_text(json.dumps({"models": [model]}))
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(
                {
                    "event_id": g.event_id,
                    "event_start_utc": g.date,
                    "home_team_id": "A",
                    "away_team_id": "B",
                    "home_score": g.score_a,
                    "away_score": g.score_b,
                }
            )
            for g in history()
        )
    )
    raw = fixture("moneyline") | {
        "sport": "NBA",
        "market": "moneyline",
        "home_team": "Home",
        "away_team": "Away",
    }
    fixtures = tmp_path / "fixtures.jsonl"
    fixtures.write_text(json.dumps(raw) + "\n")
    monkeypatch.setattr(runner, "source_path", lambda root, sport: source)
    # File capture uses the real clock. Fix that clock too, to model a real
    # preparation that precedes the decision (without bypassing hash checks).
    original_copy = runner.copy_file

    def clocked_copy(source, target):
        return original_copy(source, target) | {"captured_at_utc": "2025-05-10T10:00:00Z"}

    monkeypatch.setattr(runner, "copy_file", clocked_copy)
    monkeypatch.setattr(runner, "now", lambda: "2025-05-10T10:00:00Z")
    directory = tmp_path / "prepared"
    runner.prepare(tmp_path, generation, fixtures, directory)
    features = dict.fromkeys(artifact.raw["market_models"]["moneyline"]["feature_names"], 0.5)
    p = artifact.probability("moneyline", features)
    snapshot = build_snapshot(
        artifact,
        features,
        event_id="new",
        observed_at_utc="2025-05-10T12:00:00Z",
        event_start_utc=raw["event_start_utc"],
        base_home_probability=p,
        served_home_probability=p,
        adjustment={"method": "identity", "applied": False},
    )
    record = {
        "pick_id": "pick",
        "ledger_tier": "flat",
        "sport": "NBA",
        "model_id": artifact.version,
        "model_artifact_hash": artifact.hash,
        "event_id": "new",
        "event_start_utc": raw["event_start_utc"],
        "created_at_utc": "2025-05-10T12:00:01Z",
        "settled_at_utc": None,
        "status": "open",
        "result": None,
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "model_probability": p,
        "decision_payload_json": json.dumps({"home_team": "Home", "away_team": "Away"}),
        "feature_payload_json": json.dumps({"model_input_snapshot": snapshot}),
    }
    database = tmp_path / "ledger.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE ledger_records (" + ",".join(f"{key} TEXT" for key in record) + ")")
        connection.execute(
            "INSERT INTO ledger_records VALUES (" + ",".join("?" for _ in record) + ")", list(record.values())
        )
    monkeypatch.setattr(runner, "now", lambda: "2025-05-10T12:01:00Z")
    return directory, database, raw


def test_capture_scores_and_pairs_exact_bound_context(prepared, tmp_path):
    from scripts import evaluate_captured_pairs, score_research_forward

    directory, database, raw = prepared
    before = file_hash(database)
    output = tmp_path / "capture"
    report = runner.capture(directory, database, output)
    assert report["counts"] == {"CAPTURED_REPLAY_PASS": 1}
    assert file_hash(database) == before
    candidate = runner.rows(output / "predictions.jsonl")[0]["prediction"]
    assert candidate["observed_at_utc"] == "2025-05-10T12:00:00Z"
    assert candidate["capture_context"]["prediction_created_at_utc"] == "2025-05-10T12:01:00Z"
    with pytest.raises(FileExistsError):
        runner.capture(directory, database, output)
    outcome = tmp_path / "outcome.jsonl"
    outcome.write_text(json.dumps(raw | {"status": "completed", "home_score": 102, "away_score": 99}) + "\n")
    scores = tmp_path / "scores"
    score_research_forward.run(tmp_path, output, scores, outcome_overrides={"NBA:games": str(outcome)})
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE ledger_records SET status='settled', result='win', settled_at_utc='2025-05-10T23:00:00Z'"
        )
    result = evaluate_captured_pairs.run(tmp_path, scores, database, tmp_path / "evaluation")
    assert result["paired"] == 1
    assert not result["promote"]
    # A changed preparation cannot be accepted merely because the fitted
    # prediction still reproduces exactly.
    prep = json.loads((scores / "preparation.json").read_text())
    prep["prepared_at_utc"] = "2025-05-10T09:00:00Z"
    (scores / "preparation.json").write_text(json.dumps(prep))
    result = evaluate_captured_pairs.run(tmp_path, scores, database, tmp_path / "tampered")
    assert result["paired"] == 0


@pytest.mark.parametrize(
    "change,reason",
    [
        ("late", "fixture_not_future"),
        ("missing", "missing_incumbent_serving_snapshot"),
        ("earlier", "awaiting_new_incumbent_decision"),
        ("observed_before", "incumbent_predates_preparation_or_is_future"),
        ("participant", "pair_participant_orientation_mismatch"),
    ],
)
def test_capture_rejects_invalid_context(prepared, tmp_path, monkeypatch, change, reason):
    directory, database, raw = prepared
    if change == "late":
        monkeypatch.setattr(runner, "now", lambda: raw["event_start_utc"])
    else:
        with sqlite3.connect(database) as connection:
            if change == "missing":
                connection.execute("UPDATE ledger_records SET feature_payload_json='{}'")
            elif change == "earlier":
                connection.execute("UPDATE ledger_records SET created_at_utc='2025-05-10T09:00:00Z'")
            elif change == "observed_before":
                from model_prediction.learned_replay import snapshot_hash

                envelope = json.loads(
                    connection.execute("SELECT feature_payload_json FROM ledger_records").fetchone()[0]
                )
                envelope["model_input_snapshot"]["observed_at_utc"] = "2025-05-10T09:00:00Z"
                envelope["model_input_snapshot"]["snapshot_hash"] = snapshot_hash(
                    envelope["model_input_snapshot"]
                )
                connection.execute("UPDATE ledger_records SET feature_payload_json=?", [json.dumps(envelope)])
            elif change == "participant":
                connection.execute(
                    "UPDATE ledger_records SET decision_payload_json=?",
                    [json.dumps({"home_team": "Other", "away_team": "Away"})],
                )
    output = tmp_path / "capture"
    assert runner.capture(directory, database, output)["status"] == "PARTIAL"
    assert runner.rows(output / "predictions.jsonl")[0]["reason"] == reason


def test_prepared_input_change_is_rejected(prepared, tmp_path):
    directory, database, _ = prepared
    (directory / "sources/nba-games.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="prepared_input_changed"):
        runner.capture(directory, database, tmp_path / "capture")
    assert json.loads((tmp_path / "capture/RUN_STATUS.json").read_text())["status"] == "FAILED"


def test_observer_captures_without_waiting_for_game_start(prepared, tmp_path):
    directory, database, _ = prepared
    output = tmp_path / "observed"
    before = file_hash(database)
    result = runner.observe(directory, database, output, poll_seconds=1, max_wait_seconds=1)
    assert result["status"] == "COMPLETE" and result["pending"] == 0
    assert result["counts"] == {"CAPTURED_REPLAY_PASS": 1}
    assert result["batches"] == 1
    assert runner.rows(output / "predictions.jsonl") == runner.rows(output / "batches/0000/predictions.jsonl")
    assert file_hash(database) == before


def test_observer_timeout_is_missing_evidence_not_success(prepared, tmp_path, monkeypatch):
    directory, database, _ = prepared
    monkeypatch.setattr(runner, "read_decisions", lambda *args: [])
    result = runner.observe(directory, database, tmp_path / "observed", poll_seconds=1, max_wait_seconds=1)
    assert result["status"] == "PARTIAL" and result["pending"] == 0
    assert result["counts"] == {"NO_PREDICTION": 1}
    assert runner.rows(tmp_path / "observed/predictions.jsonl")[0]["reason"] == "observation_deadline_reached"


def test_observer_does_not_skip_bad_first_decision_for_later_valid_one(prepared, tmp_path):
    directory, database, _ = prepared
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM ledger_records").fetchone())
        connection.execute("UPDATE ledger_records SET model_probability='0.999999'")
        later = original | {"pick_id": "later", "created_at_utc": "2025-05-10T12:00:30Z"}
        connection.execute(
            "INSERT INTO ledger_records VALUES (" + ",".join("?" for _ in later) + ")", list(later.values())
        )
    output = tmp_path / "observed"
    result = runner.observe(directory, database, output, poll_seconds=1, max_wait_seconds=1)
    assert result["status"] == "PARTIAL" and result["batches"] == 1
    assert result["counts"] == {"NO_PREDICTION": 1}
    assert "probability" in runner.rows(output / "predictions.jsonl")[0]["reason"]


def test_pregame_audit_reproduces_and_retains_null_economics(prepared, tmp_path, monkeypatch):
    from datetime import datetime

    from scripts import audit_research_pair_quotes as audit

    directory, database, _ = prepared
    output = tmp_path / "capture"
    runner.capture(directory, database, output)

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat("2025-05-10T12:02:00+00:00")

    monkeypatch.setattr(audit, "datetime", Clock)
    # A mirror can arrive after capture with a lexically earlier ID. It must
    # not replace the explicitly bound original or destroy a valid pair.
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        record = dict(connection.execute("SELECT * FROM ledger_records").fetchone())
        record.update(pick_id="aaa-mirror", ledger_tier="main")
        connection.execute(
            "INSERT INTO ledger_records VALUES (" + ",".join("?" for _ in record) + ")", list(record.values())
        )
    before = file_hash(database)
    root = Path(__file__).parents[1]
    result = audit.run(root, output, database, tmp_path / "audit")
    assert result["counts"] == {"REPRODUCED_NO_VERIFIED_QUOTE": 1}
    assert result["pnl_usd"] is None and result["roi"] is None
    row = runner.rows(tmp_path / "audit/audit_rows.jsonl")[0]
    assert row["pick_id"] == "pick" and row["incumbent_reproduction"] == "PASS"
    assert file_hash(database) == before
    # The quote adapter has independent archive/semantics tests; inject a
    # deterministic payout table here to exercise the complete pregame report.
    monkeypatch.setattr(
        audit.PairedQuoteArchive,
        "contracts",
        lambda *args: {
            "contracts": [
                {
                    "market_id": "m",
                    "side": "long",
                    "ask": 0.01,
                    "ask_size": 10,
                    "settlement_values": {"a_win": 1, "b_win": 0},
                }
            ],
            "impossible_outcomes": [],
        },
    )
    positive = audit.run(root, output, database, tmp_path / "priced-audit")
    assert positive["counts"] == {"VERIFIED_PREGAME_CHOICES": 1}
    priced = runner.rows(tmp_path / "priced-audit/audit_rows.jsonl")[0]
    assert all(choice["pnl_usd"] is None for choice in priced["choices"].values())
    # A genuinely earlier decision is not a mirror and invalidates the pair.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE ledger_records SET created_at_utc='2025-05-10T11:59:59Z' WHERE pick_id='aaa-mirror'"
        )
    result = audit.run(root, output, database, tmp_path / "earlier-audit")
    assert result["counts"] == {"REPRODUCTION_BLOCKED": 1}
    assert result["reasons"] == {"pair_bound_incumbent_mismatch": 1}
    (output / "sources/nba-games.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="prepared_input_hash_mismatch"):
        audit.run(root, output, database, tmp_path / "tampered-audit")
