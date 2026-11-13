import json

import pytest

from scripts.evaluate_ready_challengers import decision_context, evaluate_market_challenger, paired_decisions


def row(**changes):
    base = {
        "event_id": "a",
        "event_start_utc": "2026-08-01T20:00:00Z",
        "market_type": "spread",
        "created_at_utc": "2026-08-01T12:01:00Z",
        "selection": "home",
        "line": -3.5,
        "model_probability": 0.0,
        "result": "loss",
        "model_artifact_hash": "artifact-a",
        "decision": "NO_CALL",
        "decision_payload_json": json.dumps(
            {"home_team": "A", "away_team": "B", "observed_at_utc": "2026-08-01T12:00:00Z"}
        ),
    }
    return base | changes


def test_valid_zero_is_preserved_and_actual_call_coverage_retained():
    a, b, excluded = paired_decisions([row()], [row(model_probability=0.1, decision="CALL")])
    assert len(a) == len(b) == 1 and not excluded
    assert a[0]["probability"] == 0.0 and a[0]["outcome"] == 0
    assert a[0]["date"] == "2026-08-01"
    assert not a[0]["called"] and b[0]["called"]
    assert a[0]["evidence_origin"] == b[0]["evidence_origin"] == "historical_backtest"


@pytest.mark.parametrize(
    "changes",
    [
        {"selection": "away"},
        {"line": -4.5},
        {"event_start_utc": "2026-08-01T21:00:00Z"},
        {
            "decision_payload_json": json.dumps(
                {"home_team": "A", "away_team": "B", "observed_at_utc": "2026-08-01T13:00:00Z"}
            )
        },
    ],
)
def test_event_id_alone_does_not_pair_different_contexts(changes):
    a, b, excluded = paired_decisions([row()], [row(**changes)])
    assert not a and not b and excluded["no_exact_challenger_context"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"result": "push"},
        {"model_probability": None},
        {"model_probability": "nan"},
        {"line": None},
        {"model_artifact_hash": None},
    ],
)
def test_missing_invalid_or_push_rows_are_excluded(changes):
    a, b, excluded = paired_decisions([row()], [row(**changes)])
    assert not a and not b and excluded["challenger_invalid_or_push"] == 1


def test_conflicts_and_different_outcomes_are_never_silently_chosen():
    a, _, excluded = paired_decisions([row()], [row(), row(model_probability=0.99)])
    assert not a and excluded["challenger_conflicting_context"] == 1
    a, _, excluded = paired_decisions([row()], [row(result="win")])
    assert not a and excluded["outcome_disagreement"] == 1


def test_earliest_incumbent_context_is_chosen_before_matching():
    later = row(
        decision_payload_json=json.dumps(
            {"home_team": "A", "away_team": "B", "observed_at_utc": "2026-08-01T13:00:00Z"}
        )
    )
    a, b, excluded = paired_decisions([row(), later], [later])
    assert not a and not b and excluded["no_exact_challenger_context"] == 1


def test_pregame_timestamp_is_required():
    with pytest.raises(ValueError, match="not_pregame"):
        decision_context(
            row(
                decision_payload_json=json.dumps(
                    {"home_team": "A", "away_team": "B", "observed_at_utc": "2026-08-01T22:00:00Z"}
                )
            )
        )


def test_comparison_stops_before_metrics_when_incumbent_replay_fails(monkeypatch, tmp_path):
    import scripts.evaluate_ready_challengers as module

    records = [row(model_id="champ", sport="ncaaf"), row(model_id="chall", sport="ncaaf")]
    monkeypatch.setattr(module, "read_records", lambda *_: records)
    monkeypatch.setattr(module, "deduplicate", lambda values: (values, {}))
    monkeypatch.setattr(module, "replay", lambda *_: {"status": "UNSUPPORTED_REPLAY"})
    monkeypatch.setattr(
        module, "PairedComparison", lambda *_: pytest.fail("must not calculate metrics without reproduction")
    )
    report = evaluate_market_challenger(
        tmp_path, "NCAAF", "spread", "champ", "chall", database=tmp_path / "canonical.db"
    )
    assert report["verdict"] == "COMPARISON_BLOCKED"
    assert report["metrics"] is None and report["economics"] is None and not report["promote"]
