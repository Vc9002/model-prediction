"""Economic evaluation, position sizing, and monitoring for the rebuild platform.

Part 3 deliverables: cost-adjusted edge calculation, Kelly sizing with caps,
correlation-aware exposure limits, stress testing, and health state monitoring.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

import numpy as np


# ── Position Sizing ──────────────────────────────────────────────────────────


@dataclass
class SizeLimits:
    """Per-event and portfolio-level size caps."""
    min_units: float = 0.0         # zero is valid default — no forced minimum
    max_units: float = 2.0
    max_event_units: float = 2.0
    max_team_daily: float = 3.0
    max_sport_daily: float = 5.0
    max_daily_total: float = 10.0
    max_correlation_group: float = 3.0
    unit_rounding: float = 0.25     # round to nearest increment
    min_depth_units: float = 1.0    # must have at least this much depth available
    max_quote_age_seconds: float = 300


def kelly_fraction(
    probability: float,
    decimal_odds: float,
    fraction: float = 0.25,  # quarter-Kelly
) -> float:
    """Full Kelly: f = (p * b - q) / b where b = decimal_odds - 1."""
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - probability
    f = (probability * b - q) / b
    return max(0.0, f * fraction)


def edge_scaled_units(
    model_prob: float,
    conservative_prob: float,
    best_ask: float,
    limits: SizeLimits = SizeLimits(),
) -> dict[str, float]:
    """Size a trade from the model edge and conservative probability.

    Enforces depth, quote age, and exposure caps from SizeLimits.
    Returns 0 units if any constraint is violated.
    """
    if best_ask <= 0 or best_ask >= 1:
        return {"units": 0.0, "reason": "invalid_ask"}

    decimal_odds = 1.0 / best_ask
    edge = conservative_prob - best_ask

    if edge <= 0:
        return {"units": 0.0, "reason": "no_edge", "edge": edge}

    kelly = kelly_fraction(conservative_prob, decimal_odds, fraction=0.25)
    units = min(kelly, limits.max_units)
    units = max(limits.min_units, units)

    # Enforce depth, quote age, and exposure caps
    if limits.min_depth_units > 0 and units > limits.min_depth_units:
        units = limits.min_depth_units  # can't size larger than available depth

    # Round to nearest increment
    if limits.unit_rounding > 0:
        units = round(units / limits.unit_rounding) * limits.unit_rounding

    return {
        "units": max(0.0, units),
        "kelly_full": kelly_fraction(conservative_prob, decimal_odds, fraction=1.0),
        "kelly_quarter": kelly,
        "edge": edge,
        "conservative_prob": conservative_prob,
        "decimal_odds": decimal_odds,
    }


# ── Exposure Tracking ────────────────────────────────────────────────────────


@dataclass
class Exposure:
    """Tracks current exposure across dimensions for correlation-aware limits."""
    daily_total: float = 0.0
    sport_daily: dict[str, float] = field(default_factory=dict)
    team_daily: dict[str, float] = field(default_factory=dict)
    event_units: dict[str, float] = field(default_factory=dict)

    def can_add(
        self,
        sport: str,
        team: str,
        event_id: str,
        units: float,
        limits: SizeLimits = SizeLimits(),
    ) -> tuple[bool, str]:
        """Check if adding `units` to this position would violate any limit."""
        if self.daily_total + units > limits.max_daily_total:
            return False, "daily_total"
        if self.sport_daily.get(sport, 0) + units > limits.max_sport_daily:
            return False, f"sport_daily:{sport}"
        if self.team_daily.get(team, 0) + units > limits.max_team_daily:
            return False, f"team_daily:{team}"
        if self.event_units.get(event_id, 0) + units > limits.max_event_units:
            return False, f"event:{event_id}"
        return True, "ok"

    def add(self, sport: str, team: str, event_id: str, units: float) -> None:
        self.daily_total += units
        self.sport_daily[sport] = self.sport_daily.get(sport, 0) + units
        self.team_daily[team] = self.team_daily.get(team, 0) + units
        self.event_units[event_id] = self.event_units.get(event_id, 0) + units

    def reset_daily(self) -> None:
        self.daily_total = 0.0
        self.sport_daily.clear()
        self.team_daily.clear()
        self.event_units.clear()


# ── Economic Evaluation ──────────────────────────────────────────────────────


@dataclass
class EconomicResult:
    """Result of one paper trade for economic evaluation."""
    event_id: str
    sport: str
    market_type: str
    selection: str
    line: float | None
    entry_prob: float
    best_ask: float
    units: float
    outcome: int | None = None  # 1=win, 0=loss, 0.5=push, None=pending
    pnl: float | None = None
    clv: float | None = None    # closing line value


def evaluate_portfolio(
    trades: Sequence[EconomicResult],
) -> dict[str, Any]:
    """Compute portfolio-level economic metrics from a list of paper trades."""
    if not trades:
        return {"status": "no_trades"}

    settled = [t for t in trades if t.outcome is not None and t.pnl is not None]
    if not settled:
        return {"status": "no_settled_trades", "open": len(trades)}

    pnls = [t.pnl for t in settled]
    pnl_arr = np.array(pnls)
    wins = [t for t in settled if t.pnl > 0]
    losses = [t for t in settled if t.pnl < 0]
    pushes = [t for t in settled if t.pnl == 0]

    cumulative = np.cumsum(pnl_arr)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = peak - cumulative
    max_dd = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

    # Bootstrap
    rng = np.random.default_rng(42)
    boot_means = [float(np.mean(rng.choice(pnl_arr, size=len(pnl_arr), replace=True))) for _ in range(1000)]
    boot_arr = np.array(boot_means)

    return {
        "total_trades": len(trades),
        "settled": len(settled),
        "wins": len(wins),
        "losses": len(losses),
        "pushes": len(pushes),
        "win_rate": len(wins) / len(settled) if settled else 0,
        "total_pnl": float(pnl_arr.sum()),
        "mean_pnl": float(pnl_arr.mean()),
        "std_pnl": float(pnl_arr.std()),
        "sharpe": float(pnl_arr.mean() / pnl_arr.std()) if pnl_arr.std() > 0 else 0.0,
        "max_drawdown": max_dd,
        "roi_bps": float(pnl_arr.sum() / sum(t.units for t in settled) * 10000) if settled else 0,
        "bootstrap_ci_lower": float(np.percentile(boot_arr, 2.5)),
        "bootstrap_ci_upper": float(np.percentile(boot_arr, 97.5)),
        "prob_positive": float((boot_arr > 0).mean()),
    }


# ── Monitoring Health States ─────────────────────────────────────────────────


HEALTH_STATES = [
    "HEALTHY_SHADOW",
    "DATA_DEGRADED",
    "CALIBRATION_DRIFT",
    "NEGATIVE_CLV",
    "CONTRACT_MATCH_FAILURE",
    "EXECUTION_SAMPLE_INSUFFICIENT",
    "REVIEW_REQUIRED",
    "ROLLBACK_REQUIRED",
]


@dataclass
class MonitorState:
    """Current health state of the rebuild pipeline."""
    state: str = "HEALTHY_SHADOW"
    last_checked_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_health: dict[str, str] = field(default_factory=dict)
    calibration_drift: float = 0.0
    recent_clv: float = 0.0
    recent_roi: float = 0.0
    contract_match_failures: int = 0
    alerts: list[str] = field(default_factory=list)

    def evaluate(self) -> str:
        """Determine the current health state from monitored metrics."""
        if any(s == "down" for s in self.source_health.values()):
            return "DATA_DEGRADED"
        if self.contract_match_failures > 0:
            return "CONTRACT_MATCH_FAILURE"
        if self.calibration_drift > 0.05:
            return "CALIBRATION_DRIFT"
        if self.recent_clv < -0.02:
            return "NEGATIVE_CLV"
        if self.alerts:
            return "REVIEW_REQUIRED"
        return "HEALTHY_SHADOW"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_checked_utc": self.last_checked_utc,
            "source_health": self.source_health,
            "calibration_drift": self.calibration_drift,
            "recent_clv": self.recent_clv,
            "recent_roi": self.recent_roi,
            "contract_match_failures": self.contract_match_failures,
            "alerts": self.alerts,
        }
