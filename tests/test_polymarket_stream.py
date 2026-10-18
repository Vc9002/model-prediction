"""Micro-test for Polymarket Order Book Streamer and Stage 2 Valuation Bridge."""

from __future__ import annotations

import tempfile
from pathlib import Path

from model_prediction.polymarket_stream import PolymarketBookStreamer
from model_prediction.two_stage import (
    Stage1CacheStore,
    Stage1Probability,
    Stage2ValuationEngine,
    Stage2ValuationTicket,
)


def test_polymarket_stream_processes_book_and_triggers_ticket():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_file = Path(tmp_dir) / "stage1.json"
        store = Stage1CacheStore(cache_file=cache_file)

        prob = Stage1Probability(
            event_id="nfl_eagles_cowboys_2026",
            sport="NFL",
            game_date="2026-10-21",
            home_team="Cowboys",
            away_team="Eagles",
            market_type="moneyline",
            selection="home",
            true_prob=0.60,
            model_id="nfl-elo-trend-lr-v4",
        )
        store.put(prob)

        qualified_tickets: list[Stage2ValuationTicket] = []

        def callback(t: Stage2ValuationTicket) -> None:
            qualified_tickets.append(t)

        streamer = PolymarketBookStreamer(
            stage1_store=store,
            valuation_engine=Stage2ValuationEngine(min_edge=0.02, max_edge=0.08),
            on_qualified_ticket=callback,
        )

        slug = "nfl-cowboys-ml-2026"
        streamer.register_market(
            market_slug=slug,
            event_id="nfl_eagles_cowboys_2026",
            market_type="moneyline",
            selection="home",
        )

        # Incoming tick: best ask is 0.55 (P_true 0.60 -> edge +0.05, qualified)
        ticket = streamer.process_book_update(market_slug=slug, best_ask=0.55, best_bid=0.52)
        assert ticket is not None
        assert ticket.qualified is True
        assert len(qualified_tickets) == 1
        assert qualified_tickets[0].net_edge == 0.05
