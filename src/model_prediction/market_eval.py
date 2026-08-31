"""Market-relative evaluation metrics — the plan's P0 foundation.

Judge a model against the market, not against 0.5: for every bet-able
decision row, compute logloss/Brier deltas vs the no-vig market
probability, calibration for both sides, and the economic battery:
- Model Disagreement: model_prob - entry_fair_prob
- Executable Edge: model_prob - entry_ask
- Expected ROI after costs: (model_prob * (1 - fee) - entry_price) / entry_price
- True Closing-Line Value (CLV): closing_fair_prob - entry_price (when closing available)
- Market Move: closing_fair_prob - entry_fair_prob

Design rules:
- Pure functions over ``MarketEvalRow`` sequences; no I/O, no imports of
  heavy pipeline modules.
- One row = one bet on one side of one market, with the outcome for that
  side. Side selection is ranked by cost-aware executable ROI.
- Reuses ``calibration.calibration_metrics`` for the calibration block.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .calibration import calibration_metrics

_BOOTSTRAP_RESAMPLES = 2000
_BOOTSTRAP_SEED = 42


@dataclass
class MarketEvalRow:
    """One settled bet-able decision on one side of one market with full pregame & closing context."""

    event_id: str
    decision_utc: str  # YYYY-MM-DD (cluster unit) or full timestamp
    market_type: str  # moneyline | spread | total | nrfi | ...
    line: float | None  # contract line (None for moneyline/nrfi)
    model_prob: float  # model's estimated win probability for this side

    entry_fair_prob: float = 0.0  # decision-time no-vig fair market probability
    entry_bid: float | None = None  # decision-time best bid
    entry_ask: float = 0.0  # decision-time best ask (executable limit price)
    entry_price: float = 0.0  # executable price actually paid

    closing_fair_prob: float | None = None  # true closing no-vig fair probability
    closing_bid: float | None = None  # closing best bid
    closing_ask: float | None = None  # closing best ask

    fee_rate: float = 0.0  # platform fee fraction (e.g. 0.02 for 2%)
    outcome: int = 0  # 1 if the bet side won, else 0

    entry_quote_utc: str = ""
    closing_quote_utc: str | None = None

    # Backward compatibility aliases
    market_prob: float | None = None
    bet_price: float | None = None

    def __post_init__(self) -> None:
        if self.market_prob is not None and self.entry_fair_prob == 0.0:
            self.entry_fair_prob = float(self.market_prob)
        if self.entry_fair_prob > 0.0 and self.market_prob is None:
            self.market_prob = self.entry_fair_prob

        if self.bet_price is not None and self.entry_price == 0.0:
            self.entry_price = float(self.bet_price)
        if self.entry_price > 0.0 and self.bet_price is None:
            self.bet_price = self.entry_price

        if self.entry_ask == 0.0:
            self.entry_ask = self.entry_price if self.entry_price > 0.0 else self.entry_fair_prob
        if self.entry_price == 0.0:
            self.entry_price = self.entry_ask if self.entry_ask > 0.0 else self.entry_fair_prob

    @property
    def model_edge_vs_market(self) -> float:
        """Model disagreement with decision-time fair market price."""
        return self.model_prob - self.entry_fair_prob

    @property
    def execution_edge(self) -> float:
        """Executable edge against entry ask price."""
        return self.model_prob - self.entry_ask

    @property
    def expected_net_ev(self) -> float:
        """Expected net dollar payoff per unit share after fee deduction."""
        return self.model_prob * (1.0 - self.fee_rate) - self.entry_price

    @property
    def expected_roi(self) -> float:
        """Expected ROI on capital paid after fee deduction."""
        return (self.expected_net_ev / self.entry_price) if self.entry_price > 0.0 else -1.0

    @property
    def true_clv(self) -> float | None:
        """True CLV: closing fair probability minus entry purchase price."""
        if self.closing_fair_prob is not None:
            return self.closing_fair_prob - self.entry_price
        return None

    @property
    def market_move(self) -> float | None:
        """Market price movement: closing fair probability minus entry fair probability."""
        if self.closing_fair_prob is not None:
            return self.closing_fair_prob - self.entry_fair_prob
        return None


def no_vig(long_mid: float, short_mid: float) -> float:
    """Two-way de-vig of Polymarket-style midpoint prices."""
    total = long_mid + short_mid
    if total <= 0:
        raise ValueError("midpoints must sum positive")
    p = long_mid / total
    return min(1 - 1e-12, max(1e-12, p))


def expected_roi_after_costs(
    model_prob: float,
    executable_price: float,
    fee_rate: float = 0.0,
) -> float:
    """Expected ROI on capital paid after platform fee deduction."""
    if executable_price <= 0.0:
        return -1.0
    net_payout = model_prob * (1.0 - fee_rate)
    return (net_payout - executable_price) / executable_price


def decide_sides(
    rows_by_event: dict[str, list[MarketEvalRow]],
    *,
    min_edge: float = 0.0,
    min_roi: float | None = None,
) -> list[MarketEvalRow]:
    """Pick the optimal side of each event ranked by cost-aware executable ROI.

    Evaluates executable edge against the ask price and fees rather than
    raw theoretical midpoint disagreement.
    """
    chosen: list[MarketEvalRow] = []
    for rows in rows_by_event.values():
        if not rows:
            continue
        # Rank by net expected ROI on capital
        best = max(rows, key=lambda r: r.expected_roi)
        # Check minimum edge thresholds
        edge_pass = (best.model_edge_vs_market >= min_edge) or (best.execution_edge >= min_edge)
        roi_pass = True if min_roi is None else (best.expected_roi >= min_roi)
        if edge_pass and roi_pass and best.expected_net_ev > 0:
            chosen.append(best)
    return chosen


def _edge_bucket(edge: float) -> str:
    """Coarse edge buckets for stability section."""
    if edge < 0.0:
        return "<0"
    if edge < 0.02:
        return "0-2%"
    if edge < 0.05:
        return "2-5%"
    if edge < 0.10:
        return "5-10%"
    return ">=10%"


def _max_drawdown(pnls: Sequence[float]) -> float:
    """Largest peak-to-trough decline in cumulative P&L."""
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def market_relative_report(
    rows: Sequence[MarketEvalRow],
    *,
    n_bootstrap: int = _BOOTSTRAP_RESAMPLES,
    seed: int = _BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Full market-relative report over settled rows."""
    rows = list(rows)
    if len(rows) < 30:
        return {"status": "insufficient_sample", "sample_size": len(rows)}

    model_probs = [r.model_prob for r in rows]
    market_probs = [r.entry_fair_prob for r in rows]
    outcomes = [r.outcome for r in rows]

    # Predictive block: model vs market on the same settled sides.
    model_cal: dict[str, Any] = calibration_metrics(model_probs, outcomes)
    market_cal: dict[str, Any] = calibration_metrics(market_probs, outcomes)
    if model_cal.get("status") == "insufficient_sample":
        model_logloss = model_brier = None
        market_logloss = market_brier = None
        delta_logloss = delta_brier = None
    else:
        model_logloss = float(model_cal["log_loss"])
        market_logloss = float(market_cal["log_loss"])
        delta_logloss = model_logloss - market_logloss
        model_brier = float(model_cal["brier_score"])
        market_brier = float(market_cal["brier_score"])
        delta_brier = model_brier - market_brier

    # Economic block: unit stakes at the executable price actually paid.
    disagreements = [r.model_edge_vs_market for r in rows]
    executable_edges = [r.execution_edge for r in rows]
    expected_rois = [r.expected_roi for r in rows]

    # Realized net PnL: win pays (1 - fee - price) / price; loss pays -1.0
    pnls = [
        ((r.outcome * (1.0 - r.fee_rate) - r.entry_price) / r.entry_price) if r.entry_price > 0 else 0.0
        for r in rows
    ]
    stake_total = float(len(rows))
    realized_roi = sum(pnls) / stake_total if stake_total > 0 else 0.0
    wins = [p for p, r in zip(pnls, rows, strict=True) if r.outcome == 1]
    losses = [p for p, r in zip(pnls, rows, strict=True) if r.outcome == 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # True CLV and Market Movement analysis
    clv_rows = [r for r in rows if r.closing_fair_prob is not None]
    if clv_rows:
        true_clvs = [r.true_clv for r in clv_rows if r.true_clv is not None]
        market_moves = [r.market_move for r in clv_rows if r.market_move is not None]
        clv_info: dict[str, Any] = {
            "clv_available": True,
            "clv_sample_size": len(clv_rows),
            "clv_rate": sum(1 for c in true_clvs if c > 0) / len(true_clvs) if true_clvs else 0.0,
            "mean_clv": sum(true_clvs) / len(true_clvs) if true_clvs else 0.0,
            "mean_market_move": sum(market_moves) / len(market_moves) if market_moves else 0.0,
        }
    else:
        clv_info = {
            "clv_available": False,
            "clv_sample_size": 0,
            "clv_rate": None,
            "mean_clv": None,
            "mean_market_move": None,
            "reason": "closing_quotes_unavailable",
        }

    # Date-clustered bootstrap CI on ROI
    dates = sorted({r.decision_utc[:10] for r in rows})
    by_date: dict[str, list[float]] = {}
    for r, p in zip(rows, pnls, strict=True):
        by_date.setdefault(r.decision_utc[:10], []).append(p)
    rng = random.Random(seed)
    boot_rois: list[float] = []
    for _ in range(n_bootstrap):
        total = 0.0
        n_rows = 0
        for day in (rng.choice(dates) for _ in dates):
            ps = by_date[day]
            total += sum(ps)
            n_rows += len(ps)
        boot_rois.append(total / n_rows if n_rows else 0.0)
    boot_rois.sort()
    roi_ci = (boot_rois[25], boot_rois[-26]) if n_bootstrap >= 2000 else (None, None)

    # Stability slices by model disagreement bucket
    buckets: dict[str, dict[str, Any]] = {}
    for r, p, e in zip(rows, pnls, disagreements, strict=True):
        b = buckets.setdefault(_edge_bucket(e), {"n": 0, "roi": 0.0})
        b["n"] += 1
        b["roi"] += p
    edge_buckets = {k: {"n": v["n"], "roi": v["roi"] / v["n"]} for k, v in sorted(buckets.items())}

    return {
        "status": "ok",
        "n_bets": len(rows),
        "n_events": len({r.event_id for r in rows}),
        "predictive": {
            "model_logloss": model_logloss,
            "market_logloss": market_logloss,
            "delta_logloss": delta_logloss,
            "model_brier": model_brier,
            "market_brier": market_brier,
            "delta_brier": delta_brier,
        },
        "calibration": {"model": model_cal, "market": market_cal},
        "economic": {
            "roi": realized_roi,
            "roi_ci_95": roi_ci,
            "profit_factor": profit_factor,
            "max_drawdown_units": _max_drawdown(pnls),
            "mean_model_disagreement": sum(disagreements) / len(disagreements),
            "mean_executable_edge": sum(executable_edges) / len(executable_edges),
            "mean_expected_roi": sum(expected_rois) / len(expected_rois),
            "clv": clv_info,
            "clv_rate": clv_info["clv_rate"],  # backward-compatible top-level key
            "mean_clv": clv_info["mean_clv"],  # backward-compatible top-level key
            "mean_edge": sum(disagreements) / len(disagreements),  # backward-compatible top-level key
            "edge_buckets": edge_buckets,
        },
    }
