"""Pre-Registered Hypothesis Ledger with Strict Preregistration Contract (Phase F1/F2).

Prevents multiple-testing false discovery across high-dimensional feature interaction
searches by requiring immutable pre-registration of feature expressions, expected signs,
primary/secondary metrics, sample thresholds, dataset snapshot hashes, and code commit SHAs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RegisteredHypothesis:
    """Immutable preregistration contract for a scientific hypothesis."""

    hypothesis_id: str
    description: str
    feature_expression: str
    category: str  # aerodynamics | pitcher_lineup | bullpen_fatigue | schedule_travel
    sport: str
    market: str
    expected_sign: str  # positive | negative
    primary_metric: str  # e.g. residual_mae_gain, brier_delta_vs_m0
    secondary_metrics: tuple[str, ...]  # e.g. (clv_line, clv_price, logloss_delta)
    discovery_period: str  # e.g. 2022-01-01 to 2024-06-30 (60%)
    validation_period: str  # e.g. 2024-07-01 to 2025-06-30 (20%)
    locked_test_period: str  # e.g. 2025-07-01 to 2026-08-27 (20%)
    minimum_n: int  # e.g. 100 games
    promotion_threshold: float  # e.g. 0.005 MAE or 0.002 Brier gain on CI lower bound
    registered_at_utc: str
    status: str  # registered | discovery_eval | validated | rejected | insufficient_evidence
    dataset_snapshot_hash: str | None = None
    code_commit_sha: str | None = None
    result_hash: str | None = None

    def compute_contract_hash(self) -> str:
        """Deterministic hash of the preregistration terms before evaluation."""
        payload = {
            "hypothesis_id": self.hypothesis_id,
            "feature_expression": self.feature_expression,
            "expected_sign": self.expected_sign,
            "primary_metric": self.primary_metric,
            "discovery_period": self.discovery_period,
            "validation_period": self.validation_period,
            "locked_test_period": self.locked_test_period,
            "minimum_n": self.minimum_n,
            "promotion_threshold": self.promotion_threshold,
            "registered_at_utc": self.registered_at_utc,
            "dataset_snapshot_hash": self.dataset_snapshot_hash or "",
            "code_commit_sha": self.code_commit_sha or "",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Canonical pre-registered interaction hypotheses for MLB (Phase F1/F2)
REGISTERED_MLB_HYPOTHESES: list[RegisteredHypothesis] = [
    RegisteredHypothesis(
        hypothesis_id="MLB-INT-001",
        description="Outward stadium wind vector amplified by batter barrel rate increases total run scoring residual.",
        feature_expression="wind_out_mph * top_order_barrel_rate",
        category="aerodynamics",
        sport="mlb",
        market="total",
        expected_sign="positive",
        primary_metric="brier_delta_vs_m0",
        secondary_metrics=("residual_mae_gain", "clv_line", "clv_price"),
        discovery_period="2022-01-01 to 2024-06-30",
        validation_period="2024-07-01 to 2025-06-30",
        locked_test_period="2025-07-01 to 2026-08-27",
        minimum_n=100,
        promotion_threshold=0.002,
        registered_at_utc="2026-08-28T00:00:00Z",
        status="registered",
    ),
    RegisteredHypothesis(
        hypothesis_id="MLB-INT-002",
        description="Air density ratio (rho/rho_0) interacting with team exit velocity suppresses home run frequency in cold/dense air.",
        feature_expression="air_density_ratio * team_avg_exit_velocity",
        category="aerodynamics",
        sport="mlb",
        market="total",
        expected_sign="negative",
        primary_metric="brier_delta_vs_m0",
        secondary_metrics=("residual_mae_gain", "clv_line", "clv_price"),
        discovery_period="2022-01-01 to 2024-06-30",
        validation_period="2024-07-01 to 2025-06-30",
        locked_test_period="2025-07-01 to 2026-08-27",
        minimum_n=100,
        promotion_threshold=0.002,
        registered_at_utc="2026-08-28T00:00:00Z",
        status="registered",
    ),
    RegisteredHypothesis(
        hypothesis_id="MLB-INT-003",
        description="Starter strikeout rate interacting with high-strikeout opponent lineup produces super-linear run suppression.",
        feature_expression="starter_k_pct * opponent_lineup_k_pct",
        category="pitcher_lineup",
        sport="mlb",
        market="total",
        expected_sign="negative",
        primary_metric="brier_delta_vs_m0",
        secondary_metrics=("residual_mae_gain", "clv_line", "clv_price"),
        discovery_period="2022-01-01 to 2024-06-30",
        validation_period="2024-07-01 to 2025-06-30",
        locked_test_period="2025-07-01 to 2026-08-27",
        minimum_n=100,
        promotion_threshold=0.002,
        registered_at_utc="2026-08-28T00:00:00Z",
        status="registered",
    ),
    RegisteredHypothesis(
        hypothesis_id="MLB-INT-004",
        description="Low expected starter innings (early hook) interacting with high bullpen pitch load increases late-inning scoring.",
        feature_expression="starter_expected_ip * bullpen_3day_pitch_count",
        category="bullpen_fatigue",
        sport="mlb",
        market="total",
        expected_sign="positive",
        primary_metric="brier_delta_vs_m0",
        secondary_metrics=("residual_mae_gain", "clv_line", "clv_price"),
        discovery_period="2022-01-01 to 2024-06-30",
        validation_period="2024-07-01 to 2025-06-30",
        locked_test_period="2025-07-01 to 2026-08-27",
        minimum_n=100,
        promotion_threshold=0.002,
        registered_at_utc="2026-08-28T00:00:00Z",
        status="registered",
    ),
    RegisteredHypothesis(
        hypothesis_id="MLB-INT-005",
        description="Short rest (<4 days) following coast-to-coast flight travel (>1500 miles) penalizes starter run suppression.",
        feature_expression="starter_rest_days * haversine_travel_miles",
        category="schedule_travel",
        sport="mlb",
        market="margin",
        expected_sign="negative",
        primary_metric="brier_delta_vs_m0",
        secondary_metrics=("residual_mae_gain", "clv_line", "clv_price"),
        discovery_period="2022-01-01 to 2024-06-30",
        validation_period="2024-07-01 to 2025-06-30",
        locked_test_period="2025-07-01 to 2026-08-27",
        minimum_n=100,
        promotion_threshold=0.002,
        registered_at_utc="2026-08-28T00:00:00Z",
        status="registered",
    ),
]


class HypothesisRegistry:
    """Manages pre-registered hypotheses and prevents post-hoc selection."""

    def __init__(self, hypotheses: list[RegisteredHypothesis] | None = None) -> None:
        self._hypotheses: dict[str, RegisteredHypothesis] = {
            h.hypothesis_id: h for h in (hypotheses or REGISTERED_MLB_HYPOTHESES)
        }

    def get(self, hypothesis_id: str) -> RegisteredHypothesis | None:
        return self._hypotheses.get(hypothesis_id)

    def list_all(self, sport: str | None = None) -> list[RegisteredHypothesis]:
        if sport:
            return [h for h in self._hypotheses.values() if h.sport.lower() == sport.lower()]
        return list(self._hypotheses.values())
