"""Tests for System Architecture Hardening & Resilience.

Verifies:
1. Transactional ledger rollback mechanism (_rollback_pre_clear and transactional_ledger_scope).
2. Universal StructuralDistributionProtocol contract.
3. Numerical calibrator float-boundary hardening (NaN, Inf, float-epsilon overflow).
4. Polymarket Kelly correlation exposure canonical game key normalization across derivatives.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from model_prediction.calibration import (
    BetaCalibrator,
    CalibrationMetadata,
    IdentityCalibrator,
    TemperatureCalibrator,
    TrainablePlattCalibrator,
)
from model_prediction.cli.commands import _rollback_pre_clear, transactional_ledger_scope
from model_prediction.models.base import StructuralDistributionProtocol
from model_prediction.portfolio.polymarket_kelly import PolymarketKellyEngine, PolymarketOrderDecision

# ── 1. Transactional Ledger Rollback ──────────────────────────────────────────


class MockLedger:
    def __init__(self, path: Path) -> None:
        self.path = path


def test_rollback_pre_clear_single_file(tmp_path: Path) -> None:
    ledger_file = tmp_path / "nfl.xlsx"
    bak_file = tmp_path / "nfl.xlsx.pre_clear.bak"

    # Original state saved in bak
    bak_file.write_text("ORIGINAL_CONTENT_PRE_CLEAR", encoding="utf-8")
    # Mutated/cleared state on ledger
    ledger_file.write_text("MUTATED_CONTENT", encoding="utf-8")

    ledger = MockLedger(ledger_file)
    restored = _rollback_pre_clear(ledger)

    assert restored is True
    assert ledger_file.read_text(encoding="utf-8") == "ORIGINAL_CONTENT_PRE_CLEAR"


def test_rollback_pre_clear_directory(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "main"
    ledger_dir.mkdir(parents=True)

    mlb_orig = ledger_dir / "mlb.xlsx.pre_clear.bak"
    mlb_file = ledger_dir / "mlb.xlsx"
    mlb_orig.write_text("MLB_ORIGINAL", encoding="utf-8")
    mlb_file.write_text("MLB_CLEARED", encoding="utf-8")

    nfl_orig = ledger_dir / "nfl.xlsx.pre_clear.bak"
    nfl_file = ledger_dir / "nfl.xlsx"
    nfl_orig.write_text("NFL_ORIGINAL", encoding="utf-8")
    nfl_file.write_text("NFL_CLEARED", encoding="utf-8")

    ledger = MockLedger(ledger_dir)
    restored = _rollback_pre_clear(ledger)

    assert restored is True
    assert mlb_file.read_text(encoding="utf-8") == "MLB_ORIGINAL"
    assert nfl_file.read_text(encoding="utf-8") == "NFL_ORIGINAL"


def test_transactional_ledger_scope_rollback_on_failure(tmp_path: Path) -> None:
    ledger_file = tmp_path / "tennis.xlsx"
    bak_file = tmp_path / "tennis.xlsx.pre_clear.bak"
    bak_file.write_text("PRE_CLEAR_DATA", encoding="utf-8")
    ledger_file.write_text("EMPTY_CLEARED_DATA", encoding="utf-8")

    ledger = MockLedger(ledger_file)

    with (
        pytest.raises(RuntimeError, match="Simulated forecast crash"),
        transactional_ledger_scope([ledger], "2026-10-17"),
    ):
        raise RuntimeError("Simulated forecast crash mid-pipeline")

    # Verify that the ledger was automatically rolled back
    assert ledger_file.read_text(encoding="utf-8") == "PRE_CLEAR_DATA"


# ── 2. Structural Distribution Protocol ───────────────────────────────────────


def test_structural_distribution_protocol_interface() -> None:
    class DummyEngine:
        def margin_distribution(self, expected_margin: float, sd: float) -> dict[int, float]:
            return {0: 1.0}

        def total_distribution(self, expected_total: float, sd: float) -> dict[int, float]:
            return {44: 1.0}

        def prob_cover(
            self, spread_home_line: float, expected_margin: float, sd: float
        ) -> tuple[float, float, float]:
            return 0.5, 0.5, 0.0

        def prob_total(
            self, total_line: float, expected_total: float, sd: float
        ) -> tuple[float, float, float]:
            return 0.5, 0.5, 0.0

    dummy = DummyEngine()
    assert isinstance(dummy, StructuralDistributionProtocol)


# ── 3. Calibrator Boundary & Precision Safety ─────────────────────────────────


def test_calibrator_boundary_and_precision_safety() -> None:
    meta = CalibrationMetadata("dummy", "v1", "base", None, None, 100, "hash123")
    platt = TrainablePlattCalibrator(intercept=0.1, slope=1.2, metadata=meta)
    temp = TemperatureCalibrator(temperature=1.1, metadata=meta)
    beta = BetaCalibrator(a=1.05, b=0.95, c=0.02, metadata=meta)
    identity = IdentityCalibrator(base_model_version="base", version="ident-v1")

    calibrators = [identity, platt, temp, beta]

    # Test edge cases that previously could crash or yield invalid output
    test_inputs = [
        1.0000000000000002,  # Floating point imprecision slightly > 1.0
        -1e-16,  # Floating point imprecision slightly < 0.0
        0.0,
        1.0,
        float("nan"),  # NaN should fail-safe to 0.5
        float("inf"),  # Inf should fail-safe to 0.5
        float("-inf"),  # -Inf should fail-safe to 0.5
    ]

    for cal in calibrators:
        for val in test_inputs:
            out = cal.transform(val)
            assert math.isfinite(out), f"{type(cal).__name__} produced non-finite output for input {val}"
            assert 0.0 <= out <= 1.0, (
                f"{type(cal).__name__} produced out-of-bounds output {out} for input {val}"
            )
            if math.isnan(val) or math.isinf(val):
                assert out == 0.5, f"{type(cal).__name__} failed to return 0.5 for invalid input {val}"


# ── 4. Kelly Correlation Exposure Canonical Game Key Normalization ────────────


def test_clean_game_key_canonicalizes_derivatives() -> None:
    engine = PolymarketKellyEngine(bankroll=1000.0)

    # 1. Moneyline order
    o_ml = PolymarketOrderDecision(
        market_id="m1",
        side="BUY_YES",
        is_maker=False,
        order_price=0.55,
        model_probability=0.60,
        market_price=0.55,
        edge=0.05,
        expected_value_pct=0.09,
        kelly_fraction_full=0.11,
        kelly_fraction_recommended=0.025,
        stake_units=25.0,
        reason="ML edge",
        home_team="Los Angeles Lakers",
        away_team="Golden State Warriors",
        question="Will the Los Angeles Lakers beat the Golden State Warriors?",
        event_start_utc="2026-10-18T02:30:00Z",
    )

    # 2. Total order (question formatted with prop and total details)
    o_tot = PolymarketOrderDecision(
        market_id="m2",
        side="BUY_YES",
        is_maker=False,
        order_price=0.52,
        model_probability=0.57,
        market_price=0.52,
        edge=0.05,
        expected_value_pct=0.096,
        kelly_fraction_full=0.10,
        kelly_fraction_recommended=0.025,
        stake_units=25.0,
        reason="Total edge",
        home_team="Over 224.5",
        away_team="Under 224.5",
        question="Total Points: Los Angeles Lakers vs. Golden State Warriors (Over 224.5)",
        event_start_utc="2026-10-18T02:30:00Z",
    )

    # 3. Spread order (away team inverted, trailing spread qualifiers)
    o_sp = PolymarketOrderDecision(
        market_id="m3",
        side="BUY_YES",
        is_maker=False,
        order_price=0.50,
        model_probability=0.55,
        market_price=0.50,
        edge=0.05,
        expected_value_pct=0.10,
        kelly_fraction_full=0.10,
        kelly_fraction_recommended=0.025,
        stake_units=25.0,
        reason="Spread edge",
        home_team="Golden State Warriors +4.5",
        away_team="Los Angeles Lakers -4.5",
        question="Golden State Warriors vs Los Angeles Lakers - Spread",
        event_start_utc="2026-10-18T02:30:00Z",
    )

    capped = engine.apply_correlation_exposure_caps([o_ml, o_tot, o_sp], max_game_exposure_pct=0.05)

    # Total stake across the three orders was 25 + 25 + 25 = 75.0 (7.5% of $1000 bankroll)
    # The cap is 5% = $50.0 max. Due to 2-decimal cent rounding ($16.67 x 3), sum is <= $50.02.
    total_staked = sum(o.stake_units for o in capped)
    assert total_staked <= 50.02, f"Expected total stake <= $50.02, but got {total_staked}"
    # Each order should be scaled down proportionally (50 / 75 = 2/3 scaling -> ~16.67)
    for o in capped:
        assert math.isclose(o.stake_units, 16.67, abs_tol=0.01)
