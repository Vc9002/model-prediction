"""Correlation-aware exposure tracking (Part 3-I).

Tracks positions grouped by event for correlation adjustment.
Only the event-group is used for correlation adjustment — positions are not
double-counted across overlapping groups. Nominal exposure is the simple sum
of all position units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CorrelationGroup:
    group_id: str
    reason: str
    trades: list[str] = field(default_factory=list)
    nominal_exposure: float = 0.0
    correlation_factor: float = 1.0


CORRELATION_TYPES = {
    "same_event": {
        "correlation": 1.0,
    },
    "same_team_moneyline_spread": {
        "correlation": 0.85,
    },
}


class CorrelationTracker:
    """Tracks positions and calculates correlation-adjusted exposure.

    Each position enters the 'same_event' group for its event.
    If multiple positions exist on the same team+event (ML+spread),
    the correlation_adjusted_exposure counts them as one bet weighted
    by the correlation factor.
    """

    def __init__(self) -> None:
        self.trades: list[dict[str, Any]] = []
        self.event_groups: dict[str, CorrelationGroup] = {}

    def add_trade(
        self, event_id: str, team: str, sport: str, market_type: str,
        units: float, model_version: str = "", date_str: str = "",
    ) -> None:
        self.trades.append({
            "event_id": event_id, "team": team, "sport": sport,
            "market_type": market_type, "units": units,
            "model_version": model_version, "date": date_str,
        })
        if event_id not in self.event_groups:
            self.event_groups[event_id] = CorrelationGroup(
                group_id=event_id, reason="same_event",
                correlation_factor=CORRELATION_TYPES["same_event"]["correlation"],
            )
        self.event_groups[event_id].trades.append(event_id)
        self.event_groups[event_id].nominal_exposure += units

    def correlation_adjusted_exposure(self) -> float:
        """Compute exposure adjusted for within-event correlations.

        For each event group with >1 position, check if they're on the same
        team (ML+spread) — those get correlation-adjusted.
        Positions in different events are independent and summed normally.
        """
        adjusted = 0.0
        for event_id, group in self.event_groups.items():
            if len(group.trades) <= 1:
                adjusted += group.nominal_exposure
                continue
            # Check for same-team ML+spread positions within this event
            teams: dict[str, list[dict[str, Any]]] = {}
            for t in self.trades:
                if t["event_id"] == event_id:
                    teams.setdefault(t["team"], []).append(t)
            for team_trades in teams.values():
                if len(team_trades) > 1:
                    # ML+spread on same team: count as one bet weighted by corr
                    avg_units = sum(t["units"] for t in team_trades) / len(team_trades)
                    corr = CORRELATION_TYPES["same_team_moneyline_spread"]["correlation"]
                    adjusted += avg_units * (1 + corr * (len(team_trades) - 1))
                else:
                    adjusted += team_trades[0]["units"]
        return adjusted

    def nominal_exposure(self) -> float:
        return sum(t["units"] for t in self.trades)

    def report(self) -> dict[str, Any]:
        groups_with_multi = {k: g for k, g in self.event_groups.items() if len(g.trades) > 1}
        return {
            "total_trades": len(self.trades),
            "nominal_exposure": self.nominal_exposure(),
            "correlation_adjusted_exposure": self.correlation_adjusted_exposure(),
            "adjustment_ratio": self.correlation_adjusted_exposure() / max(0.001, self.nominal_exposure()),
            "correlated_events": len(groups_with_multi),
            "event_details": [
                {"event": g.group_id, "trades": len(g.trades),
                 "nominal": g.nominal_exposure, "correlation": g.correlation_factor}
                for g in groups_with_multi.values()
            ],
        }
