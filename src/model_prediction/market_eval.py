"""Market-relative evaluation metrics — the plan's P0 foundation.

Judge a model against the market, not against 0.5: for every bet-able
decision row, compute logloss/Brier deltas vs the no-vig market
probability, calibration for both sides, and the economic battery (CLV
rate, ROI at executable prices, profit factor, max drawdown) with a
date-clustered bootstrap CI on ROI.

Design rules:

- Pure functions over ``MarketEvalRow`` sequences; no I/O, no imports of
  heavy pipeline modules, so research scripts and the evaluator can both
  call it.
- One row = one bet on one side of one market, with the outcome for that
  side. Side selection (model edge vs market) is the *caller's* policy —
  see ``decide_sides``; the evaluator only measures.
- Reuses ``calibration.calibration_metrics`` for the calibration block
  rather than re-deriving Brier/logloss buckets a second time.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .calibration import calibration_metrics

# One day = one resample unit for the date-clustered bootstrap. Games on
# the same date share league-level shocks (weather regime, pitcher slate
# quality), so resampling rows independently would understate the CI.
_BOOTSTRAP_RESAMPLES = 2000
_BOOTSTRAP_SEED = 42


@dataclass(frozen=True)
class MarketEvalRow:
    """One settled bet-able decision on one side of one market."""

    event_id: str
    decision_utc: str  # YYYY-MM-DD (cluster unit) or full timestamp
    market_type: str  # moneyline | spread | total | ...
    line: float | None  # contract line (None for moneyline)
    model_prob: float  # model's probability the bet side wins
    market_prob: float  # no-vig market probability the bet side wins
    bet_price: float  # executable price actually paid (ask, 0-1)
    outcome: int  # 1 if the bet side won, else 0


def no_vig(long_mid: float, short_mid: float) -> float:
    """Two-way de-vig of Polymarket-style midpoint prices.

    ``long_mid + short_mid`` exceeds 1 by the overround; the no-vig fair
    probability of the long side is its share of the summed midpoints.
    """
    total = long_mid + short_mid
    if total <= 0:
        raise ValueError("midpoints must sum positive")
    p = long_mid / total
    return min(1 - 1e-12, max(1e-12, p))


def decide_sides(
    rows_by_event: dict[str, list[MarketEvalRow]],
    *,
    min_edge: float = 0.0,
) -> list[MarketEvalRow]:
    """Pick the side of each event with model edge ≥ ``min_edge``.

    ``rows_by_event`` maps event_id to its two side rows (same line,
    complementary probabilities). A row with no measurable edge is
    skipped — no bet. The returned list is the evaluator's input.
    """
    chosen: list[MarketEvalRow] = []
    for rows in rows_by_event.values():
        if not rows:
            continue
        best = max(rows, key=lambda r: r.model_prob - r.market_prob)
        if best.model_prob - best.market_prob >= min_edge:
            chosen.append(best)
    return chosen


def _edge_bucket(edge: float) -> str:
    """Coarse edge buckets for the stability section (fixed, not tuned)."""
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
    """Largest peak-to-trough decline in cumulative P&L (≥0, as a loss)."""
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
) -> dict:
    """Full market-relative report over settled rows.

    Returns ``{"status": "insufficient_sample", "sample_size": n}`` below
    a minimal bet count, mirroring ``calibration_metrics``'s convention.
    """
    rows = list(rows)
    if len(rows) < 30:
        return {"status": "insufficient_sample", "sample_size": len(rows)}

    model_probs = [r.model_prob for r in rows]
    market_probs = [r.market_prob for r in rows]
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
    edges = [r.model_prob - r.market_prob for r in rows]
    clv = [r.market_prob - r.bet_price for r in rows]  # closing-vs-paid
    pnls = [(r.outcome - r.bet_price) / r.bet_price for r in rows]
    stake_total = float(len(rows))
    roi = sum(pnls) / stake_total
    wins = [p for p, r in zip(pnls, rows, strict=True) if r.outcome == 1]
    losses = [p for p, r in zip(pnls, rows, strict=True) if r.outcome == 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Date-clustered bootstrap CI on ROI: resample whole dates, so the
    # CI reflects league-level day shocks rather than row independence.
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

    # Stability slices by edge bucket (the plan's price/edge breakdown).
    buckets: dict[str, dict] = {}
    for r, p, e in zip(rows, pnls, edges, strict=True):
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
            "roi": roi,
            "roi_ci_95": roi_ci,
            "profit_factor": profit_factor,
            "max_drawdown_units": _max_drawdown(pnls),
            "clv_rate": sum(1 for c in clv if c > 0) / len(clv),
            "mean_clv": sum(clv) / len(clv),
            "mean_edge": sum(edges) / len(edges),
            "edge_buckets": edge_buckets,
        },
    }
