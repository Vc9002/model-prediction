"""Deterministic Point-in-Time (PIT) MarketStateVector Builder (Phase F1).

Constructs immutable, reproducible market state vectors from raw sportsbook and
exchange quotes stored in MarketQuoteWarehouse.

Features:
- Robust multi-book line consensus (median, trimmed mean, line dispersion).
- Multi-book price dispersion around the same line (no-vig over/under/home/away probabilities, SD, range).
- Sharp vs. soft book segregation and disagreement gap (both line gap and price probability gap).
- Multi-horizon line velocity (open->now, 60m, 30m).
- Standardized sign conventions:
    Margin = HomeScore - AwayScore
    MarketImpliedHomeMargin = -HomeSpreadLine  (e.g., Home -3.5 -> +3.5 margin)
- Cryptographic source hashing over all input quotes for auditability.
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..data_sources.market_warehouse import MarketQuoteWarehouse
from ..domain import MarketQuote, parse_utc

# Known sharp vs. retail/soft bookmaker classifications
SHARP_SPORTSBOOKS = {"pinnacle", "circa", "bookmaker", "betcris", "polymarket_us", "kalshi"}
SOFT_SPORTSBOOKS = {"draftkings", "fanduel", "betmgm", "caesars", "pointsbet", "espn_bet"}


@dataclass(frozen=True, slots=True)
class MarketStateVector:
    """Point-in-time multi-book market state."""

    event_id: str
    as_of_utc: str
    market_type: str  # total | spread | moneyline
    consensus_line: float | None
    mean_line: float | None
    median_line: float | None
    line_sd: float | None
    consensus_price_no_vig: float | None  # Primary side (e.g. Over for totals, Home for spreads/ML)
    consensus_counter_price_no_vig: (
        float | None
    )  # Secondary side (e.g. Under for totals, Away for spreads/ML)
    price_prob_sd: float | None
    price_prob_range: float | None
    book_count: int
    sharp_consensus_line: float | None
    soft_consensus_line: float | None
    sharp_soft_gap: float | None  # sharp - soft line gap
    sharp_price_prob: float | None
    soft_price_prob: float | None
    sharp_soft_price_gap: float | None  # sharp - soft probability gap
    open_line: float | None
    line_move_open_to_now: float | None
    line_move_60m: float | None
    line_move_30m: float | None
    quote_age_p50_seconds: float | None
    quote_age_max_seconds: float | None
    market_implied_home_margin: float | None  # Standardized home margin advantage
    source_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketStateVectorBuilder:
    """Extracts deterministic MarketStateVectors from warehouse quotes."""

    def __init__(
        self,
        warehouse: MarketQuoteWarehouse,
        stale_cutoff_hours: float = 24.0,
        min_books_for_consensus: int = 2,
    ) -> None:
        self.warehouse = warehouse
        self.stale_cutoff_hours = stale_cutoff_hours
        self.min_books_for_consensus = min_books_for_consensus

    def build_state_vector(
        self,
        event_id: str,
        market_type: str,
        as_of_utc: str | datetime,
        primary_selection: str | None = None,
    ) -> MarketStateVector:
        """Construct deterministic PIT MarketStateVector at exact as_of timestamp."""
        if isinstance(as_of_utc, datetime):
            as_of_dt = as_of_utc.astimezone(UTC)
            as_of_str = as_of_dt.isoformat()
        else:
            as_of_dt = parse_utc(as_of_utc)
            as_of_str = as_of_utc

        # Retrieve all quotes strictly on or before as_of_utc
        quotes = self.warehouse.get_quotes_for_event(
            event_id=event_id,
            as_of_utc=as_of_str,
            market_type=market_type,
        )

        if not quotes:
            return self._empty_vector(event_id, market_type, as_of_str, hashlib.sha256(b"empty").hexdigest())

        # Compute deterministic source hash across sorted input quotes
        quote_payloads = [
            f"{q.source}:{q.selection}:{q.line}:{q.best_bid}:{q.best_ask}:{q.observed_at_utc}"
            for q in sorted(quotes, key=lambda x: (x.observed_at_utc, x.source, x.selection))
        ]
        source_hash = hashlib.sha256("\n".join(quote_payloads).encode()).hexdigest()

        # Group by source book to pick the latest observation per book before as_of_utc
        latest_by_source: dict[str, MarketQuote] = {}
        first_by_source: dict[str, MarketQuote] = {}
        for q in sorted(quotes, key=lambda x: x.observed_at_utc):
            if primary_selection and q.selection.lower() != primary_selection.lower():
                continue
            src_key = q.source.lower()
            if src_key not in first_by_source:
                first_by_source[src_key] = q
            latest_by_source[src_key] = q

        # Filter out quotes older than stale_cutoff_hours
        active_quotes: list[MarketQuote] = []
        ages_seconds: list[float] = []
        for q in latest_by_source.values():
            q_time = parse_utc(q.observed_at_utc)
            age = (as_of_dt - q_time).total_seconds()
            if 0 <= age <= self.stale_cutoff_hours * 3600.0:
                active_quotes.append(q)
                ages_seconds.append(age)

        book_count = len(active_quotes)
        if book_count == 0:
            return self._empty_vector(event_id, market_type, as_of_str, source_hash)

        # Lines and Prices
        lines = [q.line for q in active_quotes if q.line is not None]
        no_vig_probs = [q.no_vig_probability for q in active_quotes if q.no_vig_probability is not None]

        mean_line = statistics.mean(lines) if lines else None
        median_line = statistics.median(lines) if lines else None
        line_sd = statistics.stdev(lines) if len(lines) >= 2 else (0.0 if lines else None)
        consensus_line = median_line

        # Primary side and Counter side probability
        consensus_price_no_vig = statistics.median(no_vig_probs) if no_vig_probs else None
        consensus_counter_price = (
            (1.0 - consensus_price_no_vig) if consensus_price_no_vig is not None else None
        )

        # Price dispersion across books
        price_sd = (
            statistics.stdev(no_vig_probs) if len(no_vig_probs) >= 2 else (0.0 if no_vig_probs else None)
        )
        price_range = (max(no_vig_probs) - min(no_vig_probs)) if len(no_vig_probs) >= 2 else 0.0

        # Sharp vs Soft segregation for Lines and Prices
        sharp_quotes = [q for q in active_quotes if q.source.lower() in SHARP_SPORTSBOOKS]
        soft_quotes = [q for q in active_quotes if q.source.lower() in SOFT_SPORTSBOOKS]

        sharp_lines = [q.line for q in sharp_quotes if q.line is not None]
        soft_lines = [q.line for q in soft_quotes if q.line is not None]

        sharp_probs = [q.no_vig_probability for q in sharp_quotes if q.no_vig_probability is not None]
        soft_probs = [q.no_vig_probability for q in soft_quotes if q.no_vig_probability is not None]

        sharp_consensus_line = statistics.median(sharp_lines) if sharp_lines else None
        soft_consensus_line = statistics.median(soft_lines) if soft_lines else None
        sharp_soft_gap = (
            round(sharp_consensus_line - soft_consensus_line, 3)
            if (sharp_consensus_line is not None and soft_consensus_line is not None)
            else None
        )

        sharp_price_prob = statistics.median(sharp_probs) if sharp_probs else None
        soft_price_prob = statistics.median(soft_probs) if soft_probs else None
        sharp_soft_price_gap = (
            round(sharp_price_prob - soft_price_prob, 4)
            if (sharp_price_prob is not None and soft_price_prob is not None)
            else None
        )

        # Opening line and velocity calculation
        all_first_lines = [q.line for q in first_by_source.values() if q.line is not None]
        open_line = statistics.median(all_first_lines) if all_first_lines else None
        line_move_open = (
            round(consensus_line - open_line, 3)
            if (consensus_line is not None and open_line is not None)
            else None
        )

        # 60m and 30m line moves
        t_60m = as_of_dt - timedelta(minutes=60)
        t_30m = as_of_dt - timedelta(minutes=30)

        line_60m = self._get_median_line_as_of(quotes, t_60m, primary_selection)
        line_30m = self._get_median_line_as_of(quotes, t_30m, primary_selection)

        line_move_60m = (
            round(consensus_line - line_60m, 3)
            if (consensus_line is not None and line_60m is not None)
            else None
        )
        line_move_30m = (
            round(consensus_line - line_30m, 3)
            if (consensus_line is not None and line_30m is not None)
            else None
        )

        # Quote ages
        age_p50 = statistics.median(ages_seconds) if ages_seconds else None
        age_max = max(ages_seconds) if ages_seconds else None

        # Standardized Home Margin (for spread markets)
        market_implied_home_margin = None
        if market_type == "spread" and consensus_line is not None:
            # Home spread line convention: -3.5 means home favored by 3.5 pts -> implied margin = +3.5
            market_implied_home_margin = -consensus_line

        return MarketStateVector(
            event_id=event_id,
            as_of_utc=as_of_str,
            market_type=market_type,
            consensus_line=round(consensus_line, 3) if consensus_line is not None else None,
            mean_line=round(mean_line, 3) if mean_line is not None else None,
            median_line=round(median_line, 3) if median_line is not None else None,
            line_sd=round(line_sd, 3) if line_sd is not None else None,
            consensus_price_no_vig=round(consensus_price_no_vig, 4)
            if consensus_price_no_vig is not None
            else None,
            consensus_counter_price_no_vig=round(consensus_counter_price, 4)
            if consensus_counter_price is not None
            else None,
            price_prob_sd=round(price_sd, 4) if price_sd is not None else None,
            price_prob_range=round(price_range, 4) if price_range is not None else None,
            book_count=book_count,
            sharp_consensus_line=round(sharp_consensus_line, 3) if sharp_consensus_line is not None else None,
            soft_consensus_line=round(soft_consensus_line, 3) if soft_consensus_line is not None else None,
            sharp_soft_gap=sharp_soft_gap,
            sharp_price_prob=round(sharp_price_prob, 4) if sharp_price_prob is not None else None,
            soft_price_prob=round(soft_price_prob, 4) if soft_price_prob is not None else None,
            sharp_soft_price_gap=sharp_soft_price_gap,
            open_line=round(open_line, 3) if open_line is not None else None,
            line_move_open_to_now=line_move_open,
            line_move_60m=line_move_60m,
            line_move_30m=line_move_30m,
            quote_age_p50_seconds=round(age_p50, 1) if age_p50 is not None else None,
            quote_age_max_seconds=round(age_max, 1) if age_max is not None else None,
            market_implied_home_margin=round(market_implied_home_margin, 3)
            if market_implied_home_margin is not None
            else None,
            source_hash=source_hash,
        )

    def _empty_vector(
        self, event_id: str, market_type: str, as_of_str: str, source_hash: str
    ) -> MarketStateVector:
        return MarketStateVector(
            event_id=event_id,
            as_of_utc=as_of_str,
            market_type=market_type,
            consensus_line=None,
            mean_line=None,
            median_line=None,
            line_sd=None,
            consensus_price_no_vig=None,
            consensus_counter_price_no_vig=None,
            price_prob_sd=None,
            price_prob_range=None,
            book_count=0,
            sharp_consensus_line=None,
            soft_consensus_line=None,
            sharp_soft_gap=None,
            sharp_price_prob=None,
            soft_price_prob=None,
            sharp_soft_price_gap=None,
            open_line=None,
            line_move_open_to_now=None,
            line_move_60m=None,
            line_move_30m=None,
            quote_age_p50_seconds=None,
            quote_age_max_seconds=None,
            market_implied_home_margin=None,
            source_hash=source_hash,
        )

    def _get_median_line_as_of(
        self,
        quotes: Sequence[MarketQuote],
        as_of_dt: datetime,
        primary_selection: str | None = None,
    ) -> float | None:
        """Compute median line as of a past timestamp."""
        by_src: dict[str, float] = {}
        for q in quotes:
            if primary_selection and q.selection.lower() != primary_selection.lower():
                continue
            if q.line is None:
                continue
            q_time = parse_utc(q.observed_at_utc)
            if q_time <= as_of_dt:
                by_src[q.source.lower()] = q.line
        if not by_src:
            return None
        return statistics.median(by_src.values())
