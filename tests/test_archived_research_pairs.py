import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from model_prediction.research_generation import file_hash
from scripts import (
    audit_research_pair_quotes,
    capture_research_pairs,
    evaluate_archived_research_pairs,
    score_research_forward,
)
from tests.test_research_forward import fixture, history, models  # noqa: F401
from tests.test_research_pair_capture import prepared  # noqa: F401


def test_removed_ledger_decision_scores_from_original_pregame_archive(prepared, tmp_path, monkeypatch):  # noqa: F811
    directory, database, raw = prepared
    captures = tmp_path / "capture"
    capture_research_pairs.capture(directory, database, captures)

    class Clock:
        @staticmethod
        def now(tz):
            return datetime.fromisoformat("2025-05-10T12:02:00+00:00")

    monkeypatch.setattr(audit_research_pair_quotes, "datetime", Clock)
    root = Path(__file__).parents[1]
    audit = tmp_path / "audit"
    audit_research_pair_quotes.run(root, captures, database, audit)
    outcome = tmp_path / "outcome.jsonl"
    outcome.write_text(json.dumps(raw | {"status": "completed", "home_score": 105, "away_score": 99}) + "\n")
    scores = tmp_path / "scores"
    score_research_forward.run(root, captures, scores, outcome_overrides={"NBA:games": str(outcome)})
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE ledger_records SET status='removed'")
    before = file_hash(database)
    report = evaluate_archived_research_pairs.run(root, audit, scores, database, tmp_path / "evaluated")
    assert report["pairs"] == 1 and not report["failures"]
    assert report["canonical_record_status_counts"] == {"removed": 1}
    assert report["economics"]["candidate"]["pnl_usd"] is None
    pair = evaluate_archived_research_pairs.rows(tmp_path / "evaluated/paired_rows.jsonl")[0]
    assert pair["outcome"] == "a_win" and pair["candidate_source_rebuild"] == "PASS"
    assert "not canonical ledger settlement" in pair["settlement_basis"]
    assert file_hash(database) == before
    # Deleting the live record must not invalidate the preserved experiment.
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM ledger_records")
    report = evaluate_archived_research_pairs.run(root, audit, scores, database, tmp_path / "missing")
    assert report["pairs"] == 1 and report["canonical_record_status_counts"] == {"MISSING": 1}
    # An archive edit cannot silently alter the pregame policy or predictions.
    (audit / "audit_rows.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="archive_or_policy_hash"):
        evaluate_archived_research_pairs.run(root, audit, scores, database, tmp_path / "tampered")
