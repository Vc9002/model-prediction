"""Polymarket US Binary Contract Edge Gate & Fractional Kelly Sizing Engine.

Specifically designed for Polymarket Central Limit Order Book (CLOB) binary contracts:
1. Binary Contract Expected Value:
   - For YES shares bought at ask A (0 < A < 1):
     EV_unit = (P_model - A) / A
     Edge_taker = P_model - A
   - For NO shares bought against bid B (0 < B < 1) (i.e. buying NO at 1 - B):
     EV_unit = (B - P_model) / (1 - B)
     Edge_taker = B - P_model
2. Closed-Form Binary Kelly Criterion:
   - f*_yes = (P_model - A) / (1 - A)
   - f*_no = (B - P_model) / B
   Fractional Kelly: f = kelly_multiplier * f* (default 0.25x Quarter-Kelly).
3. Risk & Execution Gates:
   - Minimum Executable Edge Filter: edge >= min_edge_threshold (default 2.5%).
   - Maximum Position Cap: max_bankroll_fraction (default 3.0%).
   - Maker vs Taker pricing support (posting at bid + 0.01 vs crossing spread).
4. Polymarket Dead Heat / 0.50 Tie Settlement Payoff:
   E[Payout] = P(Win) + 0.5 * P(Tie)
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MIN_EDGE = 0.025  # 2.5% minimum edge required to trigger an order
DEFAULT_KELLY_FRACTION = 0.25  # Quarter-Kelly default
DEFAULT_MAX_POSITION_PCT = 0.03  # Max 3.0% bankroll per contract
MIN_CONTRACT_PRICE = 0.01
MAX_CONTRACT_PRICE = 0.99


@dataclass(slots=True)
class PolymarketQuote:
    """Executable Best Bid and Offer (BBO) quote from Polymarket CLOB."""

    market_id: str
    question: str
    best_bid: float  # e.g. 0.58
    best_ask: float  # e.g. 0.60
    spread: float  # best_ask - best_bid
    last_traded_price: float | None = None
    home_or_player_a: str = ""
    away_or_player_b: str = ""
    event_start_utc: str = ""
    observed_at_utc: str = ""


@dataclass(slots=True)
class PolymarketOrderDecision:
    """Order ticket decision for a Polymarket binary contract."""

    market_id: str
    side: str  # "BUY_YES", "BUY_NO", or "NO_ORDER"
    is_maker: bool
    order_price: float  # Contract price (0.01 - 0.99)
    model_probability: float
    market_price: float
    edge: float
    expected_value_pct: float
    kelly_fraction_full: float
    kelly_fraction_recommended: float
    stake_units: float  # Dollars/units to stake based on bankroll
    reason: str
    question: str = ""
    target_selection: str = ""  # The explicit team/entity being bet (e.g. "Minnesota Lynx")
    target_side: str = ""  # "YES" or "NO"
    home_team: str = ""
    away_team: str = ""
    selection_label: str = ""  # e.g. "Minnesota Lynx (BUY YES)"
    event_start_utc: str = ""
    observed_at_utc: str = ""


class PolymarketKellyEngine:
    """Calculates optimal Kelly staking and enforces executable edge gates for Polymarket US."""

    def __init__(
        self,
        bankroll: float = 1000.0,
        min_edge: float = DEFAULT_MIN_EDGE,
        kelly_fraction: float = DEFAULT_KELLY_FRACTION,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    ) -> None:
        self.bankroll = max(1.0, float(bankroll))
        self.min_edge = float(min_edge)
        self.kelly_fraction = float(kelly_fraction)
        self.max_position_pct = float(max_position_pct)

    def evaluate_binary_opportunity(
        self,
        quote: PolymarketQuote,
        p_model: float,
        p_tie: float = 0.0,
        prefer_maker: bool = False,
    ) -> PolymarketOrderDecision:
        """Evaluate order opportunity for YES or NO shares on Polymarket US."""
        # Expected payout under Polymarket 0.50 tie rule
        p_effective = max(MIN_CONTRACT_PRICE, min(MAX_CONTRACT_PRICE, p_model + 0.5 * p_tie))

        bid = max(MIN_CONTRACT_PRICE, min(MAX_CONTRACT_PRICE, quote.best_bid))
        ask = max(MIN_CONTRACT_PRICE, min(MAX_CONTRACT_PRICE, quote.best_ask))

        home = quote.home_or_player_a
        away = quote.away_or_player_b

        # 1. Evaluate YES Opportunity
        # Taker buys at ask; Maker posts at bid + 0.01 (if inside spread)
        price_yes = min(ask, bid + 0.01) if prefer_maker and (ask - bid > 0.01) else ask
        edge_yes = p_effective - price_yes
        ev_yes = (edge_yes / price_yes) * 100.0 if price_yes > 0 else 0.0

        # 2. Evaluate NO Opportunity
        # Buying NO contract: Taker cost is (1.0 - bid); Maker cost is 1.0 - (ask - 0.01)
        p_effective_no = 1.0 - p_effective
        cost_no_taker = 1.0 - bid
        cost_no_maker = 1.0 - max(bid, ask - 0.01)
        price_no = cost_no_maker if prefer_maker and (ask - bid > 0.01) else cost_no_taker
        edge_no = p_effective_no - price_no
        ev_no = (edge_no / price_no) * 100.0 if price_no > 0 else 0.0

        # Determine if YES, NO, or NO_ORDER qualifies
        if edge_yes >= self.min_edge and edge_yes >= edge_no:
            # Full Kelly for YES: (P - Price) / (1 - Price)
            full_k = (p_effective - price_yes) / (1.0 - price_yes) if price_yes < 1.0 else 0.0
            rec_k = min(self.max_position_pct, max(0.0, full_k * self.kelly_fraction))
            stake = round(self.bankroll * rec_k, 2)
            is_maker = prefer_maker and (price_yes < ask)
            target = home or "YES"
            sel_lbl = f"{home} (BUY YES)" if home else "BUY YES"

            return PolymarketOrderDecision(
                market_id=quote.market_id,
                side="BUY_YES",
                is_maker=is_maker,
                order_price=round(price_yes, 4),
                model_probability=round(p_effective, 4),
                market_price=round(ask, 4),
                edge=round(edge_yes, 4),
                expected_value_pct=round(ev_yes, 2),
                kelly_fraction_full=round(full_k, 4),
                kelly_fraction_recommended=round(rec_k, 4),
                stake_units=stake,
                reason=f"BUY YES on {target}: Edge +{edge_yes:.1%} (Model {p_effective:.1%} vs Ask {price_yes * 100:.1f}¢, EV +{ev_yes:.1f}%)",
                question=quote.question,
                target_selection=target,
                target_side="YES",
                home_team=home,
                away_team=away,
                selection_label=sel_lbl,
                event_start_utc=quote.event_start_utc,
                observed_at_utc=quote.observed_at_utc,
            )

        elif edge_no >= self.min_edge:
            # Full Kelly for NO: (P_no - Price_no) / (1 - Price_no)
            full_k = (p_effective_no - price_no) / (1.0 - price_no) if price_no < 1.0 else 0.0
            rec_k = min(self.max_position_pct, max(0.0, full_k * self.kelly_fraction))
            stake = round(self.bankroll * rec_k, 2)
            is_maker = prefer_maker and (price_no < cost_no_taker)
            target = away or "NO"
            sel_lbl = f"{away} (BUY NO)" if away else "BUY NO"

            return PolymarketOrderDecision(
                market_id=quote.market_id,
                side="BUY_NO",
                is_maker=is_maker,
                order_price=round(price_no, 4),
                model_probability=round(p_effective_no, 4),
                market_price=round(cost_no_taker, 4),
                edge=round(edge_no, 4),
                expected_value_pct=round(ev_no, 2),
                kelly_fraction_full=round(full_k, 4),
                kelly_fraction_recommended=round(rec_k, 4),
                stake_units=stake,
                reason=f"BUY NO on {target}: Edge +{edge_no:.1%} (Model {p_effective_no:.1%} vs Ask {price_no * 100:.1f}¢, EV +{ev_no:.1f}%)",
                question=quote.question,
                target_selection=target,
                target_side="NO",
                home_team=home,
                away_team=away,
                selection_label=sel_lbl,
                event_start_utc=quote.event_start_utc,
                observed_at_utc=quote.observed_at_utc,
            )

        else:
            best_edge = max(edge_yes, edge_no)
            side_candidate = (
                f"YES ({home})"
                if home and edge_yes >= edge_no
                else ("NO (" + away + ")" if away else ("YES" if edge_yes >= edge_no else "NO"))
            )
            return PolymarketOrderDecision(
                market_id=quote.market_id,
                side="NO_ORDER",
                is_maker=False,
                order_price=0.0,
                model_probability=round(p_effective, 4),
                market_price=round(ask, 4),
                edge=round(best_edge, 4),
                expected_value_pct=round(max(ev_yes, ev_no), 2),
                kelly_fraction_full=0.0,
                kelly_fraction_recommended=0.0,
                stake_units=0.0,
                reason=f"Edge +{best_edge:.1%} on {side_candidate} below min threshold {self.min_edge:.1%}",
                question=quote.question,
                target_selection="",
                target_side="",
                home_team=home,
                away_team=away,
                selection_label="",
                event_start_utc=quote.event_start_utc,
                observed_at_utc=quote.observed_at_utc,
            )
