"""MultiSportPickLedger had zero direct test coverage before this file.

Added 2026-08-11 alongside the fix for the paused com.modelprediction.daily
launchd job (see 826c893's commit message): data/main was archived, and the
next scheduled daily run would have silently recreated data/main/*.xlsx via
PickLedger.initialize()'s unconditional empty-workbook bootstrap. The fix is
config["project"]["main_ledger_enabled"] = false -> cli.py::main() passes
retired=True to the one MultiSportPickLedger construction that backs Main.
These tests cover both the config wiring and the retired-mode behavior at
the MultiSportPickLedger layer (PickLedger's own retired-mode contract is
covered directly in test_ledger.py::test_a_retired_ledger_never_touches_disk).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from model_prediction.config import load_config
from model_prediction.domain import League, MarketType, PickRequest
from model_prediction.eligibility import EligibilityResult, RecordType
from model_prediction.entities import CanonicalTeam
from model_prediction.main_ledgers import MAIN_LEDGER_SPORTS, MultiSportPickLedger

AWAY = CanonicalTeam("mlb-nyy", League.MLB, "NYY", "NYY", True, None, None, ())
HOME = CanonicalTeam("mlb-bos", League.MLB, "BOS", "BOS", True, None, None, ())


def _request(event_id: str) -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        event_id=event_id,
        league=League.MLB,
        away_team="NYY",
        home_team="BOS",
        market_type=MarketType.TOTAL,
        selection="over",
        line=8.5,
        sportsbook="ExampleBook",
        american_odds=-110,
        model_probability=0.59,
        model_uncertainty=0.01,
        model_version="mlb-test-v1",
        rationale="Test rationale",
        risks="Test risk",
    )


def _qualified_call() -> EligibilityResult:
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.5, 70, 0.08, 0.08, AWAY, HOME
    )


def test_real_config_has_main_ledger_enabled() -> None:
    """The actual config/model.yaml this project ships, not a test fixture --
    proves the flag cli.py::main() reads is really set, not just that the
    retired-mode mechanism works in isolation. Un-retired 2026-08-13 by
    operator directive ("unretire main ledger"): the archived per-sport
    workbooks were restored to data/main/ and Main is back in the daily
    write path, so the shipped config must now say enabled."""
    config = load_config()
    assert config["project"]["main_ledger_enabled"] is True


def test_multisport_pick_ledger_retired_never_creates_the_main_directory(tmp_path) -> None:
    ledger = MultiSportPickLedger(tmp_path, retired=True)
    for sport in MAIN_LEDGER_SPORTS:
        row = ledger.append_evaluated(
            _request(f"event-{sport}"), _qualified_call(), now=datetime.now(UTC)
        )
        assert row["pick_id"]
    assert ledger.rows() == []
    assert not (tmp_path / "main").exists()


def test_multisport_pick_ledger_not_retired_does_create_the_main_directory(tmp_path) -> None:
    """Control proving the assertions above exercise retired's guard, not
    some unrelated reason nothing got written."""
    ledger = MultiSportPickLedger(tmp_path, retired=False)
    ledger.append_evaluated(_request("event-mlb"), _qualified_call(), now=datetime.now(UTC))
    assert (tmp_path / "main").exists()
    assert len(ledger.rows()) == 1


def test_cli_main_reads_the_retired_flag_the_same_way_it_constructs_the_ledger(tmp_path) -> None:
    """Mirrors cli.py::main()'s exact expression
    (retired=not config["project"].get("main_ledger_enabled", True)) against
    the real loaded config, pointed at a throwaway data root -- proves the
    wiring end to end without needing a full argv-parsing CLI invocation.
    Main was un-retired 2026-08-13 by operator directive, so the real config
    now yields retired=False and the ledger creates its directory."""
    config = load_config()
    retired = not config["project"].get("main_ledger_enabled", True)
    assert retired is False
    ledger = MultiSportPickLedger(tmp_path, retired=retired)
    ledger.append_evaluated(_request("event-mlb"), _qualified_call(), now=datetime.now(UTC))
    assert (tmp_path / "main").exists()
