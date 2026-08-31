"""Multi-Horizon Health & Coverage Monitor (src/model_prediction/features/multi_horizon_health.py).

Verifies multi-horizon snapshot capture across active sports (T-6h, T-3h, T-1h, T-30m, T-10m)
and ensures temporal causality: observed_at_utc < event_start_utc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from .multi_horizon_tracker import HORIZON_LABELS, MultiHorizonTracker


def generate_multi_horizon_health_report(
    tracker: MultiHorizonTracker | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root or PROJECT_ROOT
    h_tracker = tracker or MultiHorizonTracker(log_path=root / "data/horizon_observations.jsonl")
    observations = h_tracker.load_observations()

    # Group by sport and event
    by_sport_events: dict[str, dict[str, set[str]]] = {}
    invalid_causality_count = 0

    for obs in observations:
        sport = str(obs.get("sport") or "UNKNOWN").upper()
        ev_id = str(obs.get("event_id") or "")
        horizon = str(obs.get("horizon_label") or "")

        # Verify temporal causality
        obs_utc = obs.get("observed_at_utc")
        ev_utc = obs.get("event_start_utc")
        if obs_utc and ev_utc:
            try:
                obs_dt = parse_utc(obs_utc)
                ev_dt = parse_utc(ev_utc)
                if obs_dt >= ev_dt:
                    invalid_causality_count += 1
            except (ValueError, TypeError):
                invalid_causality_count += 1

        if sport not in by_sport_events:
            by_sport_events[sport] = {}
        if ev_id not in by_sport_events[sport]:
            by_sport_events[sport][ev_id] = set()
        by_sport_events[sport][ev_id].add(horizon)

    sports_report: dict[str, Any] = {}
    for sport in ("MLB", "WNBA", "NCAAF", "SOCCER", "TENNIS"):
        events_dict = by_sport_events.get(sport, {})
        n_events = len(events_dict)
        if n_events == 0:
            sports_report[sport] = {
                "total_events": 0,
                "expected_horizons_per_event": len(HORIZON_LABELS),
                "actual_avg_horizons": 0.0,
                "coverage_pct": 0.0,
                "status": "AWAITING_SLATE",
            }
        else:
            total_horizons = sum(len(h_set) for h_set in events_dict.values())
            avg_h = total_horizons / n_events
            cov = min(1.0, avg_h / len(HORIZON_LABELS))
            sports_report[sport] = {
                "total_events": n_events,
                "expected_horizons_per_event": len(HORIZON_LABELS),
                "actual_avg_horizons": round(avg_h, 2),
                "coverage_pct": round(cov * 100.0, 1),
                "status": "HEALTHY" if cov >= 0.80 else "DEGRADED",
            }

    return {
        "total_observations": len(observations),
        "invalid_causality_count": invalid_causality_count,
        "sports_coverage": sports_report,
    }


def main() -> int:
    report = generate_multi_horizon_health_report()
    print("# Multi-Horizon Evidence Coverage Report\n")
    print(f"- **Total Horizon Observations**: {report['total_observations']}")
    print(f"- **Temporal Causality Violations**: {report['invalid_causality_count']}\n")
    print("| Sport | Events Tracked | Expected Horizons | Actual Avg | Coverage | Status |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for sport, dat in report["sports_coverage"].items():
        print(
            f"| {sport} | {dat['total_events']} | {dat['expected_horizons_per_event']} | {dat['actual_avg_horizons']} | {dat['coverage_pct']}% | **{dat['status']}** |"
        )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
