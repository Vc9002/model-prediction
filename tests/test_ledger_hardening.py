from datetime import datetime, timezone

from openpyxl import Workbook, load_workbook

from model_prediction.domain import League, MarketType, ModelOrigin, ModelState, PickRequest, RecordType
from model_prediction.eligibility import evaluate_eligibility
from model_prediction.ledger import FIELDNAMES, LEGACY_FIELDNAMES, PickLedger
from model_prediction.xlsx_ledger import read_xlsx_rows
from model_prediction.units import Exposure, UnitPolicy


NOW = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


def request(
    event_id: str, probability: float = 0.6235, state: ModelState = ModelState.SHADOW_QUALIFIED
) -> PickRequest:
    return PickRequest(
        event_start_utc="2026-07-14T00:00:00Z",
        event_id=event_id,
        league=League.MLB,
        away_team="BOS",
        home_team="BAL",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="Book",
        american_odds=-110,
        model_probability=probability,
        model_uncertainty=0.004,
        model_version="stat-v1",
        rationale="fixture",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=state,
        observed_at_utc="2026-07-13T11:00:00Z",
        calibration_version="cal-v1",
        model_artifact_hash="model-hash",
        calibration_artifact_hash="calibration-hash",
        code_revision="abc123",
    )


def test_research_is_zero_unit_excluded_from_exposure_roi_but_in_calibration(
    registry, ban_list, tmp_path
) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    research_request = request("research", state=ModelState.RESEARCH)
    research_gate = evaluate_eligibility(research_request, registry, ban_list, Exposure(), UnitPolicy(), NOW)
    research_row = ledger.append_evaluated(research_request, research_gate, NOW)
    assert research_row["record_type"] == RecordType.RESEARCH_OBSERVATION.value
    assert float(research_row["units"]) == 0
    ledger.settle(research_row["pick_id"], 2, 3)

    qualified_request = request("qualified")
    qualified_gate = evaluate_eligibility(
        qualified_request, registry, ban_list, Exposure(), UnitPolicy(), NOW
    )
    qualified_row = ledger.append_evaluated(qualified_request, qualified_gate, NOW)
    ledger.settle(qualified_row["pick_id"], 3, 2)

    exposure = ledger.exposure(qualified_request, NOW, ("mlb-bos", "mlb-bal"))
    assert exposure.daily_units == float(qualified_row["units"])
    report = ledger.report()
    assert report["qualified_shadow_calls"] == 1
    assert report["research_observations"] == 1
    assert report["qualified_losses"] == 1
    assert report["qualified_pnl_units"] < 0
    assert report["calibration"]["sample_size"] == 2
    assert report["by_model_version"] == {"stat-v1": 2}


def test_research_can_auto_score_one_hypothetical_unit_on_settlement(
    registry, ban_list, tmp_path
) -> None:
    ledger = PickLedger(
        tmp_path / "picks.xlsx",
        tmp_path / "events.jsonl",
        research_score_units=1.0,
        research_scoring_note="one-unit hypothetical policy",
    )
    req = request("research-one-unit", state=ModelState.RESEARCH)
    gate = evaluate_eligibility(req, registry, ban_list, Exposure(), UnitPolicy(), NOW)
    row = ledger.append_evaluated(req, gate, NOW)

    settled = ledger.settle(row["pick_id"], 2, 3)

    assert settled["units"] == "0.00"
    assert settled["pnl_units"] == "0.0000"
    assert settled["research_score_units"] == "1.0000"
    assert float(settled["research_pnl_units"]) > 0
    assert settled["research_scoring_note"] == "one-unit hypothetical policy"


def test_banned_total_is_recorded_as_zero_unit_no_call(registry, ban_list, tmp_path) -> None:
    ban_list.add(League.MLB, "BAL")
    req = request("banned-total")
    req = PickRequest(**{**req.__dict__, "market_type": MarketType.TOTAL, "selection": "over", "line": 8.5})
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    gate = evaluate_eligibility(req, registry, ban_list, Exposure(), UnitPolicy(), NOW)
    row = ledger.append_evaluated(req, gate, NOW)
    assert row["decision"] == "NO_CALL"
    assert row["reason_code"] == "NO_CALL_TEAM_BANNED"
    assert row["banned_team_id"] == "mlb-bal"
    assert float(row["units"]) == 0
    assert ledger.report()["no_call_team_banned_count"] == 1
    assert ledger.exposure(req, NOW).daily_units == 0


