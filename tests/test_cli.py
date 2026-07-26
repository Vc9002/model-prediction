"""Targeted tests for cli.py's highest-risk functions, which previously had
zero direct test coverage despite being the entire CLI's largest module
(2,500+ lines, ~40 subcommands).

Scope is intentionally narrow: _verify_chain (audit tamper-detection, the
one thing that would silently stop working if it broke) and
_clear_today_open (the re-forecast replacement logic fixed earlier this
session for both its date-matching and started-game guards).
"""

from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.cli import _clear_today_open, _verify_chain
from model_prediction.domain import (
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
)
from model_prediction.eligibility import EligibilityResult, RecordType
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger

AWAY = CanonicalTeam("mlb-bos", League.MLB, "Boston Red Sox", "BOS", True, None, None, ())
HOME = CanonicalTeam("mlb-nyy", League.MLB, "New York Yankees", "NYY", True, None, None, ())


def _ledger(tmp_path) -> PickLedger:
    return PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")


def _log_pick(ledger: PickLedger, *, event_start_utc: str, created_at, units: float = 1.0) -> dict:
    request = PickRequest(
        event_start_utc=event_start_utc,
        event_id="event-1",
        league=League.MLB,
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="Book",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="v1",
        rationale="test",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
    )
    eligibility = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", units, 60, 0.05, 0.05, AWAY, HOME
    )
    return ledger.append_evaluated(request, eligibility, now=created_at)


# ----------------------------------------------------------------- _verify_chain


def test_verify_chain_reports_intact_for_a_freshly_logged_pick(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    result = _verify_chain(ledger.audit.path, ledger)
    assert result["chain_intact"] is True
    assert result["break_count"] == 0
    assert result["reconciled"] is True
    assert result["rows_missing_creation_event"] == []


def test_verify_chain_detects_a_tampered_event(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    import json

    lines = ledger.audit.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["units"] = "999"
    ledger.audit.path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    result = _verify_chain(ledger.audit.path, ledger)
    assert result["chain_intact"] is False
    assert result["break_count"] == 1
    assert result["breaks"][0]["kind"] == "hash_mismatch"


def test_verify_chain_flags_a_ledger_row_with_no_creation_event(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    row = _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    # Simulate a row that entered the ledger through a path that bypassed
    # the audited API entirely (the exact symptom that identified the
    # retroactively-rescored batch found and removed earlier this session).
    ledger.audit.path.write_text("", encoding="utf-8")

    result = _verify_chain(ledger.audit.path, ledger)
    assert result["rows_missing_creation_event"] == [row["pick_id"]]
    assert result["reconciled"] is False


def test_verify_chain_on_a_missing_audit_file_reports_zero_lines(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    result = _verify_chain(tmp_path / "does_not_exist.jsonl", ledger)
    assert result["audit_lines"] == 0
    assert result["chain_intact"] is True


# ------------------------------------------------------------- _clear_today_open


def test_clear_today_open_removes_a_not_yet_started_pick_by_creation_date(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    row = _log_pick(
        ledger,
        event_start_utc="2099-01-01T00:00:00Z",  # far future -- never "started"
        created_at=datetime(2026, 7, 25, 10, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2026-07-25")
    assert removed == [row["pick_id"]]
    assert ledger.rows() == []


def test_clear_today_open_by_event_date_matches_on_event_start_not_creation(tmp_path) -> None:
    """The fix from this session: a pick logged the day BEFORE its game
    (created 7/24 for a 7/25 game) must still be cleared by a 7/25 run when
    by_event_date=True, not silently frozen forever. Uses a far-future date
    so the event is guaranteed to still be "not yet started" regardless of
    when this test actually runs."""
    ledger = _ledger(tmp_path)
    row = _log_pick(
        ledger,
        event_start_utc="2030-07-25T00:00:00Z",
        created_at=datetime(2030, 7, 24, 20, tzinfo=UTC),
    )
    # created_at-only matching (main ledger's old behavior) would miss this.
    assert _clear_today_open(ledger, "2030-07-25", by_event_date=False) == []
    assert len(ledger.rows()) == 1
    # by_event_date=True correctly catches it.
    removed = _clear_today_open(ledger, "2030-07-25", by_event_date=True)
    assert removed == [row["pick_id"]]


def test_clear_today_open_never_removes_an_already_started_game(tmp_path) -> None:
    """Uses safely past-dated timestamps (both well before any real
    wall-clock run of this test) so the event is unambiguously "started"."""
    ledger = _ledger(tmp_path)
    _log_pick(
        ledger,
        event_start_utc="2020-01-02T00:00:00Z",  # after created_at, still long past
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2020-01-01")
    assert removed == []
    assert len(ledger.rows()) == 1


def test_clear_today_open_ignores_a_different_date(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(
        ledger,
        event_start_utc="2099-01-01T00:00:00Z",
        created_at=datetime(2026, 7, 20, 10, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2026-07-25")
    assert removed == []
    assert len(ledger.rows()) == 1
