"""Tests for deterministic Point-in-Time MarketStateVectorBuilder (Phase F1).

Verifies:
1. Deterministic source hashing and reproducibility.
2. Robust consensus calculation (sharp vs soft, median/mean, line SD).
3. Correct line velocity calculation (open -> now, 60m, 30m).
4. Standardized margin conventions: MarketImpliedHomeMargin = -HomeSpreadLine.
5. Strict PIT isolation (quotes after as_of_utc are ignored).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import MarketQuote
from model_prediction.features.market_state import MarketStateVectorBuilder


@pytest.fixture
def temp_warehouse(tmp_path: Path) -> MarketQuoteWarehouse:
    db_path = tmp_path / "test_market_quotes.db"
    return MarketQuoteWarehouse(db_path=db_path)


def test_market_state_vector_builder_deterministic_consensus_and_pit(
    temp_warehouse: MarketQuoteWarehouse,
) -> None:
    builder = MarketStateVectorBuilder(temp_warehouse, stale_cutoff_hours=24.0)

    # Ingest historical quotes for MLB game total:
    # Open: 8.5 at 12:00
    # Move to 9.0 at 18:00 (T-60m before 19:00 decision)
    # Pinnacle (sharp): 9.0 -110 at 18:30 (T-30m)
    # DraftKings (soft): 8.5 -115 at 18:30
    # Post-game quote at 22:00: 9.5 (must NOT be seen at 19:00 decision time)
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="pinnacle",
            observed_at_utc="2026-06-01T12:00:00Z",
            line=8.5,
            no_vig_probability=0.50,
            best_bid=0.48,
            best_ask=0.52,
        )
    )
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="draftkings",
            observed_at_utc="2026-06-01T12:00:00Z",
            line=8.5,
            no_vig_probability=0.50,
            best_bid=0.48,
            best_ask=0.52,
        )
    )
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="pinnacle",
            observed_at_utc="2026-06-01T18:30:00Z",
            line=9.0,
            no_vig_probability=0.52,
            best_bid=0.50,
            best_ask=0.54,
        )
    )
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="draftkings",
            observed_at_utc="2026-06-01T18:30:00Z",
            line=8.5,
            no_vig_probability=0.49,
            best_bid=0.47,
            best_ask=0.51,
        )
    )
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="circabet",
            observed_at_utc="2026-06-01T18:45:00Z",
            line=9.0,
            no_vig_probability=0.53,
            best_bid=0.51,
            best_ask=0.55,
        )
    )
    # Future quote (after decision time)
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="mlb_nyy_bos_20260601",
            sport="mlb",
            market_type="total",
            selection="Over",
            source="pinnacle",
            observed_at_utc="2026-06-01T22:00:00Z",
            line=9.5,
            no_vig_probability=0.60,
        )
    )

    # Evaluate at decision time T-10m: 18:50:00Z
    vec1 = builder.build_state_vector(
        event_id="mlb_nyy_bos_20260601",
        market_type="total",
        as_of_utc="2026-06-01T18:50:00Z",
        primary_selection="Over",
    )
    vec2 = builder.build_state_vector(
        event_id="mlb_nyy_bos_20260601",
        market_type="total",
        as_of_utc="2026-06-01T18:50:00Z",
        primary_selection="Over",
    )

    # 1. Determinism and identical source hash
    assert vec1.source_hash == vec2.source_hash
    assert vec1.source_hash != ""

    # 2. PIT correctness: ignores 22:00 quote (line 9.5)
    assert vec1.book_count == 3  # pinnacle, draftkings, circabet
    assert vec1.consensus_line == 9.0  # median(9.0, 8.5, 9.0) = 9.0
    assert vec1.mean_line == round((9.0 + 8.5 + 9.0) / 3.0, 3)

    # 3. Sharp vs Soft segregation
    assert vec1.sharp_consensus_line == 9.0  # Pinnacle is sharp
    assert vec1.soft_consensus_line == 8.5  # DraftKings is soft
    assert vec1.sharp_soft_gap == 0.5

    # 4. Price dispersion and probability no-vig consensus
    assert vec1.consensus_price_no_vig == 0.52
    assert vec1.consensus_counter_price_no_vig == 0.48  # 1 - 0.52
    assert vec1.sharp_price_prob == 0.52
    assert vec1.soft_price_prob == 0.49
    assert vec1.sharp_soft_price_gap == 0.03
    assert vec1.price_prob_range == pytest.approx(0.04, abs=1e-3)

    # 5. Movement velocity
    assert vec1.open_line == 8.5
    assert vec1.line_move_open_to_now == 0.5  # 9.0 - 8.5 = +0.5 total move


def test_market_state_vector_spread_home_margin_sign_convention(
    temp_warehouse: MarketQuoteWarehouse,
) -> None:
    """Home spread line must convert unambiguously to MarketImpliedHomeMargin."""
    builder = MarketStateVectorBuilder(temp_warehouse)

    # Chiefs favored by 3.5 at home (-3.5 spread line)
    temp_warehouse.record_quote(
        MarketQuote(
            event_id="nfl_kc_car_20260910",
            sport="nfl",
            market_type="spread",
            selection="Kansas City Chiefs",
            source="pinnacle",
            observed_at_utc="2026-09-10T15:00:00Z",
            line=-3.5,
            no_vig_probability=0.50,
        )
    )

    vec = builder.build_state_vector(
        event_id="nfl_kc_car_20260910",
        market_type="spread",
        as_of_utc="2026-09-10T15:30:00Z",
        primary_selection="Kansas City Chiefs",
    )

    # Assert sign convention: Home Line = -3.5 -> Market Implied Margin = +3.5
    assert vec.consensus_line == -3.5
    assert vec.market_implied_home_margin == 3.5