def test_older_excel_schema_migrates_with_backup_and_preserved_units(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    old = {field: "" for field in LEGACY_FIELDNAMES}
    old.update(
        {
            "pick_id": "legacy",
            "created_at_utc": "2026-01-01T00:00:00Z",
            "event_start_utc": "2026-01-02T00:00:00Z",
            "event_id": "old",
            "league": "MLB",
            "away_team": "NYY",
            "home_team": "BOS",
            "market_type": "total",
            "selection": "over",
            "line": "8.5",
            "sportsbook": "Book",
            "american_odds": "-110",
            "decimal_odds": "1.909091",
            "market_implied_probability": "0.523810",
            "model_probability": "0.55",
            "model_uncertainty": "0.03",
            "edge": "0.02619",
            "confidence_score": "50",
            "units": "0.25",
            "model_version": "mlb-analyst-v0",
            "status": "open",
            "call_type": "forced_call",
            "rationale": "legacy",
            "review_status": "not_applicable",
        }
    )
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Picks"
    worksheet.append(LEGACY_FIELDNAMES)
    worksheet.append([old[field] for field in LEGACY_FIELDNAMES])
    workbook.save(path)
    ledger = PickLedger(path, tmp_path / "events.jsonl")
    row = ledger.rows()[0]
    assert path.with_suffix(".xlsx.bak-v1").exists()
    assert row["record_type"] == "RESEARCH_OBSERVATION"
    assert row["units"] == "0.00" and row["legacy_units"] == "0.25"
    headers, _ = read_xlsx_rows(path)
    assert headers == FIELDNAMES
    assert ledger.audit.events()[-1]["event_type"] == "ledger_migrated"


def test_settlement_and_void_are_idempotent(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    first = ledger.append_call(request("settle"), 0.25, 60, now=NOW)
    settled = ledger.settle(first["pick_id"], 2, 3)
    assert ledger.settle(first["pick_id"], 2, 3) == settled
    second = ledger.append_call(request("void"), 0.25, 60, now=NOW)
    voided = ledger.void(second["pick_id"], "postponed")
    assert voided["result"] == "push" and float(voided["pnl_units"]) == 0
    assert ledger.void(second["pick_id"], "postponed") == voided


def test_report_filters_are_version_aware(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_call(request("one"), 0.25, 60, now=NOW)
    second = request("two")
    second = PickRequest(**{**second.__dict__, "model_version": "stat-v2", "calibration_version": "cal-v2"})
    ledger.append_call(second, 0.25, 60, now=NOW)
    assert ledger.report({"model_version": "stat-v1"})["records"] == 1
    assert ledger.report({"calibration_version": "cal-v2"})["records"] == 1
    assert ledger.report({"record_type": "qualified"})["records"] == 2


def test_report_odds_range_includes_brier_flat_pnl_and_clv(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    first = ledger.append_call(request("bucket-win"), 0.25, 60, now=NOW)
    second_request = request("bucket-loss", probability=0.45)
    second_request = PickRequest(**{**second_request.__dict__, "american_odds": 150})
    second = ledger.append_call(second_request, 0.25, 60, now=NOW)
    ledger.settle(first["pick_id"], 2, 3, closing_american_odds=-105)
    ledger.settle(second["pick_id"], 3, 2, closing_american_odds=140)

    report = ledger.report(by_odds_range=True)
    populated = [bucket for bucket in report["by_odds_range"] if bucket["count"]]

    assert len(populated) == 2
    assert all(bucket["model_brier"] is not None for bucket in populated)
    assert all(bucket["market_brier"] is not None for bucket in populated)
    assert all(bucket["mean_clv"] is not None for bucket in populated)
    assert sum(bucket["flat_one_unit_pnl"] for bucket in populated) != 0
    assert report["by_market_evaluation"]["moneyline"]["count"] == 2


def test_research_scoring_is_separate_from_qualified_units(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    research = request("research-score", state=ModelState.RESEARCH)
    row = ledger.append_call(research, 1.0, 60, call_type="forced_call", now=NOW)
    ledger.settle(row["pick_id"], 2, 3)
    scored = ledger.score_research([row["pick_id"]], 1.0)[0]
    assert scored["units"] == "0.00"
    assert scored["research_score_units"] == "1.0000"
    assert float(scored["research_pnl_units"]) > 0
    report = ledger.report()
    assert report["qualified_pnl_units"] == 0
    assert report["research_staked_units"] == 1.0
    assert report["research_pnl_units"] > 0


def test_excel_ledger_has_office_table_filter_and_frozen_header(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    ledger = PickLedger(path, tmp_path / "events.jsonl")
    ledger.append_call(request("excel-format"), 0.25, 60, now=NOW)

    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        sheet = workbook["Picks"]
        summary = workbook["Summary"]
        assert sheet.freeze_panes == "A2"
        assert "PicksLedger" in sheet.tables
        assert sheet.tables["PicksLedger"].autoFilter.ref
        assert sheet.auto_filter.ref is None
        assert sheet["A1"].font.bold
        assert sheet["A1"].fill.fgColor.rgb.endswith("1F4E78")
        assert summary["E7"].value.startswith("=IFERROR(")
        assert summary["G7"].number_format == "0.0%"
    finally:
        workbook.close()
