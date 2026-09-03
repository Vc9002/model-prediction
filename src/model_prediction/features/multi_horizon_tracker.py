"""Multi-Horizon Evidence & Information-Delta Tracking Engine (Items 17 & 18).

Captures model and market states across discrete pregame decision horizons:
- T-6h: Early market discovery (pre-lineup baseline)
- T-3h: Starting pitcher confirmation / early injury reports
- T-1h: Official starting lineups posted / confirmed weather updates
- T-30m: Primary decision horizon (maximum liquidity / minimal spread)
- T-10m: Final execution horizon (pre-game lock)

Computes point-in-time Information Deltas:
    Delta = State_now - Expected_State_open
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT

HORIZON_LABELS = ("T-6h", "T-3h", "T-1h", "T-30m", "T-10m")


@dataclass(frozen=True)
class HorizonObservation:
    event_id: str
    sport: str
    market_slug: str
    horizon_label: str
    observed_at_utc: str
    event_start_utc: str
    minutes_to_start: float
    model_id: str
    model_prob: float
    market_fair_prob: float
    market_bid: float
    market_ask: float
    starter_status: str  # "confirmed" | "projected"
    lineup_status: str  # "confirmed" | "projected"
    wind_mph: float | None
    temperature_f: float | None
    lineup_woba_delta: float
    starter_csw_delta: float
    market_move_from_open: float
    model_artifact_hash: str | None = None
    feature_schema_hash: str | None = None
    feature_snapshot_observed_at: str | None = None
    feature_snapshot_hash: str | None = None
    market_snapshot_observed_at: str | None = None
    market_snapshot_hash: str | None = None
    candidate_frozen_at: str | None = None
    prediction_created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sport": self.sport,
            "market_slug": self.market_slug,
            "horizon_label": self.horizon_label,
            "observed_at_utc": self.observed_at_utc,
            "event_start_utc": self.event_start_utc,
            "minutes_to_start": round(self.minutes_to_start, 1),
            "model_id": self.model_id,
            "model_prob": round(self.model_prob, 4),
            "market_fair_prob": round(self.market_fair_prob, 4),
            "market_bid": round(self.market_bid, 4),
            "market_ask": round(self.market_ask, 4),
            "starter_status": self.starter_status,
            "lineup_status": self.lineup_status,
            "wind_mph": self.wind_mph,
            "temperature_f": self.temperature_f,
            "lineup_woba_delta": round(self.lineup_woba_delta, 4),
            "starter_csw_delta": round(self.starter_csw_delta, 4),
            "market_move_from_open": round(self.market_move_from_open, 4),
            "model_artifact_hash": self.model_artifact_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "feature_snapshot_observed_at": self.feature_snapshot_observed_at or self.observed_at_utc,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "market_snapshot_observed_at": self.market_snapshot_observed_at or self.observed_at_utc,
            "market_snapshot_hash": self.market_snapshot_hash,
            "candidate_frozen_at": self.candidate_frozen_at,
            "prediction_created_at": self.prediction_created_at or self.observed_at_utc,
        }


class MultiHorizonTracker:
    """Stores and analyzes multi-horizon market trajectories."""

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = log_path or (PROJECT_ROOT / "data/horizon_observations.jsonl")

    def record_observation(self, obs: HorizonObservation) -> None:
        """Append an immutable horizon observation to disk."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obs.to_dict(), sort_keys=True) + "\n")

    def load_observations(self, event_id: str | None = None) -> list[dict[str, Any]]:
        """Load recorded horizon observations from disk."""
        if not self.log_path.is_file():
            return []
        records = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if event_id is None or r.get("event_id") == event_id:
                records.append(r)
        return records

    @staticmethod
    def infer_horizon_label(minutes_to_start: float) -> str:
        """Classify minutes until game into canonical horizon bin."""
        if minutes_to_start >= 300:
            return "T-6h"
        elif minutes_to_start >= 150:
            return "T-3h"
        elif minutes_to_start >= 45:
            return "T-1h"
        elif minutes_to_start >= 20:
            return "T-30m"
        else:
            return "T-10m"
