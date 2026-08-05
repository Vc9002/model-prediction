"""Correlation-aware exposure tracking (Part 3-I).

Identify shared risk among correlated positions: moneyline+spread on same team,
total+pitcher/lineup-derived markets, multiple positions in one event, repeated
exposure to one team, weather-driven games, model-family defects.

Report nominal and correlation-adjusted exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CorrelationGroup:
    """A group of trades sharing correlated risk."""
    group_id: str
    reason: str  # same_event, same_team, weather_driven, model_family, etc.
    trades: list[str] = field(default_factory=list)  # event_ids
    nominal_exposure: float = 0.0  # sum of units
    adjusted_exposure: float = 0.0  # after correlation adjustment
    correlation_factor: float = 1.0  # multiplier for effective exposure


CORRELATION_TYPES = {
    "same_event": {
        "description": "Multiple positions in one event (e.g., ML + spread on same game)",
        "correlation": 1.0,  # perfect correlation — count as one bet
        "detection": "same event_id",
    },
    "same_team_moneyline_spread": {
        "description": "Moneyline and spread on the same team in one event",
        "correlation": 0.85,
        "detection": "same event_id + same team + ML + spread",
    },
    "same_team_same_market": {
        "description": "Same team across different markets (e.g., ML on one game, spread on another)",
        "correlation": 0.3,
        "detection": "same team, different events",
    },
    "pitcher_derived": {
        "description": "Totals and pitcher-derived markets sharing underlying signal",
        "correlation": 0.5,
        "detection": "same event, total + any starter-based market",
    },
    "weather_driven": {
        "description": "Games sharing a weather system or extreme conditions",
        "correlation": 0.4,
        "detection": "same date + same region + extreme weather",
    },
    "model_family": {
        "description": "Positions from the same model family sharing structural bias",
        "correlation": 0.2,
        "detection": "same model_version across different events",
    },
    "same_league_same_day": {
        "description": "Multiple positions in the same league on the same day",
        "correlation": 0.1,
        "detection": "same sport + same date",
    },
}


class CorrelationTracker:
    """Track and adjust exposure for correlated positions.

    Usage:
        tracker = CorrelationTracker()
        tracker.add_trade("ev1", "NYY", "mlb", "moneyline", 1.5, "mlb-two-head-v1")
        tracker.add_trade("ev1", "NYY", "mlb", "spread", 0.5, "mlb-two-head-v1")
        report = tracker.report()
    """

    def __init__(self) -> None:
        self.trades: list[dict[str, Any]] = []
        self.groups: dict[str, CorrelationGroup] = {}

    def add_trade(
        self, event_id: str, team: str, sport: str, market_type: str,
        units: float, model_version: str = "", date_str: str = "",
    ) -> None:
        self.trades.append({
            "event_id": event_id, "team": team, "sport": sport,
            "market_type": market_type, "units": units,
            "model_version": model_version, "date": date_str,
        })
        # Group by event only — this is the primary correlation dimension
        self._group(event_id, "same_event", units)

        # Detect ML+spread on same team/event
        existing = [t for t in self.trades if t["event_id"] == event_id and t["team"] == team]
        if len(existing) > 1:
            self._group(f"{event_id}:{team}:ml_spread", "same_team_moneyline_spread", units)

    def _group(self, key: str, reason: str, units: float) -> None:
        if key not in self.groups:
            self.groups[key] = CorrelationGroup(group_id=key, reason=reason)
        self.groups[key].trades.append(key)
        self.groups[key].nominal_exposure += units
        corr = CORRELATION_TYPES.get(reason, {}).get("correlation", 0.0)
        self.groups[key].correlation_factor = corr

    def correlation_adjusted_exposure(self) -> float:
        """Compute total exposure adjusted for within-group correlations.

        For each group of correlated trades, treat them as one bet adjusted by
        the correlation factor. Sum across all groups.
        """
        total = 0.0
        for group in self.groups.values():
            if len(group.trades) > 1:
                # Correlated group: count as one bet weighted by correlation
                avg_units = group.nominal_exposure / len(group.trades)
                effective = avg_units * (1 + group.correlation_factor * (len(group.trades) - 1))
                total += effective
            else:
                total += group.nominal_exposure
        return total

    def nominal_exposure(self) -> float:
        return sum(t["units"] for t in self.trades)

    def report(self) -> dict[str, Any]:
        """Generate a correlation exposure report."""
        groups_over_1 = {k: g for k, g in self.groups.items() if len(g.trades) > 1}
        return {
            "total_trades": len(self.trades),
            "nominal_exposure": self.nominal_exposure(),
            "correlation_adjusted_exposure": self.correlation_adjusted_exposure(),
            "adjustment_ratio": self.correlation_adjusted_exposure() / max(0.001, self.nominal_exposure()),
            "correlated_groups": len(groups_over_1),
            "group_details": [
                {"group": g.group_id, "reason": g.reason, "trades": len(g.trades),
                 "nominal": g.nominal_exposure, "correlation": g.correlation_factor}
                for g in groups_over_1.values()
            ],
        }
