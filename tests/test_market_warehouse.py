"""Unit and PIT regression tests for MarketQuoteWarehouse."""

from __future__ import annotations

from pathlib import Path

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import MarketQuote, MarketType


def test_market_warehouse_crud_and_pit_filtering(tmp_path: Path) -> None:
    db_path = tmp_path / "test_market_quotes.db"
    warehouse = MarketQuoteWarehouse(db_path=db_path)

    try:
        # 1. Insert historical quotes
        q1 = MarketQuote(
            event_id="mlb_20260828_nyy_bos",
            sport="mlb",
            market_type=MarketType.MONEYLINE.value,
            selection="New York Yankees",
            source="polymarket",
            observed_at_utc="2026-08-28T12:00:00Z",
            best_bid=0.51,
            best_ask=0.53,
            raw_implied_probability=0.52,
            no_vig_probability=0.52,
            depth_liquidity_usd=15000.0,
        )
        q2 = MarketQuote(
            event_id="mlb_20260828_nyy_bos",
            sport="mlb",
            market_type=MarketType.MONEYLINE.value,
            selection="New York Yankees",
            source="polymarket",
            observed_at_utc="2026-08-28T16:00:00Z",
            best_bid=0.54,
            best_ask=0.56,
            raw_implied_probability=0.55,
            no_vig_probability=0.55,
            depth_liquidity_usd=25000.0,
        )
        q3 = MarketQuote(
            event_id="mlb_20260828_nyy_bos",
            sport="mlb",
            market_type=MarketType.MONEYLINE.value,
            selection="Boston Red Sox",
            source="polymarket",
            observed_at_utc="2026-08-28T16:00:00Z",
            best_bid=0.44,
            best_ask=0.46,
            raw_implied_probability=0.45,
            no_vig_probability=0.45,
            depth_liquidity_usd=20000.0,
        )

        row_id = warehouse.record_quote(q1)
        assert row_id > 0
        batch_count = warehouse.record_quotes_batch([q2, q3])
        assert batch_count == 2
        assert warehouse.count_quotes() == 3
        assert warehouse.count_quotes(sport="mlb") == 3
        assert warehouse.count_quotes(sport="nba") == 0

        # 2. Test Point-In-Time (PIT) Quote Querying
        # Querying as of 14:00 must return q1 (0.52 implied), not q2 (0.55 implied)
        pit_quote = warehouse.get_latest_quote(
            "mlb_20260828_nyy_bos",
            MarketType.MONEYLINE.value,
            "New York Yankees",
            as_of_utc="2026-08-28T14:00:00Z",
        )
        assert pit_quote is not None
        assert pit_quote.observed_at_utc == "2026-08-28T12:00:00Z"
        assert pit_quote.best_ask == 0.53

        # Querying as of 18:00 returns q2 (most recent)
        latest_quote = warehouse.get_latest_quote(
            "mlb_20260828_nyy_bos",
            MarketType.MONEYLINE.value,
            "New York Yankees",
            as_of_utc="2026-08-28T18:00:00Z",
        )
        assert latest_quote is not None
        assert latest_quote.observed_at_utc == "2026-08-28T16:00:00Z"
        assert latest_quote.best_ask == 0.56

        # Querying prior to 12:00 returns None (no leak of future quotes)
        pre_quote = warehouse.get_latest_quote(
            "mlb_20260828_nyy_bos",
            MarketType.MONEYLINE.value,
            "New York Yankees",
            as_of_utc="2026-08-28T10:00:00Z",
        )
        assert pre_quote is None

        # 3. Test get_quotes_for_event
        all_event_quotes = warehouse.get_quotes_for_event("mlb_20260828_nyy_bos")
        assert len(all_event_quotes) == 3
    finally:
        warehouse.close()
