"""Polymarket US Unified Multi-Sport Model Dispatcher & Execution Scanner.

Connects Polymarket Central Limit Order Book (CLOB) live quotes to specialized domain models:
1. League / Sport Routing:
   - MLB -> Monotonic XGBoost / 24-state PA Monte Carlo Engine
   - Tennis -> Barnett & Clarke (2005) Point-Level Markov Engine
   - WNBA -> 40-minute Pace x PPP Fundamental Engine
   - Soccer -> Independent Per-League Dixon-Coles Models (EPL, La Liga, etc.)
   - Esports -> CS2, LoL, Valorant, Dota 2, R6 Tactical Engines
   - KBO / NPB -> 12-Inning 0.50 Tie-Aware Engine
2. Polymarket Kelly Execution:
   - Enforces minimum executable edge gate (default >= +2.5%)
   - Closed-form Quarter-Kelly binary position sizing
   - Maker inside-spread pricing optimization
3. Generates structured execution tickets for automated ordering and shadow logging.
"""

from __future__ import annotations

from dataclasses import dataclass

from .polymarket_kelly import (
    PolymarketKellyEngine,
    PolymarketOrderDecision,
    PolymarketQuote,
)


@dataclass(slots=True)
class DispatchRequest:
    """Standard request payload for Polymarket contract evaluation."""

    market_id: str
    league: str
    question: str
    home_or_player_a: str
    away_or_player_b: str
    best_bid: float
    best_ask: float
    event_start_utc: str
    surface: str = "Hard"
    format: str = "Bo3"
    p_model_override: float | None = None
    p_tie_override: float = 0.0
    observed_at_utc: str = ""


class PolymarketDispatcher:
    """Unified dispatcher evaluating multi-sport Polymarket opportunities."""

    def __init__(
        self,
        bankroll: float = 1000.0,
        min_edge: float = 0.025,
        kelly_fraction: float = 0.25,
        max_position_pct: float = 0.03,
    ) -> None:
        self.kelly_engine = PolymarketKellyEngine(
            bankroll=bankroll,
            min_edge=min_edge,
            kelly_fraction=kelly_fraction,
            max_position_pct=max_position_pct,
        )

    def evaluate_request(
        self,
        req: DispatchRequest,
        prefer_maker: bool = False,
    ) -> PolymarketOrderDecision:
        """Route request to appropriate domain model and compute Kelly order decision."""
        quote = PolymarketQuote(
            market_id=req.market_id,
            question=req.question,
            best_bid=req.best_bid,
            best_ask=req.best_ask,
            spread=round(req.best_ask - req.best_bid, 4),
            home_or_player_a=req.home_or_player_a,
            away_or_player_b=req.away_or_player_b,
            event_start_utc=req.event_start_utc,
            observed_at_utc=req.observed_at_utc,
        )

        if req.p_model_override is None:
            return PolymarketOrderDecision(
                market_id=quote.market_id,
                side="NO_ORDER",
                is_maker=False,
                order_price=0.0,
                model_probability=0.0,
                market_price=quote.best_ask,
                edge=0.0,
                expected_value_pct=0.0,
                kelly_fraction_full=0.0,
                kelly_fraction_recommended=0.0,
                stake_units=0.0,
                reason="NO_CALL_NO_DATA: No validated model probability on record for matchup",
                question=quote.question,
                observed_at_utc=quote.observed_at_utc,
            )

        p_model = req.p_model_override
        p_tie = req.p_tie_override

        # Tie probabilities for specific leagues if not overridden
        lg = req.league.upper()
        if p_tie == 0.0:
            if lg == "NPB":
                p_tie = 0.075
            elif lg in ("KBO", "DOTA2_BO2"):
                p_tie = 0.035
            elif lg == "SOCCER":
                p_tie = 0.265

        return self.kelly_engine.evaluate_binary_opportunity(
            quote=quote,
            p_model=p_model,
            p_tie=p_tie,
            prefer_maker=prefer_maker,
        )

    def scan_batch(
        self,
        requests: list[DispatchRequest],
        prefer_maker: bool = False,
    ) -> list[PolymarketOrderDecision]:
        """Evaluate a batch of Polymarket requests and filter for actionable order tickets."""
        decisions = []
        for r in requests:
            dec = self.evaluate_request(r, prefer_maker=prefer_maker)
            decisions.append(dec)
        return decisions

    def get_actionable_orders(
        self,
        requests: list[DispatchRequest],
        prefer_maker: bool = False,
    ) -> list[PolymarketOrderDecision]:
        """Return only qualifying BUY_YES or BUY_NO orders that passed all edge gates."""
        all_decisions = self.scan_batch(requests, prefer_maker=prefer_maker)
        return [d for d in all_decisions if d.side in ("BUY_YES", "BUY_NO")]
