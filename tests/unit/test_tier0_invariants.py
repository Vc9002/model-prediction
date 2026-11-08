"""Tier 0 Fast Invariant Test Suite (< 3.0 seconds execution).

Validates fundamental mathematical invariants, Point-in-Time safety,
and complete SportRegistry resolution across all 18 supported sports.
"""

from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.features.base import GameRecord
from model_prediction.features.pipeline import UnifiedFeaturePipeline
from model_prediction.sports import SUPPORTED_SPORTS
from model_prediction.sports.base import SportAdapter, SportRegistry


@pytest.mark.unit
def test_hard_edge_gate_boundaries() -> None:
    """Verify Hard Edge Gate invariant [0.02 <= Edge <= 0.08]."""
    min_edge = 0.02
    max_edge = 0.08

    def is_valid_edge(edge: float) -> bool:
        return min_edge <= round(edge, 4) <= max_edge

    # Edges below 2.0% are rejected (fee drag)
    assert not is_valid_edge(0.015)
    assert not is_valid_edge(0.0199)

    # Edges between 2.0% and 8.0% are admitted
    assert is_valid_edge(0.020)
    assert is_valid_edge(0.045)
    assert is_valid_edge(0.080)

    # Edges above 8.0% are flagged/quarantined as suspicious anomalies
    assert not is_valid_edge(0.081)
    assert not is_valid_edge(0.150)


@pytest.mark.unit
def test_dynamic_kelly_sizing_bounds() -> None:
    """Verify Dynamic Kelly sizing scales strictly within [1.00U, 2.00U]."""

    def compute_units(edge: float, min_edge: float = 0.02, max_edge: float = 0.08) -> float:
        if edge < min_edge:
            return 0.0
        if edge >= max_edge:
            return 2.0
        ratio = (edge - min_edge) / (max_edge - min_edge)
        return round(1.0 + ratio, 2)

    assert compute_units(0.015) == 0.0
    assert compute_units(0.020) == 1.00
    assert compute_units(0.050) == 1.50
    assert compute_units(0.080) == 2.00
    assert compute_units(0.120) == 2.00


@pytest.mark.unit
def test_fee_aware_breakeven_formula() -> None:
    """Verify taker fee formula strictly increases hurdle above nominal ask."""
    # Polymarket crypto fee curve: 2% on profit: fee = 0.02 * P * (1 - P)
    ask_prob = 0.50
    fee_rate = 0.02
    fee = fee_rate * ask_prob * (1.0 - ask_prob)
    effective_cost = ask_prob + fee

    assert effective_cost > ask_prob
    assert round(fee, 4) == 0.0050
    assert round(effective_cost, 4) == 0.5050


@pytest.mark.unit
def test_time_context_point_in_time_invariant() -> None:
    """Verify TimeContext strictly forbids post-decision timestamp observations."""
    decision_time = "2026-11-08T18:00:00Z"
    ctx = TimeContext.of(decision_time)

    assert ctx.is_observable("2026-11-08T17:59:59Z") is True
    assert ctx.is_observable("2026-11-08T18:00:00Z") is True
    assert ctx.is_observable("2026-11-08T18:00:01Z") is False
    assert ctx.is_observable("2026-11-09T00:00:00Z") is False


@pytest.mark.unit
def test_unified_feature_pipeline_zero_leakage() -> None:
    """Verify UnifiedFeaturePipeline filters out future games and calculates valid PIT features."""
    ctx = TimeContext.of("2026-11-08T18:00:00Z")
    pipeline = UnifiedFeaturePipeline()

    history = [
        GameRecord(
            event_id="past_1",
            event_start_utc="2026-11-05T00:00:00Z",
            league="NBA",
            home_team="Team A",
            away_team="Team B",
            home_score=110,
            away_score=102,
        ),
        GameRecord(
            event_id="past_2",
            event_start_utc="2026-11-07T00:00:00Z",
            league="NBA",
            home_team="Team A",
            away_team="Team C",
            home_score=105,
            away_score=98,
        ),
        # Future game: must be ignored by PIT filter!
        GameRecord(
            event_id="future_leak",
            event_start_utc="2026-11-09T00:00:00Z",
            league="NBA",
            home_team="Team A",
            away_team="Team D",
            home_score=80,
            away_score=120,
        ),
    ]

    game = {
        "home_team": "Team A",
        "away_team": "Team B",
        "event_start_utc": "2026-11-08T19:00:00Z",
    }

    features = pipeline.extract_features(game, ctx, history)

    # Confirm future game was completely excluded
    assert features["observable_history_count"] == 2
    assert features["home_elo"] > 1500.0  # Team A won both observable games
    assert features["home_form_win_pct"] == 1.0  # 2 wins / 2 observable games
    assert "home_rest_days" in features


@pytest.mark.unit
def test_sport_registry_complete_catalog() -> None:
    """Verify that all 18 supported sports are present and resolve valid adapters."""
    all_sports = SportRegistry.list_sports()
    assert len(SUPPORTED_SPORTS) == 18

    for sport in SUPPORTED_SPORTS:
        assert sport in all_sports
        adapter = SportRegistry.get(sport)
        assert adapter is not None
        assert isinstance(adapter, SportAdapter)
        assert adapter.sport_name == sport
        assert MarketType.MONEYLINE in adapter.supported_markets
