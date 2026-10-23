import json
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.esports_context import capture_schedule, resolve_format

NOW = datetime(2026, 10, 23, 16, tzinfo=UTC)
START = NOW + timedelta(days=1)


def source_row(**changes):
    return {
        "id": 123,
        "status": "upcoming",
        "start_date": START.isoformat(),
        "end_date": None,
        "team1_id": 12,
        "team2_id": 34,
        "bo_type": 3,
        **changes,
    }


class Source:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def _get(self, path, query):
        assert path == "/matches"
        assert set(query) == {"page[limit]", "page[offset]", "filter[matches.discipline_id][eq]", "sort"}
        self.queries.append(dict(query))
        return {"results": self.rows}


def capture(tmp_path, monkeypatch, rows):
    monkeypatch.setattr("model_prediction.esports_context.utc_now", lambda: NOW)
    return capture_schedule(tmp_path, "cs2", START, Source(rows))


def test_exact_source_binding_and_immutable_prospective_capture(tmp_path, monkeypatch):
    schedule = capture(tmp_path, monkeypatch, [source_row()])
    result = resolve_format(schedule, ["bo3:1:34", "bo3:1:12"], START, NOW)
    assert result["best_of"] == 3
    assert result["source_match_id"] == 123
    assert result["status"] == "matched"
    assert result["historical_availability_verified"] is False
    [archive] = list(tmp_path.rglob("*.json"))
    raw = json.loads(archive.read_text())
    assert raw["response"]["results"] == [source_row()]
    assert raw["observed_at_utc"] == NOW.isoformat()
    assert raw["query"]["filter[matches.discipline_id][eq]"] == 1
    before = archive.read_bytes()
    capture(tmp_path, monkeypatch, [source_row()])
    assert archive.read_bytes() == before
    assert len(list(tmp_path.rglob("*.json"))) == 1


@pytest.mark.parametrize(
    ("rows", "ids", "start", "decision", "status"),
    [
        ([source_row()], ["unknown:alpha", "bo3:1:34"], START, NOW, "source_identity_unresolved"),
        ([source_row()], ["bo3:1:12", "bo3:1:35"], START, NOW, "source_match_missing"),
        ([source_row()], ["bo3:1:12", "bo3:1:34"], START + timedelta(seconds=1), NOW, "source_match_missing"),
        ([source_row(), source_row(id=124)], ["bo3:1:12", "bo3:1:34"], START, NOW, "source_match_ambiguous"),
        ([source_row(id=None)], ["bo3:1:12", "bo3:1:34"], START, NOW, "source_match_identity_missing"),
        ([source_row(bo_type=7)], ["bo3:1:12", "bo3:1:34"], START, NOW, "source_format_unsupported"),
        ([source_row(status="finished")], ["bo3:1:12", "bo3:1:34"], START, NOW, "source_not_upcoming"),
        (
            [source_row(end_date=START.isoformat())],
            ["bo3:1:12", "bo3:1:34"],
            START,
            NOW,
            "source_not_upcoming",
        ),
        (
            [source_row()],
            ["bo3:1:12", "bo3:1:34"],
            START,
            NOW - timedelta(seconds=1),
            "source_observed_after_decision",
        ),
        ([source_row(team1_id=None)], ["bo3:1:12", "bo3:1:34"], START, NOW, "source_match_missing"),
    ],
)
def test_no_fuzzy_or_late_or_unsupported_context(tmp_path, monkeypatch, rows, ids, start, decision, status):
    schedule = capture(tmp_path, monkeypatch, rows)
    result = resolve_format(schedule, ids, start, decision)
    assert result["status"] == status
    assert result["best_of"] is None


def test_bounded_scan_cannot_certify_unique_binding(tmp_path, monkeypatch):
    rows = [source_row(id=i + 1) for i in range(100)]
    source = Source(rows)
    monkeypatch.setattr("model_prediction.esports_context.utc_now", lambda: NOW)
    schedule = capture_schedule(tmp_path, "cs2", START, source)
    assert len(source.queries) == 4
    assert schedule["status"] == "pagination_incomplete"
    assert resolve_format(schedule, ["bo3:1:12", "bo3:1:34"], START, NOW)["best_of"] is None


def test_empty_schedule_is_explicit_complete_but_unmatched(tmp_path, monkeypatch):
    schedule = capture(tmp_path, monkeypatch, [])
    assert schedule["status"] == "complete"
    assert resolve_format(schedule, ["bo3:1:12", "bo3:1:34"], START, NOW)["status"] == "source_match_missing"
