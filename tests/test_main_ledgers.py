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

import pytest

from model_prediction.config import load_config
from model_prediction.domain import League, MarketType, PickRequest
from model_prediction.eligibility import EligibilityResult, RecordType
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import FIELDNAMES, PickLedger, defer_sqlite_xlsx_exports
from model_prediction.main_ledgers import (
    MAIN_LEDGER_SPORTS,
    MultiSportPickLedger,
    existing_flat_ledgers,
    main_ledger,
)
from model_prediction.xlsx_ledger import write_xlsx_rows_atomic

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
        row = ledger.append_evaluated(_request(f"event-{sport}"), _qualified_call(), now=datetime.now(UTC))
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


def test_existing_flat_ledgers_honor_sqlite_authority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "sqlite")
    path = tmp_path / "flat" / "mlb.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [])

    [ledger] = existing_flat_ledgers(tmp_path)
    try:
        assert ledger.authority == "sqlite"
        assert ledger.tier == "flat"
        assert ledger.sport == "mlb"
        assert ledger.mirror is not None
    finally:
        ledger.mirror.close()


def test_sqlite_projection_batch_rebuilds_xlsx_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "sqlite")
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    ledger = main_ledger(tmp_path, "mlb")
    writes = 0

    from model_prediction import ledger as ledger_module

    real_write = ledger_module.write_xlsx_rows_atomic

    def counted_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        return real_write(*args, **kwargs)

    monkeypatch.setattr(ledger_module, "write_xlsx_rows_atomic", counted_write)
    try:
        with defer_sqlite_xlsx_exports():
            for index in range(3):
                ledger.append_evaluated(
                    _request(f"event-batched-{index}"),
                    _qualified_call(),
                    now=datetime.now(UTC),
                )
            assert writes == 0
        assert writes == 1
        assert len(ledger.export_rows()) == 3
    finally:
        if ledger.mirror is not None:
            ledger.mirror.close()


def _nba_request(event_id: str) -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        event_id=event_id,
        league=League.NBA,
        away_team="BOS",
        home_team="NYK",
        market_type=MarketType.TOTAL,
        selection="over",
        line=215.5,
        sportsbook="espn_consensus",
        american_odds=-110,
        model_probability=0.59,
        model_uncertainty=0.01,
        model_version="nba-total-v1",
        rationale="Test rationale",
        risks="Test risk",
    )


def test_an_unlisted_sports_rows_are_still_reachable_by_operator_commands(tmp_path, monkeypatch) -> None:
    """A sport the pipeline writes before MAIN_LEDGER_SPORTS learns about it
    must still be administrable. If an unlisted sport wrote rows while
    normalize_main_sport(sport) still raised, void / settle / update-closing
    -- all of which route through _ledger_for_pick_id -- must still reach
    them."""
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    # Simulate a not-yet-admitted sport; NBA itself is now wired to Main.
    monkeypatch.setattr(
        "model_prediction.main_ledgers.MAIN_LEDGER_SPORTS",
        tuple(sport for sport in MAIN_LEDGER_SPORTS if sport != "nba"),
    )

    orphan = PickLedger(
        tmp_path / "main" / "nba.xlsx",
        audit_path=tmp_path / "events.jsonl",
        tier="main",
        sport="nba",
    )
    row = orphan.append_evaluated(_nba_request("nba-1"), _qualified_call(), now=datetime.now(UTC))

    ledger = MultiSportPickLedger(tmp_path)
    voided = ledger.void(row["pick_id"], "fabricated ask")
    assert voided["status"] == "settled"
    assert voided["result"] == "push"
    assert voided["void_reason"] == "fabricated ask"

    # Routing is deliberately NOT widened: reaching an orphaned row is a
    # repair, admitting a new sport into Main is a decision.
    with pytest.raises(ValueError, match="no Main/Flat ledger configured"):
        ledger.append_evaluated(_nba_request("nba-2"), _qualified_call(), now=datetime.now(UTC))


def test_nba_routes_to_both_operational_tiers(tmp_path, monkeypatch) -> None:
    from model_prediction.domain import PRODUCTION_SPORTS

    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    assert "nba" in PRODUCTION_SPORTS
    assert "nba" in MAIN_LEDGER_SPORTS
    for flat in (False, True):
        ledger = MultiSportPickLedger(tmp_path, flat=flat)
        row = ledger.append_evaluated(_nba_request("nba-wired"), _qualified_call(), now=datetime.now(UTC))
        assert row["pick_id"]
        assert any(saved["pick_id"] == row["pick_id"] for saved in ledger.rows())
        settled = ledger.void(row["pick_id"], "routing check")
        assert settled["status"] == "settled"


def test_dashboard_cache_discovers_legacy_nba_alongside_sport_folders(tmp_path) -> None:
    from model_prediction.dashboard_cache import DashboardCache

    for relative in ("sports/mlb/main.xlsx", "main/nba.xlsx", "main/mlb.xlsx"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    paths = DashboardCache(tmp_path)._all_tiers()["main"]
    assert tmp_path / "main/nba.xlsx" in paths
    assert tmp_path / "sports/mlb/main.xlsx" in paths
    assert tmp_path / "main/mlb.xlsx" not in paths


def test_dashboard_and_ledger_sport_lists_cannot_drift(monkeypatch) -> None:
    """dashboard/common.py keeps its own copy of the sport tuple on purpose
    (it avoids importing model_prediction at load time). Two hand-maintained
    copies of the same list drift silently, and the dashboard reading a
    different set of workbooks than the ledger writes is invisible until a
    sport goes missing from the UI -- so pin them equal here."""
    from model_prediction.dashboard.common import _MAIN_LEDGER_SPORTS

    assert tuple(_MAIN_LEDGER_SPORTS) == MAIN_LEDGER_SPORTS


def test_flat_and_main_ledgers_eliminate_shadow_downgrades_and_ensure_real_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    from model_prediction.ledger import RecordType

    # Flat ledger: everything is called flat (1.0U) with QUALIFIED_SHADOW_CALL, visible in reporting
    flat_ledger = MultiSportPickLedger(tmp_path, flat=True)
    obs_eligibility = EligibilityResult(
        RecordType.RESEARCH_OBSERVATION,
        "NO_CALL",
        "NO_CALL_LOW_EDGE",
        0.0,
        45,
        0.005,
        0.52,
        AWAY,
        HOME,
    )
    flat_row = flat_ledger.append_evaluated(
        _request("event-flat-cov"), obs_eligibility, now=datetime.now(UTC)
    )
    assert flat_row["record_type"] == "QUALIFIED_SHADOW_CALL"
    assert flat_row["decision"] == "CALL"
    assert float(flat_row["units"]) == 1.0

    # Main ledger: gated qualified calls remain QUALIFIED_SHADOW_CALL with CALL decision
    main_ledger = MultiSportPickLedger(tmp_path, flat=False)
    qual_eligibility = _qualified_call()
    main_row = main_ledger.append_evaluated(
        _request("event-main-gated"), qual_eligibility, now=datetime.now(UTC)
    )
    assert main_row["record_type"] == "QUALIFIED_SHADOW_CALL"
    assert main_row["decision"] == "CALL"
    assert main_row["reason_code"] == "QUALIFIED"
    assert float(main_row["units"]) > 0.0
