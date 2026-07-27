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

from model_prediction import cli
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
from model_prediction.learned_forward import LearnedForwardCandidate
from model_prediction.ledger import PickLedger
from model_prediction.units import Exposure

AWAY = CanonicalTeam("mlb-bos", League.MLB, "Boston Red Sox", "BOS", True, None, None, ())
HOME = CanonicalTeam("mlb-nyy", League.MLB, "New York Yankees", "NYY", True, None, None, ())


def _ledger(tmp_path) -> PickLedger:
    return PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")


class _CaptureLedger:
    def __init__(self) -> None:
        self.appended = []

    def exposure(self, request, now=None, **kwargs):
        return Exposure()

    def append_evaluated(self, request, eligibility, now=None):
        self.appended.append((request, eligibility))
        return {"pick_id": f"pick-{len(self.appended)}"}


def _international_config(*, min_edge: float) -> dict:
    return {
        "models": {
            "KBO": {
                "min_edge": min_edge,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }


def _international_forecast() -> dict:
    return {
        "model_version": "kbo-tie-aware-elo-v2",
        "priced_contracts": [
            {
                "event_start_utc": "2026-07-27T10:00:00Z",
                "event_id": "kbo-game-1",
                "market_slug": "kbo-game-1",
                "teams": ["Hanwha", "Doosan"],
                "observed_at_utc": "2026-07-26T10:00:00Z",
                "artifact_hash": "artifact-hash",
                "sides": [
                    {
                        "team": "Hanwha",
                        "model_fair_settlement_value": 0.40,
                        "executable_ask": 0.45,
                        "tie_probability": 0.04,
                    },
                    {
                        "team": "Doosan",
                        "model_fair_settlement_value": 0.60,
                        "executable_ask": 0.55,
                        "tie_probability": 0.04,
                    },
                ],
            }
        ],
    }


def _esports_forecast() -> dict:
    base = {
        "event_start_utc": "2026-07-27T10:00:00Z",
        "teams": ["Team A", "Team B"],
        "observed_at_utc": "2026-07-26T10:00:00Z",
        "artifact_hash": "artifact-hash",
    }
    return {
        "title": "lol",
        "model_version": "lol-tiered-elo-v4",
        "priced_contracts": [
            {
                **base,
                "event_id": "valid",
                "market_slug": "valid",
                "gated_research_eligible": True,
                "sides": [
                    {"team": "Team A", "model_probability": 0.60, "executable_ask": 0.55},
                    {"team": "Team B", "model_probability": 0.40, "executable_ask": 0.45},
                ],
            },
            {
                **base,
                "event_id": "negative-edge",
                "market_slug": "negative-edge",
                "gated_research_eligible": True,
                "sides": [
                    {"team": "Team A", "model_probability": 0.60, "executable_ask": 0.62},
                    {"team": "Team B", "model_probability": 0.40, "executable_ask": 0.38},
                ],
            },
            {
                **base,
                "event_id": "untrained-team",
                "market_slug": "untrained-team",
                "gated_research_eligible": False,
                "sides": [
                    {"team": "Team A", "model_probability": 0.65, "executable_ask": 0.55},
                    {"team": "Team B", "model_probability": 0.35, "executable_ask": 0.45},
                ],
            },
        ],
    }


def test_esports_research_keeps_unvalidated_teams_and_gated_requires_positive_edge(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    research = _CaptureLedger()
    gated = _CaptureLedger()
    config = {
        "models": {
            "LOL": {
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }

    logged = cli._log_esports_forecast(
        _esports_forecast(), config, research, gated_ledger=gated
    )

    # Every safely priced candidate reaches the research ledger, including
    # the unvalidated/new-team one (as a downgraded NO_CALL row) -- only the
    # gated ledger is a curated subset.
    assert logged == 3
    assert len(research.appended) == 3
    assert len(gated.appended) == 1
    assert gated.appended[0][0].event_id == "valid"
    by_event = {request.event_id: eligibility for request, eligibility in research.appended}
    assert by_event["negative-edge"].reason_code == "NO_CALL_LOW_EDGE"
    assert by_event["untrained-team"].reason_code == "NO_CALL_MODEL_UNVALIDATED"


def test_esports_exposure_check_happens_while_ledger_lock_is_held(monkeypatch) -> None:
    """Exposure must be computed and the row appended inside one held
    _LEDGER_LOCK critical section, not as two separately-lockable steps --
    otherwise two concurrent forecast threads could both read the same stale
    exposure before either writes (in-process TOCTOU)."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))

    lock_held_when_exposure_checked: list[bool] = []

    class _LockCheckingLedger(_CaptureLedger):
        def exposure(self, request, now=None, **kwargs):
            lock_held_when_exposure_checked.append(cli._LEDGER_LOCK.locked())
            return super().exposure(request, now=now, **kwargs)

    research = _LockCheckingLedger()
    gated = _CaptureLedger()
    config = {
        "models": {
            "LOL": {
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }

    cli._log_esports_forecast(_esports_forecast(), config, research, gated_ledger=gated)

    assert lock_held_when_exposure_checked, "expected exposure() to be called at least once"
    assert all(lock_held_when_exposure_checked), (
        "exposure() must be checked while _LEDGER_LOCK is held"
    )


def test_esports_flat_mode_never_writes_research_or_gated(monkeypatch) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    research = _CaptureLedger()
    gated = _CaptureLedger()
    config = {
        "models": {"LOL": {"status": "shadow_qualified"}},
        "project": {},
        "bankroll": {},
    }

    logged = cli._log_esports_forecast(
        _esports_forecast(),
        config,
        research,
        flat_mode=True,
        gated_ledger=gated,
    )

    assert logged == 0
    assert research.appended == []
    assert gated.appended == []


def test_daily_forecast_roster_includes_soccer_and_both_international_baseball_leagues() -> None:
    assert "soccer" not in cli.DAILY_LEARNED_SPORTS
    assert "soccer" in cli.SPORTS
    assert set(cli.DAILY_INTERNATIONAL_BASEBALL_SPORTS) == {"kbo", "npb"}
    assert set(cli.FLAT_LEDGER_SPORTS) == {"mlb", "nba", "wnba", "nfl"}
    assert set(cli.RESEARCH_ONLY_DAILY_SPORTS) == {
        "soccer",
        "lol",
        "cs2",
        "dota2",
        "valorant",
        "kbo",
        "npb",
    }
    assert not set(cli.FLAT_LEDGER_SPORTS) & set(cli.RESEARCH_ONLY_DAILY_SPORTS)


def test_invalid_quote_timestamp_keeps_mlb_model_opinion_visible_but_non_executable(
    monkeypatch,
    tmp_path,
) -> None:
    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    candidate = LearnedForwardCandidate(
        event_id="mlb-1",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.62,
        home_probability=0.62,
        confidence_threshold=0.55,
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_LEARNED_CONFIDENCE",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.60, "trend_gap": 0.1},
        feature_snapshot_hash="feature-hash",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(
        cli,
        "build_learned_moneyline_slate",
        lambda **kwargs: ([candidate], [], 1),
    )
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            "executable_ask": 0.55,
            "market_slug": "mlb-1",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": False,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL,
            "CALL",
            "QUALIFIED",
            1.0,
            60,
            0.07,
            0.02,
            AWAY,
            HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.05,
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb",
        "2026-07-26",
        True,
        config,
        Registry(),
        object(),
        ledger,
    )

    assert result["logged"] == 1
    request, eligibility = ledger.appended[0]
    assert request.sportsbook == "model_opinion_no_executable_quote"
    assert "executable_quote_timestamp_invalid" in request.unavailable_features
    assert eligibility.record_type is RecordType.RESEARCH_OBSERVATION
    assert eligibility.reason_code == "NO_CALL_MARKET_UNAVAILABLE"
    assert eligibility.units == 0


def test_international_forecast_preview_never_requires_or_writes_a_ledger(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        None,
    )

    assert result["logged"] == 0
    assert result["priced_contracts"]
    assert "Preview only" in result["logging_note"]


def test_international_forecast_logs_low_edge_to_research_but_not_gated(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1
    assert research.appended[0][1].record_type is RecordType.RESEARCH_OBSERVATION
    assert research.appended[0][1].reason_code == "NO_CALL_LOW_EDGE"
    assert gated.appended == []


def test_international_forecast_mirrors_only_strategy_qualified_calls(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.02),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert research.appended[0][1].decision == "CALL"
    assert gated.appended[0][1].decision == "CALL"


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
