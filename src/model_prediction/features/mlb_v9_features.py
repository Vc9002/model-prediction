"""MLB v9 Comprehensive Point-in-Time Feature Engineering Pipeline.

Unifies four high-signal feature families:
1. Starter State Vector & Expected Depth (K%, BB%, K-BB%, IP depth).
2. Empirical-Bayes PIT Batter Priors & Projected Lineup Offense (wOBA, ISO, discipline).
3. Reliever Talent x Multi-Day Workload Availability (FIP, fatigue, leverage tier).
4. Handedness Platoon Matchup Interactions (LHP/RHP splits).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT
from .park_factors import park_factor
from .platoon_matchup import platoon_matchup_gaps
from .projected_offense import projected_offense_matchup_gaps
from .reliever_availability import reliever_availability_matchup_gaps
from .starter_state import starter_state_matchup_gaps

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"


@dataclass(slots=True)
class MLBv9FeatureVector:
    """Complete unified point-in-time feature representation for an MLB matchup."""

    home_team: str
    away_team: str
    as_of: datetime

    # 1. Starter State Vector
    starter_k_pct_gap: float
    starter_bb_pct_gap: float
    starter_k_bb_gap: float
    starter_depth_gap: float
    home_expected_starter_ip: float
    away_expected_starter_ip: float

    # 2. Projected Offense
    projected_woba_gap: float
    projected_iso_gap: float
    projected_k_pct_gap: float
    projected_bb_pct_gap: float
    home_projected_woba: float
    away_projected_woba: float

    # 3. Reliever Talent x Availability
    bullpen_fip_advantage: float
    bullpen_freshness_advantage: float
    bullpen_hl_advantage: float
    home_bullpen_effective_fip: float
    away_bullpen_effective_fip: float

    # 4. Platoon Matchup
    platoon_woba_advantage: float
    platoon_iso_advantage: float

    # 5. Environment
    park_factor: float

    def to_dict(self) -> dict[str, float]:
        return {
            "starter_k_pct_gap": self.starter_k_pct_gap,
            "starter_bb_pct_gap": self.starter_bb_pct_gap,
            "starter_k_bb_gap": self.starter_k_bb_gap,
            "starter_depth_gap": self.starter_depth_gap,
            "home_expected_starter_ip": self.home_expected_starter_ip,
            "away_expected_starter_ip": self.away_expected_starter_ip,
            "projected_woba_gap": self.projected_woba_gap,
            "projected_iso_gap": self.projected_iso_gap,
            "projected_k_pct_gap": self.projected_k_pct_gap,
            "projected_bb_pct_gap": self.projected_bb_pct_gap,
            "home_projected_woba": self.home_projected_woba,
            "away_projected_woba": self.away_projected_woba,
            "bullpen_fip_advantage": self.bullpen_fip_advantage,
            "bullpen_freshness_advantage": self.bullpen_freshness_advantage,
            "bullpen_hl_advantage": self.bullpen_hl_advantage,
            "home_bullpen_effective_fip": self.home_bullpen_effective_fip,
            "away_bullpen_effective_fip": self.away_bullpen_effective_fip,
            "platoon_woba_advantage": self.platoon_woba_advantage,
            "platoon_iso_advantage": self.platoon_iso_advantage,
            "park_factor": self.park_factor,
        }


def extract_mlb_v9_features(
    home_team: str,
    away_team: str,
    as_of: datetime,
    home_starter_name: str = "",
    away_starter_name: str = "",
    home_starter_throws: str = "R",
    away_starter_throws: str = "R",
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> MLBv9FeatureVector:
    """Extract unified point-in-time v9 features for an MLB game."""
    # 1. Starter state
    starter_gaps = starter_state_matchup_gaps(
        home_starter_name, away_starter_name, as_of, snapshot_path=snapshot_path
    )

    # 2. Projected offense
    from .batter_priors import BatterPriorEngine

    engine = BatterPriorEngine(snapshot_path=snapshot_path)
    as_of_date = as_of.strftime("%Y-%m-%d")
    offense_gaps = projected_offense_matchup_gaps(
        engine,
        home_team,
        away_team,
        as_of_date,
        home_sp_hand=home_starter_throws,
        away_sp_hand=away_starter_throws,
    )

    # 3. Reliever availability
    bullpen_gaps = reliever_availability_matchup_gaps(
        home_team, away_team, as_of, snapshot_path=snapshot_path
    )

    # 4. Platoon splits
    platoon_gaps = platoon_matchup_gaps(
        home_team, away_team, home_starter_throws, away_starter_throws, as_of, snapshot_path=snapshot_path
    )

    # 5. Park factor
    pf = float(park_factor(home_team).get("park_factor", 1.0))

    return MLBv9FeatureVector(
        home_team=home_team,
        away_team=away_team,
        as_of=as_of,
        starter_k_pct_gap=starter_gaps.get("starter_k_pct_gap", 0.0),
        starter_bb_pct_gap=starter_gaps.get("starter_bb_pct_gap", 0.0),
        starter_k_bb_gap=starter_gaps.get("starter_k_bb_gap", 0.0),
        starter_depth_gap=starter_gaps.get("starter_depth_gap", 0.0),
        home_expected_starter_ip=starter_gaps.get("home_expected_starter_ip", 5.5),
        away_expected_starter_ip=starter_gaps.get("away_expected_starter_ip", 5.5),
        projected_woba_gap=offense_gaps.get("projected_offense_quality_gap", 0.0),
        projected_iso_gap=offense_gaps.get("projected_offense_power_gap", 0.0),
        projected_k_pct_gap=offense_gaps.get("projected_offense_kbb_gap", 0.0),
        projected_bb_pct_gap=0.0,
        home_projected_woba=offense_gaps.get("home_projected_xwoba", 0.318),
        away_projected_woba=offense_gaps.get("away_projected_xwoba", 0.318),
        bullpen_fip_advantage=bullpen_gaps.get("bullpen_fip_advantage", 0.0),
        bullpen_freshness_advantage=bullpen_gaps.get("bullpen_freshness_advantage", 0.0),
        bullpen_hl_advantage=bullpen_gaps.get("bullpen_hl_advantage", 0.0),
        home_bullpen_effective_fip=bullpen_gaps.get("home_bullpen_effective_fip", 4.10),
        away_bullpen_effective_fip=bullpen_gaps.get("away_bullpen_effective_fip", 4.10),
        platoon_woba_advantage=platoon_gaps.get("platoon_woba_advantage", 0.0),
        platoon_iso_advantage=platoon_gaps.get("platoon_iso_advantage", 0.0),
        park_factor=pf,
    )
