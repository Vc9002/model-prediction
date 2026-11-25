"""Moneylines and NCAAF Overs size by edge like spreads and totals (operator directive 2026-11-25).

Quick 261125-1bw: the 1.0U moneyline and NCAAF-Over caps in append_evaluated were removed.
"""

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from model_prediction.domain import League, MarketType, PickRequest, is_qualified_row
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger

_EVENT_IDS = count(1)


def _teams(league: League) -> tuple[CanonicalTeam, CanonicalTeam]:
    away = CanonicalTeam(f"{league}-away", league, "AWY", "AWY", True, None, None, ())
    home = CanonicalTeam(f"{league}-home", league, "HOM", "HOM", True, None, None, ())
    return away, home


def _request(
    market_type: MarketType,
    *,
    league: League = League.NBA,
    selection: str = "home",
    line: float | None = None,
    probability: float = 0.56,
    uncertainty: float = 0.05,
    odds: int = 100,  # 50.0% implied price
) -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        event_id=f"event-{next(_EVENT_IDS)}",
        league=league,
        away_team="AWY",
        home_team="HOM",
        market_type=market_type,
        selection=selection,
        line=line,
        sportsbook="ExampleBook",
        american_odds=odds,
        model_probability=probability,
        model_uncertainty=uncertainty,
        model_version="test-v1",
        rationale="Test rationale",
        risks="Test risk",
    )


def _eligibility(
    league: League,
    units: float = 1.0,
    *,
    qualified: bool = True,
    reason_code: str = "QUALIFIED",
    units_cap: float | None = None,
) -> EligibilityResult:
    away, home = _teams(league)
    return EligibilityResult(
        qualified, "CALL", reason_code, units, 70, 0.08, 0.08, away, home, units_cap=units_cap
    )


def _stored_row(tmp_path, tier: str, request: PickRequest, eligibility: EligibilityResult) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = PickLedger(tmp_path / f"{tier}.xlsx", tmp_path / f"{tier}.jsonl", tier=tier)
    return ledger.append_evaluated(request, eligibility)


def _units(tmp_path, tier: str, request: PickRequest, eligibility: EligibilityResult | None = None) -> float:
    eligibility = eligibility or _eligibility(request.league)
    return float(_stored_row(tmp_path, tier, request, eligibility)["units"])


def _moneyline(**kwargs) -> PickRequest:
    return _request(MarketType.MONEYLINE, **kwargs)


def _spread(**kwargs) -> PickRequest:
    return _request(MarketType.SPREAD, line=-1.5, **kwargs)


@pytest.mark.parametrize("tier", ["flat", "main"])
def test_moneyline_units_match_spread_units(tmp_path, tier: str) -> None:
    ml_units = _units(tmp_path / "ml", tier, _moneyline())
    spread_units = _units(tmp_path / "spread", tier, _spread())
    assert spread_units == pytest.approx(1.75)
    assert ml_units == spread_units


@pytest.mark.parametrize("tier", ["flat", "main"])
def test_high_edge_moneyline_reaches_the_two_unit_ceiling(tmp_path, tier: str) -> None:
    request = _moneyline(probability=0.70, uncertainty=0.01)
    assert _units(tmp_path, tier, request) == pytest.approx(2.0)


@pytest.mark.parametrize("tier", ["flat", "main"])
def test_ncaaf_over_sized_like_under(tmp_path, tier: str) -> None:
    def total(selection: str) -> PickRequest:
        return _request(
            MarketType.TOTAL, league=League.NCAAF, selection=selection, line=50.5, probability=0.56
        )

    over_units = _units(tmp_path / "over", tier, total("over"))
    under_units = _units(tmp_path / "under", tier, total("under"))
    assert under_units == pytest.approx(1.75)
    assert over_units == under_units


def test_moneyline_eligibility_units_pass_through(tmp_path) -> None:
    # The NCAAB compute_dynamic_units shape: a non-1.0 eligibility.units survives sizing.
    request = _moneyline(league=League.NCAAF)
    assert _units(tmp_path / "flat", "flat", request, _eligibility(League.NCAAF, 1.6)) == pytest.approx(1.6)
    assert _units(tmp_path / "main", "main", request, _eligibility(League.NCAAF, 1.6)) == pytest.approx(1.6)


def test_units_cap_still_binds_moneyline(tmp_path) -> None:
    request = _moneyline()
    loose = _eligibility(League.NBA, 1.6, units_cap=1.25)
    tight = _eligibility(League.NBA, 1.6, units_cap=1.0)
    assert _units(tmp_path / "loose", "flat", request, loose) == pytest.approx(1.25)
    assert _units(tmp_path / "tight", "flat", request, tight) == pytest.approx(1.0)


def test_low_edge_moneyline_stays_one_unit(tmp_path) -> None:
    # Raw edge 0.5% is under the 1% bump threshold, so the 1.0U floor holds.
    request = _moneyline(probability=0.505)
    assert _units(tmp_path, "flat", request) == pytest.approx(1.0)


def test_unqualified_main_moneyline_scales_by_edge(tmp_path) -> None:
    unqualified = _eligibility(
        League.NBA, 0.0, qualified=False, reason_code="PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED"
    )
    request = _moneyline(probability=0.70, uncertainty=0.01)
    row = _stored_row(tmp_path, "main", request, unqualified)
    assert float(row["units"]) == pytest.approx(2.0)
    assert not is_qualified_row(row)
    assert row["reason_code"] == "PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED"


def test_downgraded_main_moneyline_with_one_unit_gets_the_edge_bump(tmp_path) -> None:
    # Production downgrades (forecast._downgrade_unserved) arrive with units already set, often
    # 1.0 after edge_scaled_units rounds a small uncertainty-adjusted edge down. Main must apply
    # the same raw-edge bump as Flat, or every downgraded NBA moneyline keeps reading 1.00U.
    request = _moneyline()
    downgraded = _eligibility(
        League.NBA, 1.0, qualified=False, reason_code="PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED"
    )
    row = _stored_row(tmp_path / "main", "main", request, downgraded)
    flat_units = _units(tmp_path / "flat", "flat", request)
    assert float(row["units"]) == pytest.approx(1.75)
    assert float(row["units"]) == pytest.approx(flat_units)
    assert row["reason_code"] == "PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED"
    capped = _eligibility(
        League.NBA, 1.0, qualified=False, reason_code="PAPER_CALL_MODEL_EVIDENCE_UNVERIFIED", units_cap=1.0
    )
    assert float(_stored_row(tmp_path / "capped", "main", request, capped)["units"]) == pytest.approx(1.0)


def test_flat_moneyline_keeps_flat_call_reason_when_not_qualified(tmp_path) -> None:
    # replace() in the Flat branch must still relabel a non-qualified result as FLAT_CALL.
    request = _moneyline()
    row = _stored_row(
        tmp_path, "flat", request, _eligibility(League.NBA, 0.0, qualified=False, reason_code="X")
    )
    assert row["reason_code"] == "FLAT_CALL"
    assert float(row["units"]) == pytest.approx(1.75)
