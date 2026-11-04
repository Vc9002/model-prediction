"""Exact quote lineage and missingness coverage for college candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from model_prediction.college_contract_evidence import build_candidate_evidence, save_candidate_evidence


def _event() -> dict:
    return {
        "event_id": "event-1",
        "event_start_utc": "2026-11-05T23:00:00Z",
        "title": "Away vs. Home",
        "markets": [
            {
                "market_id": "market-1",
                "market_slug": "slug-1",
                "market_type": "spread",
                "line": -3.5,
                "sides": [
                    {"selection": "home", "is_long": True, "line": -3.5},
                    {"selection": "away", "is_long": False, "line": 3.5},
                ],
            },
            {
                "market_id": "market-2",
                "market_slug": "slug-2",
                "market_type": "total",
                "line": 51.5,
                "sides": [
                    {"selection": "over", "is_long": True, "line": 51.5},
                    {"selection": "under", "is_long": False, "line": 51.5},
                ],
            },
        ],
    }


def _quote() -> dict:
    return {
        "provider": "polymarket_us",
        "reconstructed": False,
        "timestamp_valid": True,
        "market_state": "MARKET_STATE_OPEN",
        "event_id": "event-1",
        "event_start_utc": "2026-11-05T23:00:00Z",
        "market_id": "market-1",
        "market_slug": "slug-1",
        "market_type": "spread",
        "line": -3.5,
        "observed_at_utc": "2026-11-05T19:00:00Z",
        "transact_time_utc": "2026-11-05T18:59:00Z",
        "long": {"bid": 0.49, "ask": 0.51, "ask_size": 10},
        "short": {"bid": 0.48, "ask": 0.52, "ask_size": 12},
    }


def test_exact_quote_join_and_all_listed_coverage(tmp_path) -> None:
    path = tmp_path / "odds" / "ncaaf" / "2026-11-05" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_quote()) + "\n", encoding="utf-8")
    rows, summary = build_candidate_evidence(
        sport="ncaaf",
        day="2026-11-05",
        events=[_event()],
        probabilities={"slug-1": {"model_version": "v4", "probabilities": {"home": 0.57, "away": 0.43}}},
        archive_root=tmp_path,
        decision_at=datetime(2026, 11, 5, 20, tzinfo=UTC),
    )
    assert summary["listed_contracts"] == 2
    assert summary["quote_matched"] == 1
    assert summary["model_scored"] == 1
    assert summary["complete"] == 1
    assert rows[0]["quote_hash"] is not None
    assert rows[0]["sides"][0]["executable_ask"] == 0.51
    assert rows[0]["sides"][1]["model_probability"] == 0.43
    assert rows[1]["quote_reason"] == "not_archived"
    assert save_candidate_evidence(rows, summary, tmp_path) == save_candidate_evidence(
        rows, summary, tmp_path
    )


def test_rejects_postdecision_and_wrong_line_quotes(tmp_path) -> None:
    path = tmp_path / "odds" / "ncaaf" / "2026-11-05" / "polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True)
    late = {**_quote(), "observed_at_utc": "2026-11-05T21:00:00Z"}
    wrong_line = {**_quote(), "line": -4.5}
    path.write_text(json.dumps(late) + "\n" + json.dumps(wrong_line) + "\n", encoding="utf-8")
    rows, summary = build_candidate_evidence(
        sport="ncaaf",
        day="2026-11-05",
        events=[_event()],
        probabilities={},
        archive_root=tmp_path,
        decision_at=datetime(2026, 11, 5, 20, tzinfo=UTC),
    )
    assert summary["quote_matched"] == 0
    assert rows[0]["quote_reason"] == "line_mismatch"
    assert rows[0]["quote_hash"] is None
